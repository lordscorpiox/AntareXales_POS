"""v0.12 — bitácora, notas de crédito/débito, inmutabilidad de facturas fiscales y migración de la tabla de documentos."""
import os, sqlite3, tempfile
from helpers import GET, POST, PUT, ok, err, Contador, login, c as cliente, H, db, server
import fiscal

chk = Contador()
eq = lambda a, b, t=0.006: abs(a - b) < t

ok(PUT("/api/config", {"tasa_bcv": "50", "negocio_nombre": "Prueba", "iva_activo": "1", "iva_pct": "16", "iva_modo": "adicional"}))
ok(POST("/api/usuarios", {"usuario": "caja2", "nombre": "Caja Dos", "password": "clave123", "rol": "cajero"}))
login("caja2", "clave123")
ADM = {"autoriza": {"usuario": "admin", "password": "admin123"}}

def prod(nombre, precio, **k):
    return ok(POST("/api/productos", {"nombre": nombre, "precio_usd": precio, "costo_usd": 1, "stock": 50, **k}))

def venta(items, pagos, **k):
    return POST("/api/ventas", {"items": [{"producto_id": i, "cantidad": q} for i, q in items], "pagos": pagos, **k})

def stock(p):
    return ok(GET(f"/api/productos/{p['id']}"))["stock"]

def bit(q=""):
    return ok(GET("/api/bitacora?limit=500" + (f"&q={q}" if q else "")))

a = prod("Arroz", 10)
b = prod("Pan", 10, iva_alicuota="reducida")

# ---------- bitácora: registra solo lo que cambia datos, es solo-admin y nadie la edita
n0 = bit()["total"]
err(GET("/api/bitacora", usuario="caja2"))
v = ok(venta([(a["id"], 4), (b["id"], 2)], [{"metodo": "efectivo_usd", "monto": 100}]))
chk(bit()["total"] == n0, "vender no llena la bitácora")
ok(PUT("/api/config", {"doc_pie": "Gracias"}))
reg = bit("configuración")["items"][0]
chk(reg["accion"] == "cambio de configuración" and "doc_pie" in reg["detalle"] and reg["usuario"] == "admin", reg)
err(POST("/api/auth/login", {"usuario": "admin", "password": "mala"}))
chk(any(r["accion"] == "inicio de sesión fallido" for r in bit()["items"]))
con = sqlite3.connect(db.get_db_path())
for sql in ("UPDATE bitacora SET detalle='x'", "DELETE FROM bitacora"):
    try:
        con.execute(sql); con.commit(); chk(False, "la bitácora no debe poder modificarse")
    except sqlite3.DatabaseError as e:
        chk("bitácora" in str(e))
con.close()

# ---------- factura no fiscal: se puede anular, con o sin motivo
d_nf = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v["id"]}, esperar=201))
f = d_nf
items = {i["nombre"]: i for i in f["items"]}

# ---------- nota de crédito parcial (modo no fiscal)
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "motivo": "x", "items": [{"item_id": items["Arroz"]["id"], "cantidad": 1}]}, usuario="caja2"), "administrador")
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "items": [{"item_id": items["Arroz"]["id"], "cantidad": 1}]}), "motivo")
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "motivo": "dev", "items": [{"item_id": items["Arroz"]["id"], "cantidad": 9}]}), "solo se puede acreditar")
st0 = stock(a)
nc = ok(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "motivo": "Devolución", "devolver_stock": True,
                                 "items": [{"item_id": items["Arroz"]["id"], "cantidad": 1}], "reembolso": "ninguno"}, esperar=201))
chk(nc["tipo"] == "nota_credito" and nc["numero"] == 1 and nc["doc_ref_id"] == f["id"] and nc["fiscal"] == 0)
chk(eq(nc["total_usd"], 11.6) and eq(nc["iva_usd"], 1.6) and eq(nc["base_usd"], 10.0), nc)
chk(eq(nc["total_bs"], 580), nc["total_bs"])   # a la tasa de la factura
chk(stock(a) == st0 + 1, "devolvió 1 al inventario")
# lo ya acreditado no se puede repetir: de 4 quedan 3
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "motivo": "dev", "items": [{"item_id": items["Arroz"]["id"], "cantidad": 4}]}), "solo se puede acreditar 3")
nc2 = ok(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "motivo": "Resto", "items": [{"item_id": items["Arroz"]["id"], "cantidad": 3}, {"item_id": items["Pan"]["id"], "cantidad": 2}]}, esperar=201))
chk(nc2["numero"] == 2)
# acreditado todo = total exacto de la factura (sin centavos de diferencia)
chk(eq(nc["total_usd"] + nc2["total_usd"], f["total_usd"], 0.0001) and eq(nc["total_bs"] + nc2["total_bs"], f["total_bs"], 0.0001), (nc2["total_usd"], f["total_usd"]))
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f["id"], "motivo": "otra", "items": [{"item_id": items["Pan"]["id"], "cantidad": 1}]}), "acreditar")
# una factura con notas ni la venta se anulan
err(POST(f"/api/documentos/{f['id']}/anular", {}), "notas")
err(POST(f"/api/ventas/{v['id']}/anular", {}), "notas de crédito")
err(POST(f"/api/documentos/{nc['id']}/anular", {}), "no se anula")

# ---------- reembolsos
v2 = ok(venta([(a["id"], 2)], [{"metodo": "efectivo_usd", "monto": 30}]))
f2 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"]}, esperar=201))
it2 = f2["items"][0]
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f2["id"], "motivo": "dev", "reembolso": "efectivo_usd", "items": [{"item_id": it2["id"], "cantidad": 1}]}), "turno")
ok(POST("/api/caja/abrir", {"fondo_usd": 0, "fondo_bs": 0}))
ncr = ok(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f2["id"], "motivo": "dev", "reembolso": "efectivo_usd", "items": [{"item_id": it2["id"], "cantidad": 1}]}, esperar=201))
mov = ok(GET("/api/caja/turno"))
movs = mov.get("movimientos") or mov.get("movs") or []
chk(any(m["categoria"] == "Devolución de venta" and eq(m["monto"], 11.6) for m in movs), movs)
# deuda del cliente
cli = ok(POST("/api/clientes", {"nombre": "Ana", "cedula": "V-12345678"}))
v3 = ok(venta([(a["id"], 2)], [], cliente_id=cli["id"], a_credito=True))
f3 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v3["id"]}, esperar=201))
sal = lambda: next(x for x in ok(GET("/api/clientes")) if x["id"] == cli["id"])["saldo_usd"]
chk(eq(sal(), 23.2))
ok(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f3["id"], "motivo": "dev", "reembolso": "deuda", "items": [{"item_id": f3["items"][0]["id"], "cantidad": 1}]}, esperar=201))
chk(eq(sal(), 11.6), sal())
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f2["id"], "motivo": "dev", "reembolso": "deuda", "items": [{"item_id": it2["id"], "cantidad": 1}]}), "cliente")

# ---------- nota de débito
nd = ok(POST("/api/documentos", {"tipo": "nota_debito", "doc_ref_id": f["id"], "motivo": "Flete", "items": [{"descripcion": "Flete", "monto_usd": 5, "alicuota": "general"}, {"descripcion": "Seguro", "monto_usd": 2, "alicuota": "exento"}]}, esperar=201))
chk(nd["numero"] == 1 and eq(nd["total_usd"], 5 * 1.16 + 2) and eq(nd["iva_usd"], 0.8) and eq(nd["exento_usd"], 2), nd)
err(POST("/api/documentos", {"tipo": "nota_debito", "doc_ref_id": f["id"], "motivo": "x", "items": []}), "al menos")
err(POST("/api/documentos", {"tipo": "nota_debito", "doc_ref_id": f["id"], "motivo": "x", "items": [{"descripcion": "a", "monto_usd": 1}], "reembolso": "efectivo_usd"}), "reembolso")
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": nd["id"], "motivo": "x", "items": []}), "factura")
rr = ok(GET("/api/reportes/resumen"))
chk(rr["notas_credito_n"] >= 3 and rr["notas_debito_n"] == 1 and eq(rr["notas_debito_usd"], nd["total_usd"]))

# ---------- fiscal: inmutabilidad y control
ok(PUT("/api/config", {"fiscal_razon_social": "Comercial Prueba C.A.", "rif": "J-07013380-5", "direccion": "Av. Guayana",
                       "igtf_activo": "1", "fiscal_activo": "1"}))
v4 = ok(venta([(a["id"], 3)], [{"metodo": "efectivo_usd", "monto": 35.84}]))     # 34.80 + 3 % de IGTF (1,04)
chk(eq(v4["igtf_usd"], 1.04), v4)
f4 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v4["id"], "control": "00-000900"}, esperar=201))
err(POST(f"/api/documentos/{f4['id']}/anular", {"motivo": "error"}), "nota de crédito")
err(POST(f"/api/ventas/{v4['id']}/anular", {"motivo": "error"}), "nota de crédito")
# nota de crédito fiscal: pide control, copia emisor y devuelve la parte proporcional del IGTF
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f4["id"], "motivo": "dev", "items": [{"item_id": f4["items"][0]["id"], "cantidad": 1}]}), "control")
err(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f4["id"], "motivo": "dev", "control": "00-000900", "items": [{"item_id": f4["items"][0]["id"], "cantidad": 1}]}), "ya fue usado")
nf = ok(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f4["id"], "motivo": "dev", "control": "00-000901", "items": [{"item_id": f4["items"][0]["id"], "cantidad": 1}]}, esperar=201))
chk(nf["fiscal"] == 1 and nf["emisor_rif"] == "J-07013380-5" and nf["control"] == "00-000901", nf)
chk(eq(nf["igtf_usd"], 0.35) and eq(nf["total_usd"], 11.6), nf)
nd2 = ok(POST("/api/documentos", {"tipo": "nota_debito", "doc_ref_id": f4["id"], "motivo": "int", "control": "00-000902", "items": [{"descripcion": "Intereses", "monto_usd": 1}]}, esperar=201))
chk(nd2["fiscal"] == 1)
# permitir anular facturas fiscales (opción): exige motivo
ok(PUT("/api/config", {"fiscal_permite_anular": "1"}))
v5 = ok(venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.95}]))
f5 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v5["id"], "control": "00-000903"}, esperar=201))
err(POST(f"/api/documentos/{f5['id']}/anular", {}), "motivo")
ok(POST(f"/api/documentos/{f5['id']}/anular", {"motivo": "Error de RIF"}))
chk(ok(GET(f"/api/documentos/{f5['id']}"))["anulado_motivo"] == "Error de RIF")
err(POST(f"/api/ventas/{v5['id']}/anular", {}), "motivo")   # en modo fiscal toda anulación pide motivo
ok(POST(f"/api/ventas/{v5['id']}/anular", {"motivo": "Cliente se arrepintió"}))
chk(ok(GET(f"/api/ventas/{v5['id']}"))["anulada_motivo"] == "Cliente se arrepintió")
ok(PUT("/api/config", {"fiscal_permite_anular": "0"}))
# la base de datos también protege los documentos fiscales aunque alguien la toque por fuera de la app
con = sqlite3.connect(db.get_db_path())
for sql in ("UPDATE documentos SET total_usd=1 WHERE id=%d" % f4["id"], "DELETE FROM documentos WHERE id=%d" % f4["id"],
            "DELETE FROM documento_items WHERE documento_id=%d" % f4["id"], "UPDATE documento_items SET cantidad=9 WHERE documento_id=%d" % f4["id"]):
    try:
        con.execute(sql); con.commit(); chk(False, sql)
    except sqlite3.DatabaseError:
        chk(True)
con.close()
chk(any("nota_credito" in r["detalle"] and "00-000901" in r["detalle"] for r in bit("emisión")["items"]), "la emisión queda en la bitácora con su control")
chk(any(r["accion"] == "anulación de venta" and "Cliente se arrepintió" in r["detalle"] for r in bit()["items"]))

# ---------- migración: base v0.11 con documentos y líneas → se conservan y admiten notas
p = os.path.join(tempfile.mkdtemp(), "v011.db")
con = sqlite3.connect(p)
viejo = db.SCHEMA.replace("'nota_entrega','nota_credito','nota_debito')", "'nota_entrega')")
con.executescript(viejo)
con.execute("INSERT INTO documentos(tipo, numero, fecha, tasa, total_usd, cli_nombre) VALUES('factura', 7, '2026-01-01 10:00:00', 40, 5, 'Viejo')")
con.execute("INSERT INTO documento_items(documento_id, nombre, cantidad, total_usd) VALUES(1, 'Cosa', 1, 5)")
con.commit()
try:
    con.execute("INSERT INTO documentos(tipo, numero, fecha) VALUES('nota_credito', 1, 'x')"); chk(False)
except sqlite3.IntegrityError:
    chk(True, "la tabla vieja no admitía notas")
con.close()
actual = db.get_db_path
db.get_db_path = lambda: p
db.init_db()
con = sqlite3.connect(p)
chk(con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1 and con.execute("SELECT COUNT(*) FROM documento_items").fetchone()[0] == 1, "no se perdió nada")
con.execute("INSERT INTO documentos(tipo, numero, fecha) VALUES('nota_credito', 1, 'x')")
chk(con.execute("SELECT numero, cli_nombre FROM documentos WHERE id=1").fetchone() == (7, "Viejo"))
chk(len(con.execute("PRAGMA index_list(documentos)").fetchall()) >= 3, "índices recreados")
con.close()
db.init_db()   # segunda vez: idempotente
db.get_db_path = actual

print(f"test_v012 OK ({chk.n} comprobaciones)")

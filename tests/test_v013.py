"""v0.13 — libros de ventas/compras, contingencia, respaldo externo y alertas."""
import os, sqlite3, tempfile
from helpers import GET, POST, PUT, ok, err, Contador, login, db

chk = Contador()
eq = lambda a, b, t=0.006: abs(a - b) < t

ok(PUT("/api/config", {"tasa_bcv": "50", "negocio_nombre": "Prueba", "iva_activo": "1", "iva_pct": "16", "iva_modo": "adicional"}))
ok(POST("/api/usuarios", {"usuario": "caja3", "nombre": "Caja Tres", "password": "clave123", "rol": "cajero"}))
login("caja3", "clave123")

def prod(nombre, precio, **k):
    return ok(POST("/api/productos", {"nombre": nombre, "precio_usd": precio, "costo_usd": 1, "stock": 100, **k}))

def venta(items, pagos):
    return ok(POST("/api/ventas", {"items": [{"producto_id": i, "cantidad": q} for i, q in items], "pagos": pagos}))

a = prod("Arroz", 10)
ex = prod("Medicina", 20, iva_exento=1)

# ---------- por defecto todo apagado
cfg = ok(GET("/api/config"))
chk(cfg["contingencia_activa"] == "0" and cfg["respaldo_externo_activo"] == "0" and cfg["fiscal_activo"] == "0", "valores por defecto")

# ---------- libros: solo administrador
err(GET("/api/libros/ventas", usuario="caja3"))
err(GET("/api/libros/compras", usuario="caja3"))
err(GET("/api/libros/ventas?desde=2026-02-30"), "fecha")
err(GET("/api/libros/ventas?desde=2026-05-02&hasta=2026-05-01"), "posterior")

# ---------- libro de ventas
v1 = venta([(a["id"], 2), (ex["id"], 1)], [{"metodo": "efectivo_usd", "monto": 60}])
f1 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v1["id"], "control": "00-100"}, esperar=201))
v2 = venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 20}])
f2 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"], "control": "00-101"}, esperar=201))
ok(POST("/api/documentos", {"tipo": "nota_entrega", "venta_id": v2["id"]}, esperar=201))   # no entra en el libro
adm = {"autoriza": {"usuario": "admin", "password": "admin123"}}
it = f2["items"][0]
nc = ok(POST("/api/documentos", {"tipo": "nota_credito", "doc_ref_id": f2["id"], "motivo": "devolución", "control": "00-102",
                                 "items": [{"item_id": it["id"], "cantidad": 1}], **adm}, esperar=201))
lv = ok(GET("/api/libros/ventas"))
chk(lv["n"] == 3 and [i["tipo"] for i in lv["items"]] == ["factura", "factura", "nota_credito"], lv["items"])
chk(lv["items"][2]["total_usd"] < 0 and lv["items"][2]["ref"].startswith("Factura "), lv["items"][2])
chk(eq(lv["totales"]["total_usd"], f1["total_usd"] + f2["total_usd"] - nc["total_usd"]), lv["totales"])
chk(eq(lv["totales"]["total_bs"], round(lv["totales"]["total_usd"] * 50, 2)), lv["totales"])
al = {x["alicuota"]: x for x in lv["por_alicuota"]}
chk(set(al) == {"exento", "16"} and eq(al["exento"]["base_usd"], 20) and eq(al["16"]["base_usd"], 20), al)   # 2 + 1 arroz − 1 devuelto
chk(eq(al["16"]["iva_usd"], 3.2), al)
chk(lv["emisor"]["nombre"] == "Prueba" and lv["pendientes_control"] == 0)
# período sin movimientos
vacio = ok(GET("/api/libros/ventas?desde=2020-01-01&hasta=2020-01-31"))
chk(vacio["n"] == 0 and vacio["totales"]["total_usd"] == 0)
# anulada: aparece en cero
ok(POST(f"/api/documentos/{f1['id']}/anular", {"motivo": "prueba"}))
lv2 = ok(GET("/api/libros/ventas"))
fila = [i for i in lv2["items"] if i["id"] == f1["id"]][0]
chk(fila["estado"] == "anulado" and fila["total_usd"] == 0 and lv2["anulados"] == 1, fila)

# ---------- compras con datos de factura del proveedor
prov = ok(POST("/api/proveedores", {"nombre": "Distribuidora Sur", "rif": "J-12345678-0"}))
st, j = POST("/api/compras", {"proveedor_id": prov["id"], "condicion": "contado", "metodo": "efectivo_usd",
                              "items": [{"producto_id": a["id"], "cantidad": 10, "costo_usd": 5}], "tasa": 40,
                              "fact_numero": "A-0001", "fact_control": "00-77", "fact_fecha": "2026-11-01", "iva_usd": 8, "exento_usd": 0})
chk(st == 201, j)
c1 = j["data"]
chk(c1["fact_numero"] == "A-0001" and eq(c1["iva_usd"], 8), c1)
err(POST("/api/compras", {"proveedor_id": prov["id"], "condicion": "credito", "items": [{"producto_id": a["id"], "cantidad": 1, "costo_usd": 5}],
                          "iva_usd": 9}), "superar")
ok(POST("/api/compras", {"proveedor_id": prov["id"], "condicion": "credito", "items": [{"producto_id": a["id"], "cantidad": 2, "costo_usd": 5}]}, esperar=201))
lc = ok(GET("/api/libros/compras"))
chk(lc["n"] == 2 and lc["sin_factura"] == 1, lc)
f = lc["items"][0]
chk(eq(f["total_usd"], 50) and eq(f["iva_usd"], 8) and eq(f["base_usd"], 42) and eq(f["total_bs"], 2000) and eq(f["iva_bs"], 320) and f["rif"] == "J-12345678-0", f)
chk(eq(lc["totales"]["total_usd"], 60) and eq(lc["totales"]["iva_usd"], 8), lc["totales"])
ok(POST(f"/api/compras/{c1['id']}/anular", {}))
chk(ok(GET("/api/libros/compras"))["totales"]["total_usd"] == 10)

# ---------- contingencia
ok(PUT("/api/config", {"fiscal_razon_social": "Comercial Prueba C.A.", "rif": "J-07013380-5", "direccion": "Av. Guayana", "fiscal_activo": "1"}))
v3 = venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 200}])
err(POST("/api/documentos", {"tipo": "factura", "venta_id": v3["id"]}), "control")
ok(PUT("/api/config", {"contingencia_activa": "1", "contingencia_motivo": "Sin internet"}))
cfg = ok(GET("/api/config"))
chk(cfg["contingencia_activa"] == "1" and cfg["contingencia_desde"] != "")
f3 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v3["id"]}, esperar=201))
chk(f3["control"] == "" and f3["contingencia"] == 1 and f3["fiscal"] == 1, f3)
al = ok(GET("/api/alertas"))["items"]
ids = {x["id"] for x in al}
chk({"contingencia", "pend_control"} <= ids, ids)
chk({x["id"] for x in ok(GET("/api/alertas", usuario="caja3"))["items"]} == {"contingencia", "pend_control"})
lv3 = ok(GET("/api/libros/ventas"))
chk(lv3["pendientes_control"] == 1 and [i for i in lv3["items"] if i["id"] == f3["id"]][0]["pendiente_control"] == 1)
# completar el control: solo admin, formato, único, una sola vez
err(POST(f"/api/documentos/{f3['id']}/control", {"control": "00-900"}, usuario="caja3"), "administrador")
err(POST(f"/api/documentos/{f3['id']}/control", {"control": ""}), "obligatorio")
err(POST(f"/api/documentos/{f3['id']}/control", {"control": "00-101"}), "usado")
err(POST(f"/api/documentos/{f3['id']}/control", {"control": "00 900!"}), "solo admite")
ok(POST(f"/api/documentos/{f3['id']}/control", {"control": "00-900"}))
chk(ok(GET(f"/api/documentos/{f3['id']}"))["control"] == "00-900")
err(POST(f"/api/documentos/{f3['id']}/control", {"control": "00-901"}), "contingencia")
err(POST(f"/api/documentos/{f1['id']}/control", {"control": "00-902"}), "contingencia")   # factura normal: no se toca
con = sqlite3.connect(db.get_db_path())
for sql in ("UPDATE documentos SET control='X' WHERE id=%d" % f3["id"],):
    try:
        con.execute(sql); con.commit(); raise SystemExit("el trigger debió impedir: " + sql)
    except sqlite3.DatabaseError as e:
        chk("control" in str(e))
con.close()
chk(any(r["accion"].startswith("número de control completado") for r in ok(GET("/api/bitacora?limit=50"))["items"]))
chk("pend_control" not in {x["id"] for x in ok(GET("/api/alertas"))["items"]})
ok(PUT("/api/config", {"contingencia_activa": "0"}))
chk(ok(GET("/api/config"))["contingencia_desde"] == "")
err(POST("/api/documentos", {"tipo": "factura", "venta_id": venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 200}])["id"]}), "control")

# ---------- alerta de rango de control
ok(PUT("/api/config", {"control_modo": "serie", "sig_control": "95", "control_hasta": "100", "alerta_rango_restante": "10"}))
rg = [x for x in ok(GET("/api/alertas"))["items"] if x["id"] == "rango"][0]
chk(rg["nivel"] == "warn" and "6" in rg["titulo"], rg)
ok(PUT("/api/config", {"sig_control": "100"}))
ok(POST("/api/documentos", {"tipo": "factura", "venta_id": venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 200}])["id"]}, esperar=201))   # usa el 100
err(POST("/api/documentos", {"tipo": "factura", "venta_id": venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 200}])["id"]}), "rango")
chk([x for x in ok(GET("/api/alertas"))["items"] if x["id"] == "rango"][0]["nivel"] == "crit")
ok(PUT("/api/config", {"control_modo": "manual", "sig_control": "1", "control_hasta": "0"}))
chk("rango" not in {x["id"] for x in ok(GET("/api/alertas"))["items"]})

# ---------- respaldo externo
import respaldo
datos = os.path.dirname(db.get_db_path())
err(PUT("/api/config", {"respaldo_externo_ruta": "relativa/carpeta"}), "ruta completa")
err(PUT("/api/config", {"respaldo_externo_ruta": os.path.join(datos, "respaldos")}), "fuera")
err(PUT("/api/config", {"respaldo_externo_ruta": "/no/existe/xyz"}), "no existe")
err(PUT("/api/config", {"respaldo_externo_activo": "1"}), "carpeta")
ext = tempfile.mkdtemp(prefix="axext_")
chk(ok(POST("/api/backup/externo/probar", {"ruta": ext}))["ruta"] == ext)
err(POST("/api/backup/externo/probar", {"ruta": ""}))
err(POST("/api/backup/externo"), "carpeta")
ok(PUT("/api/config", {"respaldo_externo_ruta": ext, "respaldo_externo_activo": "1"}))
err(GET("/api/backup/externo", usuario="caja3"))
est = ok(GET("/api/backup/externo"))
chk(est["activo"] and est["ruta"] == ext and est["ultimo"] is None and est["archivos"] == [], est)
al_ids = {x["id"] for x in ok(GET("/api/alertas"))["items"]}
chk("resp_ext" in al_ids, al_ids)   # activado pero sin copias
r = ok(POST("/api/backup/externo"))
chk(r["nombre"].startswith("antarexales_ext_") and os.path.isfile(os.path.join(ext, r["nombre"])))
copia = sqlite3.connect(os.path.join(ext, r["nombre"]))
chk(copia.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
chk(copia.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] >= 4)
copia.close()
chk(not [f for f in os.listdir(ext) if f.endswith(".part")], "sin residuos .part")
est = ok(GET("/api/backup/externo"))
chk(est["ultimo"]["ok"] and len(est["archivos"]) == 1, est)
chk("resp_ext" not in {x["id"] for x in ok(GET("/api/alertas"))["items"]})
# un respaldo local también hace la copia externa
import time; time.sleep(1.1)
ok(POST("/api/backup/auto"))
chk(len(ok(GET("/api/backup/externo"))["archivos"]) == 2)
# rotación: conserva «respaldo_auto_dias»
ok(PUT("/api/config", {"respaldo_auto_dias": "2"}))
time.sleep(1.1); ok(POST("/api/backup/externo")); time.sleep(1.1); ok(POST("/api/backup/externo"))
chk(len([f for f in os.listdir(ext) if f.startswith("antarexales_ext_")]) == 2)
# carpeta desconectada: alerta crítica y error registrado
import shutil; shutil.rmtree(ext)
al = {x["id"]: x for x in ok(GET("/api/alertas"))["items"]}
chk(al["resp_ext_ruta"]["nivel"] == "crit", al)
err(POST("/api/backup/externo"))
chk(ok(GET("/api/backup/externo"))["ultimo"]["ok"] is False)
chk({x["id"]: x for x in ok(GET("/api/alertas"))["items"]}["resp_ext"]["nivel"] == "crit")
# el respaldo local no se rompe aunque falle el externo
ok(POST("/api/backup/auto"))
# alerta con el modo fiscal sin respaldo externo
ok(PUT("/api/config", {"respaldo_externo_activo": "0"}))
chk("resp_ext_cfg" in {x["id"] for x in ok(GET("/api/alertas"))["items"]})
err(PUT("/api/config", {"alerta_respaldo_dias": "0"}))

print(f"test_v013 OK ({chk.n} comprobaciones)")

"""v0.11 — modo fiscal: alícuotas, IGTF, RIF, control, asistente y migración."""
from helpers import GET, POST, PUT, ok, err, Contador, server, db
import fiscal

chk = Contador()

# ---------- por defecto todo apagado
cfg = ok(GET("/api/config"))
chk(cfg["fiscal_activo"] == "0" and cfg["igtf_activo"] == "0" and cfg["iva_activo"] == "0")
est = ok(GET("/api/fiscal/estado"))
chk(not est["activo"] and not est["listo"])

# ---------- RIF
chk(fiscal.validar_rif("J-07013380-5")["dv_ok"] is True)
chk(fiscal.validar_rif("j070133805")["normalizado"] == "J-07013380-5")
chk(fiscal.validar_rif("12345")["valido"] is False)
chk(ok(GET("/api/fiscal/rif?rif=J-07013380-5"))["valido"])

# ---------- no se puede activar sin requisitos
err(PUT("/api/config", {"fiscal_activo": "1"}), "Falta")
ok(PUT("/api/config", {"fiscal_razon_social": "Comercial Prueba C.A.", "rif": "J-07013380-5", "direccion": "Av. Guayana, Pto Ordaz",
                       "iva_activo": "1", "iva_pct": "16", "iva_modo": "adicional"}))

# ---------- productos con alícuotas
def prod(nombre, precio, alic=None, **k):
    d = {"nombre": nombre, "precio_usd": precio, "costo_usd": 1, "stock": 100, **k}
    if alic is not None:
        d["iva_alicuota"] = alic
    return ok(POST("/api/productos", d))

pg = prod("General", 10)
pr = prod("Reducida", 10, "reducida")
pa = prod("Lujo", 10, "adicional")
pe = prod("Exento", 10, "exento")
chk(pe["iva_exento"] == 1 and pe["iva_alicuota"] == "exento")
err(POST("/api/productos", {"nombre": "x", "precio_usd": 1, "iva_alicuota": "loca"}), "Alícuota")

tasa = 50.0
ok(PUT("/api/config", {"tasa_bcv": "50"}))


def venta(items, pagos, **k):
    return POST("/api/ventas", {"items": [{"producto_id": i, "cantidad": c} for i, c in items], "pagos": pagos, **k})

# modo adicional: 10 → general 11.60, reducida 10.80, adicional (16+15=31%) 13.10, exento 10.00
v = ok(venta([(pg["id"], 1), (pr["id"], 1), (pa["id"], 1), (pe["id"], 1)], [{"metodo": "efectivo_usd", "monto": 45.50}]))
chk(abs(v["total_usd"] - 45.5) < 0.005, v["total_usd"])
chk(abs(v["iva_usd"] - (1.6 + 0.8 + 3.1)) < 0.005, v["iva_usd"])
pcts = sorted((it["iva_pct"], it["exento"]) for it in v["items"])
chk(pcts == [(0.0, 1), (8.0, 0), (16.0, 0), (31.0, 0)], pcts)
chk(v["igtf_usd"] == 0)  # fiscal apagado → sin IGTF aunque se pague en divisas

# ---------- activar modo fiscal con IGTF
err(PUT("/api/config", {"igtf_metodos": "bitcoin"}), "solo aplica")
err(PUT("/api/config", {"igtf_metodos": "tarjeta"}), "solo aplica")
ok(PUT("/api/config", {"igtf_activo": "1", "igtf_pct": "3", "igtf_metodos": "efectivo_usd,zelle", "fiscal_activo": "1"}))
chk(ok(GET("/api/fiscal/estado"))["listo"])
# no se puede romper un modo fiscal activo
err(PUT("/api/config", {"rif": "123"}), "Falta")
err(PUT("/api/config", {"iva_activo": "0"}), "Falta")

# Pago 100 % en divisas: total 11.60 (general); se entrega 11.60 × 1.03 = 11.948 → 11.95 con IGTF 0.35
v = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.95}]))
chk(v["total_usd"] == 11.6 and abs(v["igtf_usd"] - 0.35) < 0.0051 and v["igtf_pct"] == 3, v)
chk(v["vuelto_usd"] == 0 and v["credito_usd"] == 0, v)
# Falta el IGTF → pago insuficiente
err(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.60}]), "insuficiente")
# Pago mixto: 5.00 en Bs-equivalente + resto en divisas
bs_equiv = 5.0 * tasa
v = ok(venta([(pg["id"], 1)], [{"metodo": "pago_movil", "monto": bs_equiv}, {"metodo": "zelle", "monto": 6.80}]))
# parte en divisas = 11.60 − 5.00 = 6.60 → IGTF 0.198 → 0.20; entrega 6.80
chk(abs(v["igtf_base_usd"] - 6.60) < 0.005 and abs(v["igtf_usd"] - 0.20) < 0.0051, v)
chk(v["vuelto_usd"] == 0, v)
# Todo en Bs: sin IGTF
v = ok(venta([(pg["id"], 1)], [{"metodo": "pago_movil", "monto": 11.6 * tasa}]))
chk(v["igtf_usd"] == 0 and v["vuelto_bs"] == 0, v)
# Excede con divisas: entrega 20, debe 11.60 + 0.35 → vuelto ≈ 8.05
v = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 20}]))
chk(abs(v["vuelto_usd"] - 8.05) < 0.0151 and abs(v["igtf_usd"] - 0.35) < 0.0051, v)
# A crédito con divisas parciales
cli = ok(POST("/api/clientes", {"nombre": "Fiado SA", "cedula": "J-07013380-5"}))
v = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 5.15}], cliente_id=cli["id"], a_credito=True))
# 5.15 / 1.03 = 5.00 aplicado → IGTF 0.15 → crédito 6.60
chk(abs(v["credito_usd"] - 6.60) < 0.0151 and abs(v["igtf_usd"] - 0.15) < 0.0051, v)

# ---------- facturas fiscales
v = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.95}]))
err(POST("/api/documentos", {"tipo": "factura", "venta_id": v["id"]}), "número de control")
d = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v["id"], "control": "00-000123"}, esperar=201))
chk(d["fiscal"] == 1 and d["emisor_rif"] == "J-07013380-5" and d["emisor_nombre"] == "Comercial Prueba C.A.", d)
chk(abs(d["igtf_usd"] - 0.35) < 0.0051 and d["control"] == "00-000123", d)
v2 = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.95}]))
err(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"], "control": "00-000123"}), "ya fue usado")
err(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"], "control": "00 00!"}), "solo admite")
# proforma y nota de entrega no son fiscales
pf = ok(POST("/api/documentos", {"tipo": "proforma", "items": [{"producto_id": pg["id"], "cantidad": 1}]}, esperar=201))
chk(pf["fiscal"] == 0 and pf["igtf_usd"] == 0)

# exigir datos del cliente
ok(PUT("/api/config", {"fiscal_exige_cliente": "1"}))
err(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"], "control": "00-000124"}), "cliente")
err(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"], "control": "00-000124",
                             "cliente": {"nombre": "Pedro", "rif": "XYZ"}}), "formato")
d2 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"], "control": "00-000124",
                                 "cliente": {"nombre": "Pedro Pérez", "rif": "V-12345678"}}, esperar=201))
chk(d2["numero"] == d["numero"] + 1)

# ---------- control en serie propia
ok(PUT("/api/config", {"control_modo": "serie", "control_prefijo": "00-", "control_digitos": "8", "sig_control": "500", "control_hasta": "501",
                       "fiscal_exige_cliente": "0"}))
ids = []
for i in range(3):
    vv = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.95}]))
    ids.append(vv["id"])
a = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": ids[0], "control": "ignorado"}, esperar=201))
b = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": ids[1]}, esperar=201))
chk(a["control"] == "00-00000500" and b["control"] == "00-00000501", (a["control"], b["control"]))
err(POST("/api/documentos", {"tipo": "factura", "venta_id": ids[2]}), "rango")
chk(ok(GET("/api/config"))["sig_control"] == "502")

# ---------- apagar el modo fiscal devuelve el comportamiento anterior
ok(PUT("/api/config", {"fiscal_activo": "0"}))
v = ok(venta([(pg["id"], 1)], [{"metodo": "efectivo_usd", "monto": 11.60}]))
chk(v["igtf_usd"] == 0 and v["vuelto_usd"] == 0)
d3 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v["id"]}, esperar=201))
chk(d3["fiscal"] == 0 and d3["emisor_nombre"] == "" and d3["control"] == "")

# ---------- migración desde una base v0.10 (sin columnas nuevas)
import sqlite3, tempfile, os
p = os.path.join(tempfile.mkdtemp(), "viejo.db")
con = sqlite3.connect(p)
con.executescript(db.SCHEMA)
con.execute("INSERT INTO productos(nombre, precio_usd, costo_usd) VALUES('Viejo', 3, 1)")
con.commit(); con.close()
db.get_db_path = lambda: p
db.init_db()
con = sqlite3.connect(p)
cols = {r[1] for r in con.execute("PRAGMA table_info(productos)")}
chk("iva_alicuota" in cols)
chk({"igtf_usd", "igtf_pct"} <= {r[1] for r in con.execute("PRAGMA table_info(ventas)")})
chk(con.execute("SELECT valor FROM config WHERE clave='fiscal_activo'").fetchone()[0] == "0")
con.close()

print(f"test_fiscal OK ({chk.n} comprobaciones)")

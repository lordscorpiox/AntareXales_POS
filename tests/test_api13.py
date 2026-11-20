# v0.7 - vuelto exacto en Bs (1100 Bs fijo, paga 1500 -> vuelto 400) y método Biopago
import os, sys, tempfile
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import database as db
db.FORZAR_CAMBIO_CLAVE = False
def must(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: sys.exit(1)
tmpdir = tempfile.mkdtemp(); db.get_db_path = lambda: os.path.join(tmpdir, "t.db")
import server; db.init_db(); c = server.app.test_client()
def d(m, url, tok=None, **kw):
    r = getattr(c, m)(url, headers={"X-Auth-Token": tok} if tok else {}, **kw); j = r.get_json(silent=True) or {}
    return r.status_code, j.get("data"), j.get("error")
T = d("post", "/api/auth/login", json={"usuario": "admin", "password": "admin123"})[1]["token"]
eq = lambda a, b, t=0.005: abs(a - b) < t
P = d("post", "/api/productos", T, json={"nombre": "Fijo Bs", "precio_moneda": "BS", "precio_bs": 1100, "precio_usd": 1, "costo_usd": 1, "stock": 1000})[1]["id"]
PU = d("post", "/api/productos", T, json={"nombre": "USD", "precio_usd": 10, "costo_usd": 4, "stock": 1000})[1]["id"]
def venta(pagos, items=None, **kw): return d("post", "/api/ventas", T, json={"items": items or [{"producto_id": P, "cantidad": 1}], "pagos": pagos, **kw})
d("post", "/api/caja/abrir", T, json={"fondo_usd": 0, "fondo_bs": 0})
acum_bs = 0
for tasa in (36.5, 250.37, 453.21, 480.1234, 512, 1000.77):
    d("put", "/api/config", T, json={"tasa_bcv": str(tasa)})
    s, v, e = venta([{"metodo": "efectivo_bs", "monto": 1500}], vuelto_en="BS")
    must(s == 201 and eq(v["total_bs"], 1100) and eq(v["vuelto_bs"], 400, 0.0001), f"tasa {tasa}: 1100 Bs fijo, paga 1500 -> vuelto {v and v['vuelto_bs']} (esperado 400,00) {e or ''}")
    must(v["vuelto_en"] == "BS" and eq(v["vuelto_usd"], round(400 / tasa, 2)), f"tasa {tasa}: vuelto en BS y equivalente USD coherente")
    acum_bs += 1100
    s, v, e = venta([{"metodo": "efectivo_bs", "monto": 1100}])
    must(s == 201 and v["vuelto_bs"] == 0 and v["vuelto_usd"] == 0, f"tasa {tasa}: pago exacto en Bs sin vuelto")
    acum_bs += 1100
    s, v, e = venta([{"metodo": "efectivo_bs", "monto": 1099.97}])
    must(s == 201 and v["credito_usd"] == 0, f"tasa {tasa}: 1099,97 Bs dentro de la tolerancia en Bs (no queda a crédito)")
    acum_bs += 1099.97
    s, v, e = venta([{"metodo": "efectivo_bs", "monto": 1000}])
    must(s == 400, f"tasa {tasa}: 1000 Bs no cubre 1100 sin cliente")
# arqueo exacto: efectivo_bs recibido - vuelto
r = d("get", "/api/caja/turno", T)[1]["resumen"]
recibido = 6 * (1500 + 1100 + 1099.97) - 6 * 400
must(eq(r["esperado_bs"], recibido, 0.02), f"arqueo Bs = recibido - vuelto = {recibido:.2f} (got {r['esperado_bs']})")
# vuelto en USD cuando paga en Bs pero se elige USD
d("put", "/api/config", T, json={"tasa_bcv": "453.21"})
s, v, e = venta([{"metodo": "efectivo_bs", "monto": 1500}], vuelto_en="USD")
must(s == 201 and v["vuelto_en"] == "USD" and eq(v["vuelto_bs"], 400, 0.0001) and eq(v["vuelto_usd"], 0.88), "vuelto en USD elegido: 400 Bs ≈ $0,88")
# mixto USD + Bs: sigue por USD
d("put", "/api/config", T, json={"tasa_bcv": "40"})
s, v, e = venta([{"metodo": "efectivo_usd", "monto": 20}], items=[{"producto_id": PU, "cantidad": 1}])
must(s == 201 and eq(v["vuelto_usd"], 10) and eq(v["vuelto_bs"], 400), "pago en USD: vuelto $10 / 400 Bs como antes")
s, v, e = venta([{"metodo": "efectivo_usd", "monto": 5}, {"metodo": "efectivo_bs", "monto": 400}], items=[{"producto_id": PU, "cantidad": 1}])
must(s == 201 and eq(v["vuelto_usd"], 5) and eq(v["vuelto_bs"], 200), "pago mixto USD+Bs: vuelto $5 / 200 Bs")
# Biopago
s, v, e = venta([{"metodo": "biopago", "monto": 1100}])
must(s == 201 and v["pagos"][0]["metodo"] == "biopago" and v["pagos"][0]["moneda"] == "BS", "venta con Biopago (BS)")
must(venta([{"metodo": "biopago", "monto": 2000}])[0] == 400, "Biopago no da vuelto (no es efectivo)")
s, v2, e = venta([{"metodo": "biopago", "monto": 500}, {"metodo": "efectivo_bs", "monto": 600}])
must(s == 201 and v2["vuelto_bs"] == 0, "Biopago + efectivo Bs")
r = d("get", "/api/caja/turno", T)[1]["resumen"]
m = {x["metodo"]: x for x in r["metodos"]}
must("biopago" in m and eq(m["biopago"]["monto"], 1600, 0.01), f"Biopago aparece en el cierre como método no efectivo: {m.get('biopago')}")
s, rep, _ = d("get", "/api/reportes/resumen", T)
must(s == 200 and any(x.get("metodo") == "biopago" or "Biopago" in str(x) for x in rep["por_metodo"]), "Biopago en reportes por método")
print("\nTODAS LAS PRUEBAS v0.7 DE API PASARON")
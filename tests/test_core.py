"""Regresión del núcleo (comportamiento con el modo fiscal apagado): sesión, productos, ventas, IVA, caja, clientes, compras, respaldo."""
from helpers import GET, POST, PUT, call, ok, err, Contador, login, c as cliente, H

chk = Contador()
eq = lambda a, b, t=0.006: abs(a - b) < t

# ---- sesión y permisos
chk(cliente.get("/api/productos").status_code == 401, "sin sesión no se entra")
ok(POST("/api/usuarios", {"usuario": "caja1", "nombre": "Caja", "password": "clave123", "rol": "cajero"}))
login("caja1", "clave123")
err(GET("/api/usuarios", usuario="caja1"))                                   # solo admin
err(PUT("/api/config", {"tasa_bcv": "10"}, usuario="caja1"))                  # cajero sin permiso de tasa
err(POST("/api/auth/login", {"usuario": "admin", "password": "mala"}))

# ---- productos
ok(PUT("/api/config", {"tasa_bcv": "40"}))
a = ok(POST("/api/productos", {"nombre": "Harina", "codigo": "H1", "costo_usd": 1, "precio_usd": 2, "stock": 20}))
b = ok(POST("/api/productos", {"nombre": "Aceite fijo", "precio_moneda": "BS", "precio_bs": 200, "costo_usd": 2, "stock": 10}))
k = ok(POST("/api/productos", {"nombre": "Queso kg", "unidad": "kg", "costo_usd": 3, "precio_usd": 6, "stock": 5}))
err(POST("/api/productos", {"nombre": "Dup", "codigo": "H1", "precio_usd": 1}))
err(POST("/api/productos", {"nombre": "", "precio_usd": 1}))
chk(any(p["id"] == a["id"] for p in ok(GET("/api/productos"))))
err(POST("/api/ventas", {"items": [{"producto_id": a["id"], "cantidad": 1.5}], "pagos": [{"metodo": "efectivo_usd", "monto": 3}]}), "entera")

def venta(items, pagos, **kw):
    return POST("/api/ventas", {"items": [{"producto_id": i, "cantidad": q} for i, q in items], "pagos": pagos, **kw})

def stock(p):
    return ok(GET(f"/api/productos/{p['id']}"))["stock"]

# ---- caja con turno
t = ok(POST("/api/caja/abrir", {"fondo_usd": 10, "fondo_bs": 100}))
chk(ok(GET("/api/caja/turno")) is not None)

# ---- venta simple, pago exacto
v = ok(venta([(a["id"], 3)], [{"metodo": "efectivo_usd", "monto": 6}]), )
chk(v["total_usd"] == 6 and v["total_bs"] == 240 and v["vuelto_usd"] == 0 and stock(a) == 17)
# precio fijo en Bs: 200 Bs = 5 USD a tasa 40; pago en Bs exacto
v2 = ok(venta([(b["id"], 1)], [{"metodo": "pago_movil", "monto": 200}]))
chk(v2["total_bs"] == 200 and eq(v2["total_usd"], 5))
# pago insuficiente / crédito
err(venta([(a["id"], 1)], [{"metodo": "efectivo_usd", "monto": 1}]), "insuficiente")
cli = ok(POST("/api/clientes", {"nombre": "Ana", "cedula": "V-123"}))
v3 = ok(venta([(a["id"], 5)], [{"metodo": "efectivo_usd", "monto": 4}], cliente_id=cli["id"], a_credito=True))
chk(eq(v3["credito_usd"], 6))
chk(eq(next(x for x in ok(GET("/api/clientes")) if x["id"] == cli["id"])["saldo_usd"], 6))
ab = ok(POST(f"/api/clientes/{cli['id']}/abonos", {"monto_usd": 2, "metodo": "efectivo_usd"}))
chk(eq(next(x for x in ok(GET("/api/clientes")) if x["id"] == cli["id"])["saldo_usd"], 4))
# vuelto
v4 = ok(venta([(k["id"], 1)], [{"metodo": "efectivo_usd", "monto": 10}]))
chk(eq(v4["vuelto_usd"], 4) and v4["vuelto_en"] in ("USD", "BS"))
err(venta([(k["id"], 1)], [{"metodo": "zelle", "monto": 10}]), "vuelto")     # sin efectivo no hay vuelto
# stock insuficiente
err(venta([(k["id"], 99)], [{"metodo": "efectivo_usd", "monto": 999}]), "stock")

# ---- anular devuelve stock (cajero necesita autorización de admin)
antes = stock(a)
err(POST(f"/api/ventas/{v['id']}/anular", {}, usuario="caja1"))
ok(POST(f"/api/ventas/{v['id']}/anular", {"autoriza": {"usuario": "admin", "password": "admin123"}}, usuario="caja1"))
chk(stock(a) == antes + 3)
chk(ok(GET(f"/api/ventas/{v['id']}"))["estado"] == "anulada")

# ---- IVA clásico: una sola alícuota, incluido y adicional
ok(PUT("/api/config", {"iva_activo": "1", "iva_pct": "16", "iva_modo": "incluido"}))
vi = ok(venta([(a["id"], 5)], [{"metodo": "efectivo_usd", "monto": 10}]))
chk(vi["total_usd"] == 10 and eq(vi["iva_usd"], 1.38) and eq(vi["base_usd"], 8.62), vi)
ok(PUT("/api/config", {"iva_modo": "adicional"}))
va = ok(venta([(a["id"], 5)], [{"metodo": "efectivo_usd", "monto": 11.6}]))
chk(eq(va["total_usd"], 11.6) and eq(va["iva_usd"], 1.6))
ex = ok(POST("/api/productos", {"nombre": "Exento viejo", "precio_usd": 10, "iva_exento": 1, "stock": 5}))
chk(ex["iva_exento"] == 1)
ve = ok(venta([(ex["id"], 1)], [{"metodo": "efectivo_usd", "monto": 10}]))
chk(ve["iva_usd"] == 0 and ve["exento_usd"] == 10)
ok(PUT("/api/config", {"iva_activo": "0"}))

# ---- documentos y numeración
d1 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"]}, esperar=201))
d1b = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v2["id"]}))
chk(d1b["id"] == d1["id"] and d1b.get("ya_existia"))
d2 = ok(POST("/api/documentos", {"tipo": "factura", "venta_id": v3["id"]}, esperar=201))
chk(d2["numero"] == d1["numero"] + 1 and d1["fiscal"] == 0)
nota = ok(POST("/api/documentos", {"tipo": "nota_entrega", "venta_id": v3["id"]}, esperar=201))
pf = ok(POST("/api/documentos", {"tipo": "proforma", "items": [{"producto_id": a["id"], "cantidad": 2}]}, esperar=201))
chk(pf["total_usd"] == 4)
ok(POST(f"/api/documentos/{d2['id']}/anular", {}))
chk(ok(GET(f"/api/documentos/{d2['id']}"))["estado"] == "anulado")

# ---- caja: cierre con arqueo
ok(POST("/api/caja/movimientos", {"tipo": "gasto", "monto": 1, "moneda": "USD", "concepto": "bolsas", "categoria": "Otros"}))
res = ok(GET("/api/caja/turno"))
chk(res is not None)
cierre = ok(POST("/api/caja/cerrar", {"contado_usd": 0, "contado_bs": 0}))
chk(cierre is not None)

# ---- compras y proveedores
prov = ok(POST("/api/proveedores", {"nombre": "Distribuidora"}))
s0 = stock(a)
comp = ok(POST("/api/compras", {"proveedor_id": prov["id"], "items": [{"producto_id": a["id"], "cantidad": 10, "costo_usd": 1.5}]}))
chk(stock(a) == s0 + 10, "la compra suma stock")
chk(ok(GET(f"/api/compras/{comp['id']}")) is not None)

# ---- reportes y respaldo
rr = ok(GET("/api/reportes/resumen"))
chk(isinstance(rr, dict))
r = cliente.get("/api/backup/download-db", headers=H())
chk(r.status_code == 200 and r.data[:15] == b"SQLite format 3", "el respaldo es una base SQLite válida")

print(f"test_core OK ({chk.n} comprobaciones)")

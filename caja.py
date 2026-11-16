"""Caja (v0.5): apertura de turno con fondo, arqueo por método, gastos/retiros/ingresos, cierre con diferencia
y ventas en espera.

Efectivo esperado en cada moneda =
    fondo + cobros en efectivo (ventas y abonos) + ingresos − gastos − retiros − vuelto entregado en esa moneda.
Los métodos que no son efectivo (transferencia, pago móvil, Zelle, tarjeta, Biopago) se reportan como totales, sin arqueo."""
import json

from flask import Blueprint, g, request

import database as db
from errors import ApiError

bp = Blueprint("caja", __name__)

get_db = ok = body = num = texto = rows = r2 = tiene_valor = validar_fecha = es_admin = int_arg = get_config = None
METODOS = turno_actual = None
_ENLACES = ("get_db", "ok", "body", "num", "texto", "rows", "r2", "tiene_valor", "validar_fecha", "es_admin", "int_arg",
            "get_config", "METODOS", "turno_actual")

TIPOS_MOV = {"gasto": "Gasto", "retiro": "Retiro", "ingreso": "Ingreso"}
CATEGORIAS = ["Servicios", "Insumos", "Transporte", "Personal / Nómina", "Mantenimiento", "Compra de mercancía", "Otros"]


def register(app, srv):
    for n in _ENLACES:
        globals()[n] = getattr(srv, n)
    app.register_blueprint(bp)


# ---------------------------------------------------------------- cálculo del turno
def resumen(conn, t):
    """Arqueo del turno (en vivo): ventas, cobros por método, movimientos y efectivo esperado por moneda."""
    tid = t["id"]
    v = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(total_usd),0) usd, COALESCE(SUM(total_bs),0) bs, COALESCE(SUM(credito_usd),0) credito "
        "FROM ventas WHERE turno_id=? AND estado='completada'", (tid,)).fetchone()
    anuladas = conn.execute("SELECT COUNT(*) FROM ventas WHERE turno_id=? AND estado='anulada'", (tid,)).fetchone()[0]
    vuelto = {"USD": 0.0, "BS": 0.0}
    for r in conn.execute("SELECT vuelto_en, COALESCE(SUM(vuelto_usd),0) u, COALESCE(SUM(vuelto_bs),0) b FROM ventas "
                          "WHERE turno_id=? AND estado='completada' AND (vuelto_usd>0 OR vuelto_bs>0) GROUP BY vuelto_en", (tid,)):
        vuelto["BS" if r["vuelto_en"] == "BS" else "USD"] += r["b"] if r["vuelto_en"] == "BS" else r["u"]
    metodos = {}
    for r in conn.execute(
            "SELECT p.metodo, p.moneda, COUNT(*) n, SUM(p.monto) monto, SUM(p.monto_usd) usd FROM venta_pagos p "
            "JOIN ventas v ON v.id=p.venta_id WHERE v.turno_id=? AND v.estado='completada' GROUP BY p.metodo, p.moneda", (tid,)):
        metodos[r["metodo"]] = {"metodo": r["metodo"], "nombre": METODOS.get(r["metodo"], (r["metodo"], ""))[0],
                                "moneda": r["moneda"], "n": r["n"], "monto": r2(r["monto"]), "usd": r2(r["usd"]),
                                "abonos": 0.0}
    abonos_efectivo = {"USD": 0.0, "BS": 0.0}
    abonos_total_usd = 0.0
    for a in conn.execute("SELECT * FROM abonos WHERE turno_id=?", (tid,)):
        abonos_total_usd += a["monto_usd"]
        nombre = (a["metodo"] or "").strip().lower().replace("_", " ")
        if nombre.startswith("efectivo"):
            if a["moneda"] == "BS":
                abonos_efectivo["BS"] += a["monto_bs"]
            else:
                abonos_efectivo["USD"] += a["monto_usd"]
        else:
            # cobro de deuda por otro método: se muestra aparte, sin arqueo
            k = (a["metodo"] or "Sin método")
            e = metodos.setdefault("abono:" + k, {"metodo": "abono:" + k, "nombre": f"Abono — {k}", "moneda": a["moneda"],
                                                  "n": 0, "monto": 0.0, "usd": 0.0, "abonos": 0.0})
            e["n"] += 1
            e["monto"] = r2(e["monto"] + (a["monto_bs"] if a["moneda"] == "BS" else a["monto_usd"]))
            e["usd"] = r2(e["usd"] + a["monto_usd"])
    movs = rows(conn.execute("SELECT * FROM caja_movimientos WHERE turno_id=? ORDER BY id", (tid,)))
    tot = {k: {"USD": 0.0, "BS": 0.0} for k in TIPOS_MOV}
    for m in movs:
        if not m["anulado"]:
            tot[m["tipo"]][m["moneda"]] += m["monto"]
    ef_bs = metodos.get("efectivo_bs", {}).get("monto", 0.0)
    ef_usd = metodos.get("efectivo_usd", {}).get("monto", 0.0)
    esp_bs = (t["fondo_bs"] + ef_bs + abonos_efectivo["BS"] + tot["ingreso"]["BS"] - tot["gasto"]["BS"]
              - tot["retiro"]["BS"] - vuelto["BS"])
    esp_usd = (t["fondo_usd"] + ef_usd + abonos_efectivo["USD"] + tot["ingreso"]["USD"] - tot["gasto"]["USD"]
               - tot["retiro"]["USD"] - vuelto["USD"])
    return {
        "ventas": v["n"], "anuladas": anuladas, "total_usd": r2(v["usd"]), "total_bs": r2(v["bs"]), "credito_usd": r2(v["credito"]),
        "metodos": sorted(metodos.values(), key=lambda m: -m["usd"]),
        "efectivo_ventas_bs": r2(ef_bs), "efectivo_ventas_usd": r2(ef_usd),
        "abonos_efectivo_bs": r2(abonos_efectivo["BS"]), "abonos_efectivo_usd": r2(abonos_efectivo["USD"]),
        "abonos_total_usd": r2(abonos_total_usd),
        "ingresos_bs": r2(tot["ingreso"]["BS"]), "ingresos_usd": r2(tot["ingreso"]["USD"]),
        "gastos_bs": r2(tot["gasto"]["BS"]), "gastos_usd": r2(tot["gasto"]["USD"]),
        "retiros_bs": r2(tot["retiro"]["BS"]), "retiros_usd": r2(tot["retiro"]["USD"]),
        "vuelto_bs": r2(vuelto["BS"]), "vuelto_usd": r2(vuelto["USD"]),
        "fondo_bs": r2(t["fondo_bs"]), "fondo_usd": r2(t["fondo_usd"]),
        "esperado_bs": r2(esp_bs), "esperado_usd": r2(esp_usd),
    }


def turno_out(conn, t, con_detalle=True):
    t = dict(t)
    if t["estado"] == "cerrado" and t.get("resumen"):
        try:
            t["resumen"] = json.loads(t["resumen"])
        except ValueError:
            t["resumen"] = resumen(conn, t)
    else:
        t["resumen"] = resumen(conn, t)
    if con_detalle:
        t["movimientos"] = rows(conn.execute("SELECT * FROM caja_movimientos WHERE turno_id=? ORDER BY id", (t["id"],)))
    return t


def turno_por_id(conn, tid):
    t = conn.execute("SELECT * FROM turnos WHERE id=?", (tid,)).fetchone()
    if not t:
        raise ApiError("Turno no encontrado", 404)
    if not es_admin() and t["usuario_id"] != g.user["user_id"]:
        raise ApiError("Ese turno pertenece a otro usuario", 403)
    return t


# ---------------------------------------------------------------- turnos
@bp.get("/api/caja/turno")
def turno_get():
    """Turno abierto del usuario (o null)."""
    conn = get_db()
    tid = turno_actual(conn, g.user["user_id"])
    return ok(turno_out(conn, conn.execute("SELECT * FROM turnos WHERE id=?", (tid,)).fetchone()) if tid else None)


@bp.post("/api/caja/abrir")
def turno_abrir():
    data = body()
    conn = get_db()
    fondo_bs = r2(num(data.get("fondo_bs"), "El fondo en Bs", 0, default=0.0))
    fondo_usd = r2(num(data.get("fondo_usd"), "El fondo en USD", 0, default=0.0))
    try:
        conn.execute("BEGIN IMMEDIATE")
        if turno_actual(conn, g.user["user_id"]):
            raise ApiError("Ya tiene un turno abierto: ciérrelo antes de abrir otro", 409)
        cur = conn.execute(
            "INSERT INTO turnos(usuario_id, usuario_nombre, abierto, fondo_bs, fondo_usd, estado, notas) VALUES(?,?,?,?,?,'abierto',?)",
            (g.user["user_id"], g.user["nombre"] or g.user["usuario"], db.now_str(), fondo_bs, fondo_usd,
             texto(data.get("notas"), "Las notas", False, 200)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(turno_out(conn, conn.execute("SELECT * FROM turnos WHERE id=?", (cur.lastrowid,)).fetchone()), 201)


def _cerrar(conn, tid, data):
    contado_bs = r2(num(data.get("contado_bs"), "El efectivo contado en Bs", 0))
    contado_usd = r2(num(data.get("contado_usd"), "El efectivo contado en USD", 0))
    try:
        conn.execute("BEGIN IMMEDIATE")
        t = turno_por_id(conn, tid)
        if t["estado"] != "abierto":
            raise ApiError("El turno ya está cerrado")
        r = resumen(conn, t)
        dif_bs, dif_usd = r2(contado_bs - r["esperado_bs"]), r2(contado_usd - r["esperado_usd"])
        r.update({"contado_bs": contado_bs, "contado_usd": contado_usd, "dif_bs": dif_bs, "dif_usd": dif_usd})
        notas = texto(data.get("notas"), "Las notas", False, 300) or t["notas"]
        conn.execute(
            "UPDATE turnos SET estado='cerrado', cerrado=?, esperado_bs=?, esperado_usd=?, contado_bs=?, contado_usd=?, "
            "dif_bs=?, dif_usd=?, notas=?, resumen=? WHERE id=?",
            (db.now_str(), r["esperado_bs"], r["esperado_usd"], contado_bs, contado_usd, dif_bs, dif_usd, notas,
             json.dumps(r, ensure_ascii=False), tid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(turno_out(conn, conn.execute("SELECT * FROM turnos WHERE id=?", (tid,)).fetchone()))


@bp.post("/api/caja/cerrar")
def turno_cerrar():
    conn = get_db()
    tid = turno_actual(conn, g.user["user_id"])
    if not tid:
        raise ApiError("No tiene un turno abierto")
    return _cerrar(conn, tid, body())


@bp.post("/api/caja/turnos/<int:tid>/cerrar")
def turno_cerrar_id(tid):
    return _cerrar(get_db(), tid, body())


@bp.get("/api/caja/turnos")
def turnos_get():
    conn = get_db()
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    sql, params = "SELECT * FROM turnos WHERE 1=1", []
    if not es_admin():
        sql += " AND usuario_id=?"
        params.append(g.user["user_id"])
    elif int_arg("usuario_id"):
        sql += " AND usuario_id=?"
        params.append(int_arg("usuario_id"))
    if desde:
        sql += " AND date(abierto)>=?"
        params.append(desde)
    if hasta:
        sql += " AND date(abierto)<=?"
        params.append(hasta)
    sql += " ORDER BY id DESC LIMIT 200"
    out = []
    for t in conn.execute(sql, params).fetchall():
        o = turno_out(conn, t, False)
        r = o.pop("resumen")
        o["ventas"], o["total_usd"] = r["ventas"], r["total_usd"]
        if o["estado"] == "abierto":
            o["esperado_bs"], o["esperado_usd"] = r["esperado_bs"], r["esperado_usd"]
        out.append(o)
    return ok(out)


@bp.get("/api/caja/turnos/<int:tid>")
def turnos_detalle(tid):
    conn = get_db()
    return ok(turno_out(conn, turno_por_id(conn, tid)))


# ---------------------------------------------------------------- gastos, retiros, ingresos
@bp.get("/api/caja/categorias")
def categorias_gasto():
    return ok(CATEGORIAS)


@bp.post("/api/caja/movimientos")
def mov_post():
    data = body()
    conn = get_db()
    tid = turno_actual(conn, g.user["user_id"])
    if not tid:
        raise ApiError("Abra un turno de caja para registrar movimientos", 409)
    tipo = str(data.get("tipo") or "").lower()
    if tipo not in TIPOS_MOV:
        raise ApiError("El tipo debe ser gasto, retiro o ingreso")
    moneda = str(data.get("moneda") or "USD").upper()
    if moneda not in ("USD", "BS"):
        raise ApiError("La moneda debe ser USD o BS")
    monto = r2(num(data.get("monto"), "El monto", estricto_positivo=True))
    concepto = texto(data.get("concepto"), "El concepto", True, 150)
    categoria = texto(data.get("categoria"), "La categoría", False, 60)
    with conn:
        conn.execute(
            "INSERT INTO caja_movimientos(turno_id, fecha, tipo, categoria, concepto, moneda, monto, usuario) VALUES(?,?,?,?,?,?,?,?)",
            (tid, db.now_str(), tipo, categoria, concepto, moneda, monto, g.user["usuario"]))
    return ok(turno_out(conn, conn.execute("SELECT * FROM turnos WHERE id=?", (tid,)).fetchone()), 201)


@bp.delete("/api/caja/movimientos/<int:mid>")
def mov_anular(mid):
    """Anula un movimiento: el dueño del turno mientras siga abierto; el administrador siempre."""
    conn = get_db()
    m = conn.execute("SELECT * FROM caja_movimientos WHERE id=?", (mid,)).fetchone()
    if not m:
        raise ApiError("Movimiento no encontrado", 404)
    t = conn.execute("SELECT * FROM turnos WHERE id=?", (m["turno_id"],)).fetchone()
    if not es_admin() and (t["usuario_id"] != g.user["user_id"] or t["estado"] != "abierto"):
        raise ApiError("No puede anular este movimiento", 403)
    if t["estado"] == "cerrado":
        raise ApiError("El turno ya está cerrado: el arqueo no se modifica")
    with conn:
        conn.execute("UPDATE caja_movimientos SET anulado=1 WHERE id=?", (mid,))
    return ok(turno_out(conn, conn.execute("SELECT * FROM turnos WHERE id=?", (t["id"],)).fetchone()))


@bp.get("/api/caja/gastos")
def gastos_reporte():
    """Gastos y retiros por rango de fechas (admin): para reportes."""
    if not es_admin():
        raise ApiError("Acceso restringido a administradores", 403)
    conn = get_db()
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    sql = ("SELECT m.*, t.usuario_nombre FROM caja_movimientos m JOIN turnos t ON t.id=m.turno_id WHERE m.anulado=0")
    params = []
    if desde:
        sql += " AND date(m.fecha)>=?"
        params.append(desde)
    if hasta:
        sql += " AND date(m.fecha)<=?"
        params.append(hasta)
    return ok(rows(conn.execute(sql + " ORDER BY m.id DESC LIMIT 1000", params)))


# ---------------------------------------------------------------- ventas en espera
def _items_validos(conn, items):
    if not isinstance(items, list) or not items:
        raise ApiError("El carrito está vacío")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ApiError("Item inválido")
        try:
            pid = int(it.get("producto_id"))
        except (TypeError, ValueError):
            raise ApiError("Producto inválido")
        cant = num(it.get("cantidad"), "La cantidad", estricto_positivo=True)
        if not conn.execute("SELECT 1 FROM productos WHERE id=? AND activo=1", (pid,)).fetchone():
            raise ApiError("Un producto del carrito ya no existe")
        out.append({"producto_id": pid, "cantidad": round(cant, 3)})
    return out


def _espera_out(conn, e):
    e = dict(e)
    e["items"] = json.loads(e["items"])
    c = conn.execute("SELECT nombre, cedula FROM clientes WHERE id=?", (e["cliente_id"],)).fetchone() if e["cliente_id"] else None
    e["cliente_nombre"] = c["nombre"] if c else ""
    e["cliente_cedula"] = c["cedula"] if c else ""
    nombres = {r["id"]: r for r in conn.execute("SELECT id, nombre, precio_usd FROM productos")}
    e["lineas"] = len(e["items"])
    e["total_aprox_usd"] = r2(sum(nombres[i["producto_id"]]["precio_usd"] * i["cantidad"] for i in e["items"]
                                  if i["producto_id"] in nombres))
    return e


@bp.post("/api/ventas-espera")
def espera_post():
    data = body()
    conn = get_db()
    items = _items_validos(conn, data.get("items"))
    cid = None
    if tiene_valor(data, "cliente_id"):
        cid = int(data["cliente_id"])
        if not conn.execute("SELECT 1 FROM clientes WHERE id=? AND activo=1", (cid,)).fetchone():
            raise ApiError("El cliente seleccionado no existe")
    if conn.execute("SELECT COUNT(*) FROM ventas_espera").fetchone()[0] >= 50:
        raise ApiError("Hay demasiadas ventas en espera (máx. 50): reanude o elimine alguna")
    with conn:
        cur = conn.execute("INSERT INTO ventas_espera(fecha, usuario, etiqueta, cliente_id, items) VALUES(?,?,?,?,?)",
                           (db.now_str(), g.user["usuario"], texto(data.get("etiqueta"), "La etiqueta", False, 60), cid,
                            json.dumps(items)))
    return ok(_espera_out(conn, conn.execute("SELECT * FROM ventas_espera WHERE id=?", (cur.lastrowid,)).fetchone()), 201)


@bp.get("/api/ventas-espera")
def espera_get():
    conn = get_db()
    return ok([_espera_out(conn, e) for e in conn.execute("SELECT * FROM ventas_espera ORDER BY id")])


@bp.get("/api/ventas-espera/<int:eid>")
def espera_uno(eid):
    conn = get_db()
    e = conn.execute("SELECT * FROM ventas_espera WHERE id=?", (eid,)).fetchone()
    if not e:
        raise ApiError("La venta en espera ya no existe", 404)
    return ok(_espera_out(conn, e))


@bp.delete("/api/ventas-espera/<int:eid>")
def espera_delete(eid):
    conn = get_db()
    with conn:
        n = conn.execute("DELETE FROM ventas_espera WHERE id=?", (eid,)).rowcount
    if not n:
        raise ApiError("La venta en espera ya no existe", 404)
    return ok(True)

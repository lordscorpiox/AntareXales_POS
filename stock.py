"""Núcleo de inventario: almacenes, lotes (FEFO) y combos.

Invariantes:
  * productos.stock = suma de stock_almacen.cantidad del producto (se recalcula en `mover`).
  * Los lotes (con fecha de vencimiento) son un detalle dentro del stock de un almacén:
    suma(lotes.cantidad) <= stock_almacen.cantidad; la diferencia es stock «sin lote».
  * Al vender se consume primero el lote que vence antes (FEFO); luego lo que no tiene lote.
  * Un combo no tiene stock propio: su disponibilidad sale de sus componentes.
Todas las funciones esperan una transacción ya abierta por quien llama.
"""
from datetime import datetime

import database as db
from errors import ApiError

EPS = 1e-9


# ---------------------------------------------------------------- almacenes
def almacen_principal(conn):
    f = conn.execute("SELECT id FROM almacenes WHERE principal=1 ORDER BY id LIMIT 1").fetchone()
    if not f:
        f = conn.execute("SELECT id FROM almacenes ORDER BY id LIMIT 1").fetchone()
    return f["id"]


def almacen_ventas(conn):
    """Almacén del que salen las ventas (config «almacen_ventas»; vacío = el principal)."""
    f = conn.execute("SELECT valor FROM config WHERE clave='almacen_ventas'").fetchone()
    try:
        aid = int(f["valor"]) if f and f["valor"] else 0
    except ValueError:
        aid = 0
    if aid and conn.execute("SELECT 1 FROM almacenes WHERE id=? AND activo=1", (aid,)).fetchone():
        return aid
    return almacen_principal(conn)


def almacen_valido(conn, valor, campo="El almacén"):
    try:
        aid = int(valor)
    except (TypeError, ValueError):
        raise ApiError(f"{campo} no es válido")
    f = conn.execute("SELECT id, activo FROM almacenes WHERE id=?", (aid,)).fetchone()
    if not f:
        raise ApiError(f"{campo} no existe")
    if not f["activo"]:
        raise ApiError(f"{campo} está inactivo")
    return aid


def stock_en(conn, pid, aid):
    f = conn.execute("SELECT cantidad FROM stock_almacen WHERE producto_id=? AND almacen_id=?", (pid, aid)).fetchone()
    return f["cantidad"] if f else 0.0


# ---------------------------------------------------------------- movimientos básicos
def mover(conn, pid, aid, delta, tipo, motivo, usuario, venta_id=None, costo=0.0):
    """Suma `delta` al stock del producto en el almacén, recalcula el total y deja el movimiento en el kardex."""
    conn.execute("INSERT INTO stock_almacen(producto_id, almacen_id, cantidad) VALUES(?,?,0) "
                 "ON CONFLICT(producto_id, almacen_id) DO NOTHING", (pid, aid))
    conn.execute("UPDATE stock_almacen SET cantidad=ROUND(cantidad+?,4) WHERE producto_id=? AND almacen_id=?",
                 (delta, pid, aid))
    total = conn.execute("SELECT ROUND(COALESCE(SUM(cantidad),0),4) FROM stock_almacen WHERE producto_id=?",
                         (pid,)).fetchone()[0]
    conn.execute("UPDATE productos SET stock=? WHERE id=?", (total, pid))
    conn.execute(
        "INSERT INTO movimientos_stock(producto_id, fecha, tipo, cantidad, stock_resultante, motivo, usuario, venta_id, "
        "almacen_id, costo_usd) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (pid, db.now_str(), tipo, round(delta, 4), total, motivo, usuario, venta_id, aid, costo))
    return total


def lote_nuevo(conn, pid, aid, cantidad, codigo="", vence="", compra_id=None):
    cur = conn.execute(
        "INSERT INTO lotes(producto_id, almacen_id, codigo, vence, cantidad, cantidad_inicial, compra_id, creado) "
        "VALUES(?,?,?,?,?,?,?,?)", (pid, aid, codigo or "", vence or "", cantidad, cantidad, compra_id, db.now_str()))
    return cur.lastrowid


def ingresar(conn, pid, aid, cantidad, tipo, motivo, usuario, codigo="", vence="", compra_id=None, costo=0.0):
    """Entrada de mercancía. Si trae fecha de vencimiento o código de lote crea (o completa) el lote."""
    lote_id = None
    if vence or codigo:
        f = conn.execute("SELECT id FROM lotes WHERE producto_id=? AND almacen_id=? AND codigo=? AND vence=? "
                         "AND compra_id IS ?", (pid, aid, codigo or "", vence or "", compra_id)).fetchone()
        if f and compra_id is None:
            conn.execute("UPDATE lotes SET cantidad=ROUND(cantidad+?,4), cantidad_inicial=ROUND(cantidad_inicial+?,4) "
                         "WHERE id=?", (cantidad, cantidad, f["id"]))
            lote_id = f["id"]
        else:
            lote_id = lote_nuevo(conn, pid, aid, cantidad, codigo, vence, compra_id)
    mover(conn, pid, aid, cantidad, tipo, motivo, usuario, costo=costo)
    return lote_id


def lotes_fefo(conn, pid, aid, lote_id=None):
    """Lotes con existencia, el que vence antes primero; los lotes sin fecha, al final."""
    sql = ("SELECT * FROM lotes WHERE producto_id=? AND almacen_id=? AND cantidad>0.00005 ")
    params = [pid, aid]
    if lote_id:
        sql += "AND id=? "
        params.append(lote_id)
    sql += "ORDER BY (vence=''), vence, id"
    return [dict(r) for r in conn.execute(sql, params)]


def consumir(conn, pid, aid, cantidad, tipo, motivo, usuario, venta_id=None, lote_id=None, permitir_negativo=False,
             nombre="", costo=0.0):
    """Saca `cantidad` del almacén (FEFO o de un lote concreto). Devuelve [(almacen_id, lote_id|None, cantidad)]."""
    disp = stock_en(conn, pid, aid)
    if lote_id:
        lotes = lotes_fefo(conn, pid, aid, lote_id)
        if not lotes:
            raise ApiError("El lote indicado no tiene existencias en ese almacén")
        if lotes[0]["cantidad"] + EPS < cantidad:
            raise ApiError(f"El lote solo tiene {lotes[0]['cantidad']:g}")
    elif not permitir_negativo and disp + EPS < cantidad:
        raise ApiError(f"Stock insuficiente{(' de «' + nombre + '»') if nombre else ''} en el almacén (disponible: {disp:g})")
    falta = cantidad
    usados = []
    for l in lotes_fefo(conn, pid, aid, lote_id):
        if falta <= EPS:
            break
        t = min(l["cantidad"], falta)
        conn.execute("UPDATE lotes SET cantidad=ROUND(cantidad-?,4) WHERE id=?", (t, l["id"]))
        usados.append((aid, l["id"], round(t, 4)))
        falta = round(falta - t, 4)
    if falta > EPS:
        usados.append((aid, None, round(falta, 4)))
    mover(conn, pid, aid, -cantidad, tipo, motivo, usuario, venta_id, costo)
    return usados


def quitar_total(conn, pid, cantidad, tipo, motivo, usuario):
    """Descuenta del total repartiéndolo entre almacenes (primero el principal). Para ajustes sin almacén."""
    ppal = almacen_principal(conn)
    orden = [ppal] + [r["almacen_id"] for r in conn.execute(
        "SELECT almacen_id FROM stock_almacen WHERE producto_id=? AND almacen_id<>? AND cantidad>0", (pid, ppal))]
    falta = cantidad
    for aid in orden:
        if falta <= EPS:
            break
        t = min(max(stock_en(conn, pid, aid), 0.0), falta)
        if t > EPS:
            consumir(conn, pid, aid, t, tipo, motivo, usuario)
            falta = round(falta - t, 4)
    if falta > EPS:
        consumir(conn, pid, ppal, falta, tipo, motivo, usuario, permitir_negativo=True)


def ajustar_total(conn, pid, nuevo_total, motivo, usuario):
    """Lleva el stock total a `nuevo_total` (edición del producto / carga CSV)."""
    actual = conn.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()["stock"]
    delta = round(nuevo_total - actual, 4)
    if abs(delta) <= EPS:
        return
    if delta > 0:
        mover(conn, pid, almacen_principal(conn), delta, "ajuste", motivo, usuario)
    else:
        quitar_total(conn, pid, -delta, "ajuste", motivo, usuario)


def restituir(conn, consumos, tipo, motivo, usuario, venta_id=None):
    """Devuelve lo consumido (anulación). consumos = filas de venta_consumos."""
    for c in consumos:
        if c["lote_id"]:
            conn.execute("UPDATE lotes SET cantidad=ROUND(cantidad+?,4) WHERE id=?", (c["cantidad"], c["lote_id"]))
        mover(conn, c["producto_id"], c["almacen_id"], c["cantidad"], tipo, motivo, usuario, venta_id)


def trasladar(conn, pid, origen, destino, cantidad, usuario, notas=""):
    """Mueve stock entre almacenes llevándose los lotes (FEFO) para no perder el vencimiento."""
    if origen == destino:
        raise ApiError("El origen y el destino deben ser distintos")
    disp = stock_en(conn, pid, origen)
    if disp + EPS < cantidad:
        raise ApiError(f"Stock insuficiente en el almacén de origen (disponible: {disp:g})")
    nombre_o = conn.execute("SELECT nombre FROM almacenes WHERE id=?", (origen,)).fetchone()["nombre"]
    nombre_d = conn.execute("SELECT nombre FROM almacenes WHERE id=?", (destino,)).fetchone()["nombre"]
    falta = cantidad
    for l in lotes_fefo(conn, pid, origen):
        if falta <= EPS:
            break
        t = round(min(l["cantidad"], falta), 4)
        conn.execute("UPDATE lotes SET cantidad=ROUND(cantidad-?,4) WHERE id=?", (t, l["id"]))
        lote_nuevo(conn, pid, destino, t, l["codigo"], l["vence"], l["compra_id"])
        falta = round(falta - t, 4)
    mover(conn, pid, origen, -cantidad, "traslado", f"Traslado a {nombre_d}", usuario)
    mover(conn, pid, destino, cantidad, "traslado", f"Traslado desde {nombre_o}", usuario)
    cur = conn.execute("INSERT INTO traslados(fecha, producto_id, origen_id, destino_id, cantidad, usuario, notas) "
                       "VALUES(?,?,?,?,?,?,?)", (db.now_str(), pid, origen, destino, cantidad, usuario, notas))
    return cur.lastrowid


# ---------------------------------------------------------------- combos
def componentes(conn, combo_id):
    return [dict(r) for r in conn.execute(
        "SELECT ci.componente_id AS producto_id, ci.cantidad, p.nombre, p.codigo, p.unidad, p.costo_usd, p.stock, p.activo "
        "FROM combo_items ci JOIN productos p ON p.id=ci.componente_id WHERE ci.combo_id=? ORDER BY p.nombre", (combo_id,))]


def costo_combo(conn, combo_id):
    return round(sum(c["costo_usd"] * c["cantidad"] for c in componentes(conn, combo_id)), 4)


def disponible_combo(conn, combo_id, aid):
    comps = componentes(conn, combo_id)
    if not comps:
        return 0.0
    return float(max(0, min(int((stock_en(conn, c["producto_id"], aid) + EPS) // c["cantidad"]) for c in comps)))


def guardar_componentes(conn, combo_id, comps_in):
    """Valida y reemplaza la receta de un combo."""
    if not isinstance(comps_in, list) or not comps_in:
        raise ApiError("Un combo necesita al menos un producto componente")
    acum = {}
    for c in comps_in:
        if not isinstance(c, dict):
            raise ApiError("Componente inválido")
        try:
            cid = int(c.get("producto_id"))
            cant = float(str(c.get("cantidad")).replace(",", "."))
        except (TypeError, ValueError):
            raise ApiError("Componente inválido")
        if cant <= 0:
            raise ApiError("La cantidad de cada componente debe ser mayor que cero")
        acum[cid] = round(acum.get(cid, 0) + cant, 3)
    for cid in acum:
        p = conn.execute("SELECT tipo, activo, nombre FROM productos WHERE id=?", (cid,)).fetchone()
        if not p or not p["activo"]:
            raise ApiError("Un componente del combo no existe")
        if p["tipo"] == "combo":
            raise ApiError(f"«{p['nombre']}» es un combo: un combo no puede contener otros combos")
        if cid == combo_id:
            raise ApiError("Un combo no puede contenerse a sí mismo")
    conn.execute("DELETE FROM combo_items WHERE combo_id=?", (combo_id,))
    for cid, cant in acum.items():
        conn.execute("INSERT INTO combo_items(combo_id, componente_id, cantidad) VALUES(?,?,?)", (combo_id, cid, cant))


# ---------------------------------------------------------------- enriquecimiento de productos
def enriquecer(conn, lista):
    """Agrega stock_venta, proximo_vence y componentes a una lista de productos (dicts)."""
    if not lista:
        return lista
    av = almacen_ventas(conn)
    sv = {r["producto_id"]: r["cantidad"] for r in conn.execute(
        "SELECT producto_id, cantidad FROM stock_almacen WHERE almacen_id=?", (av,))}
    ven = {r["producto_id"]: r["v"] for r in conn.execute(
        "SELECT producto_id, MIN(vence) AS v FROM lotes WHERE cantidad>0.00005 AND vence<>'' GROUP BY producto_id")}
    for p in lista:
        if p.get("tipo") == "combo":
            p["componentes"] = componentes(conn, p["id"])
            disp = disponible_combo(conn, p["id"], av)
            p["stock"] = p["stock_venta"] = disp
            p["costo_usd"] = costo_combo(conn, p["id"])
            p["proximo_vence"] = ""
        else:
            p["stock_venta"] = round(sv.get(p["id"], 0.0), 4)
            p["proximo_vence"] = ven.get(p["id"], "")
    return lista


# ---------------------------------------------------------------- venta
def demanda(conn, lineas_cant):
    """{producto_id: cantidad} de lo que realmente sale del almacén (los combos se descomponen)."""
    d = {}
    for pid, cant in lineas_cant.items():
        p = conn.execute("SELECT tipo FROM productos WHERE id=?", (pid,)).fetchone()
        if p and p["tipo"] == "combo":
            for c in componentes(conn, pid):
                d[c["producto_id"]] = round(d.get(c["producto_id"], 0) + c["cantidad"] * cant, 4)
        else:
            d[pid] = round(d.get(pid, 0) + cant, 4)
    return d


def validar_demanda(conn, lineas_cant, aid):
    for pid, cant in demanda(conn, lineas_cant).items():
        disp = stock_en(conn, pid, aid)
        if disp + EPS < cant:
            p = conn.execute("SELECT nombre, unidad FROM productos WHERE id=?", (pid,)).fetchone()
            raise ApiError(f"Stock insuficiente de «{p['nombre']}» (disponible: {disp:g} {p['unidad']})")


def dias_hasta(fecha):
    try:
        return (datetime.strptime(fecha, "%Y-%m-%d").date() - datetime.now().date()).days
    except ValueError:
        return None

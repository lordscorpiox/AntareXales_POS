"""Inventario avanzado (v0.5): almacenes, traslados, variantes, mermas, conteo físico, vencimientos y kardex global.

Todas estas rutas son solo para el administrador (ver SOLO_ADMIN en server.py)."""
import itertools
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, g, request

import database as db
import stock as stk
from errors import ApiError

bp = Blueprint("inventario", __name__)

# Se enlazan con las utilidades de server.py en register() (evita importaciones circulares).
get_db = ok = body = num = texto = rows = r2 = tiene_valor = validar_fecha = None
calcular_producto = categoria_valida = extras_producto = ficha_producto = get_config = int_arg = producto_por_id = None
aplicar_imagen = None

_ENLACES = ("get_db", "ok", "body", "num", "texto", "rows", "r2", "tiene_valor", "validar_fecha", "calcular_producto",
            "categoria_valida", "extras_producto", "ficha_producto", "get_config", "int_arg", "producto_por_id",
            "aplicar_imagen")


def register(app, srv):
    for n in _ENLACES:
        globals()[n] = getattr(srv, n)
    app.register_blueprint(bp)


TIPOS_MERMA = {"vencido": "Vencido", "danado": "Dañado", "perdida": "Pérdida / robo",
               "consumo": "Consumo interno", "otro": "Otro"}


# ---------------------------------------------------------------- almacenes
def almacen_out(conn, a):
    a = dict(a)
    a["productos"] = conn.execute("SELECT COUNT(*) FROM stock_almacen WHERE almacen_id=? AND cantidad>0", (a["id"],)).fetchone()[0]
    a["es_ventas"] = a["id"] == stk.almacen_ventas(conn)
    return a


@bp.get("/api/almacenes")
def almacenes_get():
    conn = get_db()
    lista = conn.execute("SELECT * FROM almacenes ORDER BY principal DESC, nombre COLLATE NOCASE").fetchall()
    return ok([almacen_out(conn, a) for a in lista])


@bp.post("/api/almacenes")
def almacenes_post():
    data = body()
    conn = get_db()
    nombre = texto(data.get("nombre"), "El nombre", True, 60)
    try:
        with conn:
            cur = conn.execute("INSERT INTO almacenes(nombre, principal, activo, creado) VALUES(?,0,1,?)", (nombre, db.now_str()))
    except sqlite3.IntegrityError:
        raise ApiError("Ya existe un almacén con ese nombre")
    return ok(almacen_out(conn, conn.execute("SELECT * FROM almacenes WHERE id=?", (cur.lastrowid,)).fetchone()), 201)


def almacen_por_id(conn, aid):
    a = conn.execute("SELECT * FROM almacenes WHERE id=?", (aid,)).fetchone()
    if not a:
        raise ApiError("Almacén no encontrado", 404)
    return a


def tiene_existencias(conn, aid):
    return conn.execute("SELECT 1 FROM stock_almacen WHERE almacen_id=? AND ABS(cantidad)>0.00005", (aid,)).fetchone() is not None


@bp.put("/api/almacenes/<int:aid>")
def almacenes_put(aid):
    data = body()
    conn = get_db()
    a = almacen_por_id(conn, aid)
    nombre = texto(data.get("nombre", a["nombre"]), "El nombre", True, 60)
    activo = 1 if str(data.get("activo", a["activo"])).lower() in ("1", "true", "si", "sí") else 0
    principal = 1 if str(data.get("principal", a["principal"])).lower() in ("1", "true", "si", "sí") else 0
    if a["principal"] and not principal:
        raise ApiError("Debe existir un almacén principal: marque otro como principal")
    if principal and not activo:
        raise ApiError("El almacén principal no puede estar inactivo")
    if not activo and a["activo"]:
        if tiene_existencias(conn, aid):
            raise ApiError("No se puede desactivar un almacén con existencias: traslade primero el stock")
        if aid == stk.almacen_ventas(conn):
            raise ApiError("Es el almacén de ventas: elija otro en Configuración antes de desactivarlo")
    try:
        with conn:
            if principal and not a["principal"]:
                conn.execute("UPDATE almacenes SET principal=0")
            conn.execute("UPDATE almacenes SET nombre=?, activo=?, principal=? WHERE id=?", (nombre, activo, principal, aid))
    except sqlite3.IntegrityError:
        raise ApiError("Ya existe un almacén con ese nombre")
    return ok(almacen_out(conn, almacen_por_id(conn, aid)))


@bp.delete("/api/almacenes/<int:aid>")
def almacenes_delete(aid):
    conn = get_db()
    a = almacen_por_id(conn, aid)
    if a["principal"]:
        raise ApiError("No se puede eliminar el almacén principal")
    if aid == stk.almacen_ventas(conn):
        raise ApiError("Es el almacén de ventas: elija otro en Configuración primero")
    if tiene_existencias(conn, aid):
        raise ApiError("El almacén tiene existencias: trasládelas antes de eliminarlo")
    with conn:
        usado = (conn.execute("SELECT 1 FROM movimientos_stock WHERE almacen_id=? LIMIT 1", (aid,)).fetchone()
                 or conn.execute("SELECT 1 FROM compras WHERE almacen_id=? LIMIT 1", (aid,)).fetchone())
        if usado:
            conn.execute("UPDATE almacenes SET activo=0 WHERE id=?", (aid,))  # conserva el historial
        else:
            conn.execute("DELETE FROM lotes WHERE almacen_id=?", (aid,))
            conn.execute("DELETE FROM stock_almacen WHERE almacen_id=?", (aid,))
            conn.execute("DELETE FROM almacenes WHERE id=?", (aid,))
    return ok(True)


# ---------------------------------------------------------------- traslados
@bp.post("/api/traslados")
def traslados_post():
    data = body()
    conn = get_db()
    try:
        pid = int(data.get("producto_id"))
    except (TypeError, ValueError):
        raise ApiError("Producto inválido")
    p = producto_por_id(conn, pid)
    if p["tipo"] == "combo":
        raise ApiError("Un combo no tiene stock propio: traslade sus componentes")
    origen = stk.almacen_valido(conn, data.get("origen_id"), "El almacén de origen")
    destino = stk.almacen_valido(conn, data.get("destino_id"), "El almacén de destino")
    cant = num(data.get("cantidad"), "La cantidad", estricto_positivo=True)
    if p["unidad"] == "und" and abs(cant - round(cant)) > 1e-9:
        raise ApiError("Este producto se maneja por unidad: la cantidad debe ser entera")
    try:
        conn.execute("BEGIN IMMEDIATE")
        tid = stk.trasladar(conn, pid, origen, destino, cant, g.user["usuario"], texto(data.get("notas"), "Las notas", False, 200))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok({"id": tid, "producto": ficha_producto(conn, pid)}, 201)


@bp.get("/api/traslados")
def traslados_get():
    conn = get_db()
    return ok(rows(conn.execute(
        "SELECT t.*, p.nombre AS producto, ao.nombre AS origen, ad.nombre AS destino FROM traslados t "
        "LEFT JOIN productos p ON p.id=t.producto_id LEFT JOIN almacenes ao ON ao.id=t.origen_id "
        "LEFT JOIN almacenes ad ON ad.id=t.destino_id ORDER BY t.id DESC LIMIT 200")))


# ---------------------------------------------------------------- variantes
def _codigo_variante(base, valores):
    parte = "-".join("".join(ch for ch in v.upper() if ch.isalnum())[:6] for v in valores)
    return f"{base}-{parte}"


@bp.post("/api/productos/variantes")
def variantes_post():
    """Crea de una vez todas las combinaciones de talla/color/etc. como productos con stock propio.

    opciones: [["S","M","L"],["Rojo","Azul"]] -> 6 productos «Nombre — S / Rojo», agrupados para la pantalla de ventas."""
    data = body()
    conn = get_db()
    nombre = texto(data.get("nombre"), "El nombre", True, 120)
    opciones = data.get("opciones")
    if not isinstance(opciones, list) or not opciones or len(opciones) > 3:
        raise ApiError("Indique de 1 a 3 atributos con sus opciones (por ejemplo talla y color)")
    limpias = []
    for o in opciones:
        if not isinstance(o, list):
            raise ApiError("Las opciones de cada atributo deben ser una lista")
        vals = []
        for v in o:
            v = texto(v, "Una opción", False, 40)
            if v and v.lower() not in [x.lower() for x in vals]:
                vals.append(v)
        if not vals:
            raise ApiError("Cada atributo necesita al menos una opción")
        limpias.append(vals)
    combos = list(itertools.product(*limpias))
    if len(combos) > 200:
        raise ApiError(f"Demasiadas combinaciones ({len(combos)}): el máximo es 200")
    codigo_base = texto(data.get("codigo"), "El código", False, 40)
    pr = calcular_producto(data)
    minimo = num(data.get("stock_minimo"), "El stock mínimo", 0, default=0.0)
    stock = num(data.get("stock"), "El stock inicial", 0, default=0.0)
    cat = categoria_valida(conn, data.get("categoria_id"))
    creados = []
    try:
        with conn:
            for vals in combos:
                etiqueta = " / ".join(vals)
                cur = conn.execute(
                    "INSERT INTO productos(codigo, nombre, categoria_id, unidad, costo_usd, ganancia_pct, precio_moneda, "
                    "precio_usd, precio_bs, iva_exento, iva_alicuota, stock, stock_minimo, tipo, grupo, variante, creado) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,'simple',?,?,?)",
                    (_codigo_variante(codigo_base, vals) if codigo_base else None, f"{nombre} — {etiqueta}", cat, pr["unidad"],
                     pr["costo_usd"], pr["ganancia_pct"], pr["precio_moneda"], pr["precio_usd"], pr["precio_bs"],
                     pr["iva_exento"], pr["iva_alicuota"], minimo, nombre, etiqueta, db.now_str()))
                if stock:
                    stk.ingresar(conn, cur.lastrowid, stk.almacen_principal(conn), stock, "inicial", "Stock inicial",
                                 g.user["usuario"])
                if tiene_value_img(data):
                    aplicar_imagen(conn, cur.lastrowid, data)
                creados.append(cur.lastrowid)
    except sqlite3.IntegrityError:
        raise ApiError("Alguno de los códigos generados ya existe: cambie el código base")
    return ok([ficha_producto(conn, i) for i in creados], 201)


def tiene_value_img(data):
    return tiene_valor(data, "imagen")


# ---------------------------------------------------------------- mermas
@bp.post("/api/mermas")
def mermas_post():
    data = body()
    conn = get_db()
    try:
        pid = int(data.get("producto_id"))
    except (TypeError, ValueError):
        raise ApiError("Producto inválido")
    p = producto_por_id(conn, pid)
    if p["tipo"] == "combo":
        raise ApiError("Un combo no tiene stock propio: registre la merma de sus componentes")
    tipo = str(data.get("tipo") or "").lower()
    if tipo not in TIPOS_MERMA:
        raise ApiError("Elija el motivo de la merma")
    cant = num(data.get("cantidad"), "La cantidad", estricto_positivo=True)
    if p["unidad"] == "und" and abs(cant - round(cant)) > 1e-9:
        raise ApiError("Este producto se maneja por unidad: la cantidad debe ser entera")
    aid = stk.almacen_valido(conn, data["almacen_id"]) if tiene_valor(data, "almacen_id") else stk.almacen_principal(conn)
    lote_id = int(data["lote_id"]) if tiene_valor(data, "lote_id") else None
    nota = texto(data.get("nota"), "La nota", False, 150)
    motivo = TIPOS_MERMA[tipo] + (f": {nota}" if nota else "")
    try:
        conn.execute("BEGIN IMMEDIATE")
        stk.consumir(conn, pid, aid, cant, "merma", motivo, g.user["usuario"], lote_id=lote_id, nombre=p["nombre"],
                     costo=p["costo_usd"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(ficha_producto(conn, pid), 201)


@bp.get("/api/mermas")
def mermas_get():
    conn = get_db()
    hoy = datetime.now().strftime("%Y-%m-%d")
    desde = validar_fecha(request.args.get("desde"), "Desde") or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta") or hoy
    lista = rows(conn.execute(
        "SELECT m.id, m.fecha, m.producto_id, p.nombre AS producto, p.unidad, m.cantidad, m.motivo, m.usuario, m.costo_usd, "
        "a.nombre AS almacen FROM movimientos_stock m JOIN productos p ON p.id=m.producto_id "
        "LEFT JOIN almacenes a ON a.id=m.almacen_id WHERE m.tipo='merma' AND date(m.fecha) BETWEEN ? AND ? "
        "ORDER BY m.id DESC LIMIT 500", (desde, hasta)))
    total = 0.0
    por_tipo = {}
    for m in lista:
        m["cantidad"] = abs(m["cantidad"])
        m["total_usd"] = r2(m["cantidad"] * m["costo_usd"])
        etiqueta = m["motivo"].split(":")[0]
        m["tipo_nombre"] = etiqueta
        total += m["total_usd"]
        por_tipo[etiqueta] = r2(por_tipo.get(etiqueta, 0) + m["total_usd"])
    return ok({"desde": desde, "hasta": hasta, "mermas": lista, "total_usd": r2(total), "por_tipo": por_tipo})


# ---------------------------------------------------------------- conteo físico
def conteo_por_id(conn, cid):
    c = conn.execute("SELECT c.*, a.nombre AS almacen FROM conteos c LEFT JOIN almacenes a ON a.id=c.almacen_id "
                     "WHERE c.id=?", (cid,)).fetchone()
    if not c:
        raise ApiError("Conteo no encontrado", 404)
    return dict(c)


def conteo_detalle(conn, cid):
    c = conteo_por_id(conn, cid)
    c["items"] = rows(conn.execute(
        "SELECT i.id, i.producto_id, p.nombre, p.codigo, p.unidad, i.esperado, i.contado FROM conteo_items i "
        "JOIN productos p ON p.id=i.producto_id WHERE i.conteo_id=? ORDER BY p.nombre COLLATE NOCASE", (cid,)))
    for i in c["items"]:
        i["diferencia"] = None if i["contado"] is None else round(i["contado"] - i["esperado"], 4)
    c["contados"] = sum(1 for i in c["items"] if i["contado"] is not None)
    return c


@bp.post("/api/conteos")
def conteos_post():
    """Abre un conteo físico: congela lo que el sistema espera tener en el almacén para los productos elegidos."""
    data = body()
    conn = get_db()
    aid = stk.almacen_valido(conn, data.get("almacen_id"), "El almacén")
    sql = "SELECT id FROM productos WHERE activo=1 AND tipo='simple'"
    params = []
    if isinstance(data.get("productos"), list) and data["productos"]:
        try:
            ids = [int(x) for x in data["productos"]]
        except (TypeError, ValueError):
            raise ApiError("Lista de productos inválida")
        sql += " AND id IN (%s)" % ",".join("?" * len(ids))
        params += ids
    elif tiene_valor(data, "categoria_id"):
        sql += " AND categoria_id=?"
        params.append(int(data["categoria_id"]))
    prods = [r["id"] for r in conn.execute(sql, params)]
    if not prods:
        raise ApiError("No hay productos para contar con ese criterio")
    with conn:
        cur = conn.execute("INSERT INTO conteos(fecha, almacen_id, usuario, estado, notas) VALUES(?,?,?,?,?)",
                           (db.now_str(), aid, g.user["usuario"], "borrador", texto(data.get("notas"), "Las notas", False, 200)))
        for pid in prods:
            conn.execute("INSERT INTO conteo_items(conteo_id, producto_id, esperado) VALUES(?,?,?)",
                         (cur.lastrowid, pid, stk.stock_en(conn, pid, aid)))
    return ok(conteo_detalle(conn, cur.lastrowid), 201)


@bp.get("/api/conteos")
def conteos_get():
    conn = get_db()
    return ok(rows(conn.execute(
        "SELECT c.*, a.nombre AS almacen, (SELECT COUNT(*) FROM conteo_items i WHERE i.conteo_id=c.id) AS productos, "
        "(SELECT COUNT(*) FROM conteo_items i WHERE i.conteo_id=c.id AND i.contado IS NOT NULL) AS contados "
        "FROM conteos c LEFT JOIN almacenes a ON a.id=c.almacen_id ORDER BY c.id DESC LIMIT 100")))


@bp.get("/api/conteos/<int:cid>")
def conteos_detalle(cid):
    return ok(conteo_detalle(get_db(), cid))


@bp.put("/api/conteos/<int:cid>")
def conteos_put(cid):
    data = body()
    conn = get_db()
    c = conteo_por_id(conn, cid)
    if c["estado"] != "borrador":
        raise ApiError("El conteo ya fue aplicado y no se puede modificar")
    items = data.get("items")
    if not isinstance(items, list):
        raise ApiError("Se esperaba la lista de items contados")
    with conn:
        for it in items:
            if not isinstance(it, dict):
                raise ApiError("Item inválido")
            try:
                pid = int(it.get("producto_id"))
            except (TypeError, ValueError):
                raise ApiError("Producto inválido")
            if tiene_valor(it, "contado"):
                v = num(it["contado"], "La cantidad contada", 0)
                conn.execute("UPDATE conteo_items SET contado=? WHERE conteo_id=? AND producto_id=?", (round(v, 4), cid, pid))
            else:
                conn.execute("UPDATE conteo_items SET contado=NULL WHERE conteo_id=? AND producto_id=?", (cid, pid))
    return ok(conteo_detalle(conn, cid))


@bp.post("/api/conteos/<int:cid>/aplicar")
def conteos_aplicar(cid):
    """Ajusta el stock del almacén a lo contado (solo los productos contados) y deja el rastro en el kardex."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = conteo_por_id(conn, cid)
        if c["estado"] != "borrador":
            raise ApiError("El conteo ya fue aplicado")
        aid = c["almacen_id"]
        usuario = g.user["usuario"]
        n = 0
        for it in conn.execute("SELECT * FROM conteo_items WHERE conteo_id=? AND contado IS NOT NULL", (cid,)).fetchall():
            actual = stk.stock_en(conn, it["producto_id"], aid)
            delta = round(it["contado"] - actual, 4)
            conn.execute("UPDATE conteo_items SET esperado=? WHERE id=?", (actual, it["id"]))
            if abs(delta) <= 1e-9:
                continue
            motivo = f"Conteo físico #{cid}"
            costo = conn.execute("SELECT costo_usd FROM productos WHERE id=?", (it["producto_id"],)).fetchone()[0]
            if delta > 0:
                stk.ingresar(conn, it["producto_id"], aid, delta, "conteo", motivo, usuario, costo=costo)
            else:
                stk.consumir(conn, it["producto_id"], aid, -delta, "conteo", motivo, usuario, permitir_negativo=True, costo=costo)
            n += 1
        conn.execute("UPDATE conteos SET estado='aplicado', aplicado_fecha=? WHERE id=?", (db.now_str(), cid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    d = conteo_detalle(conn, cid)
    d["ajustados"] = n
    return ok(d)


@bp.delete("/api/conteos/<int:cid>")
def conteos_delete(cid):
    conn = get_db()
    c = conteo_por_id(conn, cid)
    if c["estado"] != "borrador":
        raise ApiError("Un conteo aplicado no se puede eliminar")
    with conn:
        conn.execute("DELETE FROM conteo_items WHERE conteo_id=?", (cid,))
        conn.execute("DELETE FROM conteos WHERE id=?", (cid,))
    return ok(True)


# ---------------------------------------------------------------- vencimientos
@bp.get("/api/vencimientos")
def vencimientos_get():
    conn = get_db()
    cfg = get_config(conn)
    try:
        dias = int(request.args.get("dias") or cfg.get("dias_alerta_vencimiento") or 30)
    except ValueError:
        raise ApiError("Días inválidos")
    limite = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
    lista = rows(conn.execute(
        "SELECT l.id AS lote_id, l.codigo AS lote, l.vence, l.cantidad, l.producto_id, p.nombre AS producto, p.unidad, "
        "p.costo_usd, l.almacen_id, a.nombre AS almacen FROM lotes l JOIN productos p ON p.id=l.producto_id "
        "JOIN almacenes a ON a.id=l.almacen_id WHERE l.cantidad>0.00005 AND l.vence<>'' AND l.vence<=? AND p.activo=1 "
        "ORDER BY l.vence, p.nombre", (limite,)))
    vencidos = proximos = 0
    valor = 0.0
    for l in lista:
        l["dias"] = stk.dias_hasta(l["vence"])
        l["vencido"] = l["dias"] is not None and l["dias"] < 0
        l["valor_usd"] = r2(l["cantidad"] * l["costo_usd"])
        valor += l["valor_usd"]
        if l["vencido"]:
            vencidos += 1
        else:
            proximos += 1
    return ok({"dias": dias, "vencidos": vencidos, "proximos": proximos, "valor_usd": r2(valor), "lotes": lista})


# ---------------------------------------------------------------- kardex global
@bp.get("/api/kardex")
def kardex_get():
    conn = get_db()
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    sql = ("SELECT m.*, p.nombre AS producto, p.unidad, a.nombre AS almacen FROM movimientos_stock m "
           "LEFT JOIN productos p ON p.id=m.producto_id LEFT JOIN almacenes a ON a.id=m.almacen_id WHERE 1=1")
    params = []
    if desde:
        sql += " AND date(m.fecha)>=?"
        params.append(desde)
    if hasta:
        sql += " AND date(m.fecha)<=?"
        params.append(hasta)
    pid = int_arg("producto_id")
    if pid:
        sql += " AND m.producto_id=?"
        params.append(pid)
    aid = int_arg("almacen_id")
    if aid:
        sql += " AND m.almacen_id=?"
        params.append(aid)
    tipo = (request.args.get("tipo") or "").strip()
    if tipo:
        sql += " AND m.tipo=?"
        params.append(tipo)
    q = (request.args.get("q") or "").strip()
    if q:
        sql += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR m.motivo LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += " ORDER BY m.id DESC LIMIT 500"
    return ok(rows(conn.execute(sql, params)))

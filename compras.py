"""Compras y proveedores (v0.5): entrada de mercancía con costo, actualización de costo/precio y cuentas por pagar."""

from flask import Blueprint, g, request

import database as db
import stock as stk
from errors import ApiError

bp = Blueprint("compras", __name__)

get_db = ok = body = num = texto = rows = r2 = tiene_valor = validar_fecha = int_arg = get_config = None
producto_por_id = turno_actual = TOLERANCIA_USD = calcular_precio = None
_ENLACES = ("get_db", "ok", "body", "num", "texto", "rows", "r2", "tiene_valor", "validar_fecha", "int_arg", "get_config",
            "producto_por_id", "turno_actual", "TOLERANCIA_USD")


def register(app, srv):
    for n in _ENLACES:
        globals()[n] = getattr(srv, n)
    app.register_blueprint(bp)


# ---------------------------------------------------------------- proveedores
SALDO_PROV = ("COALESCE((SELECT SUM(c.total_usd - c.pagado_usd) FROM compras c "
              "WHERE c.proveedor_id=p.id AND c.estado='registrada'),0)")


def proveedor_por_id(conn, pid):
    f = conn.execute(f"SELECT p.*, {SALDO_PROV} AS saldo_usd FROM proveedores p WHERE p.id=? AND p.activo=1", (pid,)).fetchone()
    if not f:
        raise ApiError("Proveedor no encontrado", 404)
    d = dict(f)
    d["saldo_usd"] = r2(d["saldo_usd"])
    return d


@bp.get("/api/proveedores")
def proveedores_get():
    conn = get_db()
    q = (request.args.get("q") or "").strip()
    sql = f"SELECT p.*, {SALDO_PROV} AS saldo_usd FROM proveedores p WHERE p.activo=1"
    params = []
    if q:
        sql += " AND (p.nombre LIKE ? OR p.rif LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    out = rows(conn.execute(sql + " ORDER BY p.nombre COLLATE NOCASE", params))
    for p in out:
        p["saldo_usd"] = r2(p["saldo_usd"])
    return ok(out)


def _datos_proveedor(data, base=None):
    base = base or {}
    return (texto(data.get("nombre", base.get("nombre")), "El nombre", True, 120),
            texto(data.get("rif", base.get("rif", "")), "El RIF", False, 30),
            texto(data.get("telefono", base.get("telefono", "")), "El teléfono", False, 40),
            texto(data.get("direccion", base.get("direccion", "")), "La dirección", False, 200))


@bp.post("/api/proveedores")
def proveedores_post():
    conn = get_db()
    nombre, rif, tel, dire = _datos_proveedor(body())
    with conn:
        cur = conn.execute("INSERT INTO proveedores(nombre, rif, telefono, direccion, creado) VALUES(?,?,?,?,?)",
                           (nombre, rif, tel, dire, db.now_str()))
    return ok(proveedor_por_id(conn, cur.lastrowid), 201)


@bp.put("/api/proveedores/<int:pid>")
def proveedores_put(pid):
    conn = get_db()
    actual = proveedor_por_id(conn, pid)
    nombre, rif, tel, dire = _datos_proveedor(body(), actual)
    with conn:
        conn.execute("UPDATE proveedores SET nombre=?, rif=?, telefono=?, direccion=? WHERE id=?", (nombre, rif, tel, dire, pid))
        conn.execute("UPDATE compras SET proveedor_nombre=? WHERE proveedor_id=?", (nombre, pid))
    return ok(proveedor_por_id(conn, pid))


@bp.delete("/api/proveedores/<int:pid>")
def proveedores_delete(pid):
    conn = get_db()
    p = proveedor_por_id(conn, pid)
    if p["saldo_usd"] > TOLERANCIA_USD:
        raise ApiError(f"El proveedor tiene una deuda pendiente (${p['saldo_usd']:.2f}): regístrela como pagada antes de eliminarlo")
    with conn:
        if conn.execute("SELECT 1 FROM compras WHERE proveedor_id=? LIMIT 1", (pid,)).fetchone():
            conn.execute("UPDATE proveedores SET activo=0 WHERE id=?", (pid,))
        else:
            conn.execute("DELETE FROM proveedores WHERE id=?", (pid,))
    return ok(True)


# ---------------------------------------------------------------- compras
def compra_out(conn, cid, con_detalle=True):
    c = conn.execute("SELECT c.*, a.nombre AS almacen FROM compras c LEFT JOIN almacenes a ON a.id=c.almacen_id WHERE c.id=?",
                     (cid,)).fetchone()
    if not c:
        raise ApiError("Compra no encontrada", 404)
    d = dict(c)
    d["saldo_usd"] = r2(d["total_usd"] - d["pagado_usd"]) if d["estado"] == "registrada" else 0.0
    if con_detalle:
        d["items"] = rows(conn.execute("SELECT * FROM compra_items WHERE compra_id=? ORDER BY id", (cid,)))
        d["pagos"] = rows(conn.execute("SELECT * FROM pagos_proveedor WHERE compra_id=? ORDER BY id", (cid,)))
    return d


def _movimiento_caja(conn, monto, moneda, concepto):
    """Gasto de caja por un pago a proveedor (en el turno abierto del usuario)."""
    tid = turno_actual(conn, g.user["user_id"])
    if not tid:
        raise ApiError("Para pagar desde caja abra un turno de caja primero", 409)
    cur = conn.execute(
        "INSERT INTO caja_movimientos(turno_id, fecha, tipo, categoria, concepto, moneda, monto, usuario) "
        "VALUES(?,?,?,?,?,?,?,?)", (tid, db.now_str(), "gasto", "Compra de mercancía", concepto, moneda, monto, g.user["usuario"]))
    return cur.lastrowid


def _pago(conn, compra, data, monto_usd=None):
    """Registra un pago a proveedor (USD o Bs con la tasa del día). Devuelve el monto en USD aplicado."""
    moneda = str(data.get("moneda") or "USD").upper()
    if moneda not in ("USD", "BS"):
        raise ApiError("La moneda del pago debe ser USD o BS")
    if moneda == "BS":
        monto_bs = r2(num(data.get("monto_bs"), "El monto en Bs", estricto_positivo=True))
        tasa = num(data.get("tasa"), "La tasa del día", estricto_positivo=True)
        monto = r2(monto_bs / tasa)
        if monto <= 0:
            raise ApiError("El monto en Bs es muy pequeño para esa tasa")
    else:
        monto = r2(monto_usd if monto_usd is not None else num(data.get("monto_usd"), "El monto", estricto_positivo=True))
        monto_bs = tasa = 0.0
    saldo = compra["total_usd"] - compra["pagado_usd"]
    if monto > saldo + TOLERANCIA_USD:
        raise ApiError(f"El pago (${monto:.2f}) excede lo que se debe (${max(saldo, 0):.2f})")
    caja_id = None
    if str(data.get("desde_caja")).lower() in ("1", "true", "si", "sí"):
        caja_id = _movimiento_caja(conn, monto_bs if moneda == "BS" else monto, moneda,
                                   f"Pago compra #{compra['id']} · {compra['proveedor_nombre'] or 'sin proveedor'}")
    conn.execute(
        "INSERT INTO pagos_proveedor(compra_id, fecha, monto_usd, moneda, monto_bs, tasa, metodo, referencia, usuario, caja_mov_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (compra["id"], db.now_str(), monto, moneda, monto_bs, tasa, texto(data.get("metodo"), "El método", False, 40),
         texto(data.get("referencia"), "La referencia", False, 80), g.user["usuario"], caja_id))
    conn.execute("UPDATE compras SET pagado_usd=ROUND(pagado_usd+?,2) WHERE id=?", (monto, compra["id"]))
    return monto


@bp.post("/api/compras")
def compras_post():
    data = body()
    conn = get_db()
    cfg = get_config(conn)
    items_in = data.get("items")
    if not isinstance(items_in, list) or not items_in:
        raise ApiError("Agregue al menos un producto a la compra")
    aid = stk.almacen_valido(conn, data["almacen_id"]) if tiene_valor(data, "almacen_id") else stk.almacen_principal(conn)
    condicion = str(data.get("condicion") or "contado").lower()
    if condicion not in ("contado", "credito"):
        raise ApiError("La condición debe ser contado o crédito")
    prov = None
    if tiene_valor(data, "proveedor_id"):
        try:
            prov = proveedor_por_id(conn, int(data["proveedor_id"]))
        except ValueError:
            raise ApiError("Proveedor inválido")
    if condicion == "credito" and not prov:
        raise ApiError("Una compra a crédito necesita un proveedor")
    metodo_costo = str(data.get("metodo_costo") or "ultimo").lower()
    if metodo_costo not in ("ultimo", "promedio", "ninguno"):
        raise ApiError("El método de costo debe ser ultimo, promedio o ninguno")
    recalcular = str(data.get("recalcular_precio", True)).lower() in ("1", "true", "si", "sí")
    fecha = db.now_str()
    tasa_compra = num(data.get("tasa"), "La tasa", estricto_positivo=True, default=float(cfg.get("tasa_bcv") or 1))

    lineas = []
    for it in items_in:
        if not isinstance(it, dict):
            raise ApiError("Item inválido")
        try:
            pid = int(it.get("producto_id"))
        except (TypeError, ValueError):
            raise ApiError("Producto inválido")
        p = producto_por_id(conn, pid)
        if not p["activo"]:
            raise ApiError(f"«{p['nombre']}» está inactivo")
        if p["tipo"] == "combo":
            raise ApiError(f"«{p['nombre']}» es un combo: compre sus componentes")
        cant = round(num(it.get("cantidad"), f"La cantidad de {p['nombre']}", estricto_positivo=True), 3)
        if p["unidad"] == "und" and abs(cant - round(cant)) > 1e-9:
            raise ApiError(f"«{p['nombre']}» se maneja por unidad: la cantidad debe ser entera")
        costo = round(num(it.get("costo_usd"), f"El costo de {p['nombre']}", 0), 4)
        vence = validar_fecha(it.get("vence"), "El vencimiento") if tiene_valor(it, "vence") else ""
        lineas.append((p, cant, costo, vence, texto(it.get("lote"), "El lote", False, 60), r2(cant * costo)))
    total = r2(sum(l[5] for l in lineas))
    if condicion == "contado" and total <= 0:
        raise ApiError("El total de la compra es cero")
    # v0.13: datos de la factura del proveedor (opcionales; alimentan el libro de compras)
    fact_numero = texto(data.get("fact_numero"), "El número de factura del proveedor", False, 40)
    fact_control = texto(data.get("fact_control"), "El número de control de la factura", False, 40)
    fact_fecha = validar_fecha(data.get("fact_fecha"), "La fecha de la factura") if tiene_valor(data, "fact_fecha") else ""
    iva_fact = round(num(data.get("iva_usd"), "El IVA de la factura", 0, default=0.0), 2)
    exento_fact = round(num(data.get("exento_usd"), "El monto exento de la factura", 0, default=0.0), 2)
    if iva_fact + exento_fact > total + 0.005:
        raise ApiError("El IVA y el monto exento no pueden superar el total de la compra")

    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO compras(fecha, proveedor_id, proveedor_nombre, almacen_id, referencia, tasa, total_usd, pagado_usd, estado, "
            "notas, usuario, fact_numero, fact_control, fact_fecha, iva_usd, exento_usd) VALUES(?,?,?,?,?,?,?,0,'registrada',?,?,?,?,?,?,?)",
            (fecha, prov["id"] if prov else None,
             prov["nombre"] if prov else texto(data.get("proveedor_nombre"), "El proveedor", False, 120), aid,
             texto(data.get("referencia"), "La referencia", False, 60),
             tasa_compra, total,
             texto(data.get("notas"), "Las notas", False, 200), g.user["usuario"],
             fact_numero, fact_control, fact_fecha or "", iva_fact, exento_fact))
        cid = cur.lastrowid
        for p, cant, costo, vence, lote, sub in lineas:
            lote_id = stk.ingresar(conn, p["id"], aid, cant, "compra", f"Compra #{cid}", g.user["usuario"], lote, vence,
                                   compra_id=cid, costo=costo)
            conn.execute("INSERT INTO compra_items(compra_id, producto_id, nombre, cantidad, costo_usd, subtotal_usd, vence, lote, "
                         "lote_id) VALUES(?,?,?,?,?,?,?,?,?)", (cid, p["id"], p["nombre"], cant, costo, sub, vence, lote, lote_id))
            if metodo_costo != "ninguno" and costo > 0:
                _actualizar_costo(conn, p, cant, costo, metodo_costo, recalcular, cfg)
        if condicion == "contado":
            pdata = dict(data)
            if str(data.get("moneda") or "USD").upper() == "BS":  # contado en Bs: total × tasa de la compra
                pdata["monto_bs"] = r2(total * tasa_compra)
                pdata["tasa"] = tasa_compra
            _pago(conn, {"id": cid, "total_usd": total, "pagado_usd": 0.0,
                         "proveedor_nombre": prov["nombre"] if prov else ""}, pdata, monto_usd=total)
            conn.execute("UPDATE compras SET pagado_usd=? WHERE id=?", (total, cid))  # contado = saldo 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(compra_out(conn, cid), 201)


def _actualizar_costo(conn, p, cant, costo, metodo, recalcular, cfg):
    """Costo = último costo de compra (o promedio ponderado con lo que hay) y, si es precio en USD, recalcula el precio."""
    actual = conn.execute("SELECT * FROM productos WHERE id=?", (p["id"],)).fetchone()
    if metodo == "promedio":
        previo = max(actual["stock"] - cant, 0.0)  # el stock ya incluye esta entrada
        nuevo = round((previo * actual["costo_usd"] + cant * costo) / (previo + cant), 4) if previo > 0 else costo
    else:
        nuevo = costo
    pct = actual["ganancia_pct"]
    if not pct or pct <= 0:
        try:
            pct = float(cfg.get("ganancia_default") or 0)
        except ValueError:
            pct = 0.0
    if recalcular and actual["precio_moneda"] != "BS" and pct > 0:
        conn.execute("UPDATE productos SET costo_usd=?, ganancia_pct=?, precio_usd=? WHERE id=?",
                     (nuevo, round(pct, 2), r2(nuevo * (1 + pct / 100)), p["id"]))
    else:
        conn.execute("UPDATE productos SET costo_usd=? WHERE id=?", (nuevo, p["id"]))


@bp.get("/api/compras")
def compras_get():
    conn = get_db()
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    sql, params = "SELECT c.*, a.nombre AS almacen FROM compras c LEFT JOIN almacenes a ON a.id=c.almacen_id WHERE 1=1", []
    if desde:
        sql += " AND date(c.fecha)>=?"
        params.append(desde)
    if hasta:
        sql += " AND date(c.fecha)<=?"
        params.append(hasta)
    if int_arg("proveedor_id"):
        sql += " AND c.proveedor_id=?"
        params.append(int_arg("proveedor_id"))
    if request.args.get("pendientes") in ("1", "true"):
        sql += " AND c.estado='registrada' AND c.total_usd - c.pagado_usd > 0.0051"
    sql += " ORDER BY c.id DESC LIMIT 500"
    out = rows(conn.execute(sql, params))
    for c in out:
        c["saldo_usd"] = r2(c["total_usd"] - c["pagado_usd"]) if c["estado"] == "registrada" else 0.0
    return ok(out)


@bp.get("/api/compras/<int:cid>")
def compras_detalle(cid):
    return ok(compra_out(get_db(), cid))


@bp.post("/api/compras/<int:cid>/pagos")
def compras_pagar(cid):
    data = body()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = compra_out(conn, cid, False)
        if c["estado"] != "registrada":
            raise ApiError("La compra está anulada")
        _pago(conn, c, data)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(compra_out(conn, cid), 201)


@bp.delete("/api/compras/<int:cid>/pagos/<int:pid>")
def compras_pago_delete(cid, pid):
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = compra_out(conn, cid, False)
        if c["estado"] != "registrada":
            raise ApiError("La compra está anulada")
        p = conn.execute("SELECT * FROM pagos_proveedor WHERE id=? AND compra_id=?", (pid, cid)).fetchone()
        if not p:
            raise ApiError("Pago no encontrado", 404)
        _revertir_pago(conn, p)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(compra_out(conn, cid))


def _revertir_pago(conn, p):
    conn.execute("DELETE FROM pagos_proveedor WHERE id=?", (p["id"],))
    conn.execute("UPDATE compras SET pagado_usd=MAX(ROUND(pagado_usd-?,2),0) WHERE id=?", (p["monto_usd"], p["compra_id"]))
    if p["caja_mov_id"]:
        # el dinero vuelve a la caja solo si ese turno sigue abierto
        conn.execute("UPDATE caja_movimientos SET anulado=1 WHERE id=? AND turno_id IN (SELECT id FROM turnos WHERE estado='abierto')",
                     (p["caja_mov_id"],))


@bp.post("/api/compras/<int:cid>/anular")
def compras_anular(cid):
    """Anula la compra y saca la mercancía que entró; solo si aún está en el almacén (no vendida)."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = compra_out(conn, cid, False)
        if c["estado"] != "registrada":
            raise ApiError("La compra ya está anulada")
        aid = c["almacen_id"]
        items = rows(conn.execute("SELECT * FROM compra_items WHERE compra_id=?", (cid,)))
        for it in items:
            if it["lote_id"]:
                l = conn.execute("SELECT cantidad FROM lotes WHERE id=?", (it["lote_id"],)).fetchone()
                if not l or l["cantidad"] + 1e-9 < it["cantidad"]:
                    raise ApiError(f"No se puede anular: parte de «{it['nombre']}» (lote) ya se vendió o se movió")
            else:
                libre = stk.stock_en(conn, it["producto_id"], aid) - (conn.execute(
                    "SELECT COALESCE(SUM(cantidad),0) FROM lotes WHERE producto_id=? AND almacen_id=?",
                    (it["producto_id"], aid)).fetchone()[0])
                if libre + 1e-9 < it["cantidad"]:
                    raise ApiError(f"No se puede anular: parte de «{it['nombre']}» ya se vendió o se movió")
        for it in items:
            if it["lote_id"]:
                stk.consumir(conn, it["producto_id"], aid, it["cantidad"], "anulación", f"Anulación compra #{cid}",
                             g.user["usuario"], lote_id=it["lote_id"], nombre=it["nombre"])
            else:
                stk.mover(conn, it["producto_id"], aid, -it["cantidad"], "anulación", f"Anulación compra #{cid}", g.user["usuario"])
        for p in conn.execute("SELECT * FROM pagos_proveedor WHERE compra_id=?", (cid,)).fetchall():
            if p["caja_mov_id"]:
                conn.execute("UPDATE caja_movimientos SET anulado=1 WHERE id=? AND turno_id IN "
                             "(SELECT id FROM turnos WHERE estado='abierto')", (p["caja_mov_id"],))
        conn.execute("UPDATE compras SET estado='anulada', anulada_fecha=?, anulada_por=? WHERE id=?",
                     (db.now_str(), g.user["usuario"], cid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(compra_out(conn, cid))


@bp.get("/api/compras-resumen")
def compras_resumen():
    """Totales de compras y cuentas por pagar (admin) para el rango de fechas."""
    conn = get_db()
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    cond, params = "estado='registrada'", []
    if desde:
        cond += " AND date(fecha)>=?"
        params.append(desde)
    if hasta:
        cond += " AND date(fecha)<=?"
        params.append(hasta)
    t = conn.execute(f"SELECT COUNT(*) n, COALESCE(SUM(total_usd),0) total FROM compras WHERE {cond}", params).fetchone()
    por_pagar = conn.execute("SELECT COALESCE(SUM(total_usd-pagado_usd),0) FROM compras WHERE estado='registrada' "
                             "AND total_usd-pagado_usd>0.0051").fetchone()[0]
    return ok({"num_compras": t["n"], "total_usd": r2(t["total"]), "por_pagar_usd": r2(por_pagar)})

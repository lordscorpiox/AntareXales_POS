"""Libros de ventas y de compras, contingencia y alertas (v0.13).

Todo es de solo lectura salvo «completar el número de control» de un documento emitido en contingencia.
Los libros se arman con lo ya registrado (documentos y compras) y se pueden filtrar por período."""
import calendar
import os
from datetime import datetime, timedelta

from flask import Blueprint, g, request

import database as db
import fiscal
from errors import ApiError

bp = Blueprint("libros", __name__)

get_db = ok = body = rows = r2 = texto = validar_fecha = get_config = es_admin = None
_ENLACES = ("get_db", "ok", "body", "rows", "r2", "texto", "validar_fecha", "get_config", "es_admin")
TIPOS_LIBRO = ("factura", "nota_credito", "nota_debito")
NOMBRE_TIPO = {"factura": "Factura", "nota_credito": "Nota de crédito", "nota_debito": "Nota de débito"}


def register(app, srv):
    for n in _ENLACES:
        globals()[n] = getattr(srv, n)
    srv.ACCIONES[("POST", "/api/documentos/<int:did>/control")] = "número de control completado (contingencia)"
    app.register_blueprint(bp)


def periodo():
    """(desde, hasta) 'AAAA-MM-DD'; sin parámetros, el mes en curso."""
    hoy = datetime.now()
    desde = validar_fecha(request.args.get("desde"), "La fecha inicial")
    hasta = validar_fecha(request.args.get("hasta"), "La fecha final")
    if not desde:
        desde = hoy.strftime("%Y-%m-01")
    if not hasta:
        hasta = hoy.strftime("%Y-%m-") + f"{calendar.monthrange(hoy.year, hoy.month)[1]:02d}"
    if desde > hasta:
        raise ApiError("La fecha inicial no puede ser posterior a la final")
    return desde, hasta


# ---------------------------------------------------------------- libro de ventas
@bp.get("/api/libros/ventas")
def libro_ventas():
    conn = get_db()
    desde, hasta = periodo()
    docs = rows(conn.execute(
        "SELECT d.*, r.numero AS ref_numero, r.tipo AS ref_tipo FROM documentos d LEFT JOIN documentos r ON r.id=d.doc_ref_id "
        "WHERE d.tipo IN ('factura','nota_credito','nota_debito') AND substr(d.fecha,1,10) BETWEEN ? AND ? ORDER BY d.fecha, d.id",
        (desde, hasta)))
    tot = {k: 0.0 for k in ("exento_bs", "base_bs", "iva_bs", "igtf_bs", "total_bs", "exento_usd", "base_usd", "iva_usd", "igtf_usd", "total_usd")}
    out, alic = [], {}
    pendientes = 0
    for d in docs:
        anulado = d["estado"] == "anulado"
        sg = -1.0 if d["tipo"] == "nota_credito" else 1.0
        z = 0.0 if anulado else sg
        fila = {"id": d["id"], "fecha": d["fecha"], "tipo": d["tipo"], "tipo_nombre": NOMBRE_TIPO[d["tipo"]], "numero": d["numero"],
                "control": d["control"], "contingencia": d["contingencia"], "fiscal": d["fiscal"], "estado": d["estado"],
                "cliente": d["cli_nombre"], "rif": d["cli_rif"], "tasa": d["tasa"],
                "ref": (f"{NOMBRE_TIPO.get(d['ref_tipo'], '')} {d['ref_numero']}" if d["doc_ref_id"] else ""),
                "pendiente_control": 1 if (d["fiscal"] and d["contingencia"] and not d["control"] and not anulado) else 0}
        pendientes += fila["pendiente_control"]
        for k in ("exento", "base", "iva"):
            fila[k + "_bs"] = r2(d[k + "_bs"] * z)
            fila[k + "_usd"] = r2(d[k + "_usd"] * z)
        fila["igtf_bs"] = r2(d["igtf_bs"] * z)
        fila["igtf_usd"] = r2(d["igtf_usd"] * z)
        fila["total_bs"] = r2(d["total_bs"] * z)
        fila["total_usd"] = r2(d["total_usd"] * z)
        for k in tot:
            tot[k] += fila[k]
        if not anulado:
            for it in conn.execute("SELECT exento, iva_pct, base_bs, iva_bs, base_usd, iva_usd FROM documento_items WHERE documento_id=?", (d["id"],)):
                clave = "exento" if it["exento"] else f"{it['iva_pct']:g}"
                a = alic.setdefault(clave, {"alicuota": clave, "base_bs": 0.0, "iva_bs": 0.0, "base_usd": 0.0, "iva_usd": 0.0})
                for k in ("base_bs", "iva_bs", "base_usd", "iva_usd"):
                    a[k] += it[k] * sg
        out.append(fila)
    por_alic = sorted(({**a, **{k: r2(a[k]) for k in ("base_bs", "iva_bs", "base_usd", "iva_usd")}} for a in alic.values()),
                      key=lambda a: (a["alicuota"] != "exento", float(a["alicuota"]) if a["alicuota"] != "exento" else 0.0))
    cfg = get_config(conn)
    emisor = {"nombre": cfg.get("fiscal_razon_social") or cfg.get("negocio_nombre") or "",
              "rif": cfg.get("rif") or "", "direccion": cfg.get("direccion") or ""}
    return ok({"desde": desde, "hasta": hasta, "emisor": emisor, "items": out, "totales": {k: r2(v) for k, v in tot.items()},
               "por_alicuota": por_alic, "pendientes_control": pendientes, "n": len(out),
               "anulados": sum(1 for d in docs if d["estado"] == "anulado")})


# ---------------------------------------------------------------- libro de compras
@bp.get("/api/libros/compras")
def libro_compras():
    conn = get_db()
    desde, hasta = periodo()
    cs = rows(conn.execute(
        "SELECT c.*, p.rif AS prov_rif FROM compras c LEFT JOIN proveedores p ON p.id=c.proveedor_id "
        "WHERE substr(c.fecha,1,10) BETWEEN ? AND ? ORDER BY c.fecha, c.id", (desde, hasta)))
    tot = {k: 0.0 for k in ("exento_bs", "base_bs", "iva_bs", "total_bs", "exento_usd", "base_usd", "iva_usd", "total_usd")}
    out, sin_factura = [], 0
    for c in cs:
        anulada = c["estado"] != "registrada"
        z = 0.0 if anulada else 1.0
        total = c["total_usd"] * z
        iva = min(c["iva_usd"], c["total_usd"]) * z
        exento = min(c["exento_usd"], max(0.0, c["total_usd"] - c["iva_usd"])) * z
        base = max(0.0, total - iva - exento)
        t = c["tasa"] or 1.0
        fila = {"id": c["id"], "fecha": c["fecha"], "fact_fecha": c["fact_fecha"], "proveedor": c["proveedor_nombre"], "rif": c["prov_rif"] or "",
                "fact_numero": c["fact_numero"], "fact_control": c["fact_control"], "referencia": c["referencia"], "estado": c["estado"],
                "tasa": t, "exento_usd": r2(exento), "base_usd": r2(base), "iva_usd": r2(iva), "total_usd": r2(total),
                "exento_bs": r2(exento * t), "base_bs": r2(base * t), "iva_bs": r2(iva * t), "total_bs": r2(total * t),
                "sin_factura": 1 if (not anulada and not c["fact_numero"]) else 0}
        sin_factura += fila["sin_factura"]
        for k in tot:
            tot[k] += fila[k]
        out.append(fila)
    cfg = get_config(conn)
    emisor = {"nombre": cfg.get("fiscal_razon_social") or cfg.get("negocio_nombre") or "",
              "rif": cfg.get("rif") or "", "direccion": cfg.get("direccion") or ""}
    return ok({"desde": desde, "hasta": hasta, "emisor": emisor, "items": out, "totales": {k: r2(v) for k, v in tot.items()},
               "sin_factura": sin_factura, "n": len(out), "anuladas": sum(1 for c in cs if c["estado"] != "registrada")})


# ---------------------------------------------------------------- contingencia: completar el número de control
@bp.post("/api/documentos/<int:did>/control")
def documento_completar_control(did):
    if not es_admin():
        raise ApiError("Solo el administrador puede completar el número de control", 403)
    data = body()
    conn = get_db()
    d = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
    if not d:
        raise ApiError("Documento no encontrado", 404)
    if not (d["fiscal"] and d["contingencia"]) or d["control"]:
        raise ApiError("Solo se puede completar el número de control de un documento emitido en contingencia que aún no lo tiene")
    if d["estado"] == "anulado":
        raise ApiError("El documento está anulado")
    control = texto(data.get("control"), "El número de control", True, 40)
    if not fiscal.RE_CONTROL.match(control):
        raise ApiError("El número de control solo admite letras, números y guion")
    if conn.execute("SELECT 1 FROM documentos WHERE tipo IN ('factura','nota_credito','nota_debito') AND control=? AND id<>?", (control, did)).fetchone():
        raise ApiError(f"El número de control {control} ya fue usado en otro documento")
    with conn:
        conn.execute("UPDATE documentos SET control=? WHERE id=?", (control, did))
    g.audit_detalle = f"{d['tipo']} N° {d['numero']} · control {control}"
    return ok({"id": did, "control": control})


# ---------------------------------------------------------------- alertas
def _dias_desde(fecha_txt):
    try:
        return (datetime.now() - datetime.strptime(fecha_txt[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None


def calcular_alertas(conn, cfg, admin):
    """Lista de avisos [{id, nivel crit|warn|info, titulo, detalle, vista}] según la configuración y el estado del negocio."""
    import respaldo
    al = []

    def a(id_, nivel, titulo, detalle, vista=""):
        al.append({"id": id_, "nivel": nivel, "titulo": titulo, "detalle": detalle, "vista": vista})

    fiscal_on = cfg.get("fiscal_activo") == "1"
    if cfg.get("contingencia_activa") == "1":
        a("contingencia", "warn", "Modo contingencia activo",
          "Las facturas se emiten sin número de control" + (f" (desde {cfg.get('contingencia_desde')})" if cfg.get("contingencia_desde") else "") +
          (f". Motivo: {cfg.get('contingencia_motivo')}" if cfg.get("contingencia_motivo") else "") + ". Desactívelo cuando se normalice el servicio.", "cfg-fiscal")
    pend = conn.execute("SELECT COUNT(*) FROM documentos WHERE fiscal=1 AND contingencia=1 AND control='' AND estado='emitido'").fetchone()[0]
    if pend:
        a("pend_control", "warn", f"{pend} documento(s) sin número de control",
          "Se emitieron en contingencia. Complete el número de control en Documentos o en el Libro de ventas.", "libros" if admin else "documentos")
    if not admin:
        return al
    if fiscal_on and cfg.get("control_modo") == "serie":
        try:
            hasta, sig = int(float(cfg.get("control_hasta") or 0)), int(float(cfg.get("sig_control") or 1))
            umbral = int(float(cfg.get("alerta_rango_restante") or 20))
        except ValueError:
            hasta = sig = umbral = 0
        if hasta:
            resta = hasta - sig + 1
            if resta <= 0:
                a("rango", "crit", "Se agotó el rango de números de control", "No se podrá facturar hasta configurar un nuevo rango autorizado.", "cfg-fiscal")
            elif resta <= umbral:
                a("rango", "warn", f"Quedan {resta} número(s) de control", f"El rango autorizado termina en {hasta}. Solicite uno nuevo con tiempo.", "cfg-fiscal")
    dias = int(float(cfg.get("alerta_respaldo_dias") or 3))
    locales = respaldo.listar()
    ult_local = locales[0]["fecha"] if locales else None
    ext_on = cfg.get("respaldo_externo_activo") == "1"
    ext = respaldo.externo_ultimo(conn)
    edad_local = _dias_desde(ult_local) if ult_local else None
    if ult_local is None:
        a("resp_local", "warn", "Aún no hay respaldos de la base de datos", "Haga un respaldo en Configuración › Respaldo.", "respaldo")
    elif edad_local is not None and edad_local > dias and not ext_on:
        a("resp_local", "warn", f"El último respaldo tiene {int(edad_local)} día(s)", "Haga un respaldo y guárdelo fuera de este equipo.", "respaldo")
    if ext_on:
        if ext and not ext["ok"]:
            a("resp_ext", "crit", "Falló el último respaldo externo", ext["detalle"], "respaldo")
        elif not ext:
            a("resp_ext", "warn", "Respaldo externo activado pero aún sin copias", "Pulse «Respaldar ahora» en Configuración › Respaldo.", "respaldo")
        else:
            e = _dias_desde(ext["fecha"])
            if e is not None and e > dias:
                a("resp_ext", "warn", f"El último respaldo externo tiene {int(e)} día(s)", "Compruebe que el disco o USB esté conectado.", "respaldo")
        ruta = cfg.get("respaldo_externo_ruta") or ""
        if ruta and not os.path.isdir(ruta):
            a("resp_ext_ruta", "crit", "La carpeta del respaldo externo no está disponible", f"{ruta}: ¿disco o USB desconectado?", "respaldo")
    elif fiscal_on:
        a("resp_ext_cfg", "info", "Configure un respaldo externo", "Con el modo fiscal activo conviene guardar copias fuera del equipo (otro disco, USB o carpeta sincronizada).", "respaldo")
    return al


@bp.get("/api/alertas")
def alertas():
    conn = get_db()
    al = calcular_alertas(conn, get_config(conn), es_admin())
    orden = {"crit": 0, "warn": 1, "info": 2}
    al.sort(key=lambda x: orden[x["nivel"]])
    return ok({"items": al, "n": len(al), "criticas": sum(1 for x in al if x["nivel"] == "crit")})

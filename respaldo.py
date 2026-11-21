"""Respaldo automático (v0.6): una copia diaria de la base de datos en la carpeta «respaldos», con rotación.

Se hace con la API de respaldo de SQLite (copia consistente aunque la app esté en uso). La carpeta queda junto a la
base de datos (%APPDATA%\\AntareXalesPOS\\respaldos). Los respaldos manuales («Respaldar ahora») no se borran solos."""
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime

from flask import Blueprint, Response, g, request

import database as db
from errors import ApiError

bp = Blueprint("respaldo", __name__)
log = logging.getLogger("antarexales")

get_db = ok = get_config = None
_ENLACES = ("get_db", "ok", "get_config")
RE_NOMBRE = re.compile(r"^antarexales_(auto|manual)_\d{8}_\d{6}\.db$")
RE_EXTERNO = re.compile(r"^antarexales_ext_\d{8}_\d{6}\.db$")
_lock = threading.Lock()
_hilo = None


def register(app, srv):
    for n in _ENLACES:
        globals()[n] = getattr(srv, n)
    app.register_blueprint(bp)


def carpeta():
    ruta = os.path.join(os.path.dirname(db.get_db_path()), "respaldos")
    os.makedirs(ruta, exist_ok=True)
    return ruta


def hacer_respaldo(tipo="auto"):
    """Crea una copia consistente de la base y, si es automática, borra las más antiguas según «respaldo_auto_dias»."""
    with _lock:
        nombre = f"antarexales_{tipo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        destino = os.path.join(carpeta(), nombre)
        src = db.get_conn()
        try:
            dst = sqlite3.connect(destino)
            try:
                src.backup(dst)
            finally:
                dst.close()
            conservar = int(get_conservar(src))
        finally:
            src.close()
        if tipo == "auto" and conservar > 0:
            autos = sorted(f for f in os.listdir(carpeta()) if f.startswith("antarexales_auto_") and RE_NOMBRE.match(f))
            for viejo in autos[:-conservar]:
                try:
                    os.remove(os.path.join(carpeta(), viejo))
                except OSError:
                    log.warning("No se pudo borrar el respaldo antiguo %s", viejo)
        log.info("Respaldo %s creado: %s", tipo, nombre)
        _externo_si_activo(destino)
        return nombre


# ---------------------------------------------------------------- respaldo externo (v0.13)
def _cfg(conn, clave, defecto=""):
    r = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    return r[0] if r and r[0] is not None else defecto


def _guardar_cfg(conn, clave, valor):
    conn.execute("INSERT INTO config(clave, valor) VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, valor))
    conn.commit()


def validar_carpeta_externa(valor):
    """Normaliza y comprueba la carpeta del respaldo externo: absoluta, existente, escribible y FUERA de los datos de la app."""
    v = str(valor or "").strip()
    if not v:
        return ""
    if len(v) > 300:
        raise ApiError("La ruta es demasiado larga")
    if not os.path.isabs(v):
        raise ApiError("Escriba la ruta completa de la carpeta (por ejemplo D:\\Respaldos o E:\\USB\\Copias)")
    v = os.path.normpath(v)
    datos = os.path.realpath(os.path.dirname(db.get_db_path()))
    real = os.path.realpath(v)
    if real == datos or real.startswith(datos + os.sep):
        raise ApiError("La carpeta externa debe estar fuera de la carpeta de datos de la aplicación (otro disco, USB o carpeta sincronizada)")
    if not os.path.isdir(v):
        raise ApiError("La carpeta no existe o no está disponible: " + v)
    prueba = os.path.join(v, f".antarexales_prueba_{os.getpid()}.tmp")
    try:
        with open(prueba, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(prueba)
    except OSError as e:
        raise ApiError("No se puede escribir en esa carpeta: " + str(e.strerror or e))
    return v


def _integridad_ok(ruta):
    c = sqlite3.connect(ruta)
    try:
        return c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        c.close()


def _copiar_externo(origen=None):
    """Copia (y verifica) un respaldo en la carpeta externa. Si no hay `origen`, hace una copia consistente de la base.
    Registra el resultado en «respaldo_externo_ultimo». Devuelve el nombre; lanza ApiError si falla. Llamar con _lock tomado."""
    conn = db.get_conn()
    try:
        ruta = _cfg(conn, "respaldo_externo_ruta")
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not ruta:
            raise ApiError("No hay carpeta de respaldo externo configurada")
        try:
            ruta = validar_carpeta_externa(ruta)
            nombre = f"antarexales_ext_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            final, parcial = os.path.join(ruta, nombre), os.path.join(ruta, nombre + ".part")
            try:
                if origen:
                    shutil.copyfile(origen, parcial)
                else:
                    dst = sqlite3.connect(parcial)
                    try:
                        conn.backup(dst)
                    finally:
                        dst.close()
                if not _integridad_ok(parcial):
                    raise ApiError("La copia externa no pasó la verificación de integridad")
                os.replace(parcial, final)
            finally:
                if os.path.exists(parcial):
                    try:
                        os.remove(parcial)
                    except OSError:
                        pass
            conservar = get_conservar(conn)
            if conservar > 0:
                viejos = sorted(f for f in os.listdir(ruta) if RE_EXTERNO.match(f))[:-conservar]
                for f in viejos:
                    try:
                        os.remove(os.path.join(ruta, f))
                    except OSError:
                        log.warning("No se pudo borrar el respaldo externo antiguo %s", f)
            _guardar_cfg(conn, "respaldo_externo_ultimo", f"{ahora}|ok|{nombre}")
            log.info("Respaldo externo creado: %s", final)
            return nombre
        except (ApiError, OSError, sqlite3.Error) as e:
            msg = e.message if isinstance(e, ApiError) and hasattr(e, "message") else str(e)
            _guardar_cfg(conn, "respaldo_externo_ultimo", f"{ahora}|error|{msg}"[:300])
            log.warning("Falló el respaldo externo: %s", msg)
            raise ApiError("Falló el respaldo externo: " + msg, 500) if not isinstance(e, ApiError) else e
    finally:
        conn.close()


def _externo_si_activo(origen=None):
    conn = db.get_conn()
    try:
        activo = _cfg(conn, "respaldo_externo_activo", "0") == "1"
    finally:
        conn.close()
    if activo:
        try:
            _copiar_externo(origen)
        except Exception:
            pass   # ya quedó registrado en «respaldo_externo_ultimo»; el respaldo local sigue siendo válido


def externo_ultimo(conn):
    """{'fecha','ok','detalle'} del último intento de respaldo externo, o None."""
    v = _cfg(conn, "respaldo_externo_ultimo")
    if not v:
        return None
    partes = v.split("|", 2)
    if len(partes) < 3:
        return None
    return {"fecha": partes[0], "ok": partes[1] == "ok", "detalle": partes[2]}


def get_conservar(conn):
    r = conn.execute("SELECT valor FROM config WHERE clave='respaldo_auto_dias'").fetchone()
    try:
        return max(0, int(float(r[0]))) if r else 14
    except (TypeError, ValueError):
        return 14


def listar():
    out = []
    for f in os.listdir(carpeta()):
        if RE_NOMBRE.match(f):
            st = os.stat(os.path.join(carpeta(), f))
            out.append({"nombre": f, "tipo": "auto" if "_auto_" in f else "manual", "bytes": st.st_size,
                        "fecha": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return sorted(out, key=lambda x: x["nombre"], reverse=True)


def hay_respaldo_de_hoy():
    hoy = datetime.now().strftime("%Y%m%d")
    return any(f.startswith(f"antarexales_auto_{hoy}_") for f in os.listdir(carpeta()))


def ciclo(intervalo=1800):
    """Hilo en segundo plano: al abrir la app y cada 30 min comprueba si falta el respaldo del día."""
    while True:
        try:
            conn = db.get_conn()
            try:
                activo = get_conservar(conn) > 0
            finally:
                conn.close()
            if activo and not hay_respaldo_de_hoy():
                hacer_respaldo("auto")
            conn = db.get_conn()
            try:
                ext = _cfg(conn, "respaldo_externo_activo") == "1"
                ult = externo_ultimo(conn)
            finally:
                conn.close()
            if ext and not (ult and ult["ok"] and ult["fecha"][:10] == datetime.now().strftime("%Y-%m-%d")):
                with _lock:
                    try:
                        _copiar_externo()
                    except Exception:
                        pass
        except Exception:
            log.exception("Falló el respaldo automático")
        time.sleep(intervalo)


def iniciar_automatico():
    global _hilo
    if _hilo is None or not _hilo.is_alive():
        _hilo = threading.Thread(target=ciclo, name="respaldo-auto", daemon=True)
        _hilo.start()


# ---------------------------------------------------------------- API (solo administrador: prefijo /api/backup)
@bp.get("/api/backup/auto")
def auto_estado():
    conn = get_db()
    lista = listar()
    autos = [b for b in lista if b["tipo"] == "auto"]
    return ok({"carpeta": carpeta(), "conservar": get_conservar(conn), "respaldos": lista[:60],
               "ultimo_auto": autos[0]["fecha"] if autos else None})


@bp.post("/api/backup/auto")
def auto_ahora():
    nombre = hacer_respaldo("manual")
    return ok({"nombre": nombre}, 201)


@bp.get("/api/backup/auto/<nombre>")
def auto_descargar(nombre):
    if not RE_NOMBRE.match(nombre):
        raise ApiError("Nombre de respaldo no válido", 400)
    ruta = os.path.join(carpeta(), nombre)
    if not os.path.isfile(ruta):
        raise ApiError("Respaldo no encontrado", 404)
    with open(ruta, "rb") as fh:
        contenido = fh.read()
    resp = Response(contenido, mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{nombre}"'
    resp.headers["Content-Length"] = str(len(contenido))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/api/backup/externo")
def externo_estado():
    conn = get_db()
    ruta = _cfg(conn, "respaldo_externo_ruta")
    archivos, error = [], ""
    if ruta:
        try:
            for f in sorted((f for f in os.listdir(ruta) if RE_EXTERNO.match(f)), reverse=True)[:30]:
                st = os.stat(os.path.join(ruta, f))
                archivos.append({"nombre": f, "bytes": st.st_size, "fecha": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
        except OSError:
            error = "La carpeta no está disponible (¿disco o USB desconectado?)"
    return ok({"activo": _cfg(conn, "respaldo_externo_activo") == "1", "ruta": ruta, "ultimo": externo_ultimo(conn),
               "archivos": archivos, "error": error})


@bp.post("/api/backup/externo/probar")
def externo_probar():
    d = request.get_json(silent=True) or {}
    ruta = validar_carpeta_externa(d.get("ruta"))
    if not ruta:
        raise ApiError("Escriba la ruta de la carpeta")
    return ok({"ruta": ruta})


@bp.post("/api/backup/externo")
def externo_ahora():
    with _lock:
        nombre = _copiar_externo()
    g.audit_detalle = "respaldo externo " + nombre
    return ok({"nombre": nombre}, 201)

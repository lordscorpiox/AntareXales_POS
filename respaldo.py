"""Respaldo automático (v0.6): una copia diaria de la base de datos en la carpeta «respaldos», con rotación.

Se hace con la API de respaldo de SQLite (copia consistente aunque la app esté en uso). La carpeta queda junto a la
base de datos (%APPDATA%\\AntareXalesPOS\\respaldos). Los respaldos manuales («Respaldar ahora») no se borran solos."""
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime

from flask import Blueprint, Response

import database as db
from errors import ApiError

bp = Blueprint("respaldo", __name__)
log = logging.getLogger("antarexales")

get_db = ok = get_config = None
_ENLACES = ("get_db", "ok", "get_config")
RE_NOMBRE = re.compile(r"^antarexales_(auto|manual)_\d{8}_\d{6}\.db$")
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
        return nombre


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

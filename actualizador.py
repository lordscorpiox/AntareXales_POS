"""Actualizaciones automáticas (v0.8).

Consulta el último *Release* del repositorio de GitHub (ver version.GITHUB_REPO), compara su versión con la instalada y,
si el administrador lo pide, descarga el nuevo AntareXales_POS.exe, verifica su huella SHA-256 (publicada junto al .exe),
hace un respaldo de la base de datos y reemplaza el ejecutable en uso mediante un pequeño script que espera a que la
aplicación se cierre y la vuelve a abrir. Solo funciona con el .exe de Windows; en modo desarrollo solo informa."""
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from flask import Blueprint

import database as db
import version as ver
from errors import ApiError

bp = Blueprint("actualizador", __name__)
log = logging.getLogger("antarexales")

get_db = ok = get_config = respaldo = None
_ENLACES = ("get_db", "ok", "get_config", "respaldo")

EXE_NOMBRE = "AntareXales_POS.exe"
SHA_NOMBRE = EXE_NOMBRE + ".sha256"
MAX_EXE = 300 * 1024 * 1024        # tope de descarga (el .exe actual pesa ~25 MB)
TIMEOUT = 10
_lock = threading.Lock()


def register(app, srv):
    for n in _ENLACES:
        globals()[n] = getattr(srv, n)
    app.register_blueprint(bp)


# ------------------------------------------------------------------------------------------------ utilidades
def _abrir(url, timeout=TIMEOUT):
    """Único punto de red del módulo (las pruebas lo reemplazan)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"{ver.APP_NAME}/{ver.VERSION}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def tupla_version(texto):
    """'v0.8' → (0, 8); '1.10.2' → (1, 10, 2). Sin números → ()."""
    return tuple(int(x) for x in re.findall(r"\d+", str(texto or "")))


def es_mayor(nueva, actual):
    a, b = tupla_version(nueva), tupla_version(actual)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b)) if a else False


def _ruta_exe():
    return sys.executable


def puede_instalar():
    """Solo el ejecutable de Windows se puede reemplazar a sí mismo."""
    return bool(db.is_frozen() and sys.platform == "win32")


def _url_permitida(url):
    base = f"https://github.com/{ver.GITHUB_REPO}/releases/download/"
    return isinstance(url, str) and url.lower().startswith(base.lower())


def consultar():
    """Pregunta a GitHub por el último release publicado. Devuelve un diccionario con el resultado."""
    url = f"https://api.github.com/repos/{ver.GITHUB_REPO}/releases/latest"
    try:
        with _abrir(url) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:   # el repositorio aún no tiene ningún Release: no es un error
            return {"actual": ver.VERSION, "ultima": ver.VERSION, "tag": "", "hay_nueva": False, "sin_versiones": True,
                    "notas": "", "pagina": "", "publicada": "", "url_exe": "", "url_sha": "", "tamano": 0,
                    "puede_instalar": False, "instalable_en_este_modo": puede_instalar()}
        if e.code in (403, 429):
            raise ApiError("GitHub limitó las consultas; intente de nuevo en unos minutos", 503)
        raise ApiError(f"GitHub respondió con el error {e.code}", 502)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        raise ApiError("No se pudo consultar GitHub. Revise la conexión a Internet e intente de nuevo", 502)
    tag = str(rel.get("tag_name") or "")
    if not tupla_version(tag):
        raise ApiError("La última versión publicada tiene un número no válido", 502)
    activos = {a.get("name"): a for a in (rel.get("assets") or []) if isinstance(a, dict)}
    exe, sha = activos.get(EXE_NOMBRE), activos.get(SHA_NOMBRE)
    ultima = ".".join(str(x) for x in tupla_version(tag))
    return {
        "actual": ver.VERSION, "ultima": ultima, "tag": tag,
        "hay_nueva": es_mayor(tag, ver.VERSION),
        "notas": str(rel.get("body") or "")[:4000], "pagina": str(rel.get("html_url") or ""),
        "publicada": str(rel.get("published_at") or "")[:10],
        "url_exe": (exe or {}).get("browser_download_url", ""), "url_sha": (sha or {}).get("browser_download_url", ""),
        "tamano": int((exe or {}).get("size") or 0),
        "puede_instalar": puede_instalar() and bool(exe) and bool(sha),
        "instalable_en_este_modo": puede_instalar(),
    }


# ------------------------------------------------------------------------------------------------ descarga e instalación
def _leer_sha(url):
    with _abrir(url) as r:
        txt = r.read(4096).decode("utf-8", "replace")
    m = re.search(r"\b([0-9a-fA-F]{64})\b", txt)
    if not m:
        raise ApiError("La huella SHA-256 publicada no es válida; no se instala la actualización", 502)
    return m.group(1).lower()


def descargar(info, destino):
    """Baja el .exe a `destino`, verifica tamaño, firma «MZ» y SHA-256. Borra el archivo si algo falla."""
    if not (_url_permitida(info["url_exe"]) and _url_permitida(info["url_sha"])):
        raise ApiError("Dirección de descarga no permitida", 400)
    esperado = _leer_sha(info["url_sha"])
    h, total = hashlib.sha256(), 0
    try:
        with _abrir(info["url_exe"], timeout=30) as r, open(destino, "wb") as f:
            while True:
                bloque = r.read(1024 * 256)
                if not bloque:
                    break
                total += len(bloque)
                if total > MAX_EXE:
                    raise ApiError("El archivo de actualización es demasiado grande; se canceló", 400)
                h.update(bloque)
                f.write(bloque)
        if total < 1024 or h.hexdigest() != esperado:
            raise ApiError("La descarga está dañada (la huella SHA-256 no coincide); no se instaló nada", 502)
        with open(destino, "rb") as f:
            if f.read(2) != b"MZ":
                raise ApiError("El archivo descargado no es un ejecutable de Windows", 502)
    except ApiError:
        _borrar(destino)
        raise
    except (urllib.error.URLError, OSError, TimeoutError):
        _borrar(destino)
        raise ApiError("Se interrumpió la descarga. Revise la conexión e intente de nuevo", 502)


def _borrar(ruta):
    try:
        os.remove(ruta)
    except OSError:
        pass


def guion_reemplazo(exe, nuevo):
    """Texto del .bat que espera a que el .exe viejo se cierre, lo sustituye y lo vuelve a abrir."""
    return (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'set "VIEJO={exe}"\r\n'
        f'set "NUEVO={nuevo}"\r\n'
        "set /a n=0\r\n"
        ":intento\r\n"
        'move /y "%NUEVO%" "%VIEJO%" >nul 2>&1\r\n'
        "if not errorlevel 1 goto listo\r\n"
        "set /a n+=1\r\n"
        "if %n% geq 90 goto fallo\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto intento\r\n"
        ":listo\r\n"
        'start "" "%VIEJO%"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
        ":fallo\r\n"
        'del "%NUEVO%" >nul 2>&1\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
    )


def _lanzar_reemplazo(bat):  # pragma: no cover - solo Windows
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # el nuevo .exe debe extraerse por su cuenta, no reutilizar la carpeta del viejo
    flags = 0x08000000 | 0x00000008              # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(["cmd.exe", "/c", bat], env=env, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _salir():  # pragma: no cover
    os._exit(0)


def instalar(info):
    """Descarga, verifica, respalda y programa el reemplazo. La aplicación se cierra sola unos segundos después."""
    exe = _ruta_exe()
    carpeta = os.path.dirname(exe)
    if not os.access(carpeta, os.W_OK):
        raise ApiError(f"No hay permiso para escribir en «{carpeta}». Mueva la aplicación a una carpeta propia "
                       "(por ejemplo Documentos) o ejecútela como administrador", 403)
    nuevo = os.path.join(carpeta, "AntareXales_POS.nueva.exe")
    descargar(info, nuevo)
    try:
        respaldo.hacer_respaldo("manual")      # copia de la base de datos antes de cambiar de versión
    except Exception:
        log.exception("No se pudo respaldar antes de actualizar")
        _borrar(nuevo)
        raise ApiError("No se pudo respaldar la base de datos antes de actualizar; no se instaló nada", 500)
    bat = os.path.join(tempfile.gettempdir(), "antarexales_actualizar.bat")
    with open(bat, "w", encoding="utf-8", newline="") as f:
        f.write(guion_reemplazo(exe, nuevo))
    _lanzar_reemplazo(bat)
    threading.Timer(2.0, _salir).start()
    log.info("Actualización a %s programada; la aplicación se cerrará", info["ultima"])


def limpiar_restos():
    """Al arrancar: borra una descarga a medias que haya quedado junto al .exe."""
    if db.is_frozen():
        _borrar(os.path.join(os.path.dirname(_ruta_exe()), "AntareXales_POS.nueva.exe"))


# ------------------------------------------------------------------------------------------------ API (solo administrador)
def _guardar_revision(conn):
    conn.execute("INSERT OR REPLACE INTO config(clave, valor) VALUES('actualizacion_ultima_revision', ?)", (db.now_str(),))
    conn.commit()


@bp.get("/api/actualizacion")
def estado():
    cfg = get_config(get_db())
    return ok({"actual": ver.VERSION, "repo": ver.GITHUB_REPO, "al_abrir": cfg.get("actualizar_al_abrir", "1") == "1",
               "ultima_revision": cfg.get("actualizacion_ultima_revision", ""), "instalable_en_este_modo": puede_instalar()})


@bp.post("/api/actualizacion/buscar")
def buscar():
    info = consultar()
    _guardar_revision(get_db())
    return ok(info)


@bp.post("/api/actualizacion/instalar")
def instalar_ahora():
    if not puede_instalar():
        raise ApiError("La instalación automática solo está disponible en el ejecutable de Windows (.exe)", 400)
    if not _lock.acquire(blocking=False):
        raise ApiError("Ya hay una actualización en curso", 409)
    try:
        info = consultar()
        if not info["hay_nueva"]:
            raise ApiError("Ya tiene la última versión instalada", 409)
        if not info["puede_instalar"]:
            raise ApiError("El Release publicado no incluye el .exe y su huella SHA-256", 502)
        instalar(info)
        return ok({"version": info["ultima"], "mensaje": "Actualización descargada. La aplicación se reiniciará en unos segundos."})
    finally:
        _lock.release()

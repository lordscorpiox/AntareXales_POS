"""AntareXales POS - API REST (Flask). Todas las rutas /api/* responden {ok, data} o {ok, error}."""
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import database as db
import stock as stk
import version as ver
from errors import ApiError

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
app.json.ensure_ascii = False
app.json.sort_keys = False

FRONTEND_DIR = os.path.join(db.base_dir(), "frontend")
ASSETS_DIR = os.path.join(db.base_dir(), "assets")

SESSION_HOURS = 8
_sessions = {}
_sessions_lock = threading.Lock()
_restore_lock = threading.Lock()

METODOS = {
    "efectivo_bs": ("Efectivo Bs", "BS"),
    "efectivo_usd": ("Efectivo USD", "USD"),
    "transferencia": ("Transferencia", "BS"),
    "pago_movil": ("Pago móvil", "BS"),
    "zelle": ("Zelle", "USD"),
    "tarjeta": ("Tarjeta", "BS"),
    "biopago": ("Biopago", "BS"),
}
EFECTIVO = {"efectivo_bs", "efectivo_usd"}
TOLERANCIA_USD = 0.0051  # margen de redondeo a centavos (pagos en Bs, precios fijos en Bs)
MAX_NUM = 1_000_000_000  # tope de cualquier cantidad/monto numérico aceptado por la API
RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_USUARIO = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

log = logging.getLogger("antarexales")


def setup_logging():
    try:
        path = os.path.join(os.path.dirname(db.get_db_path()), "antarexales.log")
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    except OSError:
        pass  # sin carpeta de datos escribible la app sigue; solo no hay archivo de log


setup_logging()


# --------------------------------------------------------------------------- utilidades
# (ApiError vive en errors.py para compartirlo con los módulos de inventario, caja y compras)


def ok(data=None, status=200):
    return jsonify({"ok": True, "data": data}), status


def fail(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def get_db():
    if "db" not in g:
        g.db = db.get_conn()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def r2(x):
    return round(float(x) + 1e-9, 2)


def num(value, campo, minimo=None, default=None, estricto_positivo=False):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if default is not None:
            return default
        raise ApiError(f"{campo} es obligatorio")
    try:
        s = str(value).strip()
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        x = float(s)
    except (TypeError, ValueError):
        raise ApiError(f"{campo} no es un número válido")
    if not math.isfinite(x):
        raise ApiError(f"{campo} no es un número válido")
    if abs(x) > MAX_NUM:
        raise ApiError(f"{campo} es demasiado grande (máx. {MAX_NUM:,.0f})".replace(",", "."))
    if estricto_positivo and x <= 0:
        raise ApiError(f"{campo} debe ser mayor que cero")
    if minimo is not None and x < minimo:
        raise ApiError(f"{campo} no puede ser menor que {minimo:g}")
    return x


def texto(value, campo=None, obligatorio=False, maximo=200):
    s = "" if value is None else str(value).strip()
    if obligatorio and not s:
        raise ApiError(f"{campo} es obligatorio")
    if len(s) > maximo:
        raise ApiError(f"{campo or 'El texto'} es demasiado largo (máx. {maximo})")
    return s


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Se esperaba un cuerpo JSON")
    return data


def fmt_num(x):
    """Número como texto sin '.0' sobrante (30.0 -> '30', 36.5 -> '36.5')."""
    t = repr(float(x))
    return t[:-2] if t.endswith(".0") else t


def get_config(conn):
    cfg = {r["clave"]: r["valor"] for r in conn.execute("SELECT clave, valor FROM config")}
    cfg.pop("salt", None)
    cfg.pop("stock_almacen_ok", None)
    return cfg


def get_salt(conn):
    return conn.execute("SELECT valor FROM config WHERE clave='salt'").fetchone()[0]


def int_arg(nombre):
    valor = request.args.get(nombre)
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except ValueError:
        raise ApiError(f"Parámetro {nombre} inválido")


def validar_fecha(valor, campo):
    if valor in (None, ""):
        return None
    if not RE_FECHA.match(str(valor)):
        raise ApiError(f"{campo} debe tener formato AAAA-MM-DD")
    try:
        datetime.strptime(str(valor), "%Y-%m-%d")
    except ValueError:
        raise ApiError(f"{campo} no es una fecha real")
    return str(valor)


def fecha_hora(valor):
    """Acepta AAAA-MM-DD o AAAA-MM-DD HH:MM:SS; vacío = ahora."""
    if not valor:
        return db.now_str()
    s = str(valor).strip().replace("T", " ")
    for fmt, tail in (("%Y-%m-%d %H:%M:%S", ""), ("%Y-%m-%d %H:%M", ":00"), ("%Y-%m-%d", None)):
        try:
            d = datetime.strptime(s, fmt)
            if tail is None:
                d = d.replace(hour=datetime.now().hour, minute=datetime.now().minute, second=datetime.now().second)
            return d.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ApiError("Fecha inválida")


# --------------------------------------------------------------------------- sesiones
def crear_sesion(user):
    token = secrets.token_urlsafe(32)
    expira = time.time() + SESSION_HOURS * 3600
    with _sessions_lock:
        _sessions[token] = {
            "user_id": user["id"], "usuario": user["usuario"], "nombre": user["nombre"],
            "rol": user["rol"], "expira": expira, "debe_cambiar": bool(user["debe_cambiar"]),
        }
    return token, expira


def obtener_sesion(token):
    if not token:
        return None
    with _sessions_lock:
        ahora = time.time()
        for t in [t for t, s in _sessions.items() if s["expira"] < ahora]:
            del _sessions[t]
        return _sessions.get(token)


def cerrar_sesiones(user_id=None):
    with _sessions_lock:
        if user_id is None:
            _sessions.clear()
        else:
            for t in [t for t, s in _sessions.items() if s["user_id"] == user_id]:
                del _sessions[t]


# --- bloqueo por intentos fallidos (por nombre de usuario; en memoria)
MAX_FALLOS = 5
BLOQUEO_SEG = 300
_fallos = {}
_fallos_lock = threading.Lock()


def comprobar_bloqueo(usuario):
    k = (usuario or "").strip().lower()
    with _fallos_lock:
        f = _fallos.get(k)
        if f and f["hasta"] > time.time():
            falta = int((f["hasta"] - time.time()) // 60) + 1
            raise ApiError(f"Demasiados intentos fallidos. Espere {falta} min e intente de nuevo", 429)


def registrar_fallo(usuario):
    k = (usuario or "").strip().lower()
    with _fallos_lock:
        f = _fallos.setdefault(k, {"n": 0, "hasta": 0})
        if f["hasta"] and f["hasta"] <= time.time():
            f["n"], f["hasta"] = 0, 0
        f["n"] += 1
        if f["n"] >= MAX_FALLOS:
            f["hasta"] = time.time() + BLOQUEO_SEG
            f["n"] = 0
            log.warning("Usuario %s bloqueado %s s por intentos fallidos", k, BLOQUEO_SEG)


def limpiar_fallos(usuario):
    with _fallos_lock:
        _fallos.pop((usuario or "").strip().lower(), None)


def verificar_credenciales(conn, usuario, password):
    """Devuelve la fila del usuario activo si usuario/clave son correctos; si no, None. Cuenta los fallos."""
    comprobar_bloqueo(usuario)
    fila = conn.execute("SELECT * FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone()
    valido = db.verificar_password(fila["password_hash"] if fila else "", password, get_salt(conn))
    if not fila or not valido or not fila["activo"]:
        registrar_fallo(usuario)
        return None
    limpiar_fallos(usuario)
    if db.necesita_rehash(fila["password_hash"]):  # clave guardada con el formato antiguo: se actualiza al validar
        conn.execute("UPDATE usuarios SET password_hash=? WHERE id=?", (db.hash_password(password), fila["id"]))
        conn.commit()
    return fila


def autorizar_admin(conn, data):
    """Acciones sensibles del cajero: exigen usuario y clave de un administrador activo. Devuelve su nombre de usuario."""
    if es_admin():
        return g.user["usuario"]
    a = data.get("autoriza") if isinstance(data.get("autoriza"), dict) else {}
    usuario = str(a.get("usuario") or "").strip()
    if not usuario or not a.get("password"):
        raise ApiError("Esta acción requiere la clave de un administrador", 403)
    fila = verificar_credenciales(conn, usuario, str(a.get("password")))
    if not fila or fila["rol"] != "admin":
        time.sleep(0.4)
        raise ApiError("Usuario o clave de administrador incorrectos", 403)
    return fila["usuario"]


def tasa_vigente(conn, data, cfg=None):
    """La tasa BCV la fija el servidor (Configuración). Si el navegador envía otra, la pantalla está desactualizada."""
    cfg = cfg or get_config(conn)
    try:
        vigente = float(cfg.get("tasa_bcv") or 0)
    except ValueError:
        vigente = 0.0
    if vigente <= 0:
        raise ApiError("Configure la tasa BCV antes de vender")
    if data.get("tasa") not in (None, ""):
        enviada = num(data.get("tasa"), "La tasa BCV", estricto_positivo=True)
        if abs(enviada - vigente) > 1e-9:
            raise ApiError(f"La tasa BCV cambió (ahora es {fmt_num(vigente)}). La pantalla se actualizó: revise los totales e intente de nuevo", 409)
    return vigente


# Rutas que solo puede usar el administrador (inventario avanzado, compras, almacenes...)
SOLO_ADMIN = ("/api/usuarios", "/api/backup", "/api/actualizacion", "/api/almacenes", "/api/traslados", "/api/compras", "/api/proveedores",
              "/api/conteos", "/api/mermas", "/api/vencimientos", "/api/kardex", "/api/reportes")


def cajero_restringido(path, metodo):
    """El cajero vende, cobra y opera su caja; el resto (inventario, compras, reportes, usuarios...) es del administrador."""
    if path.startswith(SOLO_ADMIN) or path.endswith("/bulk") or path == "/api/productos/variantes":
        return True
    if path.startswith("/api/productos") and metodo != "GET":
        return True
    if re.match(r"^/api/productos/\d+/(stock|lotes|movimientos)$", path):
        return True
    if path.startswith("/api/categorias") and metodo != "GET":
        return True
    if re.match(r"^/api/clientes/\d+$", path) and metodo == "DELETE":
        return True
    return False


@app.before_request
def autenticar():
    path = request.path
    if not path.startswith("/api/") or request.method == "OPTIONS":
        return None
    if path in ("/api/auth/login", "/api/ping"):
        return None
    token = request.headers.get("X-Auth-Token") or request.args.get("token")
    sesion = obtener_sesion(token)
    if not sesion:
        return fail("Sesión no iniciada o expirada", 401)
    g.user = sesion
    g.token = token
    if sesion.get("debe_cambiar") and path not in ("/api/auth/cambiar-clave", "/api/auth/logout"):
        return fail("Debe cambiar su contraseña antes de continuar", 403)
    if sesion["rol"] != "admin":
        if cajero_restringido(path, request.method):
            return fail("Acceso restringido a administradores", 403)
        if path == "/api/config" and request.method == "PUT":
            data = request.get_json(silent=True) or {}
            if set(data.keys()) - {"tasa_bcv"}:
                return fail("Acceso restringido a administradores", 403)
            if get_config(get_db()).get("cajero_cambia_tasa") != "1":
                return fail("Solo el administrador puede cambiar la tasa BCV", 403)
    return None


@app.errorhandler(ApiError)
def manejar_api_error(e):
    return fail(e.message, e.status)


@app.errorhandler(HTTPException)
def manejar_http(e):
    if request.path.startswith("/api/"):
        mensajes = {404: "Recurso no encontrado", 405: "Método no permitido", 413: "Archivo demasiado grande"}
        return fail(mensajes.get(e.code, e.description), e.code)
    return e


@app.errorhandler(Exception)
def manejar_error(e):
    log.exception("Error no controlado en %s %s", request.method, request.path)
    return fail("Error interno del servidor. Revise antarexales.log", 500)


# --------------------------------------------------------------------------- frontend
@app.get("/")
def index():
    resp = send_from_directory(FRONTEND_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/assets/<path:nombre>")
def assets(nombre):
    return send_from_directory(ASSETS_DIR, nombre)


@app.get("/api/ping")
def ping():
    return ok({"app": ver.APP_NAME, "version": ver.VERSION, "autor": ver.AUTHOR, "creditos": ver.CREDITS,
        "powered_by": ver.POWERED_BY,
        "contacto": {"telegram": ver.CONTACT_TELEGRAM, "telegram_url": ver.CONTACT_TELEGRAM_URL,
                     "correo": ver.CONTACT_EMAIL, "correo_url": ver.CONTACT_EMAIL_URL}, "hora": db.now_str()})


# --------------------------------------------------------------------------- auth
@app.post("/api/auth/login")
def login():
    data = body()
    usuario = texto(data.get("usuario"), "Usuario", True, 64)
    password = str(data.get("password") or "")
    conn = get_db()
    fila = verificar_credenciales(conn, usuario, password)
    if not fila:
        time.sleep(0.4)
        raise ApiError("Usuario o contraseña incorrectos", 401)
    token, expira = crear_sesion(fila)
    return ok({
        "token": token,
        "expira": int(expira * 1000),
        "usuario": {"id": fila["id"], "usuario": fila["usuario"], "nombre": fila["nombre"], "rol": fila["rol"]},
        "debe_cambiar": bool(fila["debe_cambiar"]),
    })


@app.post("/api/auth/cambiar-clave")
def cambiar_clave():
    """Cada usuario cambia su propia contraseña (obligatorio para el administrador inicial con la clave por defecto)."""
    data = body()
    conn = get_db()
    actual, nueva = str(data.get("actual") or ""), str(data.get("nueva") or "")
    fila = verificar_credenciales(conn, g.user["usuario"], actual)
    if not fila:
        raise ApiError("La contraseña actual no es correcta", 403)
    if len(nueva) < 6:
        raise ApiError("La contraseña nueva debe tener al menos 6 caracteres")
    if nueva == actual or nueva.lower() == "admin123":
        raise ApiError("Elija una contraseña distinta a la actual y a la inicial")
    with conn:
        conn.execute("UPDATE usuarios SET password_hash=?, debe_cambiar=0 WHERE id=?", (db.hash_password(nueva), fila["id"]))
    with _sessions_lock:
        if g.token in _sessions:
            _sessions[g.token]["debe_cambiar"] = False
    for t in [t for t, se in list(_sessions.items()) if se["user_id"] == fila["id"] and t != g.token]:
        _sessions.pop(t, None)
    return ok(True)


@app.post("/api/auth/logout")
def logout():
    with _sessions_lock:
        _sessions.pop(g.token, None)
    return ok(True)


# --------------------------------------------------------------------------- configuración
@app.get("/api/config")
def config_get():
    return ok(get_config(get_db()))


@app.put("/api/config")
def config_put():
    data = body()
    conn = get_db()
    permitidas = set(db.DEFAULT_CONFIG.keys())
    desconocidas = set(data.keys()) - permitidas
    if desconocidas:
        raise ApiError("Campos de configuración no válidos: " + ", ".join(sorted(desconocidas)))
    nuevos = {}
    for k, v in data.items():
        if k == "tasa_bcv":
            nuevos[k] = fmt_num(num(v, "La tasa BCV", estricto_positivo=True))
        elif k == "permitir_stock_negativo":
            nuevos[k] = "1" if str(v).lower() in ("1", "true", "si", "sí") else "0"
        elif k == "tema":
            if str(v) not in ("oscuro", "claro"):
                raise ApiError("El tema debe ser 'oscuro' o 'claro'")
            nuevos[k] = str(v)
        elif k == "almacen_ventas":
            v = str(v or "").strip()
            if v:
                v = str(stk.almacen_valido(conn, v, "El almacén de ventas"))
            nuevos[k] = v
        elif k in ("caja_obligatoria", "cajero_cambia_tasa", "actualizar_al_abrir"):
            nuevos[k] = "1" if str(v).lower() in ("1", "true", "si", "sí") else "0"
        elif k == "respaldo_auto_dias":
            n = int(num(v, "Los respaldos automáticos a conservar", 0, default=14.0))
            if n > 365:
                raise ApiError("No se pueden conservar más de 365 respaldos automáticos")
            nuevos[k] = str(n)
        elif k == "dias_alerta_vencimiento":
            n = int(num(v, "Los días de alerta de vencimiento", 1, default=30.0))
            if n > 365:
                raise ApiError("Los días de alerta no pueden pasar de 365")
            nuevos[k] = str(n)
        elif k in ("iva_activo",):
            nuevos[k] = "1" if str(v).lower() in ("1", "true", "si", "sí") else "0"
        elif k == "iva_pct":
            p = num(v, "El % de IVA", 0, default=16.0)
            if p > 100:
                raise ApiError("El % de IVA debe estar entre 0 y 100")
            nuevos[k] = fmt_num(round(p, 2))
        elif k == "iva_modo":
            if str(v) not in ("incluido", "adicional"):
                raise ApiError("El modo de IVA debe ser 'incluido' o 'adicional'")
            nuevos[k] = str(v)
        elif k == "proforma_validez":
            n = int(num(v, "Los días de validez de la proforma", 1, default=7.0))
            if n > 365:
                raise ApiError("La validez de la proforma no puede pasar de 365 días")
            nuevos[k] = str(n)
        elif k in ("sig_factura", "sig_proforma", "sig_nota"):
            n = num(v, "El próximo número", 1, default=1.0)
            if n != int(n) or n > 99999999:
                raise ApiError("El próximo número debe ser un entero positivo")
            nuevos[k] = str(int(n))
        elif k == "doc_pie":
            nuevos[k] = texto(v, "El pie de los documentos", False, 300)
        elif k == "ganancia_default":
            nuevos[k] = fmt_num(round(num(v, "El % de ganancia por defecto", 0, default=0.0), 2))
        elif k == "logo":
            v = str(v or "")
            if v and not v.startswith("data:image/"):
                raise ApiError("El logo debe ser una imagen")
            if len(v) > 2_000_000:
                raise ApiError("El logo es demasiado grande (máx. ~1.5 MB)")
            nuevos[k] = v
        else:
            nuevos[k] = texto(v, k, False, 300)
    with conn:
        for k, v in nuevos.items():
            conn.execute("INSERT INTO config(clave, valor) VALUES(?,?) "
                         "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (k, v))
    return ok(get_config(conn))


# --------------------------------------------------------------------------- categorías
@app.get("/api/categorias")
def categorias_get():
    cur = get_db().execute(
        "SELECT c.id, c.nombre, (SELECT COUNT(*) FROM productos p WHERE p.categoria_id=c.id AND p.activo=1) AS productos "
        "FROM categorias c ORDER BY c.nombre COLLATE NOCASE")
    return ok(rows(cur))


@app.post("/api/categorias")
def categorias_post():
    nombre = texto(body().get("nombre"), "El nombre", True, 60)
    conn = get_db()
    try:
        with conn:
            cur = conn.execute("INSERT INTO categorias(nombre) VALUES(?)", (nombre,))
    except sqlite3.IntegrityError:
        raise ApiError("Ya existe una categoría con ese nombre")
    return ok({"id": cur.lastrowid, "nombre": nombre, "productos": 0}, 201)


@app.delete("/api/categorias/<int:cid>")
def categorias_delete(cid):
    conn = get_db()
    if not conn.execute("SELECT 1 FROM categorias WHERE id=?", (cid,)).fetchone():
        raise ApiError("Categoría no encontrada", 404)
    with conn:
        conn.execute("DELETE FROM categorias WHERE id=?", (cid,))
    return ok(True)


# --------------------------------------------------------------------------- productos
def producto_por_id(conn, pid):
    fila = conn.execute(
        "SELECT p.*, c.nombre AS categoria FROM productos p LEFT JOIN categorias c ON c.id=p.categoria_id "
        "WHERE p.id=?", (pid,)).fetchone()
    if not fila:
        raise ApiError("Producto no encontrado", 404)
    return dict(fila)


def categoria_valida(conn, valor):
    if valor in (None, "", 0, "0"):
        return None
    try:
        cid = int(valor)
    except (TypeError, ValueError):
        raise ApiError("Categoría inválida")
    if not conn.execute("SELECT 1 FROM categorias WHERE id=?", (cid,)).fetchone():
        raise ApiError("La categoría no existe")
    return cid


def es_admin():
    return g.user["rol"] == "admin"


def prod_out(d):
    """El costo y el % de ganancia solo los ve el administrador."""
    if d is not None and not es_admin():
        d.pop("costo_usd", None)
        d.pop("ganancia_pct", None)
    return d


def sin_costos(data):
    """Un cajero no puede fijar costo ni % de ganancia."""
    if es_admin():
        return data
    return {k: v for k, v in data.items() if k not in ("costo_usd", "ganancia_pct")}


def tiene_valor(data, clave):
    return clave in data and data[clave] is not None and str(data[clave]).strip() != ""


def calcular_precio(data, actual=None):
    """Devuelve (costo, ganancia_pct, precio_usd).

    - Si se indica precio_usd, ese es el precio (ajuste manual) y el % se recalcula si no coincide.
    - Si no, y se indicó costo y/o %, el precio es  costo * (1 + % / 100)  (ganancia sobre el costo).
    - Si no se indicó nada de eso, se conserva el precio actual.
    """
    base = actual or {}
    costo = round(num(data.get("costo_usd") if tiene_valor(data, "costo_usd") else base.get("costo_usd", 0),
                      "El costo", 0, default=0.0), 4)
    pct = round(num(data.get("ganancia_pct") if tiene_valor(data, "ganancia_pct") else base.get("ganancia_pct", 0),
                    "El % de ganancia", 0, default=0.0), 2)
    if tiene_valor(data, "precio_usd"):
        precio = r2(num(data.get("precio_usd"), "El precio", 0))
    elif tiene_valor(data, "costo_usd") or tiene_valor(data, "ganancia_pct") or not actual:
        precio = r2(costo * (1 + pct / 100)) if costo > 0 else r2(base.get("precio_usd", 0))
    else:
        precio = r2(base.get("precio_usd", 0))
    if costo > 0:
        esperado = r2(costo * (1 + pct / 100))
        if abs(precio - esperado) > 0.0051:  # precio ajustado a mano: el % refleja la ganancia real
            pct = round((precio / costo - 1) * 100, 2)
    return costo, pct, precio


UNIDADES = {"und": "und", "u": "und", "unidad": "und", "unidades": "und", "pieza": "und", "pza": "und",
            "kg": "kg", "kilo": "kg", "kilos": "kg", "kilogramo": "kg", "kilogramos": "kg",
            "l": "L", "lt": "L", "lts": "L", "litro": "L", "litros": "L"}


def flag(valor, defecto=0):
    """Interpreta casillas/CSV: 1/true/si/x = 1; 0/false/no = 0; vacío = valor por defecto."""
    if valor is None or str(valor).strip() == "":
        return int(defecto or 0)
    t = str(valor).strip().lower()
    if t in ("1", "true", "si", "sí", "s", "x", "yes", "y", "exento"):
        return 1
    if t in ("0", "false", "no", "n"):
        return 0
    raise ApiError(f"Valor no válido para una casilla: {valor}")


def parse_unidad(valor, defecto="und"):
    if valor is None or str(valor).strip() == "":
        return defecto
    u = UNIDADES.get(str(valor).strip().lower())
    if u is None:
        raise ApiError("Unidad inválida: use und, kg o L")
    return u


def calcular_producto(data, actual=None):
    """Datos de precio y unidad del producto.

    precio_moneda 'USD': el precio está en dólares y su valor en Bs sigue a la tasa (comportamiento normal).
    precio_moneda 'BS' : precio FIJO en bolívares; no cambia con la tasa (su equivalente en USD sí).
    """
    base = actual or {}
    unidad = parse_unidad(data.get("unidad") if tiene_valor(data, "unidad") else None, base.get("unidad", "und"))
    moneda = str(data.get("precio_moneda") if tiene_valor(data, "precio_moneda") else base.get("precio_moneda", "USD")).upper()
    if moneda not in ("USD", "BS"):
        raise ApiError("Tipo de precio inválido (use USD o BS)")
    if moneda == "BS":
        precio_bs = r2(num(data.get("precio_bs") if tiene_valor(data, "precio_bs") else base.get("precio_bs", 0),
                           "El precio fijo en Bs", 0, default=0.0))
        if precio_bs <= 0:
            raise ApiError("Indique el precio fijo en Bs (mayor que cero)")
        costo = round(num(data.get("costo_usd") if tiene_valor(data, "costo_usd") else base.get("costo_usd", 0),
                          "El costo", 0, default=0.0), 4)
        precio_usd = r2(num(data["precio_usd"], "El precio", 0)) if tiene_valor(data, "precio_usd") else r2(base.get("precio_usd", 0))
        pct = 0.0  # el % de ganancia no aplica: el equivalente en USD cambia con la tasa
    else:
        costo, pct, precio_usd = calcular_precio(data, actual)
        precio_bs = 0.0
    return {"unidad": unidad, "precio_moneda": moneda, "costo_usd": costo, "ganancia_pct": pct,
            "precio_usd": precio_usd, "precio_bs": precio_bs,
            "iva_exento": flag(data.get("iva_exento"), base.get("iva_exento", 0)) if "iva_exento" in data else int(base.get("iva_exento", 0) or 0)}


IMAGEN_MAX_BYTES = 600 * 1024
_IMG_TIPOS = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n", "image/webp": b"RIFF"}


def procesar_imagen(valor):
    """Valida una imagen recibida como data URL (la interfaz ya la reduce a ~320 px). Devuelve (mime, bytes)."""
    import base64
    import binascii
    m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$", str(valor), re.S)
    if not m:
        raise ApiError("La imagen debe ser JPG, PNG o WEBP")
    mime = m.group(1)
    try:
        datos = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise ApiError("La imagen está dañada")
    if len(datos) > IMAGEN_MAX_BYTES:
        raise ApiError(f"La imagen es demasiado grande (máx. {IMAGEN_MAX_BYTES // 1024} KB)")
    if not datos.startswith(_IMG_TIPOS[mime]) or (mime == "image/webp" and datos[8:12] != b"WEBP"):
        raise ApiError("El archivo no es una imagen válida")
    return mime, datos


def aplicar_imagen(conn, pid, data):
    """Guarda o quita la imagen del producto solo si la petición trae el campo «imagen» ('' = quitar)."""
    if "imagen" not in data:
        return
    valor = data.get("imagen")
    if valor is None or str(valor).strip() == "":
        conn.execute("DELETE FROM producto_imagenes WHERE producto_id=?", (pid,))
        conn.execute("UPDATE productos SET imagen_ver=0 WHERE id=?", (pid,))
        return
    mime, datos = procesar_imagen(valor)
    conn.execute("INSERT INTO producto_imagenes(producto_id, mime, datos) VALUES(?,?,?) "
                 "ON CONFLICT(producto_id) DO UPDATE SET mime=excluded.mime, datos=excluded.datos", (pid, mime, datos))
    conn.execute("UPDATE productos SET imagen_ver=imagen_ver+1 WHERE id=?", (pid,))


@app.get("/api/productos/<int:pid>/imagen")
def productos_imagen(pid):
    row = get_db().execute("SELECT mime, datos FROM producto_imagenes WHERE producto_id=?", (pid,)).fetchone()
    if not row:
        return fail("El producto no tiene imagen", 404)
    resp = Response(row["datos"], mimetype=row["mime"])
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # la URL lleva ?v=imagen_ver
    return resp


def extras_producto(data, actual=None):
    """Grupo y variante (talla/color) de un producto."""
    base = actual or {}
    return (texto(data.get("grupo", base.get("grupo", "")), "El grupo", False, 100),
            texto(data.get("variante", base.get("variante", "")), "La variante", False, 100))


@app.get("/api/productos")
def productos_get():
    conn = get_db()
    sql = ("SELECT p.*, c.nombre AS categoria FROM productos p "
           "LEFT JOIN categorias c ON c.id=p.categoria_id WHERE p.activo=1")
    params = []
    q = (request.args.get("q") or "").strip()
    if q:
        sql += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR p.variante LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    cat = int_arg("categoria_id")
    if cat:
        sql += " AND p.categoria_id = ?"
        params.append(cat)
    if request.args.get("bajo_stock") in ("1", "true"):
        sql += " AND p.tipo='simple' AND p.stock <= p.stock_minimo"
    sql += " ORDER BY p.nombre COLLATE NOCASE"
    lista = stk.enriquecer(conn, rows(conn.execute(sql, params)))
    return ok([prod_out(r) for r in lista])


@app.get("/api/productos/<int:pid>")
def productos_uno(pid):
    conn = get_db()
    return ok(prod_out(stk.enriquecer(conn, [producto_por_id(conn, pid)])[0]))


def ficha_producto(conn, pid):
    return prod_out(stk.enriquecer(conn, [producto_por_id(conn, pid)])[0])


def tipo_producto(data):
    tipo = str(data.get("tipo") or "simple").lower()
    if tipo not in ("simple", "combo"):
        raise ApiError("El tipo de producto debe ser 'simple' o 'combo'")
    if tipo == "combo" and not es_admin():
        raise ApiError("Solo el administrador puede crear o editar combos", 403)
    return tipo


@app.post("/api/productos")
def productos_post():
    data = sin_costos(body())
    conn = get_db()
    tipo = tipo_producto(data)
    nombre = texto(data.get("nombre"), "El nombre", True, 150)
    codigo = texto(data.get("codigo"), "El código", False, 60) or None
    pr = calcular_producto(data)
    stock = 0.0 if tipo == "combo" else num(data.get("stock"), "El stock", 0, default=0.0)
    minimo = 0.0 if tipo == "combo" else num(data.get("stock_minimo"), "El stock mínimo", 0, default=0.0)
    if tipo == "combo":
        pr["unidad"] = "und"
    grupo, variante = extras_producto(data)
    cat = categoria_valida(conn, data.get("categoria_id"))
    vence = validar_fecha(data.get("vence"), "El vencimiento") if tiene_valor(data, "vence") else ""
    lote = texto(data.get("lote"), "El lote", False, 60)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO productos(codigo, nombre, categoria_id, unidad, costo_usd, ganancia_pct, precio_moneda, precio_usd, "
                "precio_bs, iva_exento, stock, stock_minimo, tipo, grupo, variante, creado) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                (codigo, nombre, cat, pr["unidad"], pr["costo_usd"], pr["ganancia_pct"], pr["precio_moneda"], pr["precio_usd"],
                 pr["precio_bs"], pr["iva_exento"], minimo, tipo, grupo, variante, db.now_str()))
            pid = cur.lastrowid
            if tipo == "combo":
                stk.guardar_componentes(conn, pid, data.get("componentes"))
                pr2 = calcular_producto({**data, "costo_usd": stk.costo_combo(conn, pid)})
                conn.execute("UPDATE productos SET costo_usd=?, ganancia_pct=?, precio_usd=? WHERE id=?",
                             (pr2["costo_usd"], pr2["ganancia_pct"], pr2["precio_usd"], pid))
            elif stock:
                stk.ingresar(conn, pid, stk.almacen_principal(conn), stock, "inicial", "Stock inicial",
                             g.user["usuario"], lote, vence)
            aplicar_imagen(conn, pid, data)
    except sqlite3.IntegrityError:
        raise ApiError("Ya existe un producto con ese código")
    return ok(ficha_producto(conn, pid), 201)


@app.put("/api/productos/<int:pid>")
def productos_put(pid):
    data = sin_costos(body())
    conn = get_db()
    actual = producto_por_id(conn, pid)
    es_combo = actual["tipo"] == "combo"
    if es_combo and not es_admin():
        raise ApiError("Solo el administrador puede editar combos", 403)
    nombre = texto(data.get("nombre", actual["nombre"]), "El nombre", True, 150)
    codigo = texto(data.get("codigo", actual["codigo"]), "El código", False, 60) or None
    if es_combo:
        actual["costo_usd"] = stk.costo_combo(conn, pid)
        if "componentes" in data:
            data = {k: v for k, v in data.items() if k != "costo_usd"}
    pr = calcular_producto(data, actual)
    if es_combo:
        pr["unidad"] = "und"
    minimo = 0.0 if es_combo else num(data.get("stock_minimo", actual["stock_minimo"]), "El stock mínimo", 0)
    stock = actual["stock"] if es_combo else num(data.get("stock", actual["stock"]), "El stock", 0)
    grupo, variante = extras_producto(data, actual)
    cat = categoria_valida(conn, data.get("categoria_id", actual["categoria_id"]))
    try:
        with conn:
            conn.execute(
                "UPDATE productos SET codigo=?, nombre=?, categoria_id=?, unidad=?, costo_usd=?, ganancia_pct=?, precio_moneda=?, "
                "precio_usd=?, precio_bs=?, iva_exento=?, stock_minimo=?, grupo=?, variante=? WHERE id=?",
                (codigo, nombre, cat, pr["unidad"], pr["costo_usd"], pr["ganancia_pct"], pr["precio_moneda"], pr["precio_usd"],
                 pr["precio_bs"], pr["iva_exento"], minimo, grupo, variante, pid))
            if es_combo:
                if "componentes" in data:
                    stk.guardar_componentes(conn, pid, data["componentes"])
                    pr2 = calcular_producto({**data, "costo_usd": stk.costo_combo(conn, pid)}, actual)
                    conn.execute("UPDATE productos SET costo_usd=?, ganancia_pct=?, precio_usd=? WHERE id=?",
                                 (pr2["costo_usd"], pr2["ganancia_pct"], pr2["precio_usd"], pid))
            else:
                stk.ajustar_total(conn, pid, stock, "Edición del producto", g.user["usuario"])
            aplicar_imagen(conn, pid, data)
    except sqlite3.IntegrityError:
        raise ApiError("Ya existe un producto con ese código")
    return ok(ficha_producto(conn, pid))


@app.delete("/api/productos/<int:pid>")
def productos_delete(pid):
    conn = get_db()
    producto_por_id(conn, pid)
    en_combo = conn.execute(
        "SELECT c.nombre FROM combo_items ci JOIN productos c ON c.id=ci.combo_id "
        "WHERE ci.componente_id=? AND c.activo=1 LIMIT 1", (pid,)).fetchone()
    if en_combo:
        raise ApiError(f"No se puede eliminar: forma parte del combo «{en_combo['nombre']}»")
    with conn:
        usado = (conn.execute("SELECT 1 FROM venta_items WHERE producto_id=? LIMIT 1", (pid,)).fetchone()
                 or conn.execute("SELECT 1 FROM compra_items WHERE producto_id=? LIMIT 1", (pid,)).fetchone())
        if usado:
            conn.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))  # conserva el historial
        else:
            conn.execute("DELETE FROM producto_imagenes WHERE producto_id=?", (pid,))
            conn.execute("DELETE FROM lotes WHERE producto_id=?", (pid,))
            conn.execute("DELETE FROM stock_almacen WHERE producto_id=?", (pid,))
            conn.execute("DELETE FROM combo_items WHERE combo_id=?", (pid,))
            conn.execute("DELETE FROM conteo_items WHERE producto_id=?", (pid,))
            conn.execute("DELETE FROM movimientos_stock WHERE producto_id=?", (pid,))
            conn.execute("DELETE FROM productos WHERE id=?", (pid,))
    return ok(True)


@app.patch("/api/productos/<int:pid>/stock")
def productos_stock(pid):
    """Entrada o salida manual de stock (con motivo). Opcional: almacen_id, lote, vence (entradas)."""
    data = body()
    conn = get_db()
    actual = producto_por_id(conn, pid)
    if actual["tipo"] == "combo":
        raise ApiError("Un combo no tiene stock propio: ajuste sus componentes")
    tipo = str(data.get("tipo") or "").lower()
    if tipo not in ("entrada", "salida"):
        raise ApiError("El tipo debe ser 'entrada' o 'salida'")
    cantidad = num(data.get("cantidad"), "La cantidad", estricto_positivo=True)
    motivo = texto(data.get("motivo"), "El motivo", True, 200)
    if actual["unidad"] == "und" and abs(cantidad - round(cantidad)) > 1e-9:
        raise ApiError("Este producto se maneja por unidad: la cantidad debe ser entera")
    aid = stk.almacen_valido(conn, data["almacen_id"]) if tiene_valor(data, "almacen_id") else stk.almacen_principal(conn)
    usuario = g.user["usuario"]
    with conn:
        if tipo == "entrada":
            vence = validar_fecha(data.get("vence"), "El vencimiento") if tiene_valor(data, "vence") else ""
            stk.ingresar(conn, pid, aid, cantidad, "entrada", motivo, usuario, texto(data.get("lote"), "El lote", False, 60), vence)
        else:
            lote_id = int(data["lote_id"]) if tiene_valor(data, "lote_id") else None
            if not tiene_valor(data, "almacen_id"):
                # sin almacén indicado: se descuenta del total (principal primero)
                if actual["stock"] + 1e-9 < cantidad:
                    raise ApiError(f"No hay suficiente stock para la salida (disponible: {actual['stock']:g})")
                stk.quitar_total(conn, pid, cantidad, "salida", motivo, usuario)
            else:
                stk.consumir(conn, pid, aid, cantidad, "salida", motivo, usuario, lote_id=lote_id, nombre=actual["nombre"])
    return ok(ficha_producto(conn, pid))


@app.get("/api/productos/<int:pid>/stock")
def productos_stock_detalle(pid):
    """Existencias por almacén y lotes del producto (admin)."""
    conn = get_db()
    p = producto_por_id(conn, pid)
    alm = rows(conn.execute(
        "SELECT a.id AS almacen_id, a.nombre, a.activo, COALESCE(s.cantidad,0) AS cantidad FROM almacenes a "
        "LEFT JOIN stock_almacen s ON s.almacen_id=a.id AND s.producto_id=? ORDER BY a.principal DESC, a.nombre", (pid,)))
    lotes = rows(conn.execute(
        "SELECT l.*, a.nombre AS almacen FROM lotes l JOIN almacenes a ON a.id=l.almacen_id "
        "WHERE l.producto_id=? AND l.cantidad>0.00005 ORDER BY (l.vence=''), l.vence, l.id", (pid,)))
    for l in lotes:
        l["dias"] = stk.dias_hasta(l["vence"]) if l["vence"] else None
    return ok({"producto_id": pid, "nombre": p["nombre"], "almacenes": alm, "lotes": lotes})


@app.get("/api/productos/<int:pid>/movimientos")
def productos_movimientos(pid):
    conn = get_db()
    producto_por_id(conn, pid)
    cur = conn.execute(
        "SELECT m.*, a.nombre AS almacen FROM movimientos_stock m LEFT JOIN almacenes a ON a.id=m.almacen_id "
        "WHERE m.producto_id=? ORDER BY m.id DESC LIMIT 200", (pid,))
    return ok(rows(cur))


@app.post("/api/productos/bulk")
def productos_bulk():
    """Carga masiva (admin). Crea productos nuevos y actualiza los existentes por código.

    En una actualización, las columnas ausentes o vacías conservan el valor actual del producto.
    Si hay costo pero no precio, el precio se calcula con el % de ganancia de la fila (o el % por defecto).
    """
    data = body()
    filas = data.get("rows")
    if not isinstance(filas, list) or not filas:
        raise ApiError("No hay filas para importar")
    if len(filas) > 20000:
        raise ApiError("Demasiadas filas (máx. 20000)")
    conn = get_db()
    try:
        pct_default = float(get_config(conn).get("ganancia_default") or 0)
    except ValueError:
        pct_default = 0.0
    categorias = {r["nombre"].lower(): r["id"] for r in conn.execute("SELECT id, nombre FROM categorias")}
    creados = actualizados = 0
    errores = []
    with conn:
        for i, f in enumerate(filas, start=2):  # fila 1 = encabezado del CSV
            try:
                if not isinstance(f, dict):
                    raise ApiError("Fila inválida")
                nombre = texto(f.get("nombre"), "nombre", True, 150)
                codigo = texto(f.get("codigo"), "codigo", False, 60) or None
                existente = None
                if codigo:
                    existente = conn.execute("SELECT * FROM productos WHERE codigo=? AND activo=1", (codigo,)).fetchone()
                if existente and existente["tipo"] == "combo":
                    raise ApiError("El código pertenece a un combo (no se actualiza por CSV)")
                base = dict(existente) if existente else {}
                minimo = num(f.get("stock_minimo") if tiene_valor(f, "stock_minimo") else base.get("stock_minimo", 0),
                             "stock_minimo", 0)
                stock = num(f.get("stock") if tiene_valor(f, "stock") else base.get("stock", 0), "stock", 0)
                fila_precio = {k: f[k] for k in ("costo_usd", "ganancia_pct", "precio_usd", "unidad") if tiene_valor(f, k)}
                if tiene_valor(f, "exento_iva"):
                    fila_precio["iva_exento"] = f["exento_iva"]
                if tiene_valor(f, "precio_fijo_bs") and num(f["precio_fijo_bs"], "precio_fijo_bs", 0) > 0:
                    fila_precio["precio_moneda"] = "BS"
                    fila_precio["precio_bs"] = f["precio_fijo_bs"]
                elif "precio_usd" in fila_precio and base.get("precio_moneda") == "BS":
                    fila_precio["precio_moneda"] = "USD"  # la fila trae precio en USD: deja de ser precio fijo en Bs
                costo_nuevo = num(fila_precio["costo_usd"], "costo_usd", 0) if "costo_usd" in fila_precio else None
                if ("precio_usd" not in fila_precio and "ganancia_pct" not in fila_precio and costo_nuevo and costo_nuevo > 0
                        and not (base.get("ganancia_pct") or 0) > 0):
                    fila_precio["ganancia_pct"] = pct_default
                pr = calcular_producto(fila_precio, base or None)
                cat_nombre = texto(f.get("categoria"), "categoria", False, 60)
                cat_id = base.get("categoria_id")
                if cat_nombre:
                    cat_id = categorias.get(cat_nombre.lower())
                    if cat_id is None:
                        cur = conn.execute("INSERT INTO categorias(nombre) VALUES(?)", (cat_nombre,))
                        cat_id = categorias[cat_nombre.lower()] = cur.lastrowid
                if existente:
                    conn.execute(
                        "UPDATE productos SET nombre=?, categoria_id=?, unidad=?, costo_usd=?, ganancia_pct=?, precio_moneda=?, "
                        "precio_usd=?, precio_bs=?, iva_exento=?, stock_minimo=? WHERE id=?",
                        (nombre, cat_id, pr["unidad"], pr["costo_usd"], pr["ganancia_pct"], pr["precio_moneda"], pr["precio_usd"],
                         pr["precio_bs"], pr["iva_exento"], minimo, existente["id"]))
                    stk.ajustar_total(conn, existente["id"], stock, "Carga masiva CSV", g.user["usuario"])
                    actualizados += 1
                else:
                    cur = conn.execute(
                        "INSERT INTO productos(codigo, nombre, categoria_id, unidad, costo_usd, ganancia_pct, precio_moneda, "
                        "precio_usd, precio_bs, iva_exento, stock, stock_minimo, creado) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)",
                        (codigo, nombre, cat_id, pr["unidad"], pr["costo_usd"], pr["ganancia_pct"], pr["precio_moneda"],
                         pr["precio_usd"], pr["precio_bs"], pr["iva_exento"], minimo, db.now_str()))
                    if stock:
                        stk.ingresar(conn, cur.lastrowid, stk.almacen_principal(conn), stock, "inicial",
                                     "Carga masiva CSV", g.user["usuario"])
                    creados += 1
            except ApiError as e:
                errores.append({"fila": i, "error": e.message})
            except sqlite3.IntegrityError:
                errores.append({"fila": i, "error": "Código duplicado dentro del archivo"})
    return ok({"creados": creados, "actualizados": actualizados, "errores": errores})


# --------------------------------------------------------------------------- clientes
SALDO_SQL = ("(COALESCE((SELECT SUM(v.credito_usd) FROM ventas v WHERE v.cliente_id=c.id AND v.estado='completada'),0)"
             " - COALESCE((SELECT SUM(a.monto_usd) FROM abonos a WHERE a.cliente_id=c.id),0))")


def cliente_por_id(conn, cid):
    fila = conn.execute(f"SELECT c.*, {SALDO_SQL} AS saldo_usd FROM clientes c WHERE c.id=? AND c.activo=1", (cid,)).fetchone()
    if not fila:
        raise ApiError("Cliente no encontrado", 404)
    d = dict(fila)
    d["saldo_usd"] = r2(d["saldo_usd"])
    return d


@app.get("/api/clientes")
def clientes_get():
    conn = get_db()
    cur = conn.execute(f"SELECT c.*, {SALDO_SQL} AS saldo_usd FROM clientes c WHERE c.activo=1 ORDER BY c.nombre COLLATE NOCASE")
    out = rows(cur)
    for c in out:
        c["saldo_usd"] = r2(c["saldo_usd"])
    return ok(out)


@app.get("/api/clientes/<int:cid>")
def clientes_detalle(cid):
    conn = get_db()
    cli = cliente_por_id(conn, cid)
    cli["abonos"] = rows(conn.execute("SELECT * FROM abonos WHERE cliente_id=? ORDER BY fecha DESC, id DESC", (cid,)))
    cli["ventas"] = rows(conn.execute(
        "SELECT id, fecha, total_usd, total_bs, credito_usd, estado FROM ventas WHERE cliente_id=? ORDER BY id DESC", (cid,)))
    return ok(cli)


@app.post("/api/clientes")
def clientes_post():
    data = body()
    conn = get_db()
    nombre = texto(data.get("nombre"), "El nombre", True, 120)
    with conn:
        cur = conn.execute("INSERT INTO clientes(nombre, telefono, cedula, direccion, creado) VALUES(?,?,?,?,?)",
                           (nombre, texto(data.get("telefono"), "El teléfono", False, 40),
                            texto(data.get("cedula"), "La cédula / RIF", False, 30),
                            texto(data.get("direccion"), "La dirección", False, 200), db.now_str()))
    return ok(cliente_por_id(conn, cur.lastrowid), 201)


@app.put("/api/clientes/<int:cid>")
def clientes_put(cid):
    data = body()
    conn = get_db()
    actual = cliente_por_id(conn, cid)
    with conn:
        conn.execute("UPDATE clientes SET nombre=?, telefono=?, cedula=?, direccion=? WHERE id=?", (
            texto(data.get("nombre", actual["nombre"]), "El nombre", True, 120),
            texto(data.get("telefono", actual["telefono"]), "El teléfono", False, 40),
            texto(data.get("cedula", actual["cedula"]), "La cédula / RIF", False, 30),
            texto(data.get("direccion", actual.get("direccion") or ""), "La dirección", False, 200), cid))
    return ok(cliente_por_id(conn, cid))


@app.delete("/api/clientes/<int:cid>")
def clientes_delete(cid):
    conn = get_db()
    cli = cliente_por_id(conn, cid)
    if cli["saldo_usd"] > TOLERANCIA_USD:
        raise ApiError(f"El cliente tiene un saldo pendiente de ${cli['saldo_usd']:.2f}; registre los abonos antes de eliminarlo")
    with conn:
        if conn.execute("SELECT 1 FROM ventas WHERE cliente_id=? LIMIT 1", (cid,)).fetchone():
            conn.execute("UPDATE clientes SET activo=0 WHERE id=?", (cid,))  # conserva el historial
        else:
            conn.execute("DELETE FROM clientes WHERE id=?", (cid,))
    return ok(True)


@app.post("/api/clientes/bulk")
def clientes_bulk():
    data = body()
    filas = data.get("rows")
    if not isinstance(filas, list) or not filas:
        raise ApiError("No hay filas para importar")
    conn = get_db()
    creados = actualizados = 0
    errores = []
    with conn:
        for i, f in enumerate(filas, start=2):
            try:
                if not isinstance(f, dict):
                    raise ApiError("Fila inválida")
                nombre = texto(f.get("nombre"), "nombre", True, 120)
                tel = texto(f.get("telefono"), "telefono", False, 40)
                ced = texto(f.get("cedula"), "cedula", False, 30)
                ex = conn.execute("SELECT id FROM clientes WHERE cedula=? AND activo=1", (ced,)).fetchone() if ced else None
                if ex:
                    conn.execute("UPDATE clientes SET nombre=?, telefono=? WHERE id=?", (nombre, tel, ex["id"]))
                    actualizados += 1
                else:
                    conn.execute("INSERT INTO clientes(nombre, telefono, cedula, creado) VALUES(?,?,?,?)",
                                 (nombre, tel, ced, db.now_str()))
                    creados += 1
            except ApiError as e:
                errores.append({"fila": i, "error": e.message})
    return ok({"creados": creados, "actualizados": actualizados, "errores": errores})


@app.post("/api/clientes/<int:cid>/abonos")
def abonos_post(cid):
    data = body()
    conn = get_db()
    cli = cliente_por_id(conn, cid)
    moneda = str(data.get("moneda") or "USD").upper()
    if moneda not in ("USD", "BS"):
        raise ApiError("La moneda del abono debe ser USD o BS")
    if moneda == "BS":
        # El cliente pagó en bolívares: la deuda (en USD) baja por monto_bs / tasa del día del pago.
        monto_bs = r2(num(data.get("monto_bs"), "El monto en Bs", estricto_positivo=True))
        tasa_abono = num(data.get("tasa"), "La tasa del día", estricto_positivo=True)
        monto = r2(monto_bs / tasa_abono)
        if monto <= 0:
            raise ApiError("El monto en Bs es muy pequeño para esa tasa (equivale a $0,00)")
    else:
        monto = r2(num(data.get("monto_usd"), "El monto", estricto_positivo=True))
        monto_bs = tasa_abono = 0.0
    if monto > cli["saldo_usd"] + TOLERANCIA_USD:
        raise ApiError(f"El abono (${monto:.2f}) excede el saldo deudor (${max(cli['saldo_usd'], 0):.2f})")
    turno_id = exigir_turno(conn, get_config(conn))
    with conn:
        conn.execute("INSERT INTO abonos(cliente_id, fecha, monto_usd, moneda, monto_bs, tasa, metodo, referencia, turno_id) "
                     "VALUES(?,?,?,?,?,?,?,?,?)",
                     (cid, fecha_hora(data.get("fecha")), monto, moneda, monto_bs, tasa_abono,
                      texto(data.get("metodo"), "El método", False, 40),
                      texto(data.get("referencia"), "La referencia", False, 80), turno_id))
    return ok(cliente_por_id(conn, cid), 201)


@app.delete("/api/clientes/<int:cid>/abonos/<int:aid>")
def abonos_delete(cid, aid):
    conn = get_db()
    autorizar_admin(conn, request.get_json(silent=True) or {})
    cliente_por_id(conn, cid)
    with conn:
        cur = conn.execute("DELETE FROM abonos WHERE id=? AND cliente_id=?", (aid, cid))
    if cur.rowcount == 0:
        raise ApiError("Abono no encontrado", 404)
    return ok(cliente_por_id(conn, cid))


@app.delete("/api/clientes/<int:cid>/abonos")
def abonos_delete_all(cid):
    conn = get_db()
    autorizar_admin(conn, request.get_json(silent=True) or {})
    cliente_por_id(conn, cid)
    with conn:
        conn.execute("DELETE FROM abonos WHERE cliente_id=?", (cid,))
    return ok(cliente_por_id(conn, cid))


# --------------------------------------------------------------------------- ventas
def fetch_venta(conn, vid):
    v = conn.execute("SELECT * FROM ventas WHERE id=?", (vid,)).fetchone()
    if not v:
        raise ApiError("Venta no encontrada", 404)
    d = dict(v)
    t = conn.execute("SELECT estado FROM turnos WHERE id=?", (v["turno_id"],)).fetchone() if v["turno_id"] else None
    d["turno_estado"] = t["estado"] if t else ""
    d["items"] = rows(conn.execute("SELECT * FROM venta_items WHERE venta_id=? ORDER BY id", (vid,)))
    if not es_admin():
        for it in d["items"]:
            it.pop("costo_usd", None)
    d["pagos"] = rows(conn.execute("SELECT * FROM venta_pagos WHERE venta_id=? ORDER BY id", (vid,)))
    for p in d["pagos"]:
        p["metodo_nombre"] = METODOS.get(p["metodo"], (p["metodo"], ""))[0]
    return d


@app.get("/api/ventas")
def ventas_get():
    conn = get_db()
    sql = ("SELECT v.id, v.fecha, v.cajero_id, v.cajero_nombre, v.cliente_id, v.cliente_nombre, v.tasa, v.total_usd, "
           "v.total_bs, v.pagado_usd, v.credito_usd, v.vuelto_usd, v.vuelto_bs, v.estado, v.turno_id, "
           "(SELECT GROUP_CONCAT(DISTINCT p.metodo) FROM venta_pagos p WHERE p.venta_id=v.id) AS metodos "
           "FROM ventas v WHERE 1=1")
    params = []
    if not es_admin():  # el cajero solo ve sus propias ventas
        sql += " AND v.cajero_id = ?"
        params.append(g.user["user_id"])
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    if desde:
        sql += " AND date(v.fecha) >= ?"
        params.append(desde)
    if hasta:
        sql += " AND date(v.fecha) <= ?"
        params.append(hasta)
    for campo, col in (("cajero_id", "v.cajero_id"), ("cliente_id", "v.cliente_id")):
        valor = int_arg(campo)
        if valor:
            sql += f" AND {col} = ?"
            params.append(valor)
    estado = request.args.get("estado")
    if estado in ("completada", "anulada"):
        sql += " AND v.estado = ?"
        params.append(estado)
    try:
        limite = min(int(request.args.get("limit", 2000)), 20000)
    except ValueError:
        limite = 2000
    sql += " ORDER BY v.id DESC LIMIT ?"
    params.append(limite)
    return ok(rows(conn.execute(sql, params)))


@app.get("/api/ventas/<int:vid>")
def ventas_detalle(vid):
    v = fetch_venta(get_db(), vid)
    if not es_admin() and v["cajero_id"] != g.user["user_id"]:
        raise ApiError("Esa venta pertenece a otro cajero", 403)
    return ok(v)


def iva_config(cfg):
    """(porcentaje, modo) vigentes. Con el IVA desactivado devuelve (0.0, '')."""
    if cfg.get("iva_activo") != "1":
        return 0.0, ""
    try:
        pct = float(cfg.get("iva_pct") or 0)
    except ValueError:
        pct = 0.0
    if pct <= 0:
        return 0.0, ""
    modo = cfg.get("iva_modo") if cfg.get("iva_modo") in ("incluido", "adicional") else "incluido"
    return pct, modo


def calcular_linea(p, cant, tasa, pct, modo):
    """Importes de una línea de venta, en USD y en Bs, con su desglose de IVA.

    «sub» es el importe cotizado (cantidad × precio); en modo 'incluido' ya trae el IVA y en 'adicional' no.
    total = lo que paga el cliente por la línea; base = total − IVA. Cada moneda se redondea a centavos por línea
    (los productos con precio fijo en Bs se calculan en Bs, los demás en USD)."""
    fijo_bs = p["precio_moneda"] == "BS" and p["precio_bs"] > 0
    gravado = pct > 0 and not p["iva_exento"]
    adicional = gravado and modo == "adicional"

    def iva_de(imp):
        if not gravado:
            return 0.0
        return r2(imp * pct / 100) if adicional else r2(imp - r2(imp / (1 + pct / 100)))

    if fijo_bs:
        imp_bs = r2(cant * p["precio_bs"])
        iva_bs = iva_de(imp_bs)
        total_bs = r2(imp_bs + iva_bs) if adicional else imp_bs
        total_usd = r2(total_bs / tasa)
        iva_usd = r2(iva_bs / tasa)
        sub, sub_bs, precio_usd_linea = r2(imp_bs / tasa), imp_bs, round(p["precio_bs"] / tasa, 6)
    else:
        sub = r2(cant * p["precio_usd"])
        iva_usd = iva_de(sub)
        total_usd = r2(sub + iva_usd) if adicional else sub
        iva_bs = r2(iva_usd * tasa)
        total_bs = r2(total_usd * tasa)
        sub_bs, precio_usd_linea = 0.0, p["precio_usd"]
    return {"p": p, "cant": cant, "sub": sub, "sub_bs": sub_bs, "precio_usd_linea": precio_usd_linea,
            "exento": 1 if (pct > 0 and p["iva_exento"]) else 0, "iva_pct": pct if gravado else 0.0,
            "total_usd": total_usd, "total_bs": total_bs, "iva_usd": iva_usd, "iva_bs": iva_bs,
            "base_usd": r2(total_usd - iva_usd), "base_bs": r2(total_bs - iva_bs)}


def totales_fiscales(lineas, pct, modo):
    """Totales de una venta/documento a partir de sus líneas ya calculadas."""
    t = {"iva_pct": pct, "iva_modo": modo,
         "total_usd": r2(sum(l["total_usd"] for l in lineas)), "total_bs": r2(sum(l["total_bs"] for l in lineas)),
         "iva_usd": r2(sum(l["iva_usd"] for l in lineas)), "iva_bs": r2(sum(l["iva_bs"] for l in lineas)),
         "exento_usd": r2(sum(l["total_usd"] for l in lineas if l["exento"])),
         "exento_bs": r2(sum(l["total_bs"] for l in lineas if l["exento"]))}
    t["base_usd"] = r2(t["total_usd"] - t["iva_usd"] - t["exento_usd"])
    t["base_bs"] = r2(t["total_bs"] - t["iva_bs"] - t["exento_bs"])
    return t


def calcular_lineas(conn, cantidades, tasa, cfg, validar_stock=False):
    """Valida los productos del carrito y calcula sus líneas (con IVA si está activo)."""
    pct, modo = iva_config(cfg)
    permitir_negativo = cfg.get("permitir_stock_negativo") == "1"
    lineas = []
    for pid, cant in cantidades.items():
        p = conn.execute("SELECT * FROM productos WHERE id=? AND activo=1", (pid,)).fetchone()
        if not p:
            raise ApiError("Un producto del carrito ya no existe")
        p = dict(p)
        if p["unidad"] == "und" and abs(cant - round(cant)) > 1e-9:
            raise ApiError(f"«{p['nombre']}» se vende por unidad: la cantidad debe ser entera "
                           f"(para vender fracciones, cambie su unidad a kg o L)")
        if p["tipo"] == "combo":
            p["costo_usd"] = stk.costo_combo(conn, pid)  # el costo del combo es la suma de sus componentes
            if not stk.componentes(conn, pid):
                raise ApiError(f"El combo «{p['nombre']}» no tiene componentes")
        lineas.append(calcular_linea(p, cant, tasa, pct, modo))
    if validar_stock and not permitir_negativo:
        stk.validar_demanda(conn, cantidades, stk.almacen_ventas(conn))
    return lineas, totales_fiscales(lineas, pct, modo)


def cantidades_de(items_in):
    """Une líneas repetidas del mismo producto y valida cantidades (hasta 3 decimales)."""
    if not isinstance(items_in, list) or not items_in:
        raise ApiError("El carrito está vacío")
    cantidades = {}
    for it in items_in:
        if not isinstance(it, dict):
            raise ApiError("Item inválido")
        try:
            pid = int(it.get("producto_id"))
        except (TypeError, ValueError):
            raise ApiError("Producto inválido en el carrito")
        cantidades[pid] = cantidades.get(pid, 0.0) + num(it.get("cantidad"), "La cantidad", estricto_positivo=True)
    cantidades = {pid: round(c, 3) for pid, c in cantidades.items()}
    if any(c <= 0 for c in cantidades.values()):
        raise ApiError("La cantidad es demasiado pequeña (mínimo 0,001)")
    return cantidades


def turno_actual(conn, user_id):
    f = conn.execute("SELECT id FROM turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    return f["id"] if f else None


def exigir_turno(conn, cfg):
    """Turno de caja abierto del usuario; si la caja es obligatoria y no hay, no se puede cobrar."""
    t = turno_actual(conn, g.user["user_id"])
    if t is None and cfg.get("caja_obligatoria") == "1":
        raise ApiError("Abra la caja antes de cobrar (menú Caja → Abrir turno)", 409)
    return t


def sacar_de_almacen(conn, item_id, p, cant, vid, aid, permitir_negativo):
    """Descuenta del almacén de ventas lo vendido (FEFO; un combo descuenta sus componentes) y lo registra para anular."""
    usuario = g.user["usuario"]
    if p["tipo"] == "combo":
        partes = [(c["producto_id"], round(c["cantidad"] * cant, 4), c["nombre"], f"Venta #{vid} (combo {p['nombre']})")
                  for c in stk.componentes(conn, p["id"])]
    else:
        partes = [(p["id"], cant, p["nombre"], f"Venta #{vid}")]
    for pid, c, nombre, motivo in partes:
        for alm, lote, t in stk.consumir(conn, pid, aid, c, "venta", motivo, usuario, vid,
                                         permitir_negativo=permitir_negativo, nombre=nombre):
            conn.execute("INSERT INTO venta_consumos(venta_item_id, producto_id, almacen_id, lote_id, cantidad) "
                         "VALUES(?,?,?,?,?)", (item_id, pid, alm, lote, t))


@app.post("/api/ventas")
def ventas_post():
    data = body()
    conn = get_db()
    items_in = data.get("items")
    if not isinstance(items_in, list) or not items_in:
        raise ApiError("El carrito está vacío")
    pagos_in = data.get("pagos") or []
    if not isinstance(pagos_in, list):
        raise ApiError("Pagos inválidos")
    a_credito = bool(data.get("a_credito"))
    cfg = get_config(conn)
    tasa = tasa_vigente(conn, data, cfg)
    permitir_negativo = cfg.get("permitir_stock_negativo") == "1"

    cantidades = cantidades_de(items_in)
    av = stk.almacen_ventas(conn)
    turno_id = exigir_turno(conn, cfg)

    try:
        conn.execute("BEGIN IMMEDIATE")
        cliente = None
        if data.get("cliente_id") not in (None, "", 0):
            cliente = conn.execute("SELECT id, nombre FROM clientes WHERE id=? AND activo=1", (int(data["cliente_id"]),)).fetchone()
            if not cliente:
                raise ApiError("El cliente seleccionado no existe")

        lineas, tot = calcular_lineas(conn, cantidades, tasa, cfg, validar_stock=True)
        total_usd, total_bs = tot["total_usd"], tot["total_bs"]

        pagos = []
        pagado_usd = 0.0
        pagado_bs = 0.0   # lo recibido expresado en Bs (los pagos en Bs cuentan exactos; los USD a la tasa)
        solo_bs = True
        hay_efectivo = False
        hay_efectivo_usd = False
        for pg in pagos_in:
            if not isinstance(pg, dict):
                raise ApiError("Pago inválido")
            metodo = str(pg.get("metodo") or "")
            if metodo not in METODOS:
                raise ApiError(f"Método de pago no válido: {metodo}")
            moneda = METODOS[metodo][1]
            monto = num(pg.get("monto"), f"El monto de {METODOS[metodo][0]}", estricto_positivo=True)
            monto_usd = monto if moneda == "USD" else monto / tasa
            pagado_usd += monto_usd
            pagado_bs += monto if moneda == "BS" else monto * tasa
            solo_bs = solo_bs and moneda == "BS"
            hay_efectivo = hay_efectivo or metodo in EFECTIVO
            hay_efectivo_usd = hay_efectivo_usd or metodo == "efectivo_usd"
            pagos.append((metodo, moneda, monto, monto_usd, texto(pg.get("referencia"), "La referencia", False, 80)))

        credito_usd = vuelto_usd = vuelto_bs = 0.0
        # Si todo se pagó en Bs, la comparación y el vuelto se calculan en Bs (exactos). Pasar por USD redondeado a centavos
        # desviaba el vuelto varios bolívares cuando la tasa es alta (1.500 − 1.100 daba 397,78 en vez de 400).
        solo_bs = solo_bs and bool(pagos)
        tol_bs = 0.05 + 0.01 * len(lineas)
        falta = (pagado_bs + tol_bs < total_bs) if solo_bs else (pagado_usd + TOLERANCIA_USD < total_usd)
        excede = (pagado_bs - total_bs > tol_bs) if solo_bs else (pagado_usd - total_usd > TOLERANCIA_USD)
        if falta:
            if not cliente:
                raise ApiError(f"Pago insuficiente: faltan ${total_usd - pagado_usd:.2f}. Seleccione un cliente para vender a crédito")
            if not a_credito:
                raise ApiError("El pago no cubre el total; confirme la venta a crédito")
            credito_usd = r2(total_usd - pagado_usd)
        elif excede:
            if not hay_efectivo:
                raise ApiError("El pago excede el total y no hay efectivo para dar el vuelto")
            vuelto_bs = r2(max(0.0, pagado_bs - total_bs))
            vuelto_usd = r2(vuelto_bs / tasa) if solo_bs else r2(pagado_usd - total_usd)
        vuelto_en = ""
        if excede and not falta:  # moneda en que se entrega el vuelto (afecta el arqueo de caja)
            vuelto_en = str(data.get("vuelto_en") or "").upper() or ("USD" if hay_efectivo_usd else "BS")
            if vuelto_en not in ("USD", "BS"):
                raise ApiError("El vuelto debe darse en USD o BS")

        cur = conn.execute(
            "INSERT INTO ventas(fecha, cajero_id, cajero_nombre, cliente_id, cliente_nombre, tasa, total_usd, total_bs, "
            "pagado_usd, credito_usd, vuelto_usd, vuelto_bs, iva_pct, iva_modo, base_usd, exento_usd, iva_usd, base_bs, "
            "exento_bs, iva_bs, turno_id, vuelto_en) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.now_str(), g.user["user_id"], g.user["nombre"] or g.user["usuario"],
             cliente["id"] if cliente else None, cliente["nombre"] if cliente else "",
             tasa, total_usd, total_bs, r2(pagado_usd), credito_usd, vuelto_usd, vuelto_bs,
             tot["iva_pct"], tot["iva_modo"], tot["base_usd"], tot["exento_usd"], tot["iva_usd"], tot["base_bs"],
             tot["exento_bs"], tot["iva_bs"], turno_id, vuelto_en))
        vid = cur.lastrowid
        for l in lineas:
            p, cant = l["p"], l["cant"]
            cur_it = conn.execute(
                "INSERT INTO venta_items(venta_id, producto_id, nombre, codigo, unidad, cantidad, precio_usd, precio_bs, "
                "costo_usd, subtotal_usd, subtotal_bs, exento, iva_pct, base_usd, iva_usd, base_bs, iva_bs, total_bs) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vid, p["id"], p["nombre"], p["codigo"] or "", p["unidad"], cant, l["precio_usd_linea"],
                 p["precio_bs"] if l["sub_bs"] else 0, p["costo_usd"], l["sub"], l["sub_bs"], l["exento"], l["iva_pct"],
                 l["base_usd"], l["iva_usd"], l["base_bs"], l["iva_bs"], l["total_bs"]))
            sacar_de_almacen(conn, cur_it.lastrowid, p, cant, vid, av, permitir_negativo)
        for metodo, moneda, monto, monto_usd, ref in pagos:
            conn.execute("INSERT INTO venta_pagos(venta_id, metodo, moneda, monto, monto_usd, referencia) VALUES(?,?,?,?,?,?)",
                         (vid, metodo, moneda, monto, round(monto_usd, 4), ref))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(fetch_venta(conn, vid), 201)


def efectivo_neto_venta(conn, vid):
    """Efectivo que quedó en caja por una venta (cobrado menos vuelto), por moneda."""
    v = conn.execute("SELECT vuelto_usd, vuelto_bs, vuelto_en FROM ventas WHERE id=?", (vid,)).fetchone()
    neto = {"USD": 0.0, "BS": 0.0}
    for p in conn.execute("SELECT metodo, monto FROM venta_pagos WHERE venta_id=? AND metodo IN ('efectivo_usd','efectivo_bs')", (vid,)):
        neto["USD" if p["metodo"] == "efectivo_usd" else "BS"] += p["monto"]
    if v["vuelto_usd"] > 0 or v["vuelto_bs"] > 0:
        if v["vuelto_en"] == "BS":
            neto["BS"] -= v["vuelto_bs"]
        else:
            neto["USD"] -= v["vuelto_usd"]
    return {k: r2(max(x, 0.0)) for k, x in neto.items()}


@app.post("/api/ventas/<int:vid>/anular")
def ventas_anular(vid):
    """Anula una venta y devuelve el stock. El cajero necesita la clave de un administrador (campo «autoriza»).
    Si la venta es de un turno ya cerrado, «devolver_efectivo» registra la salida del efectivo en el turno abierto de quien anula."""
    data = request.get_json(silent=True) or {}
    conn = get_db()
    autoriza = autorizar_admin(conn, data)
    quien = g.user["usuario"] if autoriza == g.user["usuario"] else f"{g.user['usuario']} (autorizó {autoriza})"
    devuelto = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        v = conn.execute("SELECT * FROM ventas WHERE id=?", (vid,)).fetchone()
        if not v:
            raise ApiError("Venta no encontrada", 404)
        if v["estado"] == "anulada":
            raise ApiError("La venta ya está anulada")
        turno = conn.execute("SELECT estado FROM turnos WHERE id=?", (v["turno_id"],)).fetchone() if v["turno_id"] else None
        if data.get("devolver_efectivo") and (turno is None or turno["estado"] == "cerrado"):
            neto = efectivo_neto_venta(conn, vid)
            if neto["USD"] > 0 or neto["BS"] > 0:
                tid = turno_actual(conn, g.user["user_id"])
                if not tid:
                    raise ApiError("Abra un turno de caja para registrar la devolución del efectivo", 409)
                for moneda, monto in neto.items():
                    if monto > 0:
                        conn.execute(
                            "INSERT INTO caja_movimientos(turno_id, fecha, tipo, categoria, concepto, moneda, monto, usuario) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (tid, db.now_str(), "retiro", "Devolución de venta", f"Devolución venta #{vid}", moneda, monto, g.user["usuario"]))
                        devuelto[moneda] = monto
        principal = stk.almacen_principal(conn)
        for it in conn.execute("SELECT * FROM venta_items WHERE venta_id=?", (vid,)).fetchall():
            if it["producto_id"] is None:
                continue
            consumos = rows(conn.execute("SELECT * FROM venta_consumos WHERE venta_item_id=?", (it["id"],)))
            if not consumos:  # venta anterior a la v0.5: se devuelve al almacén principal
                if not conn.execute("SELECT 1 FROM productos WHERE id=?", (it["producto_id"],)).fetchone():
                    continue
                consumos = [{"producto_id": it["producto_id"], "almacen_id": principal, "lote_id": None,
                             "cantidad": it["cantidad"]}]
            consumos = [c for c in consumos if conn.execute("SELECT 1 FROM productos WHERE id=?", (c["producto_id"],)).fetchone()]
            stk.restituir(conn, consumos, "anulación", f"Anulación venta #{vid}", g.user["usuario"], vid)
        conn.execute("UPDATE ventas SET estado='anulada', anulada_fecha=?, anulada_por=? WHERE id=?",
                     (db.now_str(), quien, vid))
        conn.execute("UPDATE documentos SET estado='anulado', anulado_fecha=?, anulado_por=? "
                     "WHERE venta_id=? AND estado='emitido' AND tipo IN ('factura','nota_entrega')",
                     (db.now_str(), quien, vid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    out = fetch_venta(conn, vid)
    out["devolucion"] = devuelto
    return ok(out)


# --------------------------------------------------------------------------- documentos
# Facturas y notas de entrega se generan a partir de una venta; las proformas, desde el carrito (no mueven stock).
TIPOS_DOC = {"factura": "sig_factura", "proforma": "sig_proforma", "nota_entrega": "sig_nota"}


def doc_out(conn, did, con_items=True):
    d = conn.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
    if not d:
        raise ApiError("Documento no encontrado", 404)
    d = dict(d)
    if d["tipo"] == "proforma" and d["validez_dias"] > 0:
        try:
            d["vence"] = (datetime.strptime(d["fecha"][:10], "%Y-%m-%d") + timedelta(days=d["validez_dias"])).strftime("%Y-%m-%d")
        except ValueError:
            d["vence"] = ""
    if con_items:
        d["items"] = rows(conn.execute("SELECT * FROM documento_items WHERE documento_id=? ORDER BY id", (did,)))
    return d


def datos_cliente_doc(conn, data, cliente_id):
    """Datos del cliente que se imprimen: los de la ficha como base y lo escrito en el formulario como prioridad."""
    base = {"nombre": "", "rif": "", "direccion": "", "telefono": ""}
    if cliente_id:
        c = conn.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,)).fetchone()
        if c:
            base = {"nombre": c["nombre"], "rif": c["cedula"] or "", "direccion": c["direccion"] or "", "telefono": c["telefono"] or ""}
    form = data.get("cliente") if isinstance(data.get("cliente"), dict) else {}
    for k, etiqueta, mx in (("nombre", "El nombre del cliente", 120), ("rif", "El RIF / cédula", 30),
                            ("direccion", "La dirección", 200), ("telefono", "El teléfono", 40)):
        if k in form and form[k] is not None:
            base[k] = texto(form[k], etiqueta, False, mx)
    if not base["nombre"]:
        base["nombre"] = "Consumidor final"
    return base


def items_de_venta(conn, venta):
    """Líneas de una venta con su desglose fiscal. Las ventas anteriores al IVA (sin desglose) se toman como base."""
    out = []
    for it in conn.execute("SELECT * FROM venta_items WHERE venta_id=? ORDER BY id", (venta["id"],)).fetchall():
        tiene_desglose = it["base_usd"] > 0 or it["iva_usd"] > 0 or it["total_bs"] > 0
        if tiene_desglose:
            base_usd, iva_usd, base_bs, iva_bs, total_bs = it["base_usd"], it["iva_usd"], it["base_bs"], it["iva_bs"], it["total_bs"]
        else:
            base_usd, iva_usd, iva_bs = it["subtotal_usd"], 0.0, 0.0
            total_bs = it["subtotal_bs"] if it["subtotal_bs"] > 0 else r2(it["subtotal_usd"] * venta["tasa"])
            base_bs = total_bs
        total_usd = r2(base_usd + iva_usd)
        out.append({"producto_id": it["producto_id"], "codigo": it["codigo"], "nombre": it["nombre"], "unidad": it["unidad"],
                    "cantidad": it["cantidad"], "exento": it["exento"], "iva_pct": it["iva_pct"],
                    "base_usd": base_usd, "iva_usd": iva_usd, "base_bs": base_bs, "iva_bs": iva_bs,
                    "total_usd": total_usd, "total_bs": total_bs})
    return out


def items_de_lineas(lineas):
    return [{"producto_id": l["p"]["id"], "codigo": l["p"]["codigo"] or "", "nombre": l["p"]["nombre"], "unidad": l["p"]["unidad"],
             "cantidad": l["cant"], "exento": l["exento"], "iva_pct": l["iva_pct"], "base_usd": l["base_usd"],
             "iva_usd": l["iva_usd"], "base_bs": l["base_bs"], "iva_bs": l["iva_bs"], "total_usd": l["total_usd"],
             "total_bs": l["total_bs"]} for l in lineas]


@app.post("/api/documentos")
def documentos_post():
    data = body()
    tipo = str(data.get("tipo") or "")
    if tipo not in TIPOS_DOC:
        raise ApiError("Tipo de documento no válido (factura, proforma o nota_entrega)")
    conn = get_db()
    cfg = get_config(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        venta = None
        if tipo == "proforma":
            if data.get("venta_id"):
                raise ApiError("La proforma se genera desde el carrito, no desde una venta")
            tasa = tasa_vigente(conn, data, cfg)
            lineas, tot = calcular_lineas(conn, cantidades_de(data.get("items")), tasa, cfg)
            items = items_de_lineas(lineas)
            cliente_id = int(data["cliente_id"]) if data.get("cliente_id") not in (None, "", 0) else None
            venta_id = None
            validez = int(num(data.get("validez_dias"), "La validez", 1, default=float(cfg.get("proforma_validez") or 7)))
            if validez > 365:
                raise ApiError("La validez no puede pasar de 365 días")
        else:
            try:
                venta_id = int(data.get("venta_id"))
            except (TypeError, ValueError):
                raise ApiError("La factura y la nota de entrega se generan desde una venta")
            venta = conn.execute("SELECT * FROM ventas WHERE id=?", (venta_id,)).fetchone()
            if not venta:
                raise ApiError("Venta no encontrada", 404)
            if venta["estado"] == "anulada":
                raise ApiError("La venta está anulada: no se puede generar el documento")
            previo = conn.execute("SELECT id FROM documentos WHERE venta_id=? AND tipo=? AND estado='emitido' ORDER BY id DESC LIMIT 1",
                                  (venta_id, tipo)).fetchone()
            if previo:
                conn.rollback()
                d = doc_out(conn, previo["id"])
                d["ya_existia"] = True
                return ok(d)
            tasa = venta["tasa"]
            items = items_de_venta(conn, venta)
            sin_desglose = venta["iva_modo"] == "" and venta["base_usd"] == 0 and venta["total_usd"] > 0
            tot = {"iva_pct": venta["iva_pct"], "iva_modo": venta["iva_modo"], "total_usd": venta["total_usd"],
                   "total_bs": venta["total_bs"], "iva_usd": venta["iva_usd"], "iva_bs": venta["iva_bs"],
                   "exento_usd": venta["exento_usd"], "exento_bs": venta["exento_bs"],
                   "base_usd": venta["total_usd"] if sin_desglose else venta["base_usd"],
                   "base_bs": venta["total_bs"] if sin_desglose else venta["base_bs"]}
            cliente_id = venta["cliente_id"]
            validez = 0
        cli = datos_cliente_doc(conn, data, cliente_id)
        clave = TIPOS_DOC[tipo]
        sig = int(float(cfg.get(clave) or 1))
        ultimo = conn.execute("SELECT COALESCE(MAX(numero),0) FROM documentos WHERE tipo=?", (tipo,)).fetchone()[0]
        numero = max(sig, ultimo + 1)
        mostrar = 1 if tipo != "nota_entrega" else (1 if data.get("mostrar_precios") else 0)
        cur = conn.execute(
            "INSERT INTO documentos(tipo, numero, fecha, venta_id, cliente_id, cli_nombre, cli_rif, cli_direccion, cli_telefono, "
            "tasa, iva_pct, iva_modo, base_usd, exento_usd, iva_usd, total_usd, base_bs, exento_bs, iva_bs, total_bs, control, "
            "notas, validez_dias, mostrar_precios, creado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tipo, numero, db.now_str(), venta_id, cliente_id, cli["nombre"], cli["rif"], cli["direccion"], cli["telefono"],
             tasa, tot["iva_pct"], tot["iva_modo"], tot["base_usd"], tot["exento_usd"], tot["iva_usd"], tot["total_usd"],
             tot["base_bs"], tot["exento_bs"], tot["iva_bs"], tot["total_bs"],
             texto(data.get("control"), "El número de control", False, 40) if tipo == "factura" else "",
             texto(data.get("notas"), "Las observaciones", False, 400), validez, mostrar, g.user["usuario"]))
        did = cur.lastrowid
        for it in items:
            conn.execute(
                "INSERT INTO documento_items(documento_id, producto_id, codigo, nombre, unidad, cantidad, precio_usd, precio_bs, "
                "exento, iva_pct, base_usd, iva_usd, base_bs, iva_bs, total_usd, total_bs) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, it["producto_id"], it["codigo"], it["nombre"], it["unidad"], it["cantidad"],
                 round(it["base_usd"] / it["cantidad"], 6), round(it["base_bs"] / it["cantidad"], 6), it["exento"], it["iva_pct"],
                 it["base_usd"], it["iva_usd"], it["base_bs"], it["iva_bs"], it["total_usd"], it["total_bs"]))
        conn.execute("INSERT INTO config(clave, valor) VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                     (clave, str(numero + 1)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ok(doc_out(conn, did), 201)


@app.get("/api/documentos")
def documentos_get():
    conn = get_db()
    sql = ("SELECT id, tipo, numero, fecha, venta_id, cliente_id, cli_nombre, cli_rif, total_usd, total_bs, iva_usd, estado, "
           "control, creado_por, validez_dias FROM documentos WHERE 1=1")
    params = []
    tipo = (request.args.get("tipo") or "").strip()
    if tipo:
        if tipo not in TIPOS_DOC:
            raise ApiError("Tipo de documento no válido")
        sql += " AND tipo=?"
        params.append(tipo)
    estado = (request.args.get("estado") or "").strip()
    if estado in ("emitido", "anulado"):
        sql += " AND estado=?"
        params.append(estado)
    desde = validar_fecha(request.args.get("desde"), "Desde")
    hasta = validar_fecha(request.args.get("hasta"), "Hasta")
    if desde:
        sql += " AND date(fecha) >= ?"
        params.append(desde)
    if hasta:
        sql += " AND date(fecha) <= ?"
        params.append(hasta)
    q = (request.args.get("q") or "").strip()
    if q:
        sql += " AND (cli_nombre LIKE ? OR cli_rif LIKE ? OR CAST(numero AS TEXT) LIKE ? OR control LIKE ?)"
        params += [f"%{q}%"] * 4
    venta = int_arg("venta_id")
    if venta:
        sql += " AND venta_id=?"
        params.append(venta)
    limite = min(int_arg("limit") or 500, 5000)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limite)
    return ok(rows(conn.execute(sql, params)))


@app.get("/api/documentos/<int:did>")
def documentos_detalle(did):
    return ok(doc_out(get_db(), did))


@app.post("/api/documentos/<int:did>/anular")
def documentos_anular(did):
    if not es_admin():
        raise ApiError("Solo el administrador puede anular documentos", 403)
    conn = get_db()
    d = doc_out(conn, did, con_items=False)
    if d["estado"] == "anulado":
        raise ApiError("El documento ya está anulado")
    with conn:
        conn.execute("UPDATE documentos SET estado='anulado', anulado_fecha=?, anulado_por=? WHERE id=?",
                     (db.now_str(), g.user["usuario"], did))
    return ok(doc_out(conn, did))


# --------------------------------------------------------------------------- usuarios
def usuario_publico(fila):
    return {"id": fila["id"], "usuario": fila["usuario"], "nombre": fila["nombre"], "rol": fila["rol"],
            "creado": fila["creado"]}


def admins_activos(conn, excluir=None):
    sql = "SELECT COUNT(*) FROM usuarios WHERE rol='admin' AND activo=1"
    params = []
    if excluir:
        sql += " AND id != ?"
        params.append(excluir)
    return conn.execute(sql, params).fetchone()[0]


@app.get("/api/usuarios")
def usuarios_get():
    cur = get_db().execute("SELECT * FROM usuarios WHERE activo=1 ORDER BY usuario COLLATE NOCASE")
    return ok([usuario_publico(r) for r in cur.fetchall()])


@app.post("/api/usuarios")
def usuarios_post():
    data = body()
    conn = get_db()
    usuario = texto(data.get("usuario"), "El usuario", True, 32)
    if not RE_USUARIO.match(usuario):
        raise ApiError("El usuario debe tener 3-32 caracteres (letras, números, punto, guion o guion bajo)")
    password = str(data.get("password") or "")
    if len(password) < 6:
        raise ApiError("La contraseña debe tener al menos 6 caracteres")
    rol = data.get("rol")
    if rol not in ("admin", "cajero"):
        raise ApiError("El rol debe ser 'admin' o 'cajero'")
    if conn.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone():
        raise ApiError("Ya existe un usuario con ese nombre")
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO usuarios(usuario, nombre, password_hash, rol, creado) VALUES(?,?,?,?,?)",
                (usuario, texto(data.get("nombre"), "El nombre", False, 80) or usuario,
                 db.hash_password(password), rol, db.now_str()))
    except sqlite3.IntegrityError:
        raise ApiError("Ya existe un usuario con ese nombre")
    return ok(usuario_publico(conn.execute("SELECT * FROM usuarios WHERE id=?", (cur.lastrowid,)).fetchone()), 201)


@app.put("/api/usuarios/<int:uid>")
def usuarios_put(uid):
    data = body()
    conn = get_db()
    actual = conn.execute("SELECT * FROM usuarios WHERE id=? AND activo=1", (uid,)).fetchone()
    if not actual:
        raise ApiError("Usuario no encontrado", 404)
    rol = data.get("rol", actual["rol"])
    if rol not in ("admin", "cajero"):
        raise ApiError("El rol debe ser 'admin' o 'cajero'")
    if actual["rol"] == "admin" and rol != "admin" and admins_activos(conn, excluir=uid) == 0:
        raise ApiError("Debe existir al menos un administrador")
    nombre = texto(data.get("nombre", actual["nombre"]), "El nombre", False, 80) or actual["usuario"]
    pwd_hash = actual["password_hash"]
    cambia_credenciales = rol != actual["rol"]
    if data.get("password"):
        if len(str(data["password"])) < 6:
            raise ApiError("La contraseña debe tener al menos 6 caracteres")
        pwd_hash = db.hash_password(str(data["password"]))
        cambia_credenciales = True
    with conn:
        conn.execute("UPDATE usuarios SET nombre=?, rol=?, password_hash=?, debe_cambiar=CASE WHEN ? THEN 0 ELSE debe_cambiar END WHERE id=?",
                     (nombre, rol, pwd_hash, 1 if data.get("password") else 0, uid))
    if data.get("password") and uid == g.user["user_id"]:
        with _sessions_lock:
            if g.token in _sessions:
                _sessions[g.token]["debe_cambiar"] = False
    if cambia_credenciales and uid != g.user["user_id"]:
        cerrar_sesiones(uid)
    return ok(usuario_publico(conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()))


@app.delete("/api/usuarios/<int:uid>")
def usuarios_delete(uid):
    conn = get_db()
    actual = conn.execute("SELECT * FROM usuarios WHERE id=? AND activo=1", (uid,)).fetchone()
    if not actual:
        raise ApiError("Usuario no encontrado", 404)
    if uid == g.user["user_id"]:
        raise ApiError("No puede eliminar su propio usuario")
    if actual["rol"] == "admin" and admins_activos(conn, excluir=uid) == 0:
        raise ApiError("Debe existir al menos un administrador")
    with conn:
        if conn.execute("SELECT 1 FROM ventas WHERE cajero_id=? LIMIT 1", (uid,)).fetchone():
            conn.execute("UPDATE usuarios SET activo=0 WHERE id=?", (uid,))  # conserva el historial de ventas
        else:
            conn.execute("DELETE FROM usuarios WHERE id=?", (uid,))
    cerrar_sesiones(uid)
    return ok(True)


# --------------------------------------------------------------------------- respaldo
REQUIRED_TABLES = {"config", "categorias", "productos", "clientes", "usuarios", "ventas", "venta_items", "venta_pagos", "abonos"}


@app.get("/api/backup/db-path")
def backup_db_path():
    ruta = db.get_db_path()
    return ok({
        "ruta": ruta,
        "tamano_bytes": os.path.getsize(ruta) if os.path.exists(ruta) else 0,
        "modo": "ejecutable" if db.is_frozen() else "desarrollo",
    })


@app.get("/api/backup/download-db")
def backup_download():
    # Copia consistente con la API de respaldo de SQLite (no copia el archivo "en caliente").
    src = db.get_conn()
    mem_path = os.path.join(os.path.dirname(db.get_db_path()), "antarexales.export.tmp")
    try:
        dst = sqlite3.connect(mem_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
        with open(mem_path, "rb") as fh:
            contenido = fh.read()
    finally:
        src.close()
        try:
            os.remove(mem_path)
        except OSError:
            pass
    nombre = f"antarexales_respaldo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    resp = Response(contenido, mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{nombre}"'
    resp.headers["Content-Length"] = str(len(contenido))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/backup/restore-db")
def backup_restore():
    archivo = request.files.get("file")
    if archivo is None or not archivo.filename:
        raise ApiError("Seleccione un archivo .db")
    cabecera = archivo.stream.read(16)
    archivo.stream.seek(0)
    if cabecera != db.SQLITE_MAGIC:
        raise ApiError("El archivo no es una base de datos SQLite válida")

    ruta = db.get_db_path()
    carpeta = os.path.dirname(ruta)
    tmp = os.path.join(carpeta, "antarexales.restore.tmp")
    with _restore_lock:
        archivo.save(tmp)
        try:
            try:
                chk = sqlite3.connect(Path(tmp).as_uri() + "?mode=ro", uri=True)
                try:
                    if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ApiError("El archivo está dañado (falló la verificación de integridad)")
                    tablas = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not REQUIRED_TABLES.issubset(tablas):
                        raise ApiError("El archivo no es un respaldo de AntareXales POS (faltan tablas)")
                    if chk.execute("SELECT 1 FROM config WHERE clave='salt'").fetchone() is None:
                        raise ApiError("El archivo no es un respaldo de AntareXales POS (sin credenciales de instalación)")
                finally:
                    chk.close()
            except sqlite3.DatabaseError:
                raise ApiError("El archivo no se pudo leer como base de datos SQLite")

            # Copia de seguridad de la base actual antes de reemplazarla.
            seguridad = os.path.join(carpeta, "antarexales_antes_de_restaurar.bak")
            origen = db.get_conn()
            try:
                destino = sqlite3.connect(seguridad)
                try:
                    origen.backup(destino)
                finally:
                    destino.close()
            finally:
                origen.close()

            for intento in range(15):
                try:
                    os.replace(tmp, ruta)
                    break
                except PermissionError:
                    time.sleep(0.3)
            else:
                raise ApiError("No se pudo reemplazar la base de datos (archivo en uso). Reintente.", 500)
            for ext in ("-wal", "-shm", "-journal"):
                try:
                    os.remove(ruta + ext)
                except OSError:
                    pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        db.init_db()
        cerrar_sesiones()
    log.info("Base de datos restaurada desde %s", archivo.filename)
    return ok({"restaurado": True, "copia_previa": seguridad})


# --------------------------------------------------------------------------- reportes
@app.get("/api/reportes/resumen")
def reportes_resumen():
    conn = get_db()
    hoy = datetime.now().strftime("%Y-%m-%d")
    desde = validar_fecha(request.args.get("desde"), "Desde") or hoy
    hasta = validar_fecha(request.args.get("hasta"), "Hasta") or hoy
    if desde > hasta:
        raise ApiError("La fecha inicial no puede ser posterior a la final")
    rango = (desde, hasta)
    base = "FROM ventas v WHERE v.estado='completada' AND date(v.fecha) BETWEEN ? AND ?"

    t = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(total_usd),0) usd, COALESCE(SUM(total_bs),0) bs, "
        f"COALESCE(SUM(credito_usd),0) credito, COALESCE(SUM(vuelto_usd),0) vuelto {base}", rango).fetchone()
    anuladas = conn.execute(
        "SELECT COUNT(*) FROM ventas WHERE estado='anulada' AND date(fecha) BETWEEN ? AND ?", rango).fetchone()[0]
    abonos = conn.execute(
        "SELECT COALESCE(SUM(monto_usd),0) FROM abonos WHERE date(fecha) BETWEEN ? AND ?", rango).fetchone()[0]
    por_cobrar = conn.execute(
        f"SELECT COALESCE(SUM(s),0) FROM (SELECT {SALDO_SQL} AS s FROM clientes c WHERE c.activo=1) WHERE s > 0").fetchone()[0]

    gan = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN i.costo_usd > 0 THEN (CASE WHEN i.base_usd > 0 THEN i.base_usd ELSE i.subtotal_usd END) - i.cantidad * i.costo_usd ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN i.costo_usd > 0 THEN (CASE WHEN i.base_usd > 0 THEN i.base_usd ELSE i.subtotal_usd END) ELSE 0 END), 0) "
        "FROM venta_items i JOIN ventas v ON v.id=i.venta_id "
        "WHERE v.estado='completada' AND date(v.fecha) BETWEEN ? AND ?", rango).fetchone()

    iva = conn.execute(
        "SELECT COALESCE(SUM(v.iva_usd),0), COALESCE(SUM(v.iva_bs),0) FROM ventas v "
        "WHERE v.estado='completada' AND date(v.fecha) BETWEEN ? AND ?", rango).fetchone()

    top = rows(conn.execute(
        "SELECT i.nombre, MAX(i.unidad) AS unidad, SUM(i.cantidad) AS cantidad, SUM(i.subtotal_usd) AS total_usd "
        "FROM venta_items i JOIN ventas v ON v.id=i.venta_id "
        "WHERE v.estado='completada' AND date(v.fecha) BETWEEN ? AND ? "
        "GROUP BY i.producto_id, i.nombre ORDER BY cantidad DESC, total_usd DESC LIMIT 10", rango))

    por_metodo = rows(conn.execute(
        "SELECT p.metodo, p.moneda, COUNT(*) AS num, SUM(p.monto) AS monto, SUM(p.monto_usd) AS total_usd "
        "FROM venta_pagos p JOIN ventas v ON v.id=p.venta_id "
        "WHERE v.estado='completada' AND date(v.fecha) BETWEEN ? AND ? GROUP BY p.metodo, p.moneda "
        "ORDER BY total_usd DESC", rango))
    for m in por_metodo:
        m["nombre"] = METODOS.get(m["metodo"], (m["metodo"], ""))[0]
        m["total_usd"] = r2(m["total_usd"])
        m["monto"] = r2(m["monto"])
    if t["credito"] > 0:
        n_cred = conn.execute(f"SELECT COUNT(*) {base} AND v.credito_usd > 0", rango).fetchone()[0]
        por_metodo.append({"metodo": "credito", "nombre": "Crédito (por cobrar)", "moneda": "USD",
                           "num": n_cred, "monto": r2(t["credito"]), "total_usd": r2(t["credito"])})

    por_dia = rows(conn.execute(
        f"SELECT date(v.fecha) AS fecha, COUNT(*) AS num, SUM(v.total_usd) AS total_usd, SUM(v.total_bs) AS total_bs "
        f"{base} GROUP BY date(v.fecha) ORDER BY fecha", rango))
    por_cajero = rows(conn.execute(
        f"SELECT v.cajero_nombre AS cajero, COUNT(*) AS num, SUM(v.total_usd) AS total_usd, SUM(v.total_bs) AS total_bs "
        f"{base} GROUP BY v.cajero_nombre ORDER BY total_usd DESC", rango))

    # v0.5: gastos de caja y, solo para el administrador, compras y mermas del período
    gast = {r["moneda"]: r2(r["t"]) for r in conn.execute(
        "SELECT moneda, SUM(monto) t FROM caja_movimientos WHERE anulado=0 AND tipo='gasto' AND date(fecha) BETWEEN ? AND ? "
        "GROUP BY moneda", rango)}
    extra = {"gastos_usd": gast.get("USD", 0.0), "gastos_bs": gast.get("BS", 0.0)}
    if es_admin():
        extra["compras_usd"] = r2(conn.execute(
            "SELECT COALESCE(SUM(total_usd),0) FROM compras WHERE estado='registrada' AND date(fecha) BETWEEN ? AND ?", rango).fetchone()[0])
        extra["por_pagar_usd"] = r2(conn.execute(
            "SELECT COALESCE(SUM(total_usd-pagado_usd),0) FROM compras WHERE estado='registrada' AND total_usd-pagado_usd>0.0051").fetchone()[0])
        extra["mermas_usd"] = r2(conn.execute(
            "SELECT COALESCE(SUM(ABS(cantidad)*costo_usd),0) FROM movimientos_stock WHERE tipo='merma' AND date(fecha) BETWEEN ? AND ?",
            rango).fetchone()[0])
    n = t["n"]
    return ok({
        **extra,
        "desde": desde, "hasta": hasta,
        "num_ventas": n,
        "total_usd": r2(t["usd"]), "total_bs": r2(t["bs"]),
        "ticket_promedio_usd": r2(t["usd"] / n) if n else 0,
        "ticket_promedio_bs": r2(t["bs"] / n) if n else 0,
        "credito_usd": r2(t["credito"]), "vuelto_usd": r2(t["vuelto"]),
        "iva_usd": r2(iva[0]), "iva_bs": r2(iva[1]),
        "ganancia_usd": r2(gan[0]) if es_admin() else None,
        "ventas_con_costo_usd": r2(gan[1]) if es_admin() else None,
        "anuladas": anuladas, "abonos_usd": r2(abonos), "por_cobrar_usd": r2(por_cobrar),
        "top_productos": top, "por_metodo": por_metodo, "por_dia": por_dia, "por_cajero": por_cajero,
    })


# --------------------------------------------------------------------------- módulos v0.5
# Se registran al final para que puedan usar las utilidades definidas arriba.
import sys as _sys  # noqa: E402
import inventario  # noqa: E402

import caja  # noqa: E402

inventario.register(app, _sys.modules[__name__])
caja.register(app, _sys.modules[__name__])
import compras  # noqa: E402

compras.register(app, _sys.modules[__name__])
import respaldo  # noqa: E402

respaldo.register(app, _sys.modules[__name__])
import actualizador  # noqa: E402

actualizador.register(app, _sys.modules[__name__])


# --------------------------------------------------------------------------- arranque
def crear_servidor(host="127.0.0.1", puerto=5000, intentos=10):
    """Crea el servidor WSGI. Si el puerto está ocupado prueba los siguientes."""
    from werkzeug.serving import make_server
    ultimo = None
    for p in range(puerto, puerto + intentos):
        try:
            return make_server(host, p, app, threaded=True), p
        except OSError as e:
            ultimo = e
    raise ultimo


if __name__ == "__main__":
    db.init_db()
    srv, port = crear_servidor()
    print(f"{ver.APP_NAME} v{ver.VERSION} en http://127.0.0.1:{port}")
    srv.serve_forever()

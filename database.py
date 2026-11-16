"""AntareXales POS - esquema SQLite y utilidades de base de datos."""
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime

import version as ver

SQLITE_MAGIC = b"SQLite format 3\x00"


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def base_dir():
    """Carpeta de recursos (frontend, assets). En .exe es la carpeta temporal de PyInstaller."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


_legacy_checked = False


def _data_folder():
    if is_frozen():
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, ver.APP_DIR_NAME), os.path.join(appdata, ver.LEGACY_DIR_NAME)
    aqui = os.path.dirname(os.path.abspath(__file__))
    carpeta = os.path.join(aqui, "data")
    return carpeta, carpeta


def migrar_base_antigua(destino):
    """Si existe la base de la versión anterior (MegaVenta) y aún no hay una nueva, la COPIA.

    No se borra ni se mueve nada: la base antigua queda intacta como respaldo.
    """
    global _legacy_checked
    if _legacy_checked:
        return
    _legacy_checked = True
    if os.path.exists(destino):
        return
    _, carpeta_antigua = _data_folder()
    antigua = os.path.join(carpeta_antigua, ver.LEGACY_DB_FILENAME)
    if os.path.exists(antigua):
        try:
            shutil.copy2(antigua, destino)
        except OSError:
            pass


def get_db_path():
    carpeta, _ = _data_folder()
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, ver.DB_FILENAME)
    migrar_base_antigua(ruta)
    return ruta


def get_conn():
    conn = sqlite3.connect(get_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


PBKDF2_ITER = 200_000
FORZAR_CAMBIO_CLAVE = True  # las pruebas automáticas lo desactivan; en la app real el admin inicial debe cambiar su clave


def hash_legacy(password, salt):
    """Formato de las versiones 0.1–0.5 (SHA-256 con sal de la instalación). Solo para validar claves antiguas."""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def hash_password(password):
    """PBKDF2-HMAC-SHA256 con sal propia de cada clave: «pbkdf2$vueltas$sal$hash»."""
    sal = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${sal.hex()}${dk.hex()}"


def verificar_password(guardado, password, salt_legacy):
    """Valida una clave contra el hash guardado (nuevo o antiguo). Con hash vacío (usuario inexistente) gasta el mismo tiempo."""
    if guardado.startswith("pbkdf2$"):
        try:
            _, vueltas, sal, h = guardado.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sal), int(vueltas))
            return hmac.compare_digest(dk.hex(), h)
        except ValueError:
            return False
    if not guardado:
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"\0" * 16, PBKDF2_ITER)
        return False
    return hmac.compare_digest(guardado, hash_legacy(password, salt_legacy))


def necesita_rehash(guardado):
    return not guardado.startswith("pbkdf2$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT,
    nombre TEXT NOT NULL,
    categoria_id INTEGER REFERENCES categorias(id) ON DELETE SET NULL,
    unidad TEXT NOT NULL DEFAULT 'und',
    costo_usd REAL NOT NULL DEFAULT 0,
    ganancia_pct REAL NOT NULL DEFAULT 0,
    precio_moneda TEXT NOT NULL DEFAULT 'USD',
    precio_usd REAL NOT NULL DEFAULT 0,
    precio_bs REAL NOT NULL DEFAULT 0,
    imagen_ver INTEGER NOT NULL DEFAULT 0,
    iva_exento INTEGER NOT NULL DEFAULT 0,
    tipo TEXT NOT NULL DEFAULT 'simple',
    grupo TEXT NOT NULL DEFAULT '',
    variante TEXT NOT NULL DEFAULT '',
    stock REAL NOT NULL DEFAULT 0,
    stock_minimo REAL NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_productos_codigo
    ON productos(codigo) WHERE activo = 1 AND codigo IS NOT NULL;

CREATE TABLE IF NOT EXISTS producto_imagenes (
    producto_id INTEGER PRIMARY KEY REFERENCES productos(id) ON DELETE CASCADE,
    mime TEXT NOT NULL,
    datos BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL CHECK (tipo IN ('factura','proforma','nota_entrega')),
    numero INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    venta_id INTEGER REFERENCES ventas(id) ON DELETE SET NULL,
    cliente_id INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
    cli_nombre TEXT NOT NULL DEFAULT '',
    cli_rif TEXT NOT NULL DEFAULT '',
    cli_direccion TEXT NOT NULL DEFAULT '',
    cli_telefono TEXT NOT NULL DEFAULT '',
    tasa REAL NOT NULL DEFAULT 1,
    iva_pct REAL NOT NULL DEFAULT 0,
    iva_modo TEXT NOT NULL DEFAULT '',
    base_usd REAL NOT NULL DEFAULT 0,
    exento_usd REAL NOT NULL DEFAULT 0,
    iva_usd REAL NOT NULL DEFAULT 0,
    total_usd REAL NOT NULL DEFAULT 0,
    base_bs REAL NOT NULL DEFAULT 0,
    exento_bs REAL NOT NULL DEFAULT 0,
    iva_bs REAL NOT NULL DEFAULT 0,
    total_bs REAL NOT NULL DEFAULT 0,
    control TEXT NOT NULL DEFAULT '',
    notas TEXT NOT NULL DEFAULT '',
    validez_dias INTEGER NOT NULL DEFAULT 0,
    mostrar_precios INTEGER NOT NULL DEFAULT 1,
    estado TEXT NOT NULL DEFAULT 'emitido' CHECK (estado IN ('emitido','anulado')),
    creado_por TEXT NOT NULL DEFAULT '',
    anulado_fecha TEXT,
    anulado_por TEXT,
    UNIQUE (tipo, numero)
);
CREATE INDEX IF NOT EXISTS ix_documentos_fecha ON documentos(fecha);
CREATE INDEX IF NOT EXISTS ix_documentos_venta ON documentos(venta_id);

CREATE TABLE IF NOT EXISTS documento_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    producto_id INTEGER,
    codigo TEXT NOT NULL DEFAULT '',
    nombre TEXT NOT NULL,
    unidad TEXT NOT NULL DEFAULT 'und',
    cantidad REAL NOT NULL,
    precio_usd REAL NOT NULL DEFAULT 0,
    precio_bs REAL NOT NULL DEFAULT 0,
    exento INTEGER NOT NULL DEFAULT 0,
    iva_pct REAL NOT NULL DEFAULT 0,
    base_usd REAL NOT NULL DEFAULT 0,
    iva_usd REAL NOT NULL DEFAULT 0,
    base_bs REAL NOT NULL DEFAULT 0,
    iva_bs REAL NOT NULL DEFAULT 0,
    total_usd REAL NOT NULL DEFAULT 0,
    total_bs REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_docitems_doc ON documento_items(documento_id);

CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    telefono TEXT DEFAULT '',
    cedula TEXT DEFAULT '',
    direccion TEXT DEFAULT '',
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('admin','cajero')),
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ventas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    cajero_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    cajero_nombre TEXT NOT NULL DEFAULT '',
    cliente_id INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
    cliente_nombre TEXT NOT NULL DEFAULT '',
    tasa REAL NOT NULL DEFAULT 1,
    total_usd REAL NOT NULL DEFAULT 0,
    total_bs REAL NOT NULL DEFAULT 0,
    pagado_usd REAL NOT NULL DEFAULT 0,
    credito_usd REAL NOT NULL DEFAULT 0,
    vuelto_usd REAL NOT NULL DEFAULT 0,
    vuelto_bs REAL NOT NULL DEFAULT 0,
    iva_pct REAL NOT NULL DEFAULT 0,
    iva_modo TEXT NOT NULL DEFAULT '',
    base_usd REAL NOT NULL DEFAULT 0,
    exento_usd REAL NOT NULL DEFAULT 0,
    iva_usd REAL NOT NULL DEFAULT 0,
    base_bs REAL NOT NULL DEFAULT 0,
    exento_bs REAL NOT NULL DEFAULT 0,
    iva_bs REAL NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'completada' CHECK (estado IN ('completada','anulada')),
    anulada_fecha TEXT,
    anulada_por TEXT
);
CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas(fecha);
CREATE INDEX IF NOT EXISTS ix_ventas_cliente ON ventas(cliente_id);

CREATE TABLE IF NOT EXISTS venta_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    producto_id INTEGER REFERENCES productos(id) ON DELETE SET NULL,
    nombre TEXT NOT NULL,
    codigo TEXT DEFAULT '',
    cantidad REAL NOT NULL,
    unidad TEXT NOT NULL DEFAULT 'und',
    precio_usd REAL NOT NULL,
    precio_bs REAL NOT NULL DEFAULT 0,
    costo_usd REAL NOT NULL DEFAULT 0,
    subtotal_usd REAL NOT NULL,
    subtotal_bs REAL NOT NULL DEFAULT 0,
    exento INTEGER NOT NULL DEFAULT 0,
    iva_pct REAL NOT NULL DEFAULT 0,
    base_usd REAL NOT NULL DEFAULT 0,
    iva_usd REAL NOT NULL DEFAULT 0,
    base_bs REAL NOT NULL DEFAULT 0,
    iva_bs REAL NOT NULL DEFAULT 0,
    total_bs REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_items_venta ON venta_items(venta_id);

CREATE TABLE IF NOT EXISTS venta_pagos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    metodo TEXT NOT NULL,
    moneda TEXT NOT NULL CHECK (moneda IN ('USD','BS')),
    monto REAL NOT NULL,
    monto_usd REAL NOT NULL,
    referencia TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_pagos_venta ON venta_pagos(venta_id);

CREATE TABLE IF NOT EXISTS abonos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    monto_usd REAL NOT NULL,
    moneda TEXT NOT NULL DEFAULT 'USD',
    monto_bs REAL NOT NULL DEFAULT 0,
    tasa REAL NOT NULL DEFAULT 0,
    metodo TEXT DEFAULT '',
    referencia TEXT DEFAULT '',
    turno_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_abonos_cliente ON abonos(cliente_id);

CREATE TABLE IF NOT EXISTS movimientos_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,
    cantidad REAL NOT NULL,
    stock_resultante REAL NOT NULL,
    motivo TEXT DEFAULT '',
    usuario TEXT DEFAULT '',
    venta_id INTEGER,
    almacen_id INTEGER,
    costo_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_mov_producto ON movimientos_stock(producto_id);

-- ===== v0.5: almacenes, lotes, combos =====
CREATE TABLE IF NOT EXISTS almacenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
    principal INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT
);
CREATE TABLE IF NOT EXISTS stock_almacen (
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id),
    cantidad REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (producto_id, almacen_id)
);
CREATE TABLE IF NOT EXISTS lotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    almacen_id INTEGER NOT NULL REFERENCES almacenes(id),
    codigo TEXT NOT NULL DEFAULT '',
    vence TEXT NOT NULL DEFAULT '',
    cantidad REAL NOT NULL DEFAULT 0,
    cantidad_inicial REAL NOT NULL DEFAULT 0,
    compra_id INTEGER,
    creado TEXT
);
CREATE INDEX IF NOT EXISTS ix_lotes_prod ON lotes(producto_id, almacen_id, vence);
CREATE TABLE IF NOT EXISTS venta_consumos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_item_id INTEGER NOT NULL REFERENCES venta_items(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL,
    almacen_id INTEGER NOT NULL,
    lote_id INTEGER,
    cantidad REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_consumos_item ON venta_consumos(venta_item_id);
CREATE TABLE IF NOT EXISTS combo_items (
    combo_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    componente_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad REAL NOT NULL,
    PRIMARY KEY (combo_id, componente_id)
);
CREATE TABLE IF NOT EXISTS traslados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    producto_id INTEGER NOT NULL,
    origen_id INTEGER NOT NULL,
    destino_id INTEGER NOT NULL,
    cantidad REAL NOT NULL,
    usuario TEXT DEFAULT '',
    notas TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conteos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    almacen_id INTEGER NOT NULL,
    usuario TEXT DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'borrador',
    notas TEXT DEFAULT '',
    aplicado_fecha TEXT
);
CREATE TABLE IF NOT EXISTS conteo_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conteo_id INTEGER NOT NULL REFERENCES conteos(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL,
    esperado REAL NOT NULL DEFAULT 0,
    contado REAL
);
CREATE INDEX IF NOT EXISTS ix_conteo_items ON conteo_items(conteo_id);

-- ===== v0.5: caja y ventas en espera =====
CREATE TABLE IF NOT EXISTS turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    usuario_nombre TEXT NOT NULL DEFAULT '',
    abierto TEXT NOT NULL,
    fondo_bs REAL NOT NULL DEFAULT 0,
    fondo_usd REAL NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'abierto',
    cerrado TEXT,
    esperado_bs REAL NOT NULL DEFAULT 0,
    esperado_usd REAL NOT NULL DEFAULT 0,
    contado_bs REAL NOT NULL DEFAULT 0,
    contado_usd REAL NOT NULL DEFAULT 0,
    dif_bs REAL NOT NULL DEFAULT 0,
    dif_usd REAL NOT NULL DEFAULT 0,
    notas TEXT DEFAULT '',
    resumen TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS caja_movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turno_id INTEGER NOT NULL REFERENCES turnos(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,
    categoria TEXT NOT NULL DEFAULT '',
    concepto TEXT NOT NULL DEFAULT '',
    moneda TEXT NOT NULL DEFAULT 'USD',
    monto REAL NOT NULL,
    usuario TEXT DEFAULT '',
    anulado INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_cajamov_turno ON caja_movimientos(turno_id);
CREATE TABLE IF NOT EXISTS ventas_espera (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    usuario TEXT DEFAULT '',
    etiqueta TEXT DEFAULT '',
    cliente_id INTEGER,
    items TEXT NOT NULL DEFAULT '[]'
);

-- ===== v0.5: compras y proveedores =====
CREATE TABLE IF NOT EXISTS proveedores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    rif TEXT DEFAULT '',
    telefono TEXT DEFAULT '',
    direccion TEXT DEFAULT '',
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT
);
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    proveedor_id INTEGER,
    proveedor_nombre TEXT NOT NULL DEFAULT '',
    almacen_id INTEGER NOT NULL,
    referencia TEXT DEFAULT '',
    tasa REAL NOT NULL DEFAULT 1,
    total_usd REAL NOT NULL DEFAULT 0,
    pagado_usd REAL NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'registrada',
    notas TEXT DEFAULT '',
    usuario TEXT DEFAULT '',
    anulada_fecha TEXT,
    anulada_por TEXT
);
CREATE INDEX IF NOT EXISTS ix_compras_prov ON compras(proveedor_id);
CREATE TABLE IF NOT EXISTS compra_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id INTEGER NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL,
    nombre TEXT NOT NULL DEFAULT '',
    cantidad REAL NOT NULL,
    costo_usd REAL NOT NULL,
    subtotal_usd REAL NOT NULL,
    vence TEXT NOT NULL DEFAULT '',
    lote TEXT NOT NULL DEFAULT '',
    lote_id INTEGER
);
CREATE TABLE IF NOT EXISTS pagos_proveedor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id INTEGER NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    monto_usd REAL NOT NULL,
    moneda TEXT NOT NULL DEFAULT 'USD',
    monto_bs REAL NOT NULL DEFAULT 0,
    tasa REAL NOT NULL DEFAULT 0,
    metodo TEXT DEFAULT '',
    referencia TEXT DEFAULT '',
    usuario TEXT DEFAULT '',
    caja_mov_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_pagosprov_compra ON pagos_proveedor(compra_id);
"""

DEFAULT_CATEGORIAS = ["General", "Alimentos", "Bebidas", "Limpieza", "Tecnología", "Ropa"]

DEFAULT_CONFIG = {
    "negocio_nombre": "Mi Negocio",
    "rif": "",
    "direccion": "",
    "telefono": "",
    "logo": "",
    "ticket_pie": "¡Gracias por su compra!",
    "tasa_bcv": "1",
    "tema": "oscuro",
    "ganancia_default": "30",
    "permitir_stock_negativo": "0",
    # v0.4: IVA (desactivado por defecto) y documentos
    "iva_activo": "0",
    "iva_pct": "16",
    "iva_modo": "incluido",       # incluido: los precios ya traen IVA | adicional: el IVA se suma al precio
    "proforma_validez": "7",
    "sig_factura": "1",
    "sig_proforma": "1",
    "sig_nota": "1",
    "doc_pie": "",
    # v0.5: almacén desde el que se vende (vacío = el principal), caja y vencimientos
    "almacen_ventas": "",
    "cajero_cambia_tasa": "0",
    "respaldo_auto_dias": "14",
    "caja_obligatoria": "0",
    "dias_alerta_vencimiento": "30",
}

# Columnas agregadas después de la primera versión: (tabla, columna, definición).
MIGRACIONES = [
    ("productos", "costo_usd", "REAL NOT NULL DEFAULT 0"),
    ("productos", "ganancia_pct", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "costo_usd", "REAL NOT NULL DEFAULT 0"),
    # v0.2: unidades (kg/L), precio fijo en Bs, abonos en Bs
    ("productos", "unidad", "TEXT NOT NULL DEFAULT 'und'"),
    ("productos", "precio_moneda", "TEXT NOT NULL DEFAULT 'USD'"),
    ("productos", "precio_bs", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "unidad", "TEXT NOT NULL DEFAULT 'und'"),
    ("venta_items", "precio_bs", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "subtotal_bs", "REAL NOT NULL DEFAULT 0"),
    ("abonos", "moneda", "TEXT NOT NULL DEFAULT 'USD'"),
    ("abonos", "monto_bs", "REAL NOT NULL DEFAULT 0"),
    ("abonos", "tasa", "REAL NOT NULL DEFAULT 0"),
    # v0.3: imagen del producto (los bytes viven en producto_imagenes; imagen_ver sirve para refrescar la caché)
    ("productos", "imagen_ver", "INTEGER NOT NULL DEFAULT 0"),
    # v0.4: IVA, dirección del cliente y desglose fiscal de ventas
    ("productos", "iva_exento", "INTEGER NOT NULL DEFAULT 0"),
    ("clientes", "direccion", "TEXT DEFAULT ''"),
    ("ventas", "iva_pct", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "iva_modo", "TEXT NOT NULL DEFAULT ''"),
    ("ventas", "base_usd", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "exento_usd", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "iva_usd", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "base_bs", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "exento_bs", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "iva_bs", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "exento", "INTEGER NOT NULL DEFAULT 0"),
    ("venta_items", "iva_pct", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "base_usd", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "iva_usd", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "base_bs", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "iva_bs", "REAL NOT NULL DEFAULT 0"),
    ("venta_items", "total_bs", "REAL NOT NULL DEFAULT 0"),
    # v0.5: combos, variantes, almacenes, caja
    ("productos", "tipo", "TEXT NOT NULL DEFAULT 'simple'"),
    ("productos", "grupo", "TEXT NOT NULL DEFAULT ''"),
    ("productos", "variante", "TEXT NOT NULL DEFAULT ''"),
    ("movimientos_stock", "almacen_id", "INTEGER"),
    ("movimientos_stock", "costo_usd", "REAL NOT NULL DEFAULT 0"),
    ("ventas", "turno_id", "INTEGER"),
    ("ventas", "vuelto_en", "TEXT NOT NULL DEFAULT ''"),
    ("abonos", "turno_id", "INTEGER"),
    ("usuarios", "debe_cambiar", "INTEGER NOT NULL DEFAULT 0"),
]


def migrar_esquema(conn):
    """Agrega las columnas nuevas a bases creadas por versiones anteriores (idempotente)."""
    for tabla, columna, definicion in MIGRACIONES:
        existentes = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if columna not in existentes:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


def migrar_datos_v05(conn):
    """Almacén principal y reparto del stock existente (idempotente). Invariante: productos.stock = suma de stock_almacen."""
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ventas_turno ON ventas(turno_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_abonos_turno ON abonos(turno_id)")
    if conn.execute("SELECT COUNT(*) FROM almacenes").fetchone()[0] == 0:
        conn.execute("INSERT INTO almacenes(nombre, principal, activo, creado) VALUES('Principal', 1, 1, ?)", (now_str(),))
    ya = conn.execute("SELECT valor FROM config WHERE clave='stock_almacen_ok'").fetchone()
    if not ya:
        pid = conn.execute("SELECT id FROM almacenes WHERE principal=1 ORDER BY id LIMIT 1").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO stock_almacen(producto_id, almacen_id, cantidad) "
                     "SELECT id, ?, stock FROM productos WHERE tipo='simple'", (pid,))
        conn.execute("INSERT INTO config(clave, valor) VALUES('stock_almacen_ok', '1')")


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        migrar_esquema(conn)
        migrar_datos_v05(conn)
        # Sal única por instalación (viaja con el respaldo, así los usuarios siguen válidos).
        row = conn.execute("SELECT valor FROM config WHERE clave='salt'").fetchone()
        if row is None:
            conn.execute("INSERT INTO config(clave, valor) VALUES('salt', ?)", (secrets.token_hex(16),))
        for k, v in DEFAULT_CONFIG.items():
            conn.execute("INSERT OR IGNORE INTO config(clave, valor) VALUES(?, ?)", (k, v))
        if conn.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 0:
            conn.executemany("INSERT INTO categorias(nombre) VALUES(?)", [(c,) for c in DEFAULT_CATEGORIAS])
        if conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
            salt = conn.execute("SELECT valor FROM config WHERE clave='salt'").fetchone()[0]
            conn.execute(
                "INSERT INTO usuarios(usuario, nombre, password_hash, rol, creado, debe_cambiar) VALUES(?,?,?,?,?,?)",
                ("admin", "Administrador", hash_password("admin123"), "admin", now_str(), 1 if FORZAR_CAMBIO_CLAVE else 0),
            )
        elif FORZAR_CAMBIO_CLAVE:
            # bases de versiones anteriores: quien siga con la clave inicial (admin123) debe cambiarla
            salt = conn.execute("SELECT valor FROM config WHERE clave='salt'").fetchone()[0]
            conn.execute("UPDATE usuarios SET debe_cambiar=1 WHERE password_hash=? AND activo=1",
                         (hash_legacy("admin123", salt),))
        conn.execute("INSERT INTO config(clave, valor) VALUES('app_version', ?) "
                     "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (ver.VERSION,))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("Base de datos lista en:", get_db_path())

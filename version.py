"""AntareXales POS - identidad y versión de la aplicación.

ÚNICA fuente de verdad: la interfaz, la ventana, el servidor y el CHANGELOG leen de aquí.
Cada vez que se haga un ajuste, suba VERSION y agregue su entrada en CHANGELOG.md.
"""
APP_NAME = "AntareXales POS"
VERSION = "0.13.1"
# Repositorio de GitHub donde se publican los Releases (con el .exe y su SHA-256) que usa «Buscar actualizaciones».
GITHUB_REPO = "lordscorpiox/AntareXales_POS"
AUTHOR = "Lord Scorpiox"
CREDITS = f"Desarrollado por {AUTHOR}"
POWERED_BY = "Powered by Claude AI"
# Datos de contacto que muestra la ventana al hacer clic en el nombre del desarrollador (cada uno abre su aplicación).
CONTACT_TELEGRAM = "@lordscorpiox"
CONTACT_EMAIL = "lord.scorpiox@gmail.com"
# Únicos enlaces externos que la aplicación puede abrir fuera de su ventana (ver main.Api.abrir_externo).
CONTACT_TELEGRAM_URL = "https://t.me/lordscorpiox"
CONTACT_EMAIL_URL = "mailto:lord.scorpiox@gmail.com"

# Nombres internos de almacenamiento (carpeta en %APPDATA% y archivo de base de datos).
APP_DIR_NAME = "AntareXalesPOS"
DB_FILENAME = "antarexales.db"
# Nombres usados por la versión anterior (MegaVenta); se usan solo para migrar los datos existentes.
LEGACY_DIR_NAME = "MegaVenta"
LEGACY_DB_FILENAME = "megaventa.db"

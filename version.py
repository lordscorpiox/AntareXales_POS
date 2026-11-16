"""AntareXales POS - identidad y versión de la aplicación.

ÚNICA fuente de verdad: la interfaz, la ventana, el servidor y el CHANGELOG leen de aquí.
Cada vez que se haga un ajuste, suba VERSION y agregue su entrada en CHANGELOG.md.
"""
APP_NAME = "AntareXales POS"
VERSION = "0.7"
AUTHOR = "Lord Scorpiox & Claude AI"
CREDITS = f"Desarrollado por {AUTHOR}"
# Datos de contacto que muestra la ventana al hacer clic en el nombre del desarrollador.
CONTACT_WHATSAPP = "04248556438"
CONTACT_EMAIL = "scorpiox22@gmail.com"

# Nombres internos de almacenamiento (carpeta en %APPDATA% y archivo de base de datos).
APP_DIR_NAME = "AntareXalesPOS"
DB_FILENAME = "antarexales.db"
# Nombres usados por la versión anterior (MegaVenta); se usan solo para migrar los datos existentes.
LEGACY_DIR_NAME = "MegaVenta"
LEGACY_DB_FILENAME = "megaventa.db"

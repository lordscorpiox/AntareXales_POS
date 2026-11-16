"""AntareXales POS - punto de entrada.

Arranca Flask en un hilo daemon (127.0.0.1:5000) y abre una ventana nativa con PyWebView.
Uso:  python main.py          (modo normal)
      python main.py --debug  (habilita las herramientas de desarrollo del navegador)
"""
import base64
import ctypes
import sys
import threading

import database as db
import server
import version as ver

APP_TITLE = f"{ver.APP_NAME} v{ver.VERSION}"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def mensaje_error(texto):
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, texto, APP_TITLE, 0x10)
    else:
        print(texto, file=sys.stderr)


def webview2_instalado():
    """pywebview cae a MSHTML (Internet Explorer) si falta WebView2, y la interfaz no funcionaría."""
    if sys.platform != "win32":
        return True
    import winreg
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    rutas = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients", r"SOFTWARE\Microsoft\EdgeUpdate\Clients")
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for ruta in rutas:
            try:
                with winreg.OpenKey(hive, ruta + "\\" + guid) as clave:
                    version, _ = winreg.QueryValueEx(clave, "pv")
                    if version and version != "0.0.0.0":
                        return True
            except OSError:
                continue
    return False


def instancia_unica():
    """Evita abrir dos AntareXales POS a la vez sobre la misma base de datos (solo Windows)."""
    if sys.platform != "win32":
        return True
    ctypes.windll.kernel32.CreateMutexW(None, False, "AntareXalesPOS_Instancia_Unica")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


ENLACES_PERMITIDOS = (ver.CONTACT_TELEGRAM_URL, ver.CONTACT_EMAIL_URL)


class Api:
    """Métodos expuestos a JavaScript como window.pywebview.api.*"""

    def abrir_externo(self, url):
        """Abre Telegram (navegador/aplicación) o el programa de correo. Solo acepta los enlaces de contacto."""
        import webbrowser
        if url not in ENLACES_PERMITIDOS:
            return {"ok": False, "error": "Enlace no permitido"}
        try:
            return {"ok": bool(webbrowser.open(url))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def guardar_archivo(self, nombre, contenido_base64):
        """Muestra el diálogo 'Guardar como' del sistema y escribe el archivo."""
        import webview
        try:
            destino = webview.windows[0].create_file_dialog(webview.FileDialog.SAVE, save_filename=nombre)
            if not destino:
                return {"ok": False, "cancelado": True}
            ruta = destino if isinstance(destino, str) else destino[0]
            with open(ruta, "wb") as f:
                f.write(base64.b64decode(contenido_base64))
            return {"ok": True, "ruta": ruta}
        except Exception as e:  # se devuelve al frontend para mostrarlo
            return {"ok": False, "error": str(e)}


def main():
    debug = "--debug" in sys.argv
    if not instancia_unica():
        mensaje_error(f"{ver.APP_NAME} ya está abierto.")
        return
    if not webview2_instalado():
        mensaje_error(f"{ver.APP_NAME} necesita Microsoft Edge WebView2 Runtime.\n\n"
                      f"Instálelo desde:\n{WEBVIEW2_URL}\n\ny vuelva a abrir la aplicación.")
        return

    import webview

    db.init_db()
    httpd, puerto = server.crear_servidor("127.0.0.1", 5000)
    threading.Thread(target=httpd.serve_forever, name="flask-server", daemon=True).start()
    server.actualizador.limpiar_restos()  # borra una descarga de actualización a medias
    server.respaldo.iniciar_automatico()  # copia diaria de la base de datos (carpeta «respaldos»)

    # Sin esto WebView2 cancela cualquier descarga (valor por defecto de pywebview >= 5).
    webview.settings["ALLOW_DOWNLOADS"] = True

    webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{puerto}/",
        js_api=Api(),
        width=1366,
        height=820,
        min_size=(1100, 650),
        maximized=True,   # v0.10: abre ocupando toda la pantalla (width/height solo cuentan al restaurar la ventana)
        text_select=True,
    )
    webview.start(debug=debug)


if __name__ == "__main__":
    main()

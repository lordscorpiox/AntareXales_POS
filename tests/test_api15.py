# v0.9 - contacto (Telegram / correo), créditos y enlaces externos permitidos
import os, sys, tempfile
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import database as db
def must(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: sys.exit(1)
tmp = tempfile.mkdtemp(); db.get_db_path = lambda: os.path.join(tmp, "t.db")
import server, version as ver, main; db.init_db(); c = server.app.test_client()
j = c.get("/api/ping").get_json()["data"]
must(ver.VERSION == j["version"], "la versión del servidor coincide con version.py")
must(j["autor"] == "Lord Scorpiox" and j["creditos"] == "Desarrollado por Lord Scorpiox" and "Claude" not in j["creditos"], "crédito: «Desarrollado por Lord Scorpiox» (sin «& Claude AI»)")
must(j["powered_by"] == "Powered by Claude AI", "línea «Powered by Claude AI»")
ct = j["contacto"]
must(ct["telegram"] == "@lordscorpiox" and ct["telegram_url"] == "https://t.me/lordscorpiox", "Telegram @lordscorpiox → https://t.me/lordscorpiox")
must(ct["correo"] == "lord.scorpiox@gmail.com" and ct["correo_url"] == "mailto:lord.scorpiox@gmail.com", "correo nuevo con enlace mailto")
must("whatsapp" not in ct and "04248556438" not in str(j) and "scorpiox22" not in str(j), "ya no aparece el WhatsApp ni el correo anterior")
import webbrowser; abiertos = []; webbrowser.open = lambda u: abiertos.append(u) or True
api = main.Api()
must(api.abrir_externo(ct["telegram_url"]) == {"ok": True} and api.abrir_externo(ct["correo_url"]) == {"ok": True} and abiertos == [ct["telegram_url"], ct["correo_url"]], "abrir_externo abre Telegram y el correo")
for malo in ("https://evil.example.com", "file:///C:/Windows/System32/cmd.exe", "javascript:alert(1)", "https://t.me/otro", "mailto:otro@x.com", "https://t.me/lordscorpiox?x=1", ""):
    r = api.abrir_externo(malo); must(r["ok"] is False, f"abrir_externo rechaza {malo!r}")
must(len(abiertos) == 2, "los enlaces no permitidos no se abren")
html = open(os.path.join(RAIZ, "frontend", "index.html"), encoding="utf-8").read()
must("04248556438" not in html and "scorpiox22" not in html and "Whatsapp" not in html, "la interfaz ya no menciona el WhatsApp ni el correo anterior")
print("\nTODAS LAS PRUEBAS v0.9 DE API PASARON")
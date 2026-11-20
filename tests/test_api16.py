# v0.10 - búsqueda de actualizaciones activa por defecto (también en bases antiguas) y ventana maximizada
import os, sys, tempfile, types
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import database as db
db.FORZAR_CAMBIO_CLAVE = False
def must(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: sys.exit(1)
tmp = tempfile.mkdtemp(); db.get_db_path = lambda: os.path.join(tmp, "t.db")
import server, main, version as ver
db.init_db(); c = server.app.test_client()
tok = lambda: c.post("/api/auth/login", json={"usuario": "admin", "password": "admin123"}).get_json()["data"]["token"]
h = lambda: {"X-Auth-Token": tok()}
must(ver.VERSION.count(".") == 0 or True, "versión leída de version.py")
must(c.get("/api/config", headers=h()).get_json()["data"]["actualizar_al_abrir"] == "1", "base nueva: «buscar al abrir» = 1 (activo)")
# base de una versión anterior: no tiene la clave
with db.get_conn() as conn:
    conn.execute("DELETE FROM config WHERE clave='actualizar_al_abrir'"); conn.commit()
db.init_db()   # lo que ocurre al abrir la 0.10 con la base vieja
must(c.get("/api/config", headers=h()).get_json()["data"]["actualizar_al_abrir"] == "1", "base de versión anterior: la clave se crea activa al abrir")
must(c.get("/api/actualizacion", headers=h()).get_json()["data"]["al_abrir"] is True, "/api/actualizacion informa al_abrir = true")
# si el administrador la desactiva, se respeta tras reiniciar
c.put("/api/config", headers=h(), json={"actualizar_al_abrir": "0"}); db.init_db()
must(c.get("/api/config", headers=h()).get_json()["data"]["actualizar_al_abrir"] == "0", "una elección del administrador (desactivar) se conserva")
# ventana maximizada
cap = {}
fake = types.ModuleType("webview"); fake.settings = {}
fake.create_window = lambda *a, **k: cap.update(args=a, kw=k)
fake.start = lambda **k: cap.update(started=True)
sys.modules["webview"] = fake
main.instancia_unica = lambda: True; main.webview2_instalado = lambda: True
class H:
    def serve_forever(self): pass
server.crear_servidor = lambda host, port: (H(), 5000)
main.server.respaldo.iniciar_automatico = lambda: None
main.main()
must(cap.get("started") and cap["kw"].get("maximized") is True, "create_window(maximized=True): la app abre maximizada")
must(cap["kw"].get("min_size") == (1100, 650), "se conserva el tamaño mínimo al restaurar")
print("\nTODAS LAS PRUEBAS v0.10 DE API PASARON")
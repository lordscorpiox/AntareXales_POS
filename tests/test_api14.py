# v0.8 - actualizaciones automáticas (GitHub simulado)
import io, json, os, sys, tempfile, hashlib, urllib.error
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import database as db
db.FORZAR_CAMBIO_CLAVE = False
def must(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: sys.exit(1)
tmp = tempfile.mkdtemp(); db.get_db_path = lambda: os.path.join(tmp, "t.db")
import server, actualizador as act, version as ver; db.init_db(); c = server.app.test_client()
def call(m, u, tok=None, **kw):
    r = getattr(c, m)(u, headers={"X-Auth-Token": tok} if tok else {}, **kw); j = r.get_json(silent=True) or {}
    return r.status_code, j.get("data"), j.get("error")
T = call("post", "/api/auth/login", json={"usuario": "admin", "password": "admin123"})[1]["token"]
call("post", "/api/usuarios", T, json={"usuario": "caja1", "nombre": "c", "password": "clave123", "rol": "cajero"})
TC = call("post", "/api/auth/login", json={"usuario": "caja1", "password": "clave123"})[1]["token"]

ver.VERSION = "0.8"   # la prueba simula una instalación 0.8 que busca la 0.9
must(ver.GITHUB_REPO == "lordscorpiox/AntareXales_POS", "repositorio configurado")
must(act.tupla_version("v0.10") == (0, 10) and act.es_mayor("v0.10", "0.9") and act.es_mayor("1.0", "0.9") and not act.es_mayor("v0.8", "0.8") and not act.es_mayor("0.7", "0.8") and act.es_mayor("0.8.1", "0.8") and not act.es_mayor("x", "0.8"), "comparación de versiones (0.10 > 0.9, 0.8.1 > 0.8, iguales no)")

EXE = b"MZ" + os.urandom(5000)
SHA = hashlib.sha256(EXE).hexdigest()
BASE = f"https://github.com/{ver.GITHUB_REPO}/releases/download/v0.9/"
def release(tag="v0.9", assets=True):
    a = [{"name": "AntareXales_POS.exe", "browser_download_url": BASE + "AntareXales_POS.exe", "size": len(EXE)},
         {"name": "AntareXales_POS.exe.sha256", "browser_download_url": BASE + "AntareXales_POS.exe.sha256", "size": 90}] if assets else []
    return {"tag_name": tag, "body": "- cosas nuevas", "html_url": f"https://github.com/{ver.GITHUB_REPO}/releases/tag/{tag}", "published_at": "2026-12-01T10:00:00Z", "assets": a}
class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
ESTADO = {"rel": release(), "exe": EXE, "sha": f"{SHA}  AntareXales_POS.exe\n".encode(), "err": None, "urls": []}
def falso(url, timeout=10):
    ESTADO["urls"].append(url)
    if ESTADO["err"]: raise ESTADO["err"]
    if url.startswith("https://api.github.com/"): return R(json.dumps(ESTADO["rel"]).encode())
    if url.endswith(".sha256"): return R(ESTADO["sha"])
    return R(ESTADO["exe"])
act._abrir = falso

print("--- consulta")
must(call("post", "/api/actualizacion/buscar")[0] == 401, "sin sesión: 401")
must(call("post", "/api/actualizacion/buscar", TC)[0] == 403 and call("get", "/api/actualizacion", TC)[0] == 403 and call("post", "/api/actualizacion/instalar", TC)[0] == 403, "el cajero no puede buscar ni instalar")
s, r, e = call("post", "/api/actualizacion/buscar", T)
must(s == 200 and r["hay_nueva"] and r["ultima"] == "0.9" and r["actual"] == "0.8" and r["notas"] == "- cosas nuevas" and r["tamano"] == len(EXE), f"detecta la 0.9 nueva: {r}")
must(r["puede_instalar"] is False and r["instalable_en_este_modo"] is False, "en modo desarrollo solo informa (no instalable)")
must(ESTADO["urls"][0] == f"https://api.github.com/repos/{ver.GITHUB_REPO}/releases/latest", "consulta el release más reciente del repositorio")
st = call("get", "/api/actualizacion", T)[1]
must(st["al_abrir"] is True and st["ultima_revision"] and st["actual"] == "0.8", "estado: buscar al abrir activo por defecto y fecha de la última búsqueda")
ESTADO["rel"] = release("v0.8"); r = call("post", "/api/actualizacion/buscar", T)[1]; must(r["hay_nueva"] is False, "misma versión: sin novedad")
ESTADO["rel"] = release("v0.7"); r = call("post", "/api/actualizacion/buscar", T)[1]; must(r["hay_nueva"] is False, "versión anterior publicada: sin novedad")
ESTADO["rel"] = release("nightly"); s, r, e = call("post", "/api/actualizacion/buscar", T); must(s == 502, "tag sin número: error controlado")
ESTADO["rel"] = release()
for exc, cod, txt in [(urllib.error.URLError("sin red"), 502, "Internet"), 
                      (urllib.error.HTTPError("u", 403, "rl", {}, None), 503, "limitó"), (TimeoutError(), 502, "Internet")]:
    ESTADO["err"] = exc; s, r, e = call("post", "/api/actualizacion/buscar", T); must(s == cod and txt in e, f"error de red → {cod} «{e}»")
ESTADO["err"] = urllib.error.HTTPError("u", 404, "nf", {}, None)
s, r, e = call("post", "/api/actualizacion/buscar", T); must(s == 200 and r.get("sin_versiones") and r["hay_nueva"] is False, "sin Releases publicados (404): no es un error, solo «sin novedad»")
ESTADO["err"] = None
s, cfg, e = call("put", "/api/config", T, json={"actualizar_al_abrir": "0"}); must(s == 200 and call("get", "/api/actualizacion", T)[1]["al_abrir"] is False, "se puede desactivar «buscar al abrir»")
call("put", "/api/config", T, json={"actualizar_al_abrir": "1"})

print("--- instalación (simulada)")
must(call("post", "/api/actualizacion/instalar", T)[0] == 400, "en modo desarrollo no instala")
carp = tempfile.mkdtemp(); exe_falso = os.path.join(carp, "AntareXales_POS.exe"); open(exe_falso, "wb").write(b"MZviejo")
LAN = []; act.puede_instalar = lambda: True; act._ruta_exe = lambda: exe_falso; act._lanzar_reemplazo = lambda bat: LAN.append(bat)
class T0:
    def __init__(self, *a): pass
    def start(self): LAN.append("salir-programado")
act.threading.Timer = T0
s, r, e = call("post", "/api/actualizacion/instalar", T)
must(s == 200 and r["version"] == "0.9", f"instalación aceptada: {r} {e}")
nuevo = os.path.join(carp, "AntareXales_POS.nueva.exe")
must(open(nuevo, "rb").read() == EXE, "el .exe nuevo quedó descargado junto al actual, idéntico byte a byte")
must(LAN[0].endswith("antarexales_actualizar.bat") and LAN[1] == "salir-programado", "se lanza el script de reemplazo y se programa el cierre")
bat = open(LAN[0], encoding="utf-8", newline="").read()
must(f'set "VIEJO={exe_falso}"' in bat and f'set "NUEVO={nuevo}"' in bat and 'move /y "%NUEVO%" "%VIEJO%"' in bat and 'start "" "%VIEJO%"' in bat and bat.count("\r\n") == bat.count("\n"), "el script mueve el nuevo sobre el viejo y lo reabre (CRLF)")
must(any(x["tipo"] == "manual" for x in __import__("respaldo").listar()), "se hizo un respaldo de la base antes de actualizar")
must(open(exe_falso, "rb").read() == b"MZviejo", "el .exe en uso no se toca (lo reemplaza el script al cerrarse)")
os.remove(nuevo); LAN.clear()

print("--- seguridad de la descarga")
ESTADO["sha"] = (("0" * 64) + "\n").encode(); s, r, e = call("post", "/api/actualizacion/instalar", T)
must(s == 502 and "SHA-256" in e and not os.path.exists(nuevo) and not LAN, "huella distinta → no instala y borra el archivo")
ESTADO["sha"] = b"basura"; s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 502 and not LAN, "huella ilegible → no instala")
bad = b"NOEXE" + os.urandom(3000); ESTADO["exe"] = bad; ESTADO["sha"] = hashlib.sha256(bad).hexdigest().encode()
s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 502 and "ejecutable" in e and not os.path.exists(nuevo) and not LAN, "archivo que no es .exe (sin «MZ») → rechazado")
ESTADO["exe"] = EXE; ESTADO["sha"] = SHA.encode()
ESTADO["rel"]["assets"][0]["browser_download_url"] = "https://evil.example.com/AntareXales_POS.exe"
s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 400 and "no permitida" in e and not LAN, "URL de otro dominio → rechazada")
ESTADO["rel"]["assets"][0]["browser_download_url"] = f"https://github.com/otro/repo/releases/download/v0.9/AntareXales_POS.exe"
s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 400 and not LAN, "URL de otro repositorio → rechazada")
ESTADO["rel"] = release(assets=False); s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 502 and "huella" in e, "release sin .exe/.sha256 → no instala")
ESTADO["rel"] = release("v0.8"); s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 409, "ya está al día → 409")
ESTADO["rel"] = release(); ESTADO["err"] = urllib.error.URLError("corte")
s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 502, "sin conexión durante la instalación → error controlado")
ESTADO["err"] = None
act.MAX_EXE = 1000; s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 400 and "grande" in e and not os.path.exists(nuevo), "tope de tamaño de descarga")
act.MAX_EXE = 300 * 1024 * 1024
os.chmod(carp, 0o500)
if os.geteuid() != 0:
    s, r, e = call("post", "/api/actualizacion/instalar", T); must(s == 403, "carpeta sin permiso de escritura → mensaje claro")
os.chmod(carp, 0o700)
open(nuevo, "wb").write(b"resto"); db.is_frozen = lambda: True; act.limpiar_restos(); must(not os.path.exists(nuevo), "al arrancar se limpian restos de descargas a medias")
print("\nTODAS LAS PRUEBAS v0.8 DE API PASARON")
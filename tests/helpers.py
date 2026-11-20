"""Arnés común de las pruebas: base de datos temporal, cliente de Flask y sesión de administrador."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import database as db  # noqa: E402

db.FORZAR_CAMBIO_CLAVE = False
DB_PATH = os.path.join(tempfile.mkdtemp(prefix="axpos_"), "test.db")
db.get_db_path = lambda: DB_PATH
import server  # noqa: E402

db.init_db()
c = server.app.test_client()
_tok = {}


def login(usuario="admin", password="admin123"):
    r = c.post("/api/auth/login", json={"usuario": usuario, "password": password}).get_json()
    assert r["ok"], r
    _tok[usuario] = r["data"]["token"]
    return _tok[usuario]


def H(usuario="admin"):
    return {"X-Auth-Token": _tok.get(usuario) or login(usuario)}


def call(metodo, url, json=None, usuario="admin", esperar=None):
    r = getattr(c, metodo)(url, json=json, headers=H(usuario))
    j = r.get_json()
    if esperar is not None:
        assert r.status_code == esperar, (url, r.status_code, j)
    return r.status_code, j


def GET(url, **k):
    return call("get", url, **k)


def POST(url, json=None, **k):
    return call("post", url, json, **k)


def PUT(url, json=None, **k):
    return call("put", url, json, **k)


def ok(res):
    st, j = res
    assert st < 400 and j["ok"], (st, j)
    return j["data"]


def err(res, texto=None):
    st, j = res
    assert st >= 400 and not j["ok"], (st, j)
    if texto:
        assert texto.lower() in j["error"].lower(), (texto, j["error"])
    return j["error"]


class Contador:
    def __init__(self):
        self.n = 0

    def __call__(self, cond, msg=""):
        self.n += 1
        assert cond, msg

"""Servidor de pruebas E2E: base temporal, sin cambio forzado de clave. Uso: python run_server.py PUERTO"""
import sys
from helpers import server, db  # noqa: F401  (prepara la base temporal)

if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    httpd, p = server.crear_servidor("127.0.0.1", puerto)
    print("listo", p, flush=True)
    httpd.serve_forever()

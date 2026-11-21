"""E2E v0.12: nota de crédito y de débito desde Documentos, anulación con motivo y pestaña Bitácora."""
import json, os, subprocess, sys, urllib.request
from playwright.sync_api import sync_playwright

PORT = 5074
AQUI = os.path.dirname(os.path.abspath(__file__))
srv = subprocess.Popen([sys.executable, os.path.join(AQUI, "run_server.py"), str(PORT)], stdout=subprocess.PIPE)
srv.stdout.readline()
URL = f"http://127.0.0.1:{PORT}"
errores = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1366, "height": 900})
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.goto(URL + "/"); pg.wait_for_selector("#login", state="visible")
        pg.fill('#login [name=usuario]', "admin"); pg.fill('#login [name=password]', "admin123"); pg.click('#login button[type=submit]')
        pg.wait_for_selector("#login", state="hidden", timeout=15000); pg.wait_for_timeout(2500)
        tok = pg.evaluate("S.token")

        def api(m, u, body=None):
            r = urllib.request.Request(URL + u, method=m, data=json.dumps(body).encode() if body is not None else None,
                                       headers={"X-Auth-Token": tok, "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r).read())["data"]
        api("PUT", "/api/config", {"tasa_bcv": "50", "iva_activo": "1", "iva_pct": "16", "iva_modo": "adicional"})
        a = api("POST", "/api/productos", {"nombre": "Arroz", "precio_usd": 10, "stock": 50, "costo_usd": 1})
        v = api("POST", "/api/ventas", {"items": [{"producto_id": a["id"], "cantidad": 3}], "pagos": [{"metodo": "efectivo_usd", "monto": 34.8}]})
        f = api("POST", "/api/documentos", {"tipo": "factura", "venta_id": v["id"]})
        pg.reload(); pg.wait_for_timeout(2500)
        pg.click('[data-act=ir][data-v=documentos]'); pg.wait_for_selector("#dBody tr")
        assert pg.locator('#dBody [data-act=notaCredito]').count() == 1
        # ---- nota de crédito
        pg.click('#dBody [data-act=notaCredito]'); pg.wait_for_selector("#modalAuth.open")
        pg.fill('#authExtra [data-q]', "1"); pg.fill('#authExtra [data-x=motivo]', "Devolución de 1 unidad")
        pg.screenshot(path=os.path.join(AQUI, "shot_nc_form.png"))
        pg.click("#authSi"); pg.wait_for_selector(".doc", timeout=8000)
        d = pg.inner_text(".doc")
        assert "NOTA DE CRÉDITO" in d and "NC-000001" in d and "Modifica la factura" in d and "Devolución de 1 unidad" in d, d
        pg.screenshot(path=os.path.join(AQUI, "shot_nc_doc.png"))
        assert api("GET", f"/api/productos/{a['id']}")["stock"] == 48, "devolvió 1 al inventario"
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        # ---- nota de débito
        pg.click('#dBody tr:has-text("Factura") [data-act=notaDebito]'); pg.wait_for_selector("#modalAuth.open")
        pg.fill('#ndFilas [data-nd=descripcion]', "Flete"); pg.fill('#ndFilas [data-nd=monto_usd]', "5"); pg.fill('#authExtra [data-x=motivo]', "Envío")
        pg.click("#authSi"); pg.wait_for_selector(".doc", timeout=8000)
        d = pg.inner_text(".doc"); assert "NOTA DE DÉBITO" in d and "Flete" in d and "ND-000001" in d, d
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        pg.select_option("#dTipo", "nota_credito"); pg.fill("#dSearch", ""); pg.keyboard.press("Enter"); pg.wait_for_timeout(800)
        assert pg.locator("#dBody tr").count() == 1
        # ---- anular venta: pide motivo y queda en la bitácora
        v2 = api("POST", "/api/ventas", {"items": [{"producto_id": a["id"], "cantidad": 1}], "pagos": [{"metodo": "efectivo_usd", "monto": 11.6}]})
        pg.click('[data-act=ir][data-v=historial]'); pg.wait_for_selector("#hBody tr", timeout=8000)
        pg.click(f'#hBody [data-act=anular][data-id="{v2["id"]}"]'); pg.wait_for_selector("#modalAuth.open")
        pg.fill('#authExtra [data-x=motivo]', "Prueba de anulación"); pg.click("#authSi"); pg.wait_for_timeout(1000)
        # ---- bitácora
        pg.click('[data-act=ir][data-v=config]'); pg.click('#nav [data-v=bitacora]'); pg.wait_for_selector("#bitBody tr")
        t = pg.inner_text("#bitBody")
        assert "anulación de venta" in t and "Prueba de anulación" in t and "emisión de documento" in t, t
        assert pg.locator("#cfgPie").is_hidden()
        pg.fill("#bQ", "anulación"); pg.press("#bQ", "Enter"); pg.wait_for_timeout(600)
        assert pg.locator("#bitBody tr").count() >= 1 and "emisión" not in pg.inner_text("#bitBody")
        pg.screenshot(path=os.path.join(AQUI, "shot_bitacora.png"))
        assert not errores, errores
        print("e2e_v012 OK"); b.close()
finally:
    srv.terminate()

"""E2E del modo fiscal con Playwright: configuración guiada, producto con alícuota, cobro con IGTF, ticket y factura."""
import subprocess, sys, time, os, urllib.request
from playwright.sync_api import sync_playwright

PORT = 5073
AQUI = os.path.dirname(os.path.abspath(__file__))
srv = subprocess.Popen([sys.executable, os.path.join(AQUI, "run_server.py"), str(PORT)], stdout=subprocess.PIPE)
srv.stdout.readline()
URL = f"http://127.0.0.1:{PORT}/"
errores = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1366, "height": 900})
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(URL); pg.wait_for_selector("#login", state="visible")
        pg.fill('#login [name=usuario]', "admin"); pg.fill('#login [name=password]', "admin123"); pg.click('#login button[type=submit]')
        pg.wait_for_selector("#login", state="hidden", timeout=15000)
        pg.wait_for_timeout(2500)
        # --- por defecto: sin IGTF ni fiscal
        pg.click('[data-act=ir][data-v=config]'); pg.wait_for_selector("#v-cfg-general.on")
        pg.click('#nav [data-v=cfg-fiscal]')
        assert pg.locator("#fiscalPasos .fpaso").count() >= 5
        assert pg.locator("#fiscalPasos .fpaso.no").count() >= 3, "debe mostrar pendientes"
        pg.fill('[name=tasa_bcv]', "50") if pg.locator('[name=tasa_bcv]').is_visible() else None
        # activar sin requisitos → error
        pg.check('[name=fiscal_activo]'); pg.click('#v-cfg-fiscal button[type=submit]')
        pg.wait_for_timeout(800)
        assert "Falta" in pg.inner_text("#toasts, .toast, body"), "debe rechazar la activación"
        pg.uncheck('[name=fiscal_activo]')
        # completar requisitos
        pg.click('#nav [data-v=cfg-general]')
        pg.fill('[name=negocio_nombre]', "Prueba"); pg.fill('[name=rif]', "J-07013380-5"); pg.fill('[name=direccion]', "Av. Guayana")
        pg.click('#nav [data-v=cfg-impuestos]'); pg.check('[name=iva_activo]'); pg.select_option('[name=iva_modo]', "adicional")
        pg.click('#nav [data-v=cfg-fiscal]'); pg.fill('[name=fiscal_razon_social]', "Prueba C.A.")
        pg.check('[name=igtf_activo]')
        pg.check('[name=fiscal_activo]')
        pg.click('#v-cfg-fiscal button[type=submit]'); pg.wait_for_timeout(1200)
        pg.click('#nav [data-v=cfg-fiscal]')
        assert pg.locator("#fiscalPasos .fpaso.no").count() == 0, pg.inner_text("#fiscalPasos")
        assert "Fiscal ●" in pg.inner_text("#nav [data-v=cfg-fiscal]")
        pg.screenshot(path=os.path.join(AQUI, "shot_fiscal_cfg.png"))
        # --- crear productos por API con la sesión de la página
        tok = pg.evaluate("S.token")
        def api(m, u, body=None):
            import json
            r = urllib.request.Request(URL.rstrip("/") + u, method=m, data=json.dumps(body).encode() if body is not None else None,
                                       headers={"X-Auth-Token": tok, "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r).read())["data"]
        api("PUT", "/api/config", {"tasa_bcv": "50"})
        a = api("POST", "/api/productos", {"nombre": "Arroz", "precio_usd": 10, "stock": 50, "iva_alicuota": "general"})
        c = api("POST", "/api/productos", {"nombre": "Pan", "precio_usd": 10, "stock": 50, "iva_alicuota": "reducida"})
        pg.reload(); pg.wait_for_selector("#v-ventas", state="attached"); pg.wait_for_timeout(2500)
        pg.click('[data-act=ir][data-v=ventas]')
        pg.wait_for_selector("#vGrid .prod, #vGrid [data-id]", timeout=8000)
        pg.locator("#vGrid", has_text="Arroz").locator("text=Arroz").first.click()
        pg.wait_for_timeout(300)
        # total: 10 + 16% = 11,60
        assert "11,60" in pg.inner_text("#vTotUsd"), pg.inner_text("#vTotUsd")
        pg.locator("#vGrid", has_text="Pan").locator("text=Pan").first.click(); pg.wait_for_timeout(300)
        fis = pg.inner_text("#vFiscal")
        assert "IVA 16,00%" in fis and "IVA 8,00%" in fis, fis   # dos alícuotas
        assert "22,40" in pg.inner_text("#vTotUsd"), pg.inner_text("#vTotUsd")   # 11.60 + 10.80
        # pago en efectivo USD: debe proponer el total con IGTF (22,40 × 1,03 = 23,07)
        pg.click('[data-act=addPago][data-m=efectivo_usd]') if pg.locator('[data-act=addPago][data-m=efectivo_usd]').count() else pg.click('text=Efectivo USD')
        pg.wait_for_timeout(300)
        val = pg.input_value('[data-pago="0"]')
        assert abs(float(val) - 23.07) < 0.011, val
        assert "IGTF 3,00%" in pg.inner_text("#vIgtf"), pg.inner_text("#vIgtf")
        assert "Pagado completo" in pg.inner_text("#vResumen"), pg.inner_text("#vResumen")
        pg.screenshot(path=os.path.join(AQUI, "shot_fiscal_cobro.png"))
        pg.click("#btnCobrar"); pg.wait_for_selector(".ticket", timeout=8000)
        t = pg.inner_text(".ticket")
        assert "IGTF 3,00%" in t and "TOTAL A PAGAR" in t and "IVA 8,00%" in t and "IVA 16,00%" in t, t
        # factura con control
        pg.click('[data-act=docFactura]'); pg.wait_for_selector('[data-form=documento]')
        pg.fill('[data-form=documento] [name=control]', "00-000777")
        pg.click('[data-form=documento] button[type=submit]'); pg.wait_for_selector(".doc", timeout=8000)
        d = pg.inner_text(".doc")
        assert "00-000777" in d and "J-07013380-5" in d and "Prueba C.A." in d and "IGTF" in d and "TOTAL A PAGAR" in d, d
        pg.screenshot(path=os.path.join(AQUI, "shot_fiscal_factura.png"))
        assert not errores, errores
        print("e2e_fiscal OK")
        b.close()
finally:
    srv.terminate()

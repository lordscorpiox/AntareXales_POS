"""E2E v0.13: libros de ventas/compras, contingencia con completar control, alertas y respaldo externo."""
import json, os, subprocess, sys, tempfile, urllib.request
from playwright.sync_api import sync_playwright

PORT = 5075
AQUI = os.path.dirname(os.path.abspath(__file__))
srv = subprocess.Popen([sys.executable, os.path.join(AQUI, "run_server.py"), str(PORT)], stdout=subprocess.PIPE)
srv.stdout.readline()
URL = f"http://127.0.0.1:{PORT}"
errores = []
def ok(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: errores.append(m)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1366, "height": 900})
        pg.on("pageerror", lambda e: errores.append("PAGEERROR " + str(e)))
        pg.goto(URL + "/"); pg.wait_for_selector("#login", state="visible")
        pg.fill('#login [name=usuario]', "admin"); pg.fill('#login [name=password]', "admin123"); pg.click('#login button[type=submit]')
        pg.wait_for_selector("#login", state="hidden", timeout=15000); pg.wait_for_timeout(2000)
        tok = pg.evaluate("S.token")
        def api(m, u, body=None):
            r = urllib.request.Request(URL + u, method=m, data=json.dumps(body).encode() if body is not None else None,
                                       headers={"X-Auth-Token": tok, "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r).read())["data"]
        api("PUT", "/api/config", {"tasa_bcv": "50", "iva_activo": "1", "iva_pct": "16", "iva_modo": "adicional"})
        a = api("POST", "/api/productos", {"nombre": "Arroz", "precio_usd": 10, "stock": 50, "costo_usd": 1})
        v = api("POST", "/api/ventas", {"items": [{"producto_id": a["id"], "cantidad": 3}], "pagos": [{"metodo": "efectivo_usd", "monto": 34.8}]})
        api("POST", "/api/documentos", {"tipo": "factura", "venta_id": v["id"], "control": "00-1"})
        pg.reload(); pg.wait_for_timeout(2500)

        # ---- menú y libro de ventas
        ok(pg.is_visible('#nav [data-v=libros]'), "«Libros fiscales» está en el menú")
        pg.click('#nav [data-v=libros]'); pg.wait_for_selector("#libBody tr")
        t = pg.inner_text("#libBody")
        ok("Factura" in t and "00-1" in t and "34,80" not in t or "Factura" in t, "libro de ventas lista la factura: " + t[:80])
        ok("1.740,00" in pg.inner_text("#libFoot") or "1740" in pg.inner_text("#libFoot").replace(".", ""), "total en Bs del libro: " + pg.inner_text("#libFoot"))
        ok(pg.is_visible("#libResumen") and "16" in pg.inner_text("#libResumen"), "resumen por alícuota")
        pg.screenshot(path=os.path.join(AQUI, "v13_libro_ventas.png"))
        pg.click('[data-act=libroImprimir]'); pg.wait_for_selector("#modalTicket.open .paper")
        ok("LIBRO DE VENTAS" in pg.inner_text("#modalTicketBox"), "impresión del libro")
        pg.keyboard.press("Escape"); pg.click('#modalTicketBox [data-act=cerrarTicket]') if pg.is_visible('#modalTicketBox [data-act=cerrarTicket]') else None
        pg.click('[data-act=libroTipo][data-t=compras]'); pg.wait_for_selector("#libHead th:has-text('Nº factura')")
        ok("Sin movimientos" in pg.inner_text("#libBody"), "libro de compras vacío")

        # ---- compra con datos de factura del proveedor
        pg.click('#nav [data-v=compras]'); pg.wait_for_timeout(400)
        pg.click('[data-act=nuevaCompra]') if pg.locator('[data-act=nuevaCompra]').count() else pg.click('text=Nueva compra')
        pg.wait_for_selector('[data-form=compra]')
        pg.click('[data-form=compra] details summary')
        pg.fill('[data-form=compra] [name=fact_numero]', "F-123"); pg.fill('[data-form=compra] [name=fact_control]', "00-55")
        pg.fill('[data-form=compra] [name=iva_usd]', "1,6".replace(",", "."))
        pg.fill('[data-form=compra] [data-c=prod]', "Arroz"); pg.fill('[data-form=compra] [data-c=cant]', "4"); pg.fill('[data-form=compra] [data-c=costo]', "5")
        pg.click('[data-form=compra] button[type=submit]'); pg.wait_for_function("document.querySelector('#toasts').textContent.includes('registrada')")
        pg.click('#nav [data-v=libros]'); pg.click('[data-act=libroTipo][data-t=compras]'); pg.wait_for_selector("#libBody tr td:has-text('F-123')")
        ok("1.000,00" in pg.inner_text("#libFoot") or "1000" in pg.inner_text("#libFoot").replace(".", ""), "total compras Bs: " + pg.inner_text("#libFoot"))

        # ---- contingencia desde Configuración › Fiscal
        api("PUT", "/api/config", {"fiscal_razon_social": "Comercial Prueba C.A.", "rif": "J-07013380-5", "direccion": "Av. Guayana", "fiscal_activo": "1"})
        pg.reload(); pg.wait_for_timeout(2500)
        pg.click('#nav [data-v=config]'); pg.click('#nav [data-v=cfg-fiscal]')
        pg.check('[name=contingencia_activa]'); pg.fill('[name=contingencia_motivo]', "Sin internet")
        pg.click('#v-cfg-fiscal button[type=submit]'); pg.wait_for_function("document.querySelector('#toasts').textContent.includes('guardada')")
        ok(pg.is_visible("#chipCont"), "chip CONTINGENCIA visible")
        ok(pg.is_visible("#btnAlertas") and "Modo contingencia" in pg.evaluate("S.alertas.map(a=>a.titulo).join('|')"), "campana con aviso")
        v2 = api("POST", "/api/ventas", {"items": [{"producto_id": a["id"], "cantidad": 1}], "pagos": [{"metodo": "efectivo_usd", "monto": 100}]})
        pg.click('#nav [data-v=historial]'); pg.wait_for_timeout(600)
        api("POST", "/api/documentos", {"tipo": "factura", "venta_id": v2["id"]})
        pg.click('#nav [data-v=documentos]'); pg.wait_for_selector("#dBody tr")
        ok("Control pendiente" in pg.inner_text("#dBody"), "documentos marca «Control pendiente»")
        pg.click('#dBody [data-act=completarControl]'); pg.wait_for_selector('[data-form=completarControl]')
        pg.fill('[data-form=completarControl] [name=control]', "00-777"); pg.click('[data-form=completarControl] button[type=submit]')
        pg.wait_for_function("document.querySelector('#dBody').textContent.includes('00-777')")
        ok("Control pendiente" not in pg.inner_text("#dBody"), "control completado")
        pg.click('#btnAlertas'); pg.wait_for_selector("#modalGen.open, .modal.open")
        pg.screenshot(path=os.path.join(AQUI, "v13_alertas.png"))
        pg.click('[data-act=cerrarModal]')

        # ---- respaldo externo
        ext = tempfile.mkdtemp(prefix="axe2e_")
        pg.click('#nav [data-v=config]'); pg.click('#nav [data-v=respaldo]'); pg.wait_for_selector("#reRuta")
        pg.fill("#reRuta", ext); pg.check("#reActivo"); pg.click('[data-act=reProbar]')
        pg.wait_for_function("document.querySelector('#toasts').textContent.includes('se puede escribir')")
        pg.click('[data-act=reGuardar]'); pg.wait_for_function("document.querySelector('#toasts').textContent.includes('Respaldo externo guardado')")
        pg.click('[data-act=reAhora]'); pg.wait_for_selector("#reBody td:has-text('antarexales_ext_')")
        ok("verificada" in pg.inner_text("#reInfo"), "copia externa verificada: " + pg.inner_text("#reInfo"))
        ok(len([f for f in os.listdir(ext) if f.endswith(".db")]) == 1, "archivo creado en la carpeta externa")
        pg.screenshot(path=os.path.join(AQUI, "v13_respaldo.png"), full_page=True)
        pg.set_viewport_size({"width": 1100, "height": 700}); pg.wait_for_timeout(200)
        ok(pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "sin desborde horizontal a 1100 px")
        b.close()
finally:
    srv.terminate()
print("\nFALLAS: %d %s" % (len(errores), errores)); raise SystemExit(1 if errores else 0)

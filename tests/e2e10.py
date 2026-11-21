import os
from playwright.sync_api import sync_playwright
OUT = os.path.dirname(os.path.abspath(__file__)); errs = []
def ok(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c: errs.append(m)
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1366, "height": 820}); pg = ctx.new_page(); pg.set_default_timeout(8000); logs = []
    pg.on("console", lambda m: logs.append(m.text) if m.type == "error" else None); pg.on("pageerror", lambda e: logs.append("PAGEERROR " + str(e)))
    # ---- pantalla inicial
    pg.goto("http://127.0.0.1:5000/"); ok(pg.locator("#splash").count() == 0, "con automatización no se espera la animación")
    pg.goto("http://127.0.0.1:5000/?splash=1")
    ok(pg.is_visible("#splash") and pg.inner_text("#splash .sp-name").replace("\n", "") == "AntareXales POS", "pantalla inicial visible con el nombre completo")
    ok(pg.eval_on_selector("#splash .sp-x", "e=>getComputedStyle(e).color") == "rgb(255, 45, 70)", "la X es roja")
    pg.wait_for_timeout(600)
    ok(float(pg.eval_on_selector("#splash .sp-x", "e=>getComputedStyle(e).opacity")) > 0.9 and float(pg.eval_on_selector("#splash .sp-l", "e=>getComputedStyle(e).opacity")) < 0.1, "primero aparece la X (el resto del nombre todavía no)")
    try: pg.wait_for_function("getComputedStyle(document.querySelector('#splash .sp-x')).textShadow !== 'none'", timeout=3000); brilla = True
    except Exception: brilla = False
    ok(brilla, "la X tiene resplandor (text-shadow)")
    pg.wait_for_timeout(1700)
    ok(float(pg.eval_on_selector("#splash .sp-l", "e=>getComputedStyle(e).opacity")) > 0.95, "después aparece el resto del nombre")
    ok(pg.evaluate("(()=>{const c=document.getElementById('splashRain');const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let az=0;for(let i=0;i<d.length;i+=4*97){if(d[i+2]>150&&d[i]<120)az++}return az})()") > 30, "el fondo tiene lluvia de símbolos en azul")
    pg.wait_for_selector("#splash", state="detached", timeout=9000); ok(pg.locator("#splash").count() == 0, "la pantalla inicial se cierra sola (~4,6 s)")
    ok(pg.is_visible("#login"), "queda la pantalla de inicio de sesión")
    pg.goto("http://127.0.0.1:5000/?splash=1"); pg.wait_for_timeout(500); pg.click("#splash"); pg.wait_for_timeout(1000)
    ok(pg.locator("#splash").count() == 0, "un clic la salta")
    pg.goto("http://127.0.0.1:5000/?splash=1"); pg.wait_for_timeout(500); pg.keyboard.press("Enter"); pg.wait_for_timeout(1000)
    ok(pg.locator("#splash").count() == 0, "una tecla la salta")
    ctx2 = b.new_context(viewport={"width": 1366, "height": 820}, reduced_motion="reduce"); q = ctx2.new_page(); q.goto("http://127.0.0.1:5000/?splash=1"); q.wait_for_timeout(300)
    ok(q.is_visible("#splash .sp-x") and float(q.eval_on_selector("#splash .sp-l", "e=>getComputedStyle(e).opacity")) == 1, "«reducir movimiento»: nombre completo sin animación")
    q.wait_for_timeout(1800); ok(q.locator("#splash").count() == 0, "«reducir movimiento»: se cierra enseguida"); ctx2.close()
    # ---- Configuración en pestañas
    pg.goto("http://127.0.0.1:5000/"); pg.fill("[name=usuario]", "admin"); pg.fill("[name=password]", "admin123"); pg.click("#login button[type=submit]"); pg.wait_for_selector("#v-ventas.on")
    pg.click("#nav [data-v=config]"); pg.wait_for_selector("#v-config.on")
    tabs = pg.eval_on_selector_all("#cfgTabs button", "bs=>bs.map(b=>b.textContent)")
    ok(tabs == ["General", "Ventas e inventario", "Impuestos y documentos", "Fiscal", "Bitácora", "Actualizaciones", "Acerca de"], f"pestañas: {tabs}")
    def visibles(): return pg.eval_on_selector_all("#cfgForm .tabp", "ps=>ps.filter(p=>!p.hidden).map(p=>p.dataset.tabp)")
    ok(visibles() == ["general"] and pg.is_visible("[name=negocio_nombre]") and not pg.is_visible("[name=iva_pct]"), "abre en «General»; lo demás está oculto")
    pg.screenshot(path=OUT + "/v10_cfg_general.png")
    for t, campo in [("ventas", "[name=caja_obligatoria]"), ("impuestos", "[name=iva_activo]"), ("actualizaciones", "[name=actualizar_al_abrir]"), ("acerca", "#v-config .about")]:
        pg.click(f"#cfgTabs [data-t={t}]")
        ok(visibles() == [t] and pg.is_visible(campo) and pg.eval_on_selector(f"#cfgTabs [data-t={t}]", "e=>e.classList.contains('on')"), f"pestaña «{t}»: muestra solo su contenido")
    ok(not pg.is_visible("#cfgPie"), "«Acerca de» no muestra el botón Guardar")
    pg.click("#cfgTabs [data-t=actualizaciones]"); ok(pg.is_checked("[name=actualizar_al_abrir]"), "«Buscar actualizaciones al abrir» está activada por defecto")
    pg.screenshot(path=OUT + "/v10_cfg_upd.png")
    # guardar cambios hechos en varias pestañas con un solo botón
    pg.click("#cfgTabs [data-t=general]"); pg.fill("[name=negocio_nombre]", "Bodega Tabs")
    pg.click("#cfgTabs [data-t=ventas]"); pg.fill("[name=ganancia_default]", "42")
    pg.click("#cfgTabs [data-t=impuestos]"); pg.fill("[name=doc_pie]", "Gracias")
    ok(pg.is_visible("#cfgPie"), "el botón Guardar es visible en las pestañas de ajustes")
    pg.click("#cfgPie button"); pg.wait_for_function("document.querySelector('#toasts').textContent.includes('guardada')")
    cfg = pg.evaluate("""async()=>{const t=JSON.parse(sessionStorage.getItem('mv_sesion')).token;const r=await fetch('/api/config',{headers:{'X-Auth-Token':t}});return (await r.json()).data}""")
    ok(cfg["negocio_nombre"] == "Bodega Tabs" and cfg["ganancia_default"] == "42" and cfg["doc_pie"] == "Gracias" and cfg["actualizar_al_abrir"] == "1", "un solo «Guardar» guarda lo de todas las pestañas")
    pg.click("#nav [data-v=inventario]"); pg.click("#nav [data-v=config]"); ok(visibles() == ["impuestos"], "al volver recuerda la pestaña en la que estaba")
    pg.set_viewport_size({"width": 1100, "height": 700}); pg.wait_for_timeout(200)
    ok(pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "con la ventana mínima las pestañas no desbordan")
    ok(not [l for l in logs if "PAGEERROR" in l], f"sin errores de página: {logs}")
    b.close()
print("\nFALLAS: %d" % len(errs)); raise SystemExit(1 if errs else 0)
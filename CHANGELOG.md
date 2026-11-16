# Historial de versiones · AntareXales POS

La versión vigente está en `version.py` (única fuente: la ventana, el login, el menú lateral y «Acerca de» la leen de ahí).
Cada ajuste sube el número y agrega una entrada aquí.

## 0.7
- **Nuevo método de pago: Biopago** (en Bs). Aparece en Ventas, reportes, historial y cierre de caja; se trata como los demás métodos que no son efectivo (se concilia aparte, no entra al arqueo).
- **Corrección del vuelto en Bs**: una venta de 1.100 Bs (precio fijo en Bs) pagada con 1.500 Bs en efectivo mostraba un vuelto de 397,78 Bs en vez de 400. Causa: el cálculo convertía Bs → USD (redondeado a centavos) → Bs, y con tasas altas un centavo vale varios bolívares. Ahora, cuando todo se paga en Bs, el total, lo que falta y el vuelto se calculan directamente en Bs (exactos); la pantalla, el servidor, el ticket y el arqueo de caja coinciden. Con pagos en USD o mixtos el cálculo sigue en USD como antes.
- El monto sugerido al agregar un método en Bs ahora es lo que falta en Bs (exacto).
- El ticket y el arqueo reconocen el vuelto aunque su equivalente en USD sea menor a un centavo.

## 0.6
Versión de seguridad y robustez, a partir de una auditoría de la 0.5.
- **Permisos del cajero** (ahora también los exige el servidor, no solo la pantalla): el cajero vende, cobra, opera su caja, crea clientes y abona. Ya no puede crear/editar/borrar productos ni categorías, ajustar stock, borrar clientes, ver el kardex ni entrar a Reportes (el menú del cajero queda en 6 secciones). En *Historial* solo ve sus propias ventas.
- **Anular con clave de administrador**: si el cajero anula una venta (o borra un abono) aparece una ventana que pide usuario y contraseña de un administrador; si son incorrectas no se hace nada. Queda registrado quién autorizó («caja1 (autorizó admin)»). El administrador solo confirma.
- **Devolución de efectivo**: al anular una venta de un turno ya cerrado (o sin turno) se puede marcar «devolví el efectivo al cliente»: se registra un *retiro* «Devolución de venta #N» en el turno abierto de quien anula, por el efectivo neto cobrado (cobrado − vuelto), en la moneda correspondiente. Si no hay turno abierto, la anulación no se hace (todo o nada).
- **La tasa BCV la fija el servidor**: las ventas y proformas usan siempre la tasa configurada; si la pantalla tiene otra, el servidor responde «La tasa BCV cambió» y la pantalla se actualiza sola. Si cambia la tasa en pantalla debe pulsar «Guardar» antes de cobrar. Los cajeros ya no pueden cambiarla salvo que el administrador lo permita en Configuración («Permitir que los cajeros cambien la tasa BCV»).
- **Contraseñas**: PBKDF2-HMAC-SHA256 (200.000 vueltas) con sal propia; las claves antiguas siguen valiendo y se actualizan solas al entrar. El administrador que aún tenga la clave inicial `admin123` debe cambiarla al entrar (también en bases migradas). Nuevo botón «🔑 Mi clave» para que cualquier usuario cambie la suya. **Bloqueo**: 5 intentos fallidos seguidos bloquean ese usuario 5 minutos (también al autorizar anulaciones).
- **Respaldo automático**: una copia diaria de la base de datos en la carpeta `respaldos` (junto a la base), con rotación (14 por defecto; configurable, 0 = desactivado). Configuración › Respaldo muestra el último respaldo, permite «Respaldar ahora» (manual, no se borra solo) y descargar cada copia.
- **Rendimiento**: Inventario y Clientes pintan 150 filas con «Mostrar más» (con 5.000 productos tardaba ~4,5 s en abrir). El buscador sigue filtrando todo.
- **Topes**: cualquier monto o cantidad por encima de 1.000.000.000 se rechaza.
- Limpieza: imports y variables sin usar, `except` demasiado amplio.
- Las bases de la versión 0.5 se actualizan solas. Después de actualizar, el administrador deberá elegir una contraseña nueva si seguía con `admin123`.
- Nota: los hallazgos de la auditoría que **no** se incluyeron (índices adicionales, modo WAL de SQLite, token solo por cabecera) quedan para una versión posterior.

## 0.5
- **Cierre de caja (turnos)**. Nueva sección *Caja*: cada cajero abre su turno con un fondo inicial (Bs y USD) y lo cierra contando el efectivo. El sistema calcula el efectivo esperado por moneda = fondo + cobros en efectivo + abonos en efectivo + ingresos − gastos − retiros − vuelto entregado, y muestra el *sobrante/faltante*. Los demás métodos (transferencia, pago móvil, Zelle, tarjeta) se informan como totales para conciliar aparte. Hay arqueo parcial sin cerrar, historial de turnos (el administrador ve todos) y el **reporte de cierre imprimible** (modal interno + `window.print()`), que queda congelado al cerrar. Opcional: Configuración › *Caja obligatoria* (no se puede cobrar sin turno abierto).
- **Gastos, retiros e ingresos de caja** con categoría y concepto; entran en el arqueo y se pueden anular. El vuelto se registra en la moneda en que se entregó (selector en Ventas cuando hay vuelto).
- **Ventas en espera**: «Suspender venta» guarda el carrito (cliente, pagos y notas) con una etiqueta y «En espera» lo recupera con los precios vigentes (máx. 50).
- **Compras y proveedores** (solo administrador): proveedores con saldo; entrada de mercancía con costo, lote y vencimiento por línea; actualización del costo del producto (último costo, promedio ponderado o no cambiarlo) y, opcionalmente, del precio según el % de ganancia; compra de contado o a crédito; **cuentas por pagar** con pagos parciales en USD o Bs (a la tasa del día); pagar desde la caja registra el gasto en el turno abierto; anular una compra revierte el ingreso si la mercancía sigue en el almacén. Reportes suma gastos de caja, compras, por pagar y mermas.
- **Inventario avanzado** (solo administrador):
  - *Varios almacenes* y traslados entre ellos; ventas salen del «almacén de ventas» (Configuración; por defecto el principal). Existencias por almacén y lote.
  - *Lotes y vencimientos* con alerta (días configurables) y venta **FEFO** (primero el lote que vence antes); «Dar de baja» lotes vencidos.
  - *Mermas con motivo* (vencido, dañado, pérdida, consumo propio, otro) valoradas a costo.
  - *Conteo físico* con diferencia en vivo, guardado de avance y aplicación de ajustes al kardex.
  - *Combos* (productos compuestos de otros: descuentan los componentes, disponibilidad y costo calculados; no se anidan) y *variantes talla/color* (genera el producto cartesiano, hasta 200; en Ventas se agrupan en una tarjeta con selector).
  - *Kardex global* con almacén, lote y costo de cada movimiento.
- Las bases de la versión 0.4 se actualizan solas: se crea el almacén «Principal» con el stock actual y las ventas anteriores se pueden anular normalmente. Los respaldos viejos también se migran al restaurarlos.
- Menú: nuevas secciones *Caja* y *Compras* (esta última solo administrador). El cajero ve Caja pero no las pestañas de inventario avanzado ni Compras.
- Pendiente fuera de esta versión: lado fiscal (libro de ventas, factura fiscal), devoluciones parciales, cotizaciones de proveedores.

## 0.4
- **Facturas, proformas y notas de entrega**. La *factura* y la *nota de entrega* se generan desde el ticket de una venta (justo después de cobrar o desde Historial); la *proforma* (presupuesto, no mueve stock ni registra venta) se genera con el botón «Proforma» del carrito. Cada documento tiene su propia numeración (factura 00000001, PF-000001, NE-000001), con los datos del negocio, del cliente (nombre, RIF/C.I., dirección y teléfono, precargados de su ficha y editables) y los montos en Bs y USD a la tasa del momento. Se imprimen en hoja completa con el mismo modal interno + `window.print()`.
- Factura: campo opcional «Nº de control» para quien usa talonario o imprenta autorizada. Proforma: validez en días (7 por defecto) y botón «Al carrito». Nota de entrega: sin precios por defecto (opción para mostrarlos) y con espacio para firmas.
- Nueva sección **Documentos** (lista con filtros por tipo, estado, fechas y búsqueda por cliente, RIF o número; reimprimir; anular —solo administrador—). Un documento anulado no se borra: se imprime con sello ANULADO; al anular una venta se anulan también sus documentos. Una venta tiene una sola factura vigente (volver a pedirla muestra la existente).
- **IVA (desactivado por defecto)**: Configuración › Impuestos (IVA): activarlo, alícuota (16 %) y modo *precios ya incluyen IVA* (el total no cambia y el IVA se discrimina) o *IVA se suma al precio*. Cada producto puede marcarse **Exento de IVA** (también por CSV: columna `exento_iva`). Con el IVA activo, el carrito, el ticket, las facturas y los reportes muestran base imponible, exento e IVA; los reportes suman «IVA cobrado» y la ganancia estimada se calcula sobre la base sin IVA. Con el IVA apagado, todo funciona exactamente como antes.
- Configuración › Documentos: próximo número de cada tipo (para continuar una numeración existente), validez de la proforma y texto al pie.
- **Menú**: *Usuarios* y *Respaldo* pasan a ser submenú de *Configuración* (se despliega al abrirla; el administrador sigue siendo el único que la ve).
- Clientes: nuevo campo Dirección (para facturas). Las bases de la versión 0.3 se actualizan solas; las ventas anteriores se facturan como sin IVA.

## 0.3
- **Imágenes de producto**: en Inventario › Nuevo/Editar producto se puede elegir una imagen (JPG, PNG o WEBP) con vista previa y botón «Quitar imagen». La app la recorta y reduce sola a 400×300 antes de guardarla, así la base de datos casi no crece (≈ 20–40 KB por foto).
- **Pantalla de Ventas**: las tarjetas muestran la foto del producto; los que no tienen foto muestran un monograma (iniciales) sobre un degradado rojo/azul, para que la cuadrícula se vea uniforme. El carrito y la tabla de Inventario muestran una miniatura.
- Botón 🖼 junto al buscador de Ventas: alterna entre la cuadrícula con fotos y la vista compacta sin fotos (la preferencia se recuerda).
- Las imágenes viajan dentro de la base de datos, por lo que el respaldo/restauración las incluye. Una foto que falla al cargar se reemplaza por el monograma. Editar un producto sin tocar la foto la conserva; eliminarlo borra su imagen.
- Las bases de la versión 0.2 se actualizan solas (columna `imagen_ver` y tabla `producto_imagenes`).

## 0.2
- **Contacto del desarrollador**: «Lord Scorpiox» (en el login, el menú lateral y Configuración › Acerca de) ahora es clicable y abre una ventana con el logo y la *Información de Contacto* (WhatsApp 04248556438, correo scorpiox22@gmail.com, con botón Copiar). Se cierra con «Cerrar», Escape o clic fuera.
- **Productos por Kg o por Litro**: cada producto se vende por *Unidad*, *Kg* o *Litro*. Kg y Litro admiten fracciones (0,5 kg, 0,25 L…, hasta 3 decimales); Unidad exige cantidades enteras. El stock, el ticket, los reportes y los CSV muestran la unidad. En el carrito, +/− avanza de 0,5 en 0,5 para kg/L.
- **Precio fijo en Bs**: «Tipo de precio» del producto: *En dólares* (el Bs sigue la tasa, como antes) o *Fijo en Bs* (el precio en Bs no cambia al cambiar la tasa; su equivalente en USD sí). Los totales en Bs de ventas con precio fijo son exactos.
- **Abonos en bolívares**: al abonar a un crédito se elige si el cliente pagó en USD o en Bs; en Bs se indica el monto y la **tasa de ese día** (precargada con la tasa actual). La deuda baja por `Bs ÷ tasa`; el historial guarda el monto en Bs y la tasa usada.
- CSV: nuevas columnas `unidad` y `precio_fijo_bs` (importación y exportación). Las bases de la versión 0.1 se actualizan solas: los productos existentes quedan como *Unidad* y *en dólares*; los abonos existentes quedan en USD.
- Margen de redondeo a centavos ajustado a 0,0051 USD.

## 0.1
- La aplicación pasa a llamarse **AntareXales POS** (antes MegaVenta). Ejecutable: `AntareXales_POS.exe`.
- Número de versión visible en el login, el menú lateral, Configuración («Acerca de») y el título de la ventana.
- Crédito «Desarrollado por Lord Scorpiox & Claude AI» en el login, el menú lateral y Configuración.
- **Porcentaje de ganancia**: cada producto tiene costo y % de ganancia; el precio de venta se calcula como `costo × (1 + %/100)`. También se puede escribir el precio y el % se ajusta solo. Configuración permite fijar un % por defecto (30 % de fábrica).
- Reportes: nueva tarjeta «Ganancia estimada» (con el costo vigente al momento de cada venta).
- El cajero no ve costos, % de ganancia ni ganancia estimada.
- Carga masiva y exportación CSV con columnas `costo_usd` y `ganancia_pct`; en una actualización, las columnas ausentes conservan el valor actual.
- Nuevo diseño en negro, rojo y azul con pantalla de inicio oscura (el login es oscuro aunque se elija el tema claro). Tema oscuro por defecto; se puede cambiar en Configuración.
- Ícono nuevo (estrella Antares).
- Los avisos emergentes ya no tapan el botón Cobrar.
- Datos: al abrir por primera vez, la base de MegaVenta (`%APPDATA%\MegaVenta\megaventa.db`) se **copia** a `%APPDATA%\AntareXalesPOS\antarexales.db`; la original no se toca. Las bases y respaldos anteriores se migran solos.

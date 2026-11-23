# Historial de versiones · AntareXales POS

La versión vigente está en `version.py` (única fuente: la ventana, el login, el menú lateral y «Acerca de» la leen de ahí).
Cada ajuste sube el número y agrega una entrada aquí.

## 0.13.1
Corrección de la pantalla inicial animada.
- **Nombre más pausado**: las letras ya no aparecen de golpe; entran una a una (con un leve ascenso y enfoque), desde la X hacia los extremos, y luego aparece «Punto de venta». La X roja también entra más despacio. La pantalla dura unos 7 s (un clic o una tecla la salta).
- **Lluvia de símbolos nítida**: los caracteres se veían borrosos porque cada cuadro se dibujaba encima del anterior; ahora se redibuja completo, cada columna lleva su velocidad y una cola que se apaga gradualmente, sin estelas empastadas.

## 0.13
Tercera entrega de la adecuación fiscal y **Configuración homogénea**. El modo fiscal sigue **desactivado por defecto**; todo es configurable.
- **Configuración homogénea**: ya no hay pestañas en una ventana pequeña. Cada sección es un **submenú de Configuración** con su **ventana completa**, igual que el resto de módulos: General · Ventas e inventario · Impuestos y documentos · Fiscal · Usuarios · Respaldo · Bitácora · Actualizaciones · Acerca de. Cada sección de ajustes tiene su botón «Guardar cambios»; al pasar de una sección a otra no se pierde lo editado.
- **Libros fiscales** (menú «Libros fiscales», solo administrador): **libro de ventas** (facturas, notas de crédito —restan— y notas de débito, con control, cliente, exento/base/IVA/IGTF y totales en Bs y USD, resumen por alícuota; los anulados salen en cero) y **libro de compras** (con los datos de la factura del proveedor que se anotan al registrar la compra: Nº de factura, control, fecha, IVA y exento). Filtro por período, **imprimir** (ventana interna) y **exportar a CSV** (para Excel).
- **Contingencia** (Configuración › Fiscal): cuando la imprenta digital o la máquina fiscal no están disponibles se puede facturar **sin número de control**; la factura queda marcada «Control pendiente» y el administrador lo completa después (Documentos o Libros fiscales; una sola vez, queda en la bitácora). El número de control completado ya no se puede cambiar.
- **Respaldo externo** (Configuración › Respaldo): copia **verificada** (comprobación de integridad) de la base de datos en una carpeta fuera de los datos de la aplicación (otro disco, USB o carpeta sincronizada), automática cada día y con cada respaldo, con rotación y botón «Respaldar ahora».
- **Alertas** (campana 🔔 en la barra superior, con avisos «Atención/Urgente»): contingencia activa y documentos sin control, rango de números de control por agotarse o agotado, sin respaldos o respaldo viejo (días configurables), respaldo externo fallido o carpeta desconectada, y sugerencia de respaldo externo en modo fiscal. Se actualizan solas cada 5 minutos.
- Migración automática desde la 0.12 (nuevas columnas y regla del número de control); pruebas nuevas (`test_v013.py`, `e2e_v013.py`).
- Pendiente (opcional): conexión directa con una imprenta digital.

## 0.12
Segunda entrega de la adecuación fiscal: **corregir sin borrar y dejar rastro**. Con el modo fiscal apagado todo funciona como antes (las notas y la bitácora están disponibles siempre).
- **Notas de crédito y de débito** (Documentos › botones en cada factura emitida): una factura emitida no se edita, se corrige con una nota.
  - *Crédito*: acredita total o parcialmente líneas de la factura (a la tasa de la factura, sin diferencias de centavos; lo ya acreditado no se puede repetir). Opciones: devolver la mercancía al inventario (almacén y lote reales de la venta), devolver efectivo USD/Bs como retiro de caja, o reducir la deuda del cliente. Devuelve también la parte proporcional del IGTF cobrado.
  - *Débito*: cargos adicionales (fletes, intereses, ajustes) con su alícuota, a la tasa del día.
  - Numeración propia (NC-000001 / ND-000001, configurable), número de control como en las facturas (manual o serie propia) y copia de los datos del emisor en modo fiscal. Piden clave de administrador. Aparecen en Documentos y en el resumen de Reportes.
- **Facturas inmutables**: en modo fiscal una factura emitida no se anula ni se edita (tampoco su venta); se corrige con notas. Una opción en Fiscal permite anular (exige motivo). Una factura con notas no se puede anular, y las notas no se anulan. La base de datos lo refuerza: no se pueden borrar documentos ni sus líneas, ni modificar los montos de un documento fiscal.
- **Motivo de anulación** de ventas y documentos (obligatorio en modo fiscal).
- **Bitácora de auditoría** (Configuración › Bitácora, solo administrador): registra quién hizo qué y cuándo —inicios de sesión (y fallidos), anulaciones, documentos emitidos, cambios de configuración (valor anterior → nuevo), usuarios, abonos eliminados, ajustes de stock, mermas, conteos, compras anuladas, cierres de caja, restauraciones…—. Solo se agrega: la base de datos impide modificarla o borrarla. Con filtros y paginación.
- Migración automática de la tabla de documentos (conserva todo); pruebas nuevas en `tests/`.
- Pendiente (0.13): libros de ventas/compras, contingencia, respaldo externo y alertas.

## 0.11
Base del **modo fiscal** (primera de tres entregas de adecuación al SENIAT). **Viene desactivado**: con el modo fiscal apagado la aplicación se comporta exactamente como en la 0.10. Todo es configurable por el comercio; los porcentajes y reglas son parámetros que conviene confirmar con su contador.
- **Pestaña Configuración › Fiscal** con un **asistente de activación**: lista qué falta (razón social, RIF válido, domicilio fiscal, IVA activo, número de control) con un botón «Ir» a donde corregirlo. El modo fiscal no se puede activar —ni dejar incompleto— mientras falte algo obligatorio.
- **Validación del RIF** (formato `J-12345678-9` y dígito verificador; el dígito verificador solo avisa, no bloquea).
- **Alícuotas de IVA por producto**: general, reducida, adicional (se suma a la general, p. ej. 16 % + 15 %) y exento. Ticket, carrito y facturas muestran el IVA **desglosado por alícuota**. Los productos «exentos» de versiones anteriores se conservan.
- **IGTF** (pagos en divisas): porcentaje y métodos de pago configurables (Efectivo USD y Zelle por defecto). Se calcula sobre la parte de la factura pagada en divisas, se suma al total a cobrar, y el efectivo y los pagos ya incluyen el impuesto (el arqueo de caja cuadra solo). Ticket y factura lo muestran aparte («TOTAL A PAGAR»).
- **Facturas fiscales**: guardan una copia de los datos del emisor (razón social, RIF, domicilio) tal como estaban al emitir; **número de control** que se escribe al facturar (imprenta digital o máquina fiscal, sin repetidos) o se asigna solo desde una **serie propia** dentro de un rango autorizado; opción de exigir nombre y RIF/cédula del cliente.
- Nuevas **pruebas automáticas** en la carpeta `tests/` (núcleo, fiscal, actualizaciones, navegador) y se ejecutan antes de compilar en GitHub.
- Pendiente para las próximas entregas: facturas inmutables y notas de crédito/débito con bitácora (0.12); libros de ventas/compras, contingencia y respaldo externo (0.13).

## 0.10
- **Pantalla inicial animada** al abrir la aplicación: lluvia de símbolos en azul eléctrico (estilo Matrix) sobre fondo oscuro; aparece primero la **X roja** con resplandor y luego se despliega el resto del nombre «AntareXales POS». Dura unos 4,5 s y se salta con un clic o cualquier tecla. Con «reducir movimiento» activado en Windows se muestra el nombre fijo y se cierra enseguida.
- **Configuración en pestañas**: General · Ventas e inventario · Impuestos y documentos · Actualizaciones · Acerca de. Un solo botón «Guardar configuración» guarda lo de todas las pestañas, y la aplicación recuerda la pestaña en la que estaba.
- **Búsqueda de actualizaciones activa por defecto** (también al abrir una base de datos creada con una versión anterior). Si el administrador la desactiva, se respeta su elección.
- **La ventana abre maximizada**, ocupando toda la pantalla (al restaurarla conserva un tamaño cómodo).

## 0.9
- **Contacto**: la ventana del desarrollador ahora muestra **Telegram `@lordscorpiox`** (en lugar del WhatsApp) y el correo **lord.scorpiox@gmail.com**. Al tocarlos se abren fuera de la aplicación: Telegram (aplicación o navegador) y el programa de correo. Cada uno conserva su botón «Copiar». Solo estos dos enlaces pueden abrirse desde la aplicación.
- **Crédito**: ahora dice «Desarrollado por **Lord Scorpiox**» y, debajo, «Powered by Claude AI» (pantalla de inicio, barra lateral y Configuración).
- Corrección en la compilación automática: las notas del Release ya no salen con caracteres dañados.
- Es la primera versión que se puede instalar desde la 0.8 con «Buscar actualizaciones».

## 0.8
- **Actualizaciones automáticas.** La aplicación consulta el último *Release* de GitHub (`lordscorpiox/AntareXales_POS`):
  - **Al abrir** (solo si entra el administrador y la opción está activa) aparece un aviso «Hay una actualización disponible» con la versión y las novedades. Si no hay Internet, no muestra ningún error.
  - **Configuración › Actualizaciones**: botón «Buscar actualizaciones», casilla «Buscar actualizaciones al abrir la aplicación» (activada por defecto) y fecha de la última búsqueda.
  - **Instalar ahora** (solo en el .exe de Windows): descarga el nuevo .exe, verifica su huella **SHA-256** (publicada en el mismo Release), hace un **respaldo de la base de datos**, y la aplicación se cierra y se reabre sola ya actualizada. Si algo falla (descarga cortada, huella distinta, archivo que no es un .exe, carpeta sin permiso de escritura) no se instala nada y el .exe actual queda intacto.
  - Solo se aceptan descargas del repositorio oficial en GitHub. En modo desarrollo (sin .exe) solo informa y permite copiar el enlace.
  - Solo el administrador puede buscar e instalar.
- Para publicar una versión: subir `version.py` y el `CHANGELOG`, crear la etiqueta `vX.Y` en GitHub; la compilación automática genera el Release con `AntareXales_POS.exe` y `AntareXales_POS.exe.sha256`, que es lo que las instalaciones detectan.
- Nota: la 0.7 y anteriores no traen el actualizador; la 0.8 se instala una vez a mano y de ahí en adelante se actualiza sola.

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

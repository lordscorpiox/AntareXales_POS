# AntareXales POS

Sistema POS de escritorio para Windows (Flask + SQLite + PyWebView). Precios en USD y Bs con tasa BCV editable.
Versión actual: ver `version.py` · Historial de cambios: `CHANGELOG.md`

## Modo desarrollo
```
python -m pip install -r requirements.txt
python main.py
```
La base de datos queda en `data\antarexales.db`. Con `python main.py --debug` se habilitan las herramientas de desarrollo del navegador.

## Generar el .exe
Ejecute `build.bat` (usa `python -m PyInstaller`). El resultado es `dist\AntareXales_POS.exe`.
En modo .exe la base de datos queda en `%APPDATA%\AntareXalesPOS\antarexales.db` (junto a `antarexales.log`).

## Si venía de MegaVenta
Al abrir por primera vez, su base de datos anterior (`%APPDATA%\MegaVenta\megaventa.db`) se copia sola a la ubicación nueva. La original queda intacta.

## Cambiar la versión (para quien modifique la app)
Edite `VERSION` en `version.py` y agregue la entrada en `CHANGELOG.md`.

## Requisitos
- Windows 10/11 con **Microsoft Edge WebView2 Runtime** (ya viene en Windows 11 y en Windows 10 actualizado).
- Python 3.13 o superior para desarrollo/compilación.

## Acceso inicial
Usuario `admin` / contraseña `admin123`; al entrar el sistema le pedirá elegir una contraseña propia.
Actualice la **tasa BCV** (campo en Ventas o en Configuración): arranca en 1.

## Precios y ganancia
Al crear o editar un producto escriba el **costo** y el **% de ganancia**: el precio de venta se calcula como `costo × (1 + %/100)`.
También puede escribir el precio directamente y el % se ajusta. El % por defecto se cambia en Configuración.
Solo el administrador ve costos, % y la ganancia estimada de los reportes.

## Unidades, fracciones y precio fijo en Bs
- **Se vende por**: Unidad, Kg o Litro. Kg y Litro permiten fracciones (0,5 kg / 0,5 L). Los productos que ya existían quedan como *Unidad*: si vende alguno por peso o volumen, edítelo y cambie su unidad.
- **Tipo de precio**: *En dólares* (el valor en Bs sigue la tasa) o *Fijo en Bs* (no cambia con la tasa; no requiere reprecio manual).
- **Abonos**: elija si el cliente pagó en USD o en Bs; en Bs indique la tasa de ese día.
- Haga clic en **Lord Scorpiox** (en el crédito) para ver el contacto del desarrollador.

## Imágenes de producto
En **Inventario › Nuevo/Editar producto** use «Elegir imagen…» (JPG, PNG o WEBP). Se recorta a 4:3 y se reduce sola. Aparece en las tarjetas de **Ventas**, en el carrito y en Inventario; sin imagen se muestra un monograma con las iniciales.
El botón 🖼 junto al buscador de Ventas muestra u oculta las fotos (vista compacta). Las imágenes se guardan dentro de la base de datos y viajan en el respaldo.

## IVA, facturas, proformas y notas de entrega
- **IVA**: viene **desactivado**. Actívelo en *Configuración › Impuestos (IVA)* (alícuota 16 %; precios con IVA incluido o IVA adicional). Marque como *Exento* los productos que no lo llevan.
- **Documentos**: la factura y la nota de entrega salen del ticket de una venta; la proforma, del botón *Proforma* del carrito. Todo queda en la sección *Documentos* (reimprimir, anular).
- **Importante**: estos documentos los genera el sistema para uso interno y de gestión. No sustituyen una factura fiscal autorizada por el SENIAT (imprenta o sistema autorizado); si su negocio debe emitir factura fiscal, use el campo «Nº de control» solo con el talonario o sistema que corresponda y consulte a su contador.
- *Usuarios* y *Respaldo* están ahora dentro de *Configuración*.

## Caja, compras e inventario avanzado (v0.5)
- **Caja**: abra su turno con el fondo inicial al empezar; al cerrar, cuente el efectivo (Bs y USD) y el sistema muestra faltante/sobrante e imprime el reporte de cierre. Gastos y retiros de caja entran en el cálculo. *Configuración › Caja obligatoria* exige turno abierto para cobrar.
- **Ventas en espera**: botón *Suspender venta* y *En espera* en la pantalla de Ventas.
- **Compras** (administrador): proveedores, entrada de mercancía con costo/lote/vencimiento, cuentas por pagar y pagos parciales.
- **Inventario** (administrador): pestañas Movimientos (kardex), Vencimientos, Conteo físico, Mermas y Almacenes; botones *＋Variantes* y *＋Combo*. Las ventas salen del «almacén de ventas» elegido en Configuración y consumen primero el lote que vence antes (FEFO).
- Los datos de versiones anteriores se migran solos al abrir la app; haga un respaldo antes de actualizar.

## Métodos de pago
Efectivo Bs, Efectivo USD, Transferencia, Pago móvil, Zelle, Tarjeta y **Biopago** (v0.7). Solo el efectivo entra al arqueo de caja; los demás se reportan como totales para conciliar. Si todo se paga en Bs, el vuelto se calcula en Bs exactos (v0.7).

## Seguridad y roles (v0.6)
- **Primer inicio**: usuario `admin`, contraseña `admin123`; el sistema obliga a cambiarla al entrar. Cada usuario puede cambiar la suya con «🔑 Mi clave».
- **Cajero**: vende, cobra y opera su caja. No edita inventario, no ve reportes ni compras. Para **anular una venta** necesita la clave de un administrador.
- **La tasa BCV** la fija el administrador (Configuración); puede permitir que los cajeros la cambien.
- **Respaldos automáticos** diarios en la carpeta `respaldos` junto a la base de datos (Configuración › Respaldo). Copie esa carpeta a otra unidad o a la nube de vez en cuando.
- 5 intentos fallidos de contraseña bloquean al usuario 5 minutos.

## Notas
- Las contraseñas se guardan como SHA-256 con una sal propia de cada instalación (viaja dentro del respaldo).
- Anular una venta devuelve el stock y descuenta el crédito del cliente; los productos, clientes y usuarios con historial se archivan en vez de borrarse.
- Restaurar un respaldo guarda antes una copia (`antarexales_antes_de_restaurar.bak`) y cierra todas las sesiones. Los respaldos hechos con MegaVenta se pueden restaurar.
- Los CSV usan «;» como separador y coma decimal (apertura directa en Excel en español).

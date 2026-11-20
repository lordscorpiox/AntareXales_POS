"""Modo fiscal (v0.11): reglas puras, sin Flask ni base de datos (se prueban solas).

Todo es configurable y viene DESACTIVADO por defecto: con «fiscal_activo = 0» la aplicación se comporta como en
versiones anteriores. Aquí viven:
  - alícuotas de IVA por producto (general, reducida, adicional, exento);
  - IGTF (impuesto a las grandes transacciones financieras: % sobre lo pagado en divisas);
  - validación del RIF (formato y dígito verificador);
  - revisión de que el comercio tiene lo necesario para activar el modo fiscal (asistente).

AVISO: la normativa fiscal cambia. Los porcentajes son parámetros que el comercio ajusta; confirme con su contador
o con el SENIAT lo que le corresponde antes de activar el modo fiscal en producción."""
import re

ALICUOTAS = ("general", "reducida", "adicional", "exento")
ALICUOTAS_NOMBRE = {"general": "General", "reducida": "Reducida", "adicional": "Adicional (lujo)", "exento": "Exento / exonerado"}

# Valor numérico de la letra inicial del RIF para el dígito verificador (algoritmo módulo 11 de uso común).
_LETRA_RIF = {"V": 1, "E": 2, "J": 3, "P": 4, "G": 5, "C": 3}
_PESOS_RIF = (3, 2, 7, 6, 5, 4, 3, 2)
RE_RIF = re.compile(r"^([VEJPGC])-?(\d{8})-?(\d)$")
RE_CEDULA_RIF = re.compile(r"^([VEJPGC])-?(\d{5,9})(?:-?(\d))?$")
RE_CONTROL = re.compile(r"^[0-9A-Za-z][0-9A-Za-z\-]{0,39}$")


def r2(x):
    return round(x + (1e-9 if x >= 0 else -1e-9), 2)


def _limpiar(s):
    return re.sub(r"[\s.]", "", str(s or "")).upper()


def validar_rif(valor):
    """Devuelve {'valido': bool, 'normalizado': 'J-12345678-9', 'dv_ok': bool|None, 'mensaje': str}.

    «valido» = formato J-12345678-9 (letra V/E/J/P/G/C + 8 dígitos + dígito verificador). «dv_ok» indica si el dígito
    verificador coincide con el cálculo; es solo informativo porque el algoritmo no se publica en una fuente oficial
    única, por eso no bloquea."""
    s = _limpiar(valor)
    m = RE_RIF.match(s)
    if not m:
        return {"valido": False, "normalizado": s, "dv_ok": None,
                "mensaje": "El RIF debe tener el formato J-12345678-9 (letra, 8 dígitos y dígito verificador)"}
    letra, cuerpo, dv = m.group(1), m.group(2), int(m.group(3))
    total = _LETRA_RIF[letra] * 4 + sum(int(d) * w for d, w in zip(cuerpo, _PESOS_RIF))
    esperado = 11 - (total % 11)
    if esperado >= 10:
        esperado = 0
    return {"valido": True, "normalizado": f"{letra}-{cuerpo}-{dv}", "dv_ok": esperado == dv,
            "mensaje": "" if esperado == dv else "El dígito verificador no coincide: revise el RIF"}


def validar_id_cliente(valor):
    """RIF o cédula del cliente (V12345678, J-12345678-9...). Devuelve el texto normalizado o None si no es válido."""
    s = _limpiar(valor)
    if RE_RIF.match(s) or RE_CEDULA_RIF.match(s):
        return s
    return None


# ----------------------------------------------------------------------------- IVA por producto
def num_cfg(cfg, clave, defecto=0.0):
    try:
        return float(cfg.get(clave) or defecto)
    except (TypeError, ValueError):
        return defecto


def alicuota_producto(p):
    """Alícuota efectiva de un producto. «iva_exento» (v0.4) sigue mandando; sin alícuota guardada vale 'general'."""
    if p.get("iva_exento") or p.get("iva_alicuota") == "exento":
        return "exento"
    a = p.get("iva_alicuota") or ""
    return a if a in ("reducida", "adicional") else "general"


def tasas_iva(cfg):
    """% de IVA de cada alícuota. La adicional se SUMA a la general (p. ej. 16 % + 15 % = 31 % en bienes de lujo)."""
    general = num_cfg(cfg, "iva_pct", 16.0)
    return {"general": general, "reducida": num_cfg(cfg, "iva_pct_reducida", 8.0),
            "adicional": general + num_cfg(cfg, "iva_pct_adicional", 15.0), "exento": 0.0}


# ----------------------------------------------------------------------------- IGTF
def igtf_config(cfg):
    """(porcentaje, conjunto de métodos de pago) o (0.0, set()) si el IGTF no está activo."""
    if cfg.get("fiscal_activo") != "1" or cfg.get("igtf_activo") != "1":
        return 0.0, set()
    pct = num_cfg(cfg, "igtf_pct", 3.0)
    metodos = {m.strip() for m in str(cfg.get("igtf_metodos") or "").split(",") if m.strip()}
    if pct <= 0 or not metodos:
        return 0.0, set()
    return pct, metodos


def calcular_igtf(pct, recibido_divisa, pagado_otros, total):
    """IGTF de una venta. Todo en USD.

    recibido_divisa: lo entregado por el cliente en métodos sujetos a IGTF (incluye el impuesto).
    pagado_otros:    lo entregado en los demás métodos (valorado en USD).
    total:           total de la factura sin IGTF.
    La parte de la factura pagada en divisas es lo que falta tras los otros pagos, con tope en lo entregado en divisas
    sin su impuesto. El impuesto es pct % de esa parte y se cobra además del total.
    Devuelve (base_igtf, igtf, a_cobrar) con a_cobrar = total + igtf."""
    if pct <= 0 or recibido_divisa <= 0:
        return 0.0, 0.0, total
    k = pct / 100.0
    base = min(recibido_divisa / (1.0 + k), max(0.0, total - pagado_otros))
    igtf = r2(base * k)
    return r2(base), igtf, r2(total + igtf)


# ----------------------------------------------------------------------------- asistente de activación
def estado(cfg):
    """Lista de requisitos para activar el modo fiscal. Cada paso: {id, titulo, ok, obligatorio, detalle}."""
    pasos = []

    def paso(id_, titulo, ok, detalle="", obligatorio=True):
        pasos.append({"id": id_, "titulo": titulo, "ok": bool(ok), "obligatorio": obligatorio, "detalle": detalle})

    razon = (cfg.get("fiscal_razon_social") or "").strip()
    paso("razon", "Razón social del comercio", bool(razon), "" if razon else "Escriba el nombre o razón social tal como figura en el RIF")
    rif = validar_rif(cfg.get("rif"))
    paso("rif", "RIF del comercio", rif["valido"], rif["mensaje"] if not rif["valido"] else (rif["mensaje"] or rif["normalizado"]))
    direccion = (cfg.get("direccion") or "").strip()
    paso("domicilio", "Domicilio fiscal", bool(direccion), "" if direccion else "Indique la dirección del comercio (pestaña General)")
    pct = num_cfg(cfg, "iva_pct", 0.0)
    paso("iva", "IVA activo con su alícuota general", cfg.get("iva_activo") == "1" and pct > 0,
         "" if cfg.get("iva_activo") == "1" and pct > 0 else "Active el IVA y fije la alícuota general")
    modo = cfg.get("control_modo") or "manual"
    if modo == "serie":
        sig = int(num_cfg(cfg, "sig_control", 1))
        hasta = int(num_cfg(cfg, "control_hasta", 0))
        ok = sig >= 1 and (hasta == 0 or hasta >= sig)
        paso("control", "Números de control (serie propia)", ok,
             "" if ok else "El próximo número de control debe estar dentro del rango autorizado")
    else:
        paso("control", "Números de control (se escriben al facturar)", True,
             "Cada factura pedirá el número de control que asigne su imprenta digital o máquina fiscal"
             if cfg.get("control_obligatorio", "1") == "1" else "No se exigirá número de control")
    if cfg.get("igtf_activo") == "1":
        pi, metodos = igtf_config({**cfg, "fiscal_activo": "1"})
        paso("igtf", "IGTF (pagos en divisas)", pi > 0, "" if pi > 0 else "Indique el % y al menos un método de pago", False)
    else:
        paso("igtf", "IGTF (pagos en divisas)", True, "No activo: no se cobrará IGTF", False)
    listo = all(p["ok"] for p in pasos if p["obligatorio"])
    return {"activo": cfg.get("fiscal_activo") == "1", "listo": listo, "pasos": pasos}


def formatear_control(cfg, numero):
    prefijo = str(cfg.get("control_prefijo") or "")
    digitos = int(num_cfg(cfg, "control_digitos", 8))
    return f"{prefijo}{int(numero):0{digitos}d}"

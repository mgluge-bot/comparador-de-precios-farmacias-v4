"""
utils.py — Helpers compartidos: logging y normalización de precios.
"""

import logging
import re


def setup_logger(name: str = "scraper", log_file: str = "scraper.log") -> logging.Logger:
    """
    Configura y devuelve un logger que escribe tanto en consola como en archivo.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger  # evita duplicar handlers si se llama varias veces

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler consola (INFO y superiores)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # Handler archivo (DEBUG y superiores — captura todo)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


def limpiar_precio(texto: str) -> float | None:
    """
    Convierte un string de precio argentino a float.

    Ejemplos aceptados:
        "$ 17.003"       → 17003.0
        "$17.003,50"     → 17003.50
        "$21.166,79"     → 21166.79
        "$21.16679"      → 21166.79  (Selma: sin separador de miles, coma omitida)
        "17003"          → 17003.0
        17003.0 (float)  → 17003.0

    Devuelve None si no puede parsear.
    """
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)

    s = str(texto).replace("$", "").replace(" ", "").replace(" ", "").strip()

    if not s:
        return None

    # Caso 1: tiene coma → formato argentino claro ("17.003,50" o "21.166,79")
    if "," in s:
        s = s.replace(".", "")   # sacar puntos de miles
        s = s.replace(",", ".")  # coma decimal → punto
        try:
            return float(s)
        except ValueError:
            return None

    # Caso 2: tiene punto → puede ser miles ("17.003") o decimal ("17.50")
    # Caso especial Selma: "$21.16679" = $21.166,79 (miles + centavos sin coma)
    if "." in s:
        partes = s.split(".")
        decimales = partes[-1]
        if len(decimales) == 2:
            # Decimal real: "17.50" → 17.50
            try:
                return float(s)
            except ValueError:
                return None
        elif len(decimales) > 3:
            # Formato Selma: "21.16679" → miles="21.166", centavos="79"
            # Los ultimos 2 digitos son centavos, el resto son miles
            miles = s.replace(".", "")[:-2]   # "2116679" → "21166"
            centavos = s.replace(".", "")[-2:] # "2116679" → "79"
            try:
                return float(f"{miles}.{centavos}")
            except ValueError:
                return None
        else:
            # Son miles exactos: "17.003" → 17003
            s = s.replace(".", "")
            try:
                return float(s)
            except ValueError:
                return None

    # Caso 3: solo digitos
    try:
        return float(s)
    except ValueError:
        return None


def ordenar_precio_lista(
    precio: float | None, lista: float | None
) -> tuple[float | None, float | None]:
    """
    Garantiza que (precio_oferta, precio_lista) estén en el orden correcto:
    precio_oferta <= precio_lista (el precio de oferta es siempre el menor).

    Si ambos son iguales, lista queda como None (no hay descuento real).
    Si solo hay uno de los dos, se devuelve como precio y lista=None.
    """
    if precio is None and lista is None:
        return None, None
    if precio is None:
        return lista, None
    if lista is None:
        return precio, None
    if precio == lista:
        return precio, None   # sin descuento real
    # Si están invertidos, los corregimos
    return min(precio, lista), max(precio, lista)

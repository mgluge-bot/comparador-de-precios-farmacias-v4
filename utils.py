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
        "$ 17.003"      → 17003.0
        "$17.003,50"    → 17003.50
        "17003"         → 17003.0
        17003.0 (float) → 17003.0   ← ya viene limpio desde Selma/LD+JSON

    Devuelve None si no puede parsear.
    """
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)

    # Elimina símbolo $ y espacios
    s = str(texto).replace("$", "").strip()

    # Formato argentino: puntos como separador de miles, coma como decimal
    # Ej: "17.003,50" → "17003.50"
    if re.search(r"\d\.\d{3}", s):          # tiene puntos de miles
        s = s.replace(".", "")              # elimina puntos de miles
    s = s.replace(",", ".")                 # coma decimal → punto decimal

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

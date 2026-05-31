"""
farmacias.py — Una clase por farmacia.

Convenciones:
  - precio_oferta es siempre el MENOR (precio con descuento)
  - precio_lista  es siempre el MAYOR (precio original/tachado)
  - Si no hay descuento activo, lista=None
  - Los precios son float limpio, nunca string
  - Los errores se loggean pero no se propagan

Farmacias con Selenium (JS):  Farmacity, FarmaOnline  → reciben driver en __init__
Farmacias con requests:       FarmaPlus, Selma, CentralOeste
"""

import json
import re
import logging

import requests
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils import limpiar_precio, ordenar_precio_lista

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15


# ---------------------------------------------------------------------------
# Clase base
# ---------------------------------------------------------------------------

class FarmaciaScraper:
    nombre: str = "base"

    def __init__(self):
        self.logger = logging.getLogger(f"scraper.{self.nombre}")

    def obtener_precios(self, url: str | None) -> tuple[float | None, float | None]:
        if url is None:
            self.logger.debug("URL no configurada para este producto.")
            return None, None
        try:
            precio, lista = self._scrape(url)
            self.logger.info(f"OK | precio={precio} | lista={lista} | {url}")
            return precio, lista
        except Exception as e:
            self.logger.error(f"ERROR en {url}: {e}", exc_info=True)
            return None, None

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helper VTEX: extrae precio y lista del bloque del producto principal
# ---------------------------------------------------------------------------

def _extraer_precios_vtex(driver, wait) -> tuple[float | None, float | None]:
    """
    Extrae precio oferta y precio lista de paginas VTEX.

    La clave es la clase --hasListPrice: VTEX la agrega al elemento
    sellingPrice SOLO cuando hay un descuento activo. Si esa clase no existe,
    no hay precio lista real, evitamos asi capturar precios de productos
    sugeridos que aparecen mas abajo en la pagina.
    """
    selling_elem = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, ".vtex-product-price-1-x-sellingPriceValue")
        )
    )
    precio = limpiar_precio(selling_elem.text)

    lista = None
    try:
        hay_descuento = driver.find_elements(
            By.CSS_SELECTOR,
            ".vtex-product-price-1-x-sellingPrice--hasListPrice"
        )
        if hay_descuento:
            lista_elems = driver.find_elements(
                By.CSS_SELECTOR, ".vtex-product-price-1-x-listPriceValue"
            )
            if lista_elems:
                lista = limpiar_precio(lista_elems[0].text)
    except Exception:
        pass

    return ordenar_precio_lista(precio, lista)


# ---------------------------------------------------------------------------
# Farmacity  (VTEX — Selenium)
# ---------------------------------------------------------------------------

class FarmacityScraper(FarmaciaScraper):
    nombre = "farmacity"

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.wait = WebDriverWait(driver, TIMEOUT)

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        self.driver.get(url)
        return _extraer_precios_vtex(self.driver, self.wait)


# ---------------------------------------------------------------------------
# FarmaOnline  (VTEX — Selenium)
# ---------------------------------------------------------------------------

class FarmaOnlineScraper(FarmaciaScraper):
    nombre = "farmaonline"

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.wait = WebDriverWait(driver, TIMEOUT)

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        self.driver.get(url)
        return _extraer_precios_vtex(self.driver, self.wait)


# ---------------------------------------------------------------------------
# FarmaPlus  (requests)
# ---------------------------------------------------------------------------

class FarmaPlusScraper(FarmaciaScraper):
    """
    FarmaPlus sirve HTML estático.
    SKU: busca EAN-13 (13 dígitos) entre los párrafos de ficha técnica.
    """

    nombre = "farmaplus"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _fetch(self, url: str) -> str:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        html = self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        texto = soup.get_text()
        matches = re.findall(r"\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?", texto)

        precio = limpiar_precio(matches[0]) if len(matches) > 0 else None
        lista  = limpiar_precio(matches[1]) if len(matches) > 1 else None
        return ordenar_precio_lista(precio, lista)

    def obtener_sku(self, url: str | None) -> str | None:
        if url is None:
            return None
        try:
            html = self._fetch(url)
            soup = BeautifulSoup(html, "html.parser")

            candidatos = soup.select("p.pedidosfarma-fp-fdp-0-x-contentFichaText")
            self.logger.debug(f"Candidatos SKU: {len(candidatos)}")

            ean13 = None
            fallback = None
            for elem in candidatos:
                texto = elem.get_text(strip=True)
                self.logger.debug(f"  Candidato: '{texto}'")
                if re.fullmatch(r"\d{13}", texto):
                    ean13 = texto
                    break
                elif re.fullmatch(r"\d{8,}", texto) and fallback is None:
                    fallback = texto

            sku = ean13 or fallback
            if sku:
                self.logger.info(f"SKU encontrado: {sku} | {url}")
            else:
                self.logger.warning(f"SKU no encontrado en {url}")
            return sku
        except Exception as e:
            self.logger.error(f"Error al obtener SKU de {url}: {e}")
            return None


# ---------------------------------------------------------------------------
# Selma Digital  (requests — LD+JSON)
# ---------------------------------------------------------------------------

class SelmaScraper(FarmaciaScraper):
    nombre = "selma"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _fetch(self, url: str) -> str:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        html = self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        precio = None
        script = soup.find("script", {"type": "application/ld+json"})
        if script and script.string:
            data = json.loads(script.string)
            precio = limpiar_precio(data.get("offers", {}).get("price"))

        texto = soup.get_text()
        matches = re.findall(r"\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?", texto)
        lista = limpiar_precio(matches[0]) if matches else None

        return ordenar_precio_lista(precio, lista)


# ---------------------------------------------------------------------------
# Central Oeste  (requests)
# ---------------------------------------------------------------------------

class CentralOesteScraper(FarmaciaScraper):
    """
    Dos span.price por producto: primero oferta, segundo lista.
    """

    nombre = "central_oeste"

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _fetch(self, url: str) -> str:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        html = self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        precios = soup.select("span.price")
        precio = limpiar_precio(precios[0].get_text()) if len(precios) > 0 else None
        lista  = limpiar_precio(precios[1].get_text()) if len(precios) > 1 else None

        return ordenar_precio_lista(precio, lista)

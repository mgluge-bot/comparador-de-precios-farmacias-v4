"""
farmacias.py — Una clase por farmacia.

Cada clase expone un único método público:
    scraper.obtener_precios(url) → (precio_oferta: float|None, precio_lista: float|None)

FarmaPlus además expone:
    scraper.obtener_sku(url) → str|None
    (extrae el código de barras/SKU del producto para cruces futuros)

Convenciones:
  - Siempre devuelve una tupla de dos elementos (precios).
  - None indica que el dato no está disponible o falló el scraping.
  - Los precios se devuelven como float (ya limpios), nunca como string.
  - Los errores se loggean pero nunca se propagan.

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

from utils import limpiar_precio

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
    """Clase base. Las subclases implementan `_scrape(url)`."""

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
# Farmacity  (VTEX — requiere Selenium)
# ---------------------------------------------------------------------------

class FarmacityScraper(FarmaciaScraper):
    """
    Farmacity corre sobre VTEX y renderiza precios con JavaScript.
    Selectores VTEX:
      - Precio oferta: .vtex-product-price-1-x-sellingPriceValue
      - Precio lista:  .vtex-product-price-1-x-listPrice
    """

    nombre = "farmacity"

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.wait = WebDriverWait(driver, TIMEOUT)

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        self.driver.get(url)

        precio_elem = self.wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".vtex-product-price-1-x-sellingPriceValue")
            )
        )
        precio = limpiar_precio(precio_elem.text)

        lista = None
        try:
            lista_elems = self.driver.find_elements(
                By.CSS_SELECTOR, ".vtex-product-price-1-x-listPrice"
            )
            if lista_elems:
                lista = limpiar_precio(lista_elems[0].text)
        except Exception:
            pass

        return precio, lista


# ---------------------------------------------------------------------------
# FarmaOnline  (VTEX — requiere Selenium, igual que Farmacity)
# ---------------------------------------------------------------------------

class FarmaOnlineScraper(FarmaciaScraper):
    """
    FarmaOnline también corre sobre VTEX.
    Mismos selectores que Farmacity.
    """

    nombre = "farmaonline"

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.wait = WebDriverWait(driver, TIMEOUT)

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        self.driver.get(url)

        precio_elem = self.wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".vtex-product-price-1-x-sellingPriceValue")
            )
        )
        precio = limpiar_precio(precio_elem.text)

        lista = None
        try:
            lista_elems = self.driver.find_elements(
                By.CSS_SELECTOR, ".vtex-product-price-1-x-listPrice"
            )
            if lista_elems:
                lista = limpiar_precio(lista_elems[0].text)
        except Exception:
            pass

        return precio, lista


# ---------------------------------------------------------------------------
# FarmaPlus  (HTML estático — requests + BeautifulSoup)
# ---------------------------------------------------------------------------

class FarmaPlusScraper(FarmaciaScraper):
    """
    FarmaPlus sirve HTML estático.
    El primer match de precio en el texto es el precio oferta,
    el segundo es el precio lista.

    Además expone obtener_sku() que extrae el código de barras (EAN/GTIN)
    desde la ficha técnica del producto:
      <p class="pedidosfarma-fp-fdp-0-x-contentFichaText">7509552875416</p>
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
        return precio, lista

    def obtener_sku(self, url: str | None) -> str | None:
        """
        Extrae el código de barras (SKU) del producto desde la ficha técnica.
        Devuelve el SKU como string o None si no se encuentra.
        Se llama una sola vez por producto (el SKU no cambia con el tiempo).
        """
        if url is None:
            return None
        try:
            html = self._fetch(url)
            soup = BeautifulSoup(html, "html.parser")
            # El SKU está en el primer párrafo con esta clase
            elem = soup.select_one("p.pedidosfarma-fp-fdp-0-x-contentFichaText")
            if elem:
                sku = elem.get_text(strip=True)
                if sku.isdigit():   # los EAN/GTIN son solo dígitos
                    self.logger.info(f"SKU encontrado: {sku} | {url}")
                    return sku
            self.logger.warning(f"SKU no encontrado en {url}")
            return None
        except Exception as e:
            self.logger.error(f"Error al obtener SKU de {url}: {e}")
            return None


# ---------------------------------------------------------------------------
# Selma Digital  (LD+JSON para precio oferta, regex para lista)
# ---------------------------------------------------------------------------

class SelmaScraper(FarmaciaScraper):
    """
    Selma expone el precio de oferta en un bloque application/ld+json,
    y el precio lista está en el texto visible de la página.
    """

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

        return precio, lista


# ---------------------------------------------------------------------------
# Central Oeste  (HTML estático — requests + BeautifulSoup)
# ---------------------------------------------------------------------------

class CentralOesteScraper(FarmaciaScraper):
    """
    Central Oeste sirve HTML estático con los precios en span.price.
    Dos span.price por producto: primero oferta, segundo lista.
    Ej: <span class="price">$&nbsp;20.930,00</span>
        <span class="price">$&nbsp;29.900,00</span>
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

        return precio, lista

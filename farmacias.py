"""
farmacias.py — Una clase por farmacia.

Cada clase expone un único método público:
    scraper.obtener_precios(url) → (precio_oferta: float|None, precio_lista: float|None)

Convenciones:
  - Siempre devuelve una tupla de dos elementos.
  - None indica que el dato no está disponible o falló el scraping.
  - Los precios se devuelven como float (ya limpios), nunca como string.
  - Los errores se loggean pero nunca se propagan: el script principal siempre recibe algo.
"""

import json
import re
import time
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
TIMEOUT = 15  # segundos para requests


# ---------------------------------------------------------------------------
# Clase base
# ---------------------------------------------------------------------------

class FarmaciaScraper:
    """Clase base. Las subclases implementan `_scrape(url)`."""

    nombre: str = "base"

    def __init__(self):
        self.logger = logging.getLogger(f"scraper.{self.nombre}")

    def obtener_precios(self, url: str | None) -> tuple[float | None, float | None]:
        """Punto de entrada público. Maneja el caso url=None y loggea errores."""
        if url is None:
            self.logger.debug("URL no configurada para este producto.")
            return None, None
        try:
            precio, lista = self._scrape(url)
            self.logger.info(f"OK | precio={precio} | lista={lista} | {url}")
            return precio, lista
        except Exception as e:
            self.logger.error(f"ERROR inesperado en {url}: {e}", exc_info=True)
            return None, None

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Farmacity  (VTEX — requiere Selenium)
# ---------------------------------------------------------------------------

class FarmacityScraper(FarmaciaScraper):
    """
    Farmacity corre sobre VTEX y renderiza precios con JavaScript,
    por eso necesita Selenium.

    Selectores VTEX:
      - Precio oferta: .vtex-product-price-1-x-sellingPriceValue
      - Precio lista:  .vtex-product-price-1-x-listPrice  (aparece tachado cuando hay descuento)
    """

    nombre = "farmacity"

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.wait = WebDriverWait(driver, TIMEOUT)

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        self.driver.get(url)

        # Precio oferta (siempre presente)
        precio_elem = self.wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".vtex-product-price-1-x-sellingPriceValue")
            )
        )
        precio = limpiar_precio(precio_elem.text)

        # Precio lista (solo aparece cuando hay descuento activo)
        lista = None
        try:
            lista_elems = self.driver.find_elements(
                By.CSS_SELECTOR, ".vtex-product-price-1-x-listPrice"
            )
            if lista_elems:
                lista = limpiar_precio(lista_elems[0].text)
        except Exception:
            pass  # sin descuento activo → lista queda None

        return precio, lista


# ---------------------------------------------------------------------------
# FarmaPlus  (HTML estático — requests + BeautifulSoup)
# ---------------------------------------------------------------------------

class FarmaPlusScraper(FarmaciaScraper):
    """
    FarmaPlus sirve HTML estático, podemos usar requests.
    Extrae precios buscando el patrón $XX.XXX en el texto visible.
    El primer match es el precio de oferta, el segundo el precio lista.
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

        # Precio oferta desde LD+JSON
        precio = None
        script = soup.find("script", {"type": "application/ld+json"})
        if script and script.string:
            data = json.loads(script.string)
            precio = limpiar_precio(data.get("offers", {}).get("price"))

        # Precio lista desde texto visible
        texto = soup.get_text()
        matches = re.findall(r"\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?", texto)
        lista = limpiar_precio(matches[0]) if matches else None

        return precio, lista


# ---------------------------------------------------------------------------
# Central Oeste  (PENDIENTE — completar selectores tras inspección)
# ---------------------------------------------------------------------------

class CentralOesteScraper(FarmaciaScraper):
    """
    Central Oeste — selectores a confirmar inspeccionando el HTML.

    INSTRUCCIONES PARA COMPLETAR:
      1. Abrí un producto en Chrome → clic derecho en el precio → Inspeccionar.
      2. Buscá el atributo class del elemento con el precio de oferta.
      3. Buscá el precio tachado (lista) si existe.
      4. Reemplazá los TODO de abajo con los selectores reales.

    Si el sitio renderiza con JavaScript (los precios no aparecen en
    "Ver código fuente"), cambiá _fetch por un método con Selenium
    similar al de FarmacityScraper y pasá el driver en __init__.
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

        # TODO: reemplazar con el selector CSS real del precio de oferta
        precio_elem = soup.select_one("TODO_SELECTOR_PRECIO_OFERTA")
        precio = limpiar_precio(precio_elem.get_text()) if precio_elem else None

        # TODO: reemplazar con el selector CSS real del precio lista (tachado)
        lista_elem = soup.select_one("TODO_SELECTOR_PRECIO_LISTA")
        lista = limpiar_precio(lista_elem.get_text()) if lista_elem else None

        return precio, lista


# ---------------------------------------------------------------------------
# FarmaOnline  (PENDIENTE — completar selectores tras inspección)
# ---------------------------------------------------------------------------

class FarmaOnlineScraper(FarmaciaScraper):
    """
    FarmaOnline — selectores a confirmar inspeccionando el HTML.

    Es probable que sea Prestashop. Selectores comunes de Prestashop:
      - Precio oferta: span.current-price-value  o  span[itemprop="price"]
      - Precio lista:  span.regular-price

    INSTRUCCIONES PARA COMPLETAR: ídem CentralOesteScraper.
    Si el HTML estático no contiene los precios, se necesita Selenium.
    """

    nombre = "farmaonline"

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

        # Intento con selectores típicos de Prestashop
        precio_elem = (
            soup.select_one("span.current-price-value")
            or soup.select_one("span[itemprop='price']")
        )
        precio = limpiar_precio(precio_elem.get_text()) if precio_elem else None

        lista_elem = soup.select_one("span.regular-price")
        lista = limpiar_precio(lista_elem.get_text()) if lista_elem else None

        return precio, lista

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

def _extraer_precios_vtex_principal(driver, wait) -> tuple[float | None, float | None]:
    """
    Extrae precio oferta y precio lista del producto PRINCIPAL en paginas VTEX.

    El primer sellingPriceValue de la pagina siempre es el del producto principal.
    Para la lista, solo la aceptamos si esta dentro de los 6 niveles superiores
    del primer selling — si no esta ahi, es de un producto sugerido y la ignoramos.
    """
    selling_elem = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, ".vtex-product-price-1-x-sellingPriceValue")
        )
    )
    precio = limpiar_precio(selling_elem.text)

    lista = None
    try:
        # Subir hasta 6 niveles desde el primer selling buscando listPriceValue
        node = selling_elem
        for _ in range(6):
            node = node.find_element(By.XPATH, "..")
            lista_elems = node.find_elements(
                By.CSS_SELECTOR, ".vtex-product-price-1-x-listPriceValue"
            )
            if lista_elems:
                lista = limpiar_precio(lista_elems[0].text)
                break
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
        return _extraer_precios_vtex_principal(self.driver, self.wait)


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
        return _extraer_precios_vtex_principal(self.driver, self.wait)


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
    """
    Selma corre sobre Vue.js — los precios se renderizan con JavaScript.
    Necesita Selenium igual que Farmacity.

    Selectores dentro del contenedor principal (.v-main):
      - Precio oferta: primer elemento con "price" en la clase que sea solo numero
      - Precio lista:  elemento con "prev-price" en la clase
    """
    nombre = "selma"

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.wait = WebDriverWait(driver, TIMEOUT)

    def _scrape(self, url: str) -> tuple[float | None, float | None]:
        self.driver.get(url)

        # Esperamos que cargue el bloque del producto principal
        self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.price.text-start"))
        )

        precio = None
        lista = None

        try:
            # Precio oferta: div.price.text-start es especifico del producto principal
            # (los sugeridos usan product-card-design8-vertical__price)
            precio_elem = self.driver.find_element(By.CSS_SELECTOR, "div.price.text-start")
            precio = limpiar_precio(precio_elem.text.strip())

            # Precio lista: esta en el primer hijo del abuelo (2 niveles arriba)
            # Estructura: abuelo > [hijo0: lista, hijo1: d-flex(precio + descuento)]
            abuelo = precio_elem.find_element(By.XPATH, "../..")
            hijos = abuelo.find_elements(By.XPATH, "./*")
            if hijos:
                texto_lista = hijos[0].text.strip()
                # Verificar que sea un precio (empieza con $ y tiene numeros)
                if texto_lista.startswith("$") and any(c.isdigit() for c in texto_lista):
                    lista = limpiar_precio(texto_lista)
        except Exception:
            pass

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

        # Central Oeste muestra 4 span.price por producto:
        #   0: precio oferta CON impuestos     <- queremos este
        #   1: precio oferta SIN impuestos     <- ignorar
        #   2: precio lista  CON impuestos     <- queremos este
        #   3: precio lista  SIN impuestos     <- ignorar
        # Filtramos solo los que tienen clase price-including-tax
        precios = soup.select("span.price-wrapper.price-including-tax span.price")
        precio = limpiar_precio(precios[0].get_text()) if len(precios) > 0 else None
        lista  = limpiar_precio(precios[1].get_text()) if len(precios) > 1 else None

        return ordenar_precio_lista(precio, lista)

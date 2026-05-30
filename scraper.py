"""
scraper.py — Orquestador principal del scraper de precios.

Uso:
    python scraper.py                   # corre todo
    python scraper.py --dry-run         # muestra productos sin scrapear
    python scraper.py --producto "Nivea Sun Tono Medio"   # scrapea un solo producto

Salida:
    precios.csv  — acumula una fila por producto por día (append)
    scraper.log  — log completo con errores y tiempos
"""

import argparse
import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from farmacias import (
    FarmacityScraper,
    FarmaPlusScraper,
    SelmaScraper,
    CentralOesteScraper,
    FarmaOnlineScraper,
)
from utils import setup_logger

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

PRODUCTOS_FILE = "productos.json"
OUTPUT_CSV     = "precios.csv"
LOG_FILE       = "scraper.log"
MAX_WORKERS    = 4   # hilos para farmacias que usan requests (no Selenium)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def crear_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def cargar_productos(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def guardar_csv(rows: list[dict], path: str) -> None:
    df = pd.DataFrame(rows)
    escribir_header = not pd.io.common.file_exists(path)
    df.to_csv(path, mode="a", index=False, header=escribir_header)


# ---------------------------------------------------------------------------
# Scraping de un producto con farmacias paralelas (requests) + Selenium aparte
# ---------------------------------------------------------------------------

def scrape_producto(
    producto: dict,
    farmacity: FarmacityScraper,
    farmaplus: FarmaPlusScraper,
    selma: SelmaScraper,
    central_oeste: CentralOesteScraper,
    farmaonline: FarmaOnlineScraper,
    logger,
) -> dict:
    """
    Scrapea todas las farmacias para un producto.
    Las farmacias requests-based se lanzan en paralelo;
    Farmacity (Selenium) corre en el hilo principal del driver.
    """
    nombre = producto["nombre"]
    logger.info(f"── Scrapeando: {nombre}")

    # Farmacity (Selenium — no thread-safe, corre secuencial)
    pf, lf = farmacity.obtener_precios(producto.get("farmacity"))

    # Farmacias requests-based en paralelo
    def _farmaplus():
        return farmaplus.obtener_precios(producto.get("farmaplus"))

    def _selma():
        return selma.obtener_precios(producto.get("selma"))

    def _central_oeste():
        return central_oeste.obtener_precios(producto.get("central_oeste"))

    def _farmaonline():
        return farmaonline.obtener_precios(producto.get("farmaonline"))

    tareas = {
        "farmaplus":    _farmaplus,
        "selma":        _selma,
        "central_oeste": _central_oeste,
        "farmaonline":  _farmaonline,
    }

    resultados = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fn): key for key, fn in tareas.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                resultados[key] = future.result()
            except Exception as e:
                logger.error(f"Fallo en hilo {key} para {nombre}: {e}")
                resultados[key] = (None, None)

    pp, lp = resultados["farmaplus"]
    ps, ls = resultados["selma"]
    pco, lco = resultados["central_oeste"]
    pfo, lfo = resultados["farmaonline"]

    return {
        "fecha":               datetime.date.today().isoformat(),
        "producto":            nombre,
        "precio_farmacity":    pf,
        "lista_farmacity":     lf,
        "precio_farmaplus":    pp,
        "lista_farmaplus":     lp,
        "precio_selma":        ps,
        "lista_selma":         ls,
        "precio_central_oeste": pco,
        "lista_central_oeste":  lco,
        "precio_farmaonline":   pfo,
        "lista_farmaonline":    lfo,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scraper de precios de farmacias")
    parser.add_argument("--dry-run", action="store_true", help="Lista productos sin scrapear")
    parser.add_argument("--producto", type=str, default=None, help="Scrapea solo un producto por nombre")
    args = parser.parse_args()

    logger = setup_logger(log_file=LOG_FILE)
    logger.info("=" * 60)
    logger.info("Iniciando scraper")

    productos = cargar_productos(PRODUCTOS_FILE)

    if args.dry_run:
        logger.info("DRY RUN — productos configurados:")
        for p in productos:
            logger.info(f"  • {p['nombre']}")
        sys.exit(0)

    if args.producto:
        productos = [p for p in productos if p["nombre"].lower() == args.producto.lower()]
        if not productos:
            logger.error(f"Producto no encontrado: {args.producto}")
            sys.exit(1)

    # Inicializar scrapers
    logger.info("Iniciando ChromeDriver...")
    driver = crear_driver()

    farmacity    = FarmacityScraper(driver)
    farmaplus    = FarmaPlusScraper()
    selma        = SelmaScraper()
    central_oeste = CentralOesteScraper()
    farmaonline  = FarmaOnlineScraper()

    rows = []
    try:
        for producto in productos:
            row = scrape_producto(
                producto, farmacity, farmaplus, selma, central_oeste, farmaonline, logger
            )
            rows.append(row)
            logger.debug(f"Fila generada: {row}")
    finally:
        driver.quit()
        logger.info("ChromeDriver cerrado.")

    guardar_csv(rows, OUTPUT_CSV)
    logger.info(f"CSV actualizado: {OUTPUT_CSV} ({len(rows)} filas nuevas)")

    # Resumen rápido en consola
    df = pd.DataFrame(rows)
    print("\n── Resumen del día ──────────────────────────────────────")
    print(df[["producto", "precio_farmacity", "precio_farmaplus",
              "precio_selma", "precio_central_oeste", "precio_farmaonline"]].to_string(index=False))
    print("─────────────────────────────────────────────────────────\n")

    # Alertar si hay muchos NA
    total_celdas = len(rows) * 5   # 5 farmacias
    nulos = df[["precio_farmacity","precio_farmaplus","precio_selma",
                "precio_central_oeste","precio_farmaonline"]].isna().sum().sum()
    if nulos > total_celdas * 0.3:
        logger.warning(
            f"⚠ Más del 30% de los precios son None ({nulos}/{total_celdas}). "
            "Revisá scraper.log para ver los errores."
        )

    logger.info("Scraper finalizado exitosamente.")


if __name__ == "__main__":
    main()

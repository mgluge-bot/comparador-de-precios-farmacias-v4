"""
scraper.py — Orquestador principal del scraper de precios.

Uso:
    python scraper.py                                      # corre todo
    python scraper.py --dry-run                            # lista productos sin scrapear
    python scraper.py --producto "Nivea Sun Tono Medio"    # scrapea un solo producto

Salida:
    precios.csv  — acumula una fila por producto por día (append)
    scraper.log  — log completo con errores y tiempos

Arquitectura de ejecución:
    ┌─ Selenium (secuencial, un solo driver compartido) ─┐
    │   1. Farmacity                                      │
    │   2. FarmaOnline                                    │
    └────────────────────────────────────────────────────┘
    ┌─ Requests (paralelo, ThreadPoolExecutor) ──────────┐
    │   3. FarmaPlus  ┐                                   │
    │   4. Selma      ├─ corren al mismo tiempo           │
    │   5. CentralOeste┘                                  │
    └────────────────────────────────────────────────────┘

Columna SKU:
    Se obtiene desde FarmaPlus (código de barras en ficha técnica).
    Se carga SOLO la primera vez que aparece el producto en el CSV
    (el SKU no cambia con el tiempo → no hace falta re-scrapearlo cada día).
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
    FarmaOnlineScraper,
    FarmaPlusScraper,
    SelmaScraper,
    CentralOesteScraper,
)
from utils import setup_logger
from sheets import actualizar_sheet
from alertas import analizar_y_alertar

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

PRODUCTOS_FILE = "productos.json"
OUTPUT_CSV     = "precios.csv"
LOG_FILE       = "scraper.log"
MAX_WORKERS    = 2   # hilos para las 2 farmacias requests-based (FarmaPlus y CentralOeste)

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


def cargar_skus_existentes(path: str) -> dict[str, str]:
    """
    Lee el CSV existente y devuelve un dict {nombre_producto: sku}
    con los SKUs ya guardados. Así no re-scrapeamos SKUs que ya tenemos.
    """
    if not pd.io.common.file_exists(path):
        return {}
    try:
        df = pd.read_csv(path, usecols=["producto", "sku"])
        # Filtramos filas con SKU válido (no nulo, no vacío)
        df = df[df["sku"].notna() & (df["sku"].astype(str).str.strip() != "")]
        return dict(zip(df["producto"], df["sku"].astype(str)))
    except Exception:
        return {}


def guardar_csv(rows: list[dict], path: str) -> None:
    df = pd.DataFrame(rows)
    escribir_header = not pd.io.common.file_exists(path)
    df.to_csv(path, mode="a", index=False, header=escribir_header)


# ---------------------------------------------------------------------------
# Scraping de un producto
# ---------------------------------------------------------------------------

def scrape_producto(
    producto: dict,
    farmacity: FarmacityScraper,
    farmaonline: FarmaOnlineScraper,
    farmaplus: FarmaPlusScraper,
    selma: SelmaScraper,
    central_oeste: CentralOesteScraper,
    sku_conocido: str | None,
    logger,
) -> dict:
    """
    Scrapea todas las farmacias para un producto.

    - Farmacity y FarmaOnline usan Selenium → corren SECUENCIAL.
    - FarmaPlus, Selma y CentralOeste usan requests → corren en PARALELO.
    - El SKU se obtiene de FarmaPlus solo si no está ya en el CSV.
    """
    nombre = producto["nombre"]
    logger.info(f"── Scrapeando: {nombre}")

    # ── 1. Selenium: secuencial (Farmacity, Selma y FarmaOnline usan JS) ──
    pf,  lf  = farmacity.obtener_precios(producto.get("farmacity"))
    ps,  ls  = selma.obtener_precios(producto.get("selma"))
    pfo, lfo = farmaonline.obtener_precios(producto.get("farmaonline"))

    # ── 2. Requests: paralelo (HTML estatico) ──────────────────
    tareas = {
        "farmaplus":     lambda: farmaplus.obtener_precios(producto.get("farmaplus")),
        "central_oeste": lambda: central_oeste.obtener_precios(producto.get("central_oeste")),
    }

    resultados = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fn): key for key, fn in tareas.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                resultados[key] = future.result()
            except Exception as e:
                logger.error(f"Fallo en hilo '{key}' para '{nombre}': {e}")
                resultados[key] = (None, None)

    pp,  lp  = resultados["farmaplus"]
    pco, lco = resultados["central_oeste"]

    # ── 3. SKU: solo si no lo tenemos ya ────────────────────────────────
    if sku_conocido:
        sku = sku_conocido
        logger.debug(f"SKU reutilizado del CSV: {sku}")
    else:
        sku = farmaplus.obtener_sku(producto.get("farmaplus"))
        if sku:
            logger.info(f"SKU nuevo obtenido: {sku}")
        else:
            logger.warning(f"No se pudo obtener SKU para: {nombre}")

    return {
        "fecha":                datetime.date.today().isoformat(),
        "producto":             nombre,
        "sku":                  sku,
        "precio_farmacity":     pf,
        "lista_farmacity":      lf,
        "precio_farmaplus":     pp,
        "lista_farmaplus":      lp,
        "precio_selma":         ps,
        "lista_selma":          ls,
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
    parser.add_argument("--dry-run",  action="store_true",
                        help="Lista productos sin scrapear")
    parser.add_argument("--producto", type=str, default=None,
                        help="Scrapea solo un producto (por nombre exacto)")
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
            logger.error(f"Producto no encontrado: '{args.producto}'")
            sys.exit(1)

    # SKUs ya guardados en el CSV (evita re-scraping innecesario)
    skus_existentes = cargar_skus_existentes(OUTPUT_CSV)
    logger.info(f"SKUs ya conocidos: {len(skus_existentes)}")

    # ── Inicializar scrapers ─────────────────────────────────────────────
    logger.info("Iniciando ChromeDriver...")
    driver = crear_driver()

    farmacity     = FarmacityScraper(driver)
    farmaonline   = FarmaOnlineScraper(driver)
    farmaplus     = FarmaPlusScraper()
    selma         = SelmaScraper(driver)   # Vue.js -> necesita Selenium
    central_oeste = CentralOesteScraper()

    rows = []
    try:
        for producto in productos:
            sku_conocido = skus_existentes.get(producto["nombre"])
            row = scrape_producto(
                producto,
                farmacity, farmaonline,
                farmaplus, selma, central_oeste,
                sku_conocido,
                logger,
            )
            rows.append(row)
            logger.debug(f"Fila generada: {row}")
    finally:
        driver.quit()
        logger.info("ChromeDriver cerrado.")

    guardar_csv(rows, OUTPUT_CSV)
    logger.info(f"CSV actualizado: {OUTPUT_CSV} ({len(rows)} filas nuevas)")

    # Escribir en Google Sheets si las credenciales estan disponibles
    credentials_json = os.environ.get("GOOGLE_CREDENTIALS")
    spreadsheet_id   = os.environ.get("SPREADSHEET_ID")
    if credentials_json and spreadsheet_id:
        logger.info("Actualizando Google Sheets...")
        actualizar_sheet(rows, spreadsheet_id, credentials_json)
    else:
        logger.warning("Variables GOOGLE_CREDENTIALS o SPREADSHEET_ID no encontradas. Saltando Sheets.")

    # Alertas por Telegram
    telegram_token   = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if telegram_token and telegram_chat_id:
        logger.info("Analizando alertas...")
        analizar_y_alertar(rows, telegram_token, telegram_chat_id)
    else:
        logger.warning("Variables TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no encontradas. Saltando alertas.")

    # ── Resumen en consola ───────────────────────────────────────────────
    df = pd.DataFrame(rows)
    print("\n── Resumen del día ──────────────────────────────────────────────────")
    print(df[[
        "producto", "sku",
        "precio_farmacity", "precio_farmaplus", "precio_selma",
        "precio_central_oeste", "precio_farmaonline"
    ]].to_string(index=False))
    print("─────────────────────────────────────────────────────────────────────\n")

    # ── Alerta si hay muchos None ────────────────────────────────────────
    cols_precio = [
        "precio_farmacity", "precio_farmaplus", "precio_selma",
        "precio_central_oeste", "precio_farmaonline"
    ]
    total = len(rows) * len(cols_precio)
    nulos = df[cols_precio].isna().sum().sum()
    if nulos > total * 0.3:
        logger.warning(
            f"⚠  Más del 30% de los precios son None ({nulos}/{total}). "
            "Revisá scraper.log para ver los errores."
        )

    logger.info("Scraper finalizado exitosamente.")


if __name__ == "__main__":
    main()

"""
sheets.py — Manejo de Google Sheets.

Expone una sola función publica:
    actualizar_sheet(rows, spreadsheet_id, credentials_json)

Comportamiento:
  - Si la hoja "precios" no existe, la crea
  - Agrega las filas nuevas al final (append), nunca sobreescribe
  - La primera fila siempre es el header — si la hoja esta vacia lo escribe
"""

import json
import logging

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger("scraper.sheets")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "precios"

COLUMNAS = [
    "fecha", "producto", "sku",
    "precio_farmacity", "lista_farmacity",
    "precio_farmaplus", "lista_farmaplus",
    "precio_selma", "lista_selma",
    "precio_central_oeste", "lista_central_oeste",
    "precio_farmaonline", "lista_farmaonline",
]


def _conectar(credentials_json: str) -> gspread.Client:
    info = json.loads(credentials_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def _obtener_o_crear_hoja(client: gspread.Client, spreadsheet_id: str) -> gspread.Worksheet:
    """Devuelve la hoja 'precios', creandola si no existe."""
    spreadsheet = client.open_by_key(spreadsheet_id)
    try:
        return spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        logger.info(f"Hoja '{SHEET_NAME}' no encontrada, creando...")
        hoja = spreadsheet.add_worksheet(title=SHEET_NAME, rows=10000, cols=20)
        return hoja


def actualizar_sheet(rows: list[dict], spreadsheet_id: str, credentials_json: str) -> None:
    """
    Agrega las filas nuevas al Google Sheet.
    Si la hoja esta vacia, escribe el header primero.
    """
    if not rows:
        logger.warning("No hay filas para escribir en Sheets.")
        return

    try:
        client = _conectar(credentials_json)
        hoja = _obtener_o_crear_hoja(client, spreadsheet_id)

        # Ver si la hoja esta vacia para escribir header
        valores_existentes = hoja.get_all_values()
        if not valores_existentes:
            logger.info("Hoja vacia, escribiendo header...")
            hoja.append_row(COLUMNAS)

        # Convertir rows (list of dicts) a list of lists en el orden de COLUMNAS
        filas = []
        for row in rows:
            fila = [str(row.get(col, "")) if row.get(col) is not None else "" for col in COLUMNAS]
            filas.append(fila)

        hoja.append_rows(filas, value_input_option="USER_ENTERED")
        logger.info(f"Sheets actualizado: {len(filas)} filas agregadas.")

    except Exception as e:
        logger.error(f"Error al escribir en Google Sheets: {e}", exc_info=True)
        raise

"""
alertas.py — Alertas por Telegram.

Dos tipos de alerta:
  1. COMPETENCIA MAS BARATA: otra farmacia tiene precio menor que FarmaPlus
  2. PRECIO ANOMALO: otra farmacia tiene precio > 50% por encima de FarmaPlus

Se llama al final de cada ejecucion del scraper.
"""

import logging
import os

import requests

logger = logging.getLogger("scraper.alertas")

FARMACIAS = {
    "farmacity":     "Farmacity",
    "selma":         "Selma",
    "central_oeste": "Central Oeste",
    "farmaonline":   "FarmaOnline",
}

UMBRAL_ANOMALIA = 0.50  # 50% por encima de FarmaPlus = precio anomalo


def _enviar_mensaje(token: str, chat_id: str, texto: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       texto,
        "parse_mode": "HTML",
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()


def analizar_y_alertar(rows: list[dict], token: str, chat_id: str) -> None:
    """
    Analiza las filas del dia y envia alertas por Telegram si corresponde.
    rows: lista de dicts con los precios del dia (misma estructura que el CSV)
    """
    if not rows:
        return

    mas_baratas  = []  # productos donde la competencia es mas barata
    anomalos     = []  # productos con precio anomalamente alto en otra farmacia

    for row in rows:
        producto    = row.get("producto", "")
        precio_fp   = row.get("precio_farmaplus")

        # Sin precio de FarmaPlus no podemos comparar
        if precio_fp is None:
            continue

        for key, label in FARMACIAS.items():
            precio_comp = row.get(f"precio_{key}")
            if precio_comp is None:
                continue

            diferencia_pct = (precio_comp - precio_fp) / precio_fp * 100

            # Alerta 1: competencia mas barata (al menos 1% menos)
            if precio_comp < precio_fp * 0.99:
                mas_baratas.append({
                    "producto":  producto,
                    "farmacia":  label,
                    "precio_fp": precio_fp,
                    "precio_comp": precio_comp,
                    "diff_pct":  round(diferencia_pct, 1),  # negativo
                })

            # Alerta 2: precio anomalo (mas del 50% por encima de FarmaPlus)
            elif diferencia_pct > UMBRAL_ANOMALIA * 100:
                anomalos.append({
                    "producto":    producto,
                    "farmacia":    label,
                    "precio_fp":   precio_fp,
                    "precio_comp": precio_comp,
                    "diff_pct":    round(diferencia_pct, 1),
                })

    # ── Mensaje alerta 1: competencia mas barata ─────────────────────────
    if mas_baratas:
        lineas = ["⚠️ <b>COMPETENCIA MÁS BARATA QUE FARMAPLUS</b>\n"]
        for item in sorted(mas_baratas, key=lambda x: x["diff_pct"]):
            lineas.append(
                f"• <b>{item['producto'][:35]}</b>\n"
                f"  {item['farmacia']}: ${item['precio_comp']:,.0f} "
                f"vs FarmaPlus: ${item['precio_fp']:,.0f} "
                f"({item['diff_pct']}%)"
            )
        mensaje = "\n".join(lineas)
        try:
            _enviar_mensaje(token, chat_id, mensaje)
            logger.info(f"Alerta competencia enviada: {len(mas_baratas)} productos")
        except Exception as e:
            logger.error(f"Error enviando alerta competencia: {e}")

    # ── Mensaje alerta 2: precios anomalos ───────────────────────────────
    if anomalos:
        lineas = ["🚨 <b>PRECIOS ANÓMALOS DETECTADOS</b>\n"]
        for item in sorted(anomalos, key=lambda x: x["diff_pct"], reverse=True):
            lineas.append(
                f"• <b>{item['producto'][:35]}</b>\n"
                f"  {item['farmacia']}: ${item['precio_comp']:,.0f} "
                f"vs FarmaPlus: ${item['precio_fp']:,.0f} "
                f"(+{item['diff_pct']}%)"
            )
        mensaje = "\n".join(lineas)
        try:
            _enviar_mensaje(token, chat_id, mensaje)
            logger.info(f"Alerta anomalos enviada: {len(anomalos)} productos")
        except Exception as e:
            logger.error(f"Error enviando alerta anomalos: {e}")

    if not mas_baratas and not anomalos:
        logger.info("Sin alertas para enviar hoy.")

"""
dashboard.py — Dashboard de precios de farmacias.
Lee datos desde Google Sheets y muestra:
  1. Detector de ofertas del dia
  2. Evolucion de precios por producto
  3. Tabla resumen del dia
"""

import json
import os

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Precios Farmacias",
    page_icon="💊",
    layout="wide",
)

FARMACIAS = ["farmacity", "farmaplus", "selma", "central_oeste", "farmaonline"]
FARMACIAS_LABELS = {
    "farmacity":    "Farmacity",
    "farmaplus":    "FarmaPlus",
    "selma":        "Selma",
    "central_oeste": "Central Oeste",
    "farmaonline":  "FarmaOnline",
}
COLORES = {
    "Farmacity":    "#e63946",
    "FarmaPlus":    "#457b9d",
    "Selma":        "#2a9d8f",
    "Central Oeste": "#e9c46a",
    "FarmaOnline":  "#f4a261",
}

# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)  # refresca cada hora
def cargar_datos() -> pd.DataFrame:
    creds_raw = st.secrets.get("GOOGLE_CREDENTIALS") or os.environ.get("GOOGLE_CREDENTIALS")
    sheet_id  = st.secrets.get("SPREADSHEET_ID")     or os.environ.get("SPREADSHEET_ID")

    if not creds_raw or not sheet_id:
        st.error("Faltan credenciales. Configurá GOOGLE_CREDENTIALS y SPREADSHEET_ID en Streamlit Secrets.")
        st.stop()

    info  = json.loads(creds_raw)
    creds = Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    client      = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    hoja        = spreadsheet.worksheet("precios")
    COLUMNAS_ESPERADAS = [
        "fecha", "producto", "sku",
        "precio_farmacity", "lista_farmacity",
        "precio_farmaplus", "lista_farmaplus",
        "precio_selma", "lista_selma",
        "precio_central_oeste", "lista_central_oeste",
        "precio_farmaonline", "lista_farmaonline",
    ]

    valores = hoja.get_all_values()
    if not valores:
        return pd.DataFrame()

    # Detectar si la primera fila es header o datos
    primera = valores[0]
    es_header = primera[0].lower() in ("fecha", "date") if primera else False

    if es_header:
        headers = primera
        filas   = valores[1:]
    else:
        # No hay header — usamos los nombres de columna esperados
        headers = COLUMNAS_ESPERADAS
        filas   = valores

    # Ajustar cantidad de columnas si no coincide
    n_cols = len(headers)
    filas_norm = [fila[:n_cols] + [""] * max(0, n_cols - len(fila)) for fila in filas]

    df = pd.DataFrame(filas_norm, columns=headers)
    df = df.replace("", float("nan"))

    # Tipos
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    for farmacia in FARMACIAS:
        for col in [f"precio_{farmacia}", f"lista_{farmacia}"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def calcular_descuento(precio, lista):
    if pd.isna(precio) or pd.isna(lista) or lista == 0:
        return None
    return round((1 - precio / lista) * 100, 1)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("💊 Monitor de Precios de Farmacias")

with st.spinner("Cargando datos..."):
    df = cargar_datos()

if df.empty:
    st.warning("No hay datos disponibles.")
    st.stop()

ultima_fecha = df["fecha"].max()
st.caption(f"Última actualización: {ultima_fecha.strftime('%d/%m/%Y')}")

# Selector de fecha para la tabla y ofertas (por defecto la más reciente)
fechas_disponibles = sorted(df["fecha"].dropna().unique(), reverse=True)
fecha_sel = st.sidebar.selectbox(
    "Fecha",
    fechas_disponibles,
    format_func=lambda x: pd.Timestamp(x).strftime("%d/%m/%Y"),
)

df_dia = df[df["fecha"] == fecha_sel].copy()

# ---------------------------------------------------------------------------
# Sección 1 — Detector de ofertas
# ---------------------------------------------------------------------------

st.header("🔥 Ofertas del día")

ofertas = []
for _, row in df_dia.iterrows():
    for farmacia in FARMACIAS:
        precio = row.get(f"precio_{farmacia}")
        lista  = row.get(f"lista_{farmacia}")
        descuento = calcular_descuento(precio, lista)
        if descuento and descuento >= 5:  # solo descuentos reales >= 5%
            ofertas.append({
                "Producto":  row["producto"],
                "Farmacia":  FARMACIAS_LABELS[farmacia],
                "Precio":    precio,
                "Lista":     lista,
                "Descuento": descuento,
            })

if ofertas:
    df_ofertas = pd.DataFrame(ofertas).sort_values("Descuento", ascending=False)

    # Métrica destacada: mejor oferta del día
    mejor = df_ofertas.iloc[0]
    col1, col2, col3 = st.columns(3)
    col1.metric("Mejor oferta", mejor["Producto"][:30])
    col2.metric("Farmacia", mejor["Farmacia"])
    col3.metric("Descuento", f"{mejor['Descuento']}%")

    st.divider()

    # Tabla de ofertas con formato
    df_ofertas_display = df_ofertas.copy()
    df_ofertas_display["Precio"] = df_ofertas_display["Precio"].apply(lambda x: f"$ {x:,.0f}".replace(",", "."))
    df_ofertas_display["Lista"]  = df_ofertas_display["Lista"].apply(lambda x: f"$ {x:,.0f}".replace(",", "."))
    df_ofertas_display["Descuento"] = df_ofertas_display["Descuento"].apply(lambda x: f"{x}%")
    st.dataframe(df_ofertas_display, use_container_width=True, hide_index=True)
else:
    st.info("No hay ofertas con descuento significativo para esta fecha.")

# ---------------------------------------------------------------------------
# Sección 2 — Evolución de precios
# ---------------------------------------------------------------------------

st.header("📈 Evolución de precios")

productos = sorted(df["producto"].unique())
producto_sel = st.selectbox("Seleccioná un producto", productos)

df_prod = df[df["producto"] == producto_sel].copy()

# Armar dataframe largo para plotly
registros = []
for _, row in df_prod.iterrows():
    for farmacia in FARMACIAS:
        precio = row.get(f"precio_{farmacia}")
        if pd.notna(precio):
            registros.append({
                "fecha":    row["fecha"],
                "farmacia": FARMACIAS_LABELS[farmacia],
                "precio":   precio,
            })

if registros:
    df_long = pd.DataFrame(registros)
    fig = px.line(
        df_long,
        x="fecha",
        y="precio",
        color="farmacia",
        color_discrete_map=COLORES,
        markers=True,
        labels={"fecha": "Fecha", "precio": "Precio ($)", "farmacia": "Farmacia"},
        title=f"Evolución de precio — {producto_sel}",
    )
    fig.update_layout(
        hovermode="x unified",
        yaxis_tickformat="$,.0f",
        legend_title="Farmacia",
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No hay datos de precio para este producto.")

# ---------------------------------------------------------------------------
# Sección 3 — Tabla resumen del día
# ---------------------------------------------------------------------------

st.header("📋 Tabla resumen del día")

# Construir tabla pivoteada: productos vs farmacias
filas = []
for _, row in df_dia.iterrows():
    fila = {"Producto": row["producto"]}
    mejor_precio = None
    mejor_farmacia = None
    for farmacia in FARMACIAS:
        precio = row.get(f"precio_{farmacia}")
        lista  = row.get(f"lista_{farmacia}")
        if pd.notna(precio):
            desc = calcular_descuento(precio, lista)
            fila[FARMACIAS_LABELS[farmacia]] = f"$ {precio:,.0f}".replace(",", ".") + (f" ({desc}% off)" if desc else "")
            if mejor_precio is None or precio < mejor_precio:
                mejor_precio   = precio
                mejor_farmacia = FARMACIAS_LABELS[farmacia]
        else:
            fila[FARMACIAS_LABELS[farmacia]] = "—"
    fila["💰 Más barata"] = mejor_farmacia or "—"
    filas.append(fila)

if filas:
    df_resumen = pd.DataFrame(filas)
    st.dataframe(df_resumen, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Sidebar — info extra
# ---------------------------------------------------------------------------

st.sidebar.divider()
st.sidebar.caption(f"📦 {len(df['producto'].unique())} productos monitoreados")
st.sidebar.caption(f"🏪 {len(FARMACIAS)} farmacias")
st.sidebar.caption(f"📅 {len(fechas_disponibles)} días de datos")
if st.sidebar.button("🔄 Refrescar datos"):
    st.cache_data.clear()
    st.rerun()

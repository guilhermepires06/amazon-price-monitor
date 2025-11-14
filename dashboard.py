import sqlite3
import json
import re
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import seaborn as sns
import requests
import streamlit as st
from bs4 import BeautifulSoup

from utils import extract_price

# =============================================================================
# CONFIG BÁSICA
# =============================================================================

DB_NAME = "scraping.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# =============================================================================
# SCHEMA
# =============================================================================

def ensure_schema():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()

ensure_schema()

# =============================================================================
# CACHE – HTML
# =============================================================================

@st.cache_data(show_spinner=False, ttl=600)
def cached_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text

# =============================================================================
# BANCO
# =============================================================================

def get_data():
    conn = sqlite3.connect(DB_NAME)
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    df_prices = pd.read_sql_query("SELECT * FROM prices", conn)
    conn.close()

    if "date" in df_prices.columns:
        df_prices["date"] = pd.to_datetime(df_prices["date"])
        df_prices = df_prices.sort_values("date")
        df_prices["date_local"] = df_prices["date"] - pd.Timedelta(hours=3)
    else:
        df_prices["date_local"] = pd.NaT

    return df_products, df_prices

def get_latest_price(df_prices: pd.DataFrame, product_id: int):
    df_prod = df_prices[df_prices["product_id"] == product_id].dropna(subset=["price"])
    if df_prod.empty:
        return None
    return df_prod["price"].iloc[-1]

# =============================================================================
# SCRAPING IMAGEM
# =============================================================================

def get_product_image(url: str) -> str | None:
    try:
        html = cached_html(url)
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")

    img = soup.find("img", {"id": "landingImage"})
    if img and img.get("src"):
        return img["src"]

    img = soup.find("img", attrs={"data-old-hires": True})
    if img and img.get("data-old-hires"):
        return img["data-old-hires"]

    img = soup.find("img", attrs={"data-a-dynamic-image": True})
    if img:
        try:
            dyn = json.loads(img["data-a-dynamic-image"])
            urls = list(dyn.keys())
            if urls:
                return urls[0]
        except Exception:
            pass

    meta = soup.find("meta", {"property": "og:image"})
    if meta and meta.get("content"):
        return meta["content"]

    return None

# =============================================================================
# UI / CSS
# =============================================================================

st.set_page_config(
    page_title="Monitor de Preços Amazon",
    layout="wide",
    page_icon="💹",
)

st.markdown(
    """
    <style>
    .detail-card {
        padding: 1rem;
        border-radius: 0.9rem;
        background: #020617;
        border: 1px solid rgba(148,163,184,0.5);
        box-shadow: 0 12px 30px rgba(15,23,42,0.7);
        margin-bottom: 1.25rem;
    }
    .metric-badge {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        background: #0f172a;
        font-size: 0.7rem;
        margin-right: 0.3rem;
        color: #e5e7eb;
    }
    .metric-badge.positive { border: 1px solid #22c55e; }
    .metric-badge.negative { border: 1px solid #ef4444; }
    .metric-badge.neutral  { border: 1px solid #64748b; }
    .last-update-pill {
        padding: 0.35rem 0.9rem;
        border-radius: 999px;
        border: 1px solid rgba(148,163,184,0.6);
        background: #020617;
        font-size: 0.75rem;
        display: inline-flex;
        gap: 0.35rem;
        align-items: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# SIDEBAR
# =============================================================================

with st.sidebar:
    st.markdown("## Produtos monitorados")
    st.markdown("Interface somente de leitura — dados vêm do `scraping.db`.")
    st.markdown("---")

# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

df_products, df_prices = get_data()

st.title("💹 Monitor de Preços")

# Última atualização
if not df_prices.empty:
    last_dt = df_prices["date_local"].max()
    last_str = last_dt.strftime("%d/%m %H:%M") if pd.notna(last_dt) else "--/-- --:--"
else:
    last_str = "--/-- --:--"

col_title, col_last = st.columns([4, 1])
with col_last:
    st.markdown(
        f"""
        <div class="last-update-pill">
            🕒 Última atualização: <strong>{last_str}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

if df_products.empty:
    st.warning("Nenhum produto encontrado no banco.")
    st.stop()

sns.set_style("whitegrid")

st.markdown("## Produtos monitorados")

# =============================================================================
# BLOCO DE PRODUTOS (imagem + gráfico + insights)
# =============================================================================

for _, product in df_products.iterrows():
    df_prod = df_prices[df_prices["product_id"] == product["id"]].copy()

    with st.container():
        st.markdown('<div class="detail-card">', unsafe_allow_html=True)

        st.markdown(f"### {product['name']}")

        col_img, col_graph = st.columns([1, 1.8])

        # IMAGEM FIXA (sem edição)
        with col_img:
            img_url = product.get("image_url") or get_product_image(product["url"])
            if img_url:
                st.image(img_url, width=220)
            else:
                st.info("Sem imagem disponível")

            st.markdown(f"[Ver na Amazon]({product['url']})")

        # GRÁFICO
        with col_graph:
            if df_prod.empty:
                st.info("Sem histórico deste produto ainda.")
            else:
                fig, ax = plt.subplots(figsize=(6, 2.5))
                sns.lineplot(data=df_prod, x="date_local", y="price", marker="o", ax=ax)
                ax.set_xlabel("Data/Hora (BR)")
                ax.set_ylabel("Preço (R$)")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
                plt.xticks(rotation=25)
                st.pyplot(fig)

                # INSIGHTS
                df_valid = df_prod.dropna(subset=["price"])
                if len(df_valid) >= 2:
                    first = df_valid["price"].iloc[0]
                    last = df_valid["price"].iloc[-1]
                    diff = last - first
                    pct = diff / first * 100 if first != 0 else 0

                    max_p = df_valid["price"].max()
                    min_p = df_valid["price"].min()
                    mean_p = df_valid["price"].mean()

                    trend = (
                        ("subiu", "positive") if diff > 0 else
                        ("caiu", "negative") if diff < 0 else
                        ("estável", "neutral")
                    )

                    st.markdown(
                        f"""
                        <span class="metric-badge {trend[1]}">Tendência: {trend[0]}</span>
                        <span class="metric-badge">Atual: R$ {last:.2f}</span>
                        <span class="metric-badge">Mín: R$ {min_p:.2f}</span>
                        <span class="metric-badge">Máx: R$ {max_p:.2f}</span>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.write(
                        f"**1. Tendência:** O preço variou de R$ {first:.2f} para R$ {last:.2f} "
                        f"({diff:+.2f}, {pct:+.1f}%)."
                    )

                    st.write(
                        f"**2. Faixa:** mínimo R$ {min_p:.2f}, máximo R$ {max_p:.2f}, média R$ {mean_p:.2f}."
                    )

                    if last == min_p:
                        st.write("**3. Momento:** Preço no mínimo histórico — excelente p/ compra.")
                    elif last == max_p:
                        st.write("**3. Momento:** Preço no máximo histórico — talvez esperar.")
                    else:
                        st.write("**3. Momento:** Preço dentro da faixa normal do histórico.")
                else:
                    st.write("Dados insuficientes para análises.")

        st.markdown("</div>", unsafe_allow_html=True)

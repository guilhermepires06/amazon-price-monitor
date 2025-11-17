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

from utils import extract_price  # converte texto de preço em float

# =============================================================================
# CONFIG BÁSICA / GITHUB
# =============================================================================

DB_TEMP_PATH = "/tmp/scraping_remote.db"

GITHUB_REPO = "guilhermepires06/amazon-price-monitor"
GITHUB_BRANCH = "main"
GITHUB_FILE_PATH = "scraping.db"

GITHUB_DB_URL = (
    f"https://raw.githubusercontent.com/"
    f"{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE_PATH}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# =============================================================================
# CACHE – HTML (para imagens da Amazon)
# =============================================================================


@st.cache_data(show_spinner=False, ttl=600)
def cached_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


# =============================================================================
# FUNÇÕES DE BANCO – SOMENTE LEITURA (PEGA SEMPRE DO GITHUB)
# =============================================================================


@st.cache_data(show_spinner=False, ttl=60)
def get_data():
    """
    Baixa SEMPRE o scraping.db do GitHub (RAW) e lê products e prices.

    ttl=60 -> no máximo 1 minuto de defasagem em relação ao GitHub Actions,
    que está rodando o scraper de 5 em 5 minutos.
    """
    resp = requests.get(GITHUB_DB_URL, timeout=20)
    if resp.status_code != 200:
        st.error(
            f"❌ Não foi possível baixar o scraping.db do GitHub "
            f"(GET {resp.status_code})."
        )
        st.stop()

    with open(DB_TEMP_PATH, "wb") as f:
        f.write(resp.content)

    conn = sqlite3.connect(DB_TEMP_PATH)
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    df_prices = pd.read_sql_query("SELECT * FROM prices", conn)
    conn.close()

    if "date" in df_prices.columns:
        df_prices["date"] = pd.to_datetime(df_prices["date"])
        df_prices = df_prices.sort_values("date")
        # ajusta fuso (caso precise)
        df_prices["date_local"] = df_prices["date"]  # aqui você ajusta se quiser
    else:
        df_prices["date_local"] = pd.NaT

    return df_products, df_prices


def get_latest_price(df_prices: pd.DataFrame, product_id: int):
    df_prod = df_prices[df_prices["product_id"] == product_id]
    df_prod = df_prod.dropna(subset=["price"])
    if df_prod.empty:
        return None
    return df_prod["price"].iloc[-1]


# =============================================================================
# FUNÇÕES DE SCRAPING – SÓ PARA PEGAR IMAGEM (NÃO MEXEM EM BANCO)
# =============================================================================


def get_product_image(url: str) -> str | None:
    """Tenta achar a imagem principal da Amazon (somente leitura)."""
    try:
        html = cached_html(url)
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1) landingImage
    img = soup.find("img", {"id": "landingImage"})
    if img and img.get("src"):
        return img["src"]

    # 2) data-old-hires
    img = soup.find("img", attrs={"data-old-hires": True})
    if img and img.get("data-old-hires"):
        return img["data-old-hires"]

    # 3) data-a-dynamic-image
    img = soup.find("img", attrs={"data-a-dynamic-image": True})
    if img and img.get("data-a-dynamic-image"):
        try:
            dyn = json.loads(img["data-a-dynamic-image"])
            urls = list(dyn.keys())
            for u in urls:
                if "images/I/" in u or "m.media-amazon.com" in u:
                    return u
            if urls:
                return urls[0]
        except Exception:
            pass

    # 4) meta og:image
    meta = soup.find("meta", {"property": "og:image"})
    if meta and meta.get("content"):
        return meta["content"]

    # 5) qualquer img com /images/I/
    any_img = soup.find("img", src=lambda x: x and "images/I/" in x)
    if any_img and any_img.get("src"):
        return any_img["src"]

    # 6) script com "hiRes"
    for script in soup.find_all("script"):
        if script.string and "hiRes" in script.string:
            m = re.search(r'"hiRes":"(.*?)"', script.string)
            if m:
                return m.group(1).replace("\\/", "/")

    return None


# =============================================================================
# CONFIG STREAMLIT + CSS
# =============================================================================

st.set_page_config(
    page_title="Monitor de Preços Amazon",
    layout="wide",
    page_icon="💹",
)

st.markdown(
    """
    <style>
    .main {
        background: radial-gradient(circle at top left, #111827, #020617);
        color: #e5e7eb;
    }

    /* SIDEBAR --------------------------------------------------------------- */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #020617 0%, #020617 40%, #020617 100%);
        color: #e5e7eb;
        border-right: 1px solid #1f2937;
    }
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {
        color: #f9fafb !important;
    }
    .sidebar-title {
        font-size: 1.1rem;
        font-weight: 700;
        margin-bottom: 0.15rem;
    }
    .sidebar-sub {
        font-size: 0.80rem;
        color: #9ca3af;
        margin-bottom: 0.8rem;
    }
    .sidebar-box {
        padding: 0.9rem 1rem;
        background: rgba(15,23,42,0.85);
        border-radius: 0.75rem;
        border: 1px solid rgba(148,163,184,0.35);
        box-shadow: 0 10px 30px rgba(15,23,42,0.75);
    }

    /* TÍTULOS --------------------------------------------------------------- */
    h1, h2, h3, h4, h5, h6 {
        color: #e5e7eb !important;
    }

    .main-title {
        font-size: 1.9rem;
        font-weight: 800;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-bottom: 0.2rem;
    }
    .main-title span.icon {
        font-size: 1.6rem;
    }
    .section-title {
        font-size: 1.2rem;
        font-weight: 700;
        margin-top: 1.5rem;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 0.4rem;
    }
    .section-title::after {
        content: "";
        flex: 1;
        height: 1px;
        background: linear-gradient(90deg, rgba(148,163,184,0.7), transparent);
        opacity: 0.7;
    }

    /* PILL DE ÚLTIMA ATUALIZAÇÃO -------------------------------------------*/
    .last-update-pill {
        padding: 0.35rem 0.9rem;
        border-radius: 999px;
        border: 1px solid rgba(148,163,184,0.5);
        background: rgba(15,23,42,0.9);
        font-size: 0.78rem;
        display: inline-flex;
        gap: 0.35rem;
        align-items: center;
        justify-content: flex-end;
        white-space: nowrap;
    }
    .last-update-pill strong {
        color: #e5e7eb;
    }

    /* CARDS DE PRODUTO ------------------------------------------------------ */
    .product-card-flag {
        display: none;
    }

    div[data-testid="stVerticalBlock"]:has(.product-card-flag) {
        position: relative;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        gap: 0.45rem;
        background: radial-gradient(circle at top left, #020617, #020617 40%, #020617 100%);
        border-radius: 1rem;
        border: 1px solid rgba(148,163,184,0.45);
        box-shadow: 0 12px 35px rgba(15,23,42,0.9);
        padding: 0.9rem 1rem 0.9rem 1rem;
        min-height: 320px;
        transition: all 0.18s ease-out;
        margin-bottom: 1.7rem;
        overflow: hidden;
    }
    div[data-testid="stVerticalBlock"]:has(.product-card-flag)::before {
        content: "";
        position: absolute;
        inset: 0;
        background: radial-gradient(circle at top right, rgba(56,189,248,0.10), transparent 55%);
        opacity: 0.9;
        pointer-events: none;
    }
    div[data-testid="stVerticalBlock"]:has(.product-card-flag):hover {
        transform: translateY(-4px);
        box-shadow: 0 20px 50px rgba(15,23,42,0.95);
        border-color: rgba(129,140,248,0.8);
    }

    .product-title {
        font-size: 0.90rem;
        font-weight: 600;
        color: #e5e7eb;
        margin-bottom: 0.25rem;
        min-height: 2.6em;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        position: relative;
        z-index: 1;
    }

    .product-image-wrapper {
        width: 100%;
        text-align: center;
        margin: 0.25rem 0 0.5rem 0;
        position: relative;
        z-index: 1;
    }
    .product-image-wrapper img {
        max-width: 230px;
        max-height: 170px;
        width: 100%;
        object-fit: contain;
        border-radius: 0.75rem;
    }

    .product-image-placeholder {
        width: 100%;
        height: 170px;
        background: #111827;
        border-radius: 0.75rem;
        border: 1px solid #334155;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.8rem;
        color: #64748b;
    }

    .product-card-footer {
        position: relative;
        z-index: 1;
        margin-top: auto;
        padding-top: 0.35rem;
        border-top: 1px dashed rgba(55,65,81,0.8);
    }

    .product-price-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.25rem;
        padding: 0.2rem 0.55rem;
        border-radius: 999px;
        font-size: 0.76rem;
        background: rgba(15,23,42,0.9);
        border: 1px solid rgba(129,140,248,0.6);
        color: #ede9fe;
    }

    .product-actions-row {
        position: relative;
        z-index: 1;
        margin-top: 0.45rem;
    }

    /* BADGES --------------------------------------------------------------- */
    .metric-badge {
        display: inline-block;
        padding: 0.22rem 0.6rem;
        border-radius: 999px;
        background: #020617;
        font-size: 0.72rem;
        margin-right: 0.3rem;
        margin-bottom: 0.15rem;
        color: #e5e7eb;
        border: 1px solid #64748b;
    }
    .metric-badge.positive { border-color: #22c55e; }
    .metric-badge.negative { border-color: #ef4444; }
    .metric-badge.neutral  { border-color: #64748b; }

    a { color: #38bdf8 !important; }

    .stButton>button {
        border-radius: 999px !important;
        font-size: 0.78rem !important;
        padding: 0.35rem 0.85rem !important;
        border: 1px solid rgba(148,163,184,0.4);
        background: rgba(15,23,42,0.85);
    }
    .stButton>button:hover {
        border-color: rgba(129,140,248,0.9);
        background: rgba(30,64,175,0.95);
    }

    /* CARD DE DETALHES ----------------------------------------------------- */

    .detail-card-flag {
        display: none;
    }

    div[data-testid="stVerticalBlock"]:has(.detail-card-flag) {
        position: relative;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        gap: 0.5rem;

        background: radial-gradient(circle at top left, #020617, #020617 40%, #020617 100%);
        border-radius: 1.2rem;
        border: 1px solid rgba(148,163,184,0.6);
        box-shadow: 0 18px 45px rgba(15,23,42,0.95);

        padding: 1.6rem 2rem 2rem 2rem;
        max-width: 1800px !important;
        width: 100% !important;
        min-height: 620px;

        overflow: hidden;
    }

    div[data-testid="stVerticalBlock"]:has(.detail-card-flag)::before {
        content: "";
        position: absolute;
        inset: 0;
        background: radial-gradient(circle at top right, rgba(56,189,248,0.20), transparent 60%);
        opacity: 0.95;
        pointer-events: none;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# SIDEBAR – INFORMAÇÕES / ASSINATURA (SEM CADASTRO)
# =============================================================================

with st.sidebar:
    st.markdown('<div class="sidebar-box">', unsafe_allow_html=True)
    st.markdown(
        '<div class="sidebar-title">📊 Fonte dos dados</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="sidebar-sub">
        Este painel está em <strong>modo somente leitura</strong>.<br><br>
        Os dados vêm do arquivo <code>scraping.db</code> que é
        atualizado automaticamente pelo <strong>GitHub Actions</strong>
        a cada 5 minutos.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div style="
            margin-top: 1rem;
            padding: 0.85rem 1rem;
            border-radius: 10px;
            background: rgba(30,41,59,0.55);
            border: 1px solid rgba(148,163,184,0.25);
            box-shadow: 0 0 12px rgba(0,0,0,0.25);
            color: #cbd5e1;
            font-size: 0.80rem;
            text-align: center;
            line-height: 1.25rem;
        ">
            <span style="opacity:0.8;">🧑‍💻 Sistema desenvolvido por:</span><br>
            <strong style="color:#f8fafc;">👨‍💻 Eduardo Feres</strong><br>
            <strong style="color:#f8fafc;">🧙‍♂️ Guilherme Pires</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

df_products, df_prices = get_data()

# Última atualização (baseada na última linha de prices.date_local)
if not df_prices.empty and "date_local" in df_prices.columns:
    last_dt = df_prices["date_local"].max()
    last_str = (
        last_dt.strftime("%d/%m %H:%M") if pd.notna(last_dt) else "--/-- --:--"
    )
else:
    last_str = "--/-- --:--"

header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.markdown(
        """
        <div class="main-title">
            <span class="icon">💹</span>
            <span>Monitor de Preços</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with header_col2:
    st.markdown(
        f"""
        <div style="display:flex; justify-content:flex-end; margin-top:0.3rem;">
            <div class="last-update-pill">
                <span>🕒 Última atualização:</span>
                <strong>{last_str}</strong>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

if df_products.empty:
    st.warning(
        "Nenhum produto cadastrado no banco de dados do GitHub. "
        "Verifique o scraping.db do repositório."
    )
    st.stop()

sns.set_style("whitegrid")

# ----------------------------------------------------------------------------- #
# CARD DE DETALHES – CENTRALIZADO (SOMENTE LEITURA)
# ----------------------------------------------------------------------------- #

selected_id = st.session_state.get("selected_product_id")

if selected_id is not None and selected_id in df_products["id"].values:
    product = df_products[df_products["id"] == selected_id].iloc[0]
    df_prod = df_prices[df_prices["product_id"] == selected_id].copy()

    st.markdown("### Detalhes do produto selecionado")

    _, center_col, _ = st.columns([1, 2, 1])
    with center_col:
        with st.container():
            st.markdown(
                '<div class="detail-card-flag"></div>',
                unsafe_allow_html=True,
            )

            top_cols = st.columns([5, 1])
            with top_cols[0]:
                st.markdown(f"**{product['name']}**")
            with top_cols[1]:
                if st.button("✕ Fechar", key="close_detail"):
                    st.session_state["selected_product_id"] = None
                    st.rerun()

            img_col, info_col = st.columns([1, 1])
            with img_col:
                img_url = product.get("image_url")
                if not img_url:
                    img_url = get_product_image(product["url"])

                if img_url:
                    st.image(img_url, width=170)
                else:
                    st.info("Sem imagem disponível.")
            with info_col:
                st.markdown(f"[Ver na Amazon]({product['url']})")

            st.markdown("---")
            st.write("**Histórico de preços**")

            if df_prod.empty:
                st.info("Sem histórico ainda para este produto.")
            else:
                fig, ax = plt.subplots(figsize=(4.5, 2.2))
                sns.lineplot(
                    data=df_prod,
                    x="date_local",
                    y="price",
                    marker="o",
                    ax=ax,
                )
                ax.set_xlabel("Data/Hora", fontsize=7)
                ax.set_ylabel("Preço (R$)", fontsize=7)
                ax.tick_params(axis="both", labelsize=7)
                ax.xaxis.set_major_formatter(
                    mdates.DateFormatter("%d/%m\n%H:%M")
                )
                plt.tight_layout()
                st.pyplot(fig)

                df_valid = df_prod.dropna(subset=["price"])
                if len(df_valid) >= 2:
                    first_price = df_valid["price"].iloc[0]
                    last_price = df_valid["price"].iloc[-1]
                    max_price = df_valid["price"].max()
                    min_price = df_valid["price"].min()
                    diff_abs = last_price - first_price

                    if diff_abs > 0:
                        tendencia = "subiu"
                        badge_class = "positive"
                    elif diff_abs < 0:
                        tendencia = "caiu"
                        badge_class = "negative"
                    else:
                        tendencia = "estável"
                        badge_class = "neutral"

                    st.markdown(
                        f"""
                        <span class="metric-badge {badge_class}">
                            Tendência: {tendencia}
                        </span>
                        <span class="metric-badge">
                            Atual: R$ {last_price:.2f}
                        </span>
                        <span class="metric-badge">
                            Mín: R$ {min_price:.2f}
                        </span>
                        <span class="metric-badge">
                            Máx: R$ {max_price:.2f}
                        </span>
                        """,
                        unsafe_allow_html=True,
                    )

# ----------------------------------------------------------------------------- #
# GRID DE CARDS – PRODUTOS MONITORADOS
# ----------------------------------------------------------------------------- #

st.markdown(
    '<h2 class="section-title">Produtos monitorados</h2>',
    unsafe_allow_html=True,
)

cols = st.columns(3, gap="large")

for idx, (_, product) in enumerate(df_products.iterrows()):
    col = cols[idx % 3]

    with col:
        with st.container():
            st.markdown(
                '<div class="product-card-flag"></div>',
                unsafe_allow_html=True,
            )

            st.markdown(
                f'<div class="product-title">{product["name"]}</div>',
                unsafe_allow_html=True,
            )

            img_url = product.get("image_url")
            if not img_url:
                img_url = get_product_image(product["url"])

            st.markdown(
                '<div class="product-image-wrapper">',
                unsafe_allow_html=True,
            )
            if img_url:
                st.image(img_url, use_column_width=False, width=230)
            else:
                st.markdown(
                    '<div class="product-image-placeholder">Imagem indisponível</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

            latest_price = get_latest_price(df_prices, product["id"])
            st.markdown(
                '<div class="product-card-footer">',
                unsafe_allow_html=True,
            )
            if latest_price is not None:
                st.markdown(
                    f'<span class="product-price-badge">💰 R$ {latest_price:.2f}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<span class="product-price-badge">Sem preço ainda</span>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown(
                '<div class="product-actions-row">',
                unsafe_allow_html=True,
            )
            b1, _ = st.columns(2)
            with b1:
                if st.button("Ver detalhes", key=f"view_{product['id']}"):
                    st.session_state["selected_product_id"] = product["id"]
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

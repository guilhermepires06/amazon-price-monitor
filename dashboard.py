import sqlite3
import json
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
        # coluna já existe
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
# UI / CSS (SIDEBAR FIXA + CARDS)
# =============================================================================

st.set_page_config(
    page_title="Monitor de Preços Amazon",
    layout="wide",
    page_icon="💹",
)

st.markdown(
    """
    <style>
    /* SIDEBAR FIXA */
    [data-testid="stSidebar"] {
        position: fixed !important;
        top: 0;
        left: 0;
        height: 100vh !important;
        z-index: 999;
        overflow-y: auto !important;
    }

    /* Conteúdo principal deslocado */
    [data-testid="stAppViewContainer"] {
        padding-left: 18rem !important;
    }

    /* Header acompanha deslocamento */
    [data-testid="stHeader"] {
        margin-left: 18rem !important;
    }

    /* CARD PRINCIPAL */
    .detail-card {
        padding: 1.1rem 1.3rem;
        border-radius: 0.9rem;
        background: radial-gradient(circle at top left, #020617 0, #020617 55%, #020617 100%);
        border: 1px solid rgba(148,163,184,0.45);
        box-shadow: 0 14px 35px rgba(15,23,42,0.85);
        margin-bottom: 1.3rem;
        transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
    }

    .detail-card:hover {
        border-color: #38bdf8;
        box-shadow: 0 18px 45px rgba(8,47,73,0.95);
        transform: translateY(-2px);
    }

    /* CABEÇALHO DO CARD */
    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 0.75rem;
        margin-bottom: 0.6rem;
    }

    .card-title {
        font-size: 1.05rem;
        font-weight: 600;
        color: #e5e7eb;
        letter-spacing: 0.01em;
    }

    .card-subtitle {
        font-size: 0.8rem;
        color: #9ca3af;
    }

    .card-header-right {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 0.25rem;
        min-width: 260px;
    }

    /* BADGES / MÉTRICAS */
    .card-metrics {
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
        justify-content: flex-end;
    }

    .metric-badge {
        display: inline-flex;
        align-items: center;
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        background: #020617;
        font-size: 0.7rem;
        margin-right: 0;
        color: #e5e7eb;
        border: 1px solid rgba(148,163,184,0.6);
    }
    .metric-badge.positive { border-color: #22c55e; }
    .metric-badge.negative { border-color: #ef4444; }
    .metric-badge.neutral  { border-color: #64748b; }

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

    /* AJUSTE DOS TEXTOS DE INSIGHT */
    .insight-text {
        font-size: 0.85rem;
        color: #e5e7eb;
        margin-bottom: 0.1rem;
    }

    .insight-text strong {
        color: #f9fafb;
    }

    /* Link "Ver na Amazon" */
    .amazon-link {
        font-size: 0.8rem;
        color: #38bdf8 !important;
        text-decoration: none;
    }
    .amazon-link:hover {
        text-decoration: underline;
    }

    /* Responsivo */
    @media (max-width: 1024px) {
        [data-testid="stAppViewContainer"] {
            padding-left: 0 !important;
        }
        [data-testid="stHeader"] {
            margin-left: 0 !important;
        }
        .card-header-right {
            align-items: flex-start;
        }
        .card-metrics {
            justify-content: flex-start;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# SIDEBAR
# =============================================================================

with st.sidebar:
    st.markdown("### 📦 Produtos monitorados")
    st.markdown(
        "Interface somente de leitura  \n"
        "Sistema hospedado no GitHub"
    )

    st.markdown(
        "[🔗 Repositório no GitHub](https://github.com/guilhermepires06/amazon-price-monitor)"
    )

    st.markdown("---")

    st.markdown("**Sistema desenvolvido por:**")
    st.markdown("🧠 Eduardo Feres")
    st.markdown("👨‍💻 Guilherme Pires")

    st.markdown("---")
    st.markdown("© 2025 - Amazon Price Monitor")

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
# BLOCO DE PRODUTOS (cards melhorados)
# =============================================================================

for _, product in df_products.iterrows():
    df_prod = df_prices[df_prices["product_id"] == product["id"]].copy()
    df_valid = df_prod.dropna(subset=["price"])

    # --------- CÁLCULO DE MÉTRICAS / INSIGHTS ---------
    if not df_prod.empty and len(df_valid) >= 2:
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

        metrics_html = (
            f'<div class="card-metrics">'
            f'<span class="metric-badge {trend[1]}">Tendência: {trend[0]}</span>'
            f'<span class="metric-badge">Atual: R$ {last:.2f}</span>'
            f'<span class="metric-badge">Mín: R$ {min_p:.2f}</span>'
            f'<span class="metric-badge">Máx: R$ {max_p:.2f}</span>'
            f'</div>'
        )

        insight_1 = (
            f"**1. Tendência:** O preço variou de R$ {first:.2f} "
            f"para R$ {last:.2f} ({diff:+.2f}, {pct:+.1f}%)."
        )
        insight_2 = (
            f"**2. Faixa:** mínimo R$ {min_p:.2f}, máximo R$ {max_p:.2f}, "
            f"média R$ {mean_p:.2f}."
        )

        if last == min_p:
            insight_3 = "**3. Momento:** Preço no mínimo histórico — excelente p/ compra."
        elif last == max_p:
            insight_3 = "**3. Momento:** Preço no máximo histórico — talvez esperar."
        else:
            insight_3 = "**3. Momento:** Preço dentro da faixa normal do histórico."

    elif not df_prod.empty and len(df_valid) == 1:
        # Só um ponto de preço válido
        last = df_valid["price"].iloc[-1]
        metrics_html = (
            f'<div class="card-metrics">'
            f'<span class="metric-badge neutral">Histórico curto</span>'
            f'<span class="metric-badge">Atual: R$ {last:.2f}</span>'
            f'</div>'
        )
        insight_1 = "**1. Tendência:** Ainda não há dados suficientes para análise de variação."
        insight_2 = ""
        insight_3 = ""
    else:
        # Sem histórico
        metrics_html = (
            '<div class="card-metrics">'
            '<span class="metric-badge neutral">Sem histórico de preços</span>'
            '</div>'
        )
        insight_1 = ""
        insight_2 = ""
        insight_3 = ""

    # --------- CARD VISUAL ---------
    st.markdown('<div class="detail-card">', unsafe_allow_html=True)

    # Cabeçalho do card (título + métricas)
    st.markdown(
        f"""
        <div class="card-header">
            <div>
                <div class="card-title">{product['name']}</div>
            </div>
            <div class="card-header-right">
                {metrics_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Corpo: imagem + gráfico
    col_img, col_graph = st.columns([1, 1.9])

    with col_img:
        img_url = product.get("image_url") or get_product_image(product["url"])
        if img_url:
            st.image(img_url, width=220)
        else:
            st.info("Sem imagem disponível")

        st.markdown(
            f'<a class="amazon-link" href="{product["url"]}" target="_blank">Ver na Amazon</a>',
            unsafe_allow_html=True,
        )

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
            plt.tight_layout()
            st.pyplot(fig)

    # Textos de insight embaixo, alinhados com o card
    if insight_1 or insight_2 or insight_3:
        if insight_1:
            st.markdown(f'<div class="insight-text">{insight_1}</div>', unsafe_allow_html=True)
        if insight_2:
            st.markdown(f'<div class="insight-text">{insight_2}</div>', unsafe_allow_html=True)
        if insight_3:
            st.markdown(f'<div class="insight-text">{insight_3}</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="insight-text">Ainda não há dados suficientes para análises.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

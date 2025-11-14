import sqlite3
import json
import os
import tempfile
from datetime import datetime, timedelta

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

GITHUB_DB_URL = (
    "https://raw.githubusercontent.com/guilhermepires06/amazon-price-monitor/main/scraping.db"
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
# BANCO – SEMPRE DO GITHUB
# =============================================================================

@st.cache_data(show_spinner=False, ttl=300)
def get_data():
    """
    Baixa o scraping.db diretamente do GitHub (RAW),
    grava temporariamente e lê com sqlite.
    Interpreta o campo `date` como UTC e converte para horário de Brasília.
    NÃO mexe nos valores de preço (nem 0, nem outlier).
    """
    resp = requests.get(GITHUB_DB_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    conn = sqlite3.connect(tmp_path)
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    df_prices = pd.read_sql_query("SELECT * FROM prices", conn)
    conn.close()

    # remove o arquivo temporário
    try:
        os.remove(tmp_path)
    except OSError:
        pass

    # Ajuste de datas
    if "date" in df_prices.columns:
        df_prices["date"] = pd.to_datetime(df_prices["date"], utc=True, errors="coerce")
        df_prices = df_prices.dropna(subset=["date"])
        df_prices = df_prices.sort_values("date")

        try:
            df_prices["date_local"] = (
                df_prices["date"]
                .dt.tz_convert("America/Sao_Paulo")
                .dt.tz_localize(None)
            )
        except Exception:
            df_prices["date_local"] = (
                df_prices["date"].dt.tz_localize(None) - pd.Timedelta(hours=3)
            )
    else:
        df_prices["date_local"] = pd.NaT

    # NÃO mexe em df_prices["price"]: mostra o valor cru do banco
    return df_products, df_prices


# =============================================================================
# SCRAPING IMAGEM
# =============================================================================

@st.cache_data(show_spinner=False, ttl=600)
def get_product_image(url: str):
    try:
        html = requests.get(url, headers=HEADERS, timeout=20).text
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
            return list(dyn.keys())[0]
        except Exception:
            pass

    meta = soup.find("meta", {"property": "og:image"})
    if meta:
        return meta.get("content")

    return None


# =============================================================================
# UI / CSS
# =============================================================================

st.set_page_config(
    page_title="Monitor de Preços Amazon",
    layout="wide",
    page_icon="💹",
)

st.markdown("""
<style>

/* SIDEBAR FIXA */
[data-testid="stSidebar"] {
    position: fixed !important;
    top:0;
    left:0;
    height: 100vh !important;
    z-index: 999;
    overflow-y: auto !important;
}

/* Conteúdo principal deslocado */
[data-testid="stAppViewContainer"] {
    padding-left: 18rem !important;
}

[data-testid="stHeader"] {
    margin-left: 18rem !important;
}

/* Card */
.detail-card {
    padding: 1rem;
    border-radius: 0.9rem;
    background: #020617;
    border: 1px solid rgba(148,163,184,0.5);
    margin-bottom: 1.25rem;
    box-shadow: 0 12px 30px rgba(15,23,42,0.7);
}

/* Badges */
.metric-badge {
    display:inline-block;
    padding:0.2rem 0.6rem;
    border-radius:999px;
    background:#0f172a;
    font-size:0.7rem;
    margin-right:0.3rem;
    color:#e5e7eb;
    border:1px solid rgba(148,163,184,0.6);
}
.metric-badge.positive { border-color:#22c55e; }
.metric-badge.negative { border-color:#ef4444; }
.metric-badge.neutral  { border-color:#64748b; }

.last-update-pill {
    padding:0.35rem 0.9rem;
    border-radius:999px;
    border:1px solid rgba(148,163,184,0.6);
    background:#020617;
    font-size:0.75rem;
    display:flex;
    gap:0.35rem;
    align-items:center;
}

</style>
""", unsafe_allow_html=True)


# =============================================================================
# SIDEBAR
# =============================================================================

with st.sidebar:
    st.markdown("### 📦 Produtos monitorados")
    st.markdown("Dados carregados 100% do GitHub (banco remoto).")
    st.markdown("Robô atualiza o banco a cada 5 minutos.")
    st.markdown("Você pode forçar a leitura clicando em **Atualizar agora** no topo.")
    st.markdown("[🔗 Repositório no GitHub](https://github.com/guilhermepires06/amazon-price-monitor)")
    st.markdown("---")
    st.markdown("**Sistema desenvolvido por:**")
    st.markdown("🧠 Eduardo Feres\n 👨‍💻 Guilherme Pires")
    st.markdown("---")
    st.markdown("📌 *Dashboard em modo somente leitura.*")
    st.markdown("© 2025 - Amazon Price Monitor")


# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

st.title("💹 Monitor de Preços")

# Botão para forçar atualização (limpa cache e recarrega)
col_top1, col_top2 = st.columns([4, 1])
with col_top2:
    if st.button("🔄 Atualizar agora", use_container_width=True):
        get_data.clear()
        st.rerun()

df_products, df_prices = get_data()

if not df_prices.empty and df_prices["date_local"].notna().any():
    global_last = df_prices["date_local"].max()
    global_last_str = global_last.strftime("%d/%m %H:%M")
else:
    global_last_str = "--/-- --:--"

st.markdown(
    f"""<div class="last-update-pill">🕒 Última atualização no banco: <strong>{global_last_str}</strong></div>""",
    unsafe_allow_html=True,
)

st.markdown("## Produtos monitorados")

sns.set_style("whitegrid")

# =============================================================================
# LOOP DOS PRODUTOS
# =============================================================================

for _, product in df_products.iterrows():
    df_prod = df_prices[df_prices["product_id"] == product["id"]].copy()

    # Último registro bruto (mesmo que preço seja 0 ou NaN)
    if not df_prod.empty:
        last_row = df_prod.sort_values("date_local").iloc[-1]
        last_dt = last_row["date_local"]
        last_price = last_row["price"]
        last_dt_str = last_dt.strftime("%d/%m %H:%M") if pd.notna(last_dt) else "--:--"
    else:
        last_dt_str = "--:--"
        last_price = None

    st.markdown('<div class="detail-card">', unsafe_allow_html=True)
    st.markdown(f"### {product['name']}")

    col_img, col_graph = st.columns([1, 1.8])

    # IMAGEM
    with col_img:
        img_url = product.get("image_url") or get_product_image(product["url"])
        if img_url:
            st.image(img_url, width=220)
        st.markdown(f"[Ver na Amazon]({product['url']})")

    # GRÁFICO + INSIGHTS
    with col_graph:
        if df_prod.empty:
            st.info("Sem histórico deste produto.")
        else:
            # ---------- GRÁFICO COM TODOS OS VALORES ----------
            fig, ax = plt.subplots(figsize=(6, 2.5))
            sns.lineplot(df_prod, x="date_local", y="price", marker="o", ax=ax)
            ax.set_xlabel("Data/Hora (BR)")
            ax.set_ylabel("Preço (R$)")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
            plt.xticks(rotation=25)
            st.pyplot(fig)

            # Badge do último valor exatamente como está no banco
            if last_price is not None and pd.notna(last_price):
                last_price_str = f"R$ {last_price:.2f}"
            else:
                last_price_str = "sem valor"

            st.markdown(
                f'<span class="metric-badge neutral">Últ. coleta: {last_dt_str} — {last_price_str}</span>',
                unsafe_allow_html=True,
            )

            # ---------- INSIGHTS SIMPLES (SEM FILTRO) ----------
            df_valid = df_prod.dropna(subset=["price"])
            if len(df_valid) >= 2:
                df_valid = df_valid.sort_values("date_local")
                first = df_valid["price"].iloc[0]
                last = df_valid["price"].iloc[-1]
                diff = last - first
                pct = diff / first * 100 if first != 0 else 0

                max_p = df_valid["price"].max()
                min_p = df_valid["price"].min()
                mean_p = df_valid["price"].mean()

                st.write(f"**Tendência:** {diff:+.2f} ({pct:+.1f}%)")
                st.write(
                    f"**Faixa:** min R$ {min_p:.2f}, máx R$ {max_p:.2f}, "
                    f"média R$ {mean_p:.2f}"
                )
            else:
                st.write("Dados insuficientes para análises.")

    st.markdown("</div>", unsafe_allow_html=True)

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

# 👉 ATENÇÃO: esse é o ÚNICO banco que o dashboard lê
GITHUB_DB_URL = ("https://raw.githubusercontent.com/guilhermepires06/amazon-price-monitor/main/scraping.db")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


# =============================================================================
# BANCO – SEMPRE DO GITHUB (COM CACHE LIMPO PELO BOTÃO)
# =============================================================================

@st.cache_data(show_spinner=False, ttl=300)
def get_data():
    """
    Baixa o scraping.db diretamente do GitHub (RAW),
    grava em arquivo temporário e lê com sqlite.
    Interpreta o campo `date` como UTC e converte para horário de Brasília.
    NÃO altera os valores de price (mostra o que está no banco).
    Se houver erro HTTP/rede, retorna DataFrames vazios e não derruba o app.
    """
    try:
        resp = requests.get(GITHUB_DB_URL, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        st.error(f"❌ Erro ao baixar o banco do GitHub: {e}")
        # dataframes vazios para o app continuar rodando
        return pd.DataFrame(), pd.DataFrame()

    if resp.status_code != 200:
        st.error(
            f"❌ Falha ao baixar scraping.db do GitHub. "
            f"Status HTTP: {resp.status_code}"
        )
        return pd.DataFrame(), pd.DataFrame()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    try:
        conn = sqlite3.connect(tmp_path)
        df_products = pd.read_sql_query("SELECT * FROM products", conn)
        df_prices = pd.read_sql_query("SELECT * FROM prices", conn)
        conn.close()
    except Exception as e:
        st.error(f"❌ Erro ao ler o arquivo de banco de dados: {e}")
        return pd.DataFrame(), pd.DataFrame()
    finally:
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

    # aqui NÃO mexemos em price: é o valor cru que o scraper salvou
    return df_products, df_prices


# =============================================================================
# SCRAPING IMAGEM (SOMENTE PARA O THUMB DA PÁGINA)
# =============================================================================

@st.cache_data(show_spinner=False, ttl=60 * 60)
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
    page_title="Monitor de Preços Amazon (v2)",
    layout="wide",
    page_icon="💹",
)

st.markdown(
    """
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

/* Versão do dashboard */
.version-chip {
    font-size: 0.7rem;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    border: 1px solid #64748b;
    margin-left: 0.5rem;
    color: #cbd5e1;
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
    st.markdown("Dados carregados **100%** do GitHub (`scraping.db`).")
    st.markdown("Robô atualiza o banco a cada 5 minutos via GitHub Actions.")
    st.markdown("Use o botão **🔄 Atualizar agora** para forçar leitura nova.")
    st.markdown("[🔗 Repositório no GitHub](https://github.com/guilhermepires06/amazon-price-monitor)")
    st.markdown("---")
    st.markdown("**Sistema desenvolvido por:**")
    st.markdown("🧠 Eduardo Feres\n👨‍💻 Guilherme Pires")
    st.markdown("---")
    st.markdown("📌 *Dashboard somente leitura (não altera o banco).*")
    st.markdown("© 2025 - Amazon Price Monitor")


# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

# título com chip de versão pra você ver que trocou de fato
title_col, ver_col, btn_col = st.columns([3, 1, 1])

with title_col:
    st.title("💹 Monitor de Preços Amazon")

with ver_col:
    st.markdown('<div class="version-chip">v2 • dashboard.py</div>', unsafe_allow_html=True)

with btn_col:
    if st.button("🔄 Atualizar agora", use_container_width=True):
        # Zera cache do get_data() e recarrega tudo
        get_data.clear()
        st.rerun()

# Carrega dados (do cache ou frescos, se acabou de clicar no botão)
df_products, df_prices = get_data()

# Se falhou para baixar o banco, df_products/df_prices estarão vazios
if df_products.empty or df_prices.empty:
    st.warning("Não foi possível carregar dados do banco remoto no momento.")
    st.stop()

if df_prices["date_local"].notna().any():
    global_last = df_prices["date_local"].max()
    global_last_str = global_last.strftime("%d/%m %H:%M")
else:
    global_last_str = "--/-- --:--"

st.markdown(
    f"""<div class="last-update-pill">
        🕒 Última data registrada no banco: <strong>{global_last_str}</strong>
    </div>""",
    unsafe_allow_html=True,
)

st.markdown("## Produtos monitorados")

sns.set_style("whitegrid")

# =============================================================================
# LOOP DOS PRODUTOS
# =============================================================================

for _, product in df_products.iterrows():
    df_prod = df_prices[df_prices["product_id"] == product["id"]].copy()

    # Info de último registro desse produto
    if not df_prod.empty and df_prod["date_local"].notna().any():
        last_row = df_prod.sort_values("date_local").iloc[-1]
        last_dt = last_row["date_local"]
        last_price = last_row["price"]
        last_dt_str = last_dt.strftime("%d/%m %H:%M")
    else:
        last_dt_str = "--/-- --:--"
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

    # GRÁFICO + INFO
    with col_graph:
        if df_prod.empty:
            st.info("Sem histórico deste produto.")
        else:
            # gráfico cru: exatamente como está no banco
            fig, ax = plt.subplots(figsize=(6, 2.5))
            sns.lineplot(df_prod, x="date_local", y="price", marker="o", ax=ax)
            ax.set_xlabel("Data/Hora (BR)")
            ax.set_ylabel("Preço (R$)")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
            plt.xticks(rotation=25)
            st.pyplot(fig)

            # Badge com última linha da tabela
            if last_price is not None and pd.notna(last_price):
                last_price_str = f"R$ {last_price:.2f}"
            else:
                last_price_str = "sem valor (NULL / None)"

            st.markdown(
                f'<span class="metric-badge neutral">'
                f'Últ. registro no banco: {last_dt_str} — {last_price_str}'
                f'</span>',
                unsafe_allow_html=True,
            )

            # Pequeno debug pra você ver o que o banco tem pra esse produto
            with st.expander("Ver últimos registros brutos desse produto"):
                st.write(
                    df_prod.sort_values("date_local", ascending=False)
                    .head(10)[["date_local", "price"]]
                )

    st.markdown("</div>", unsafe_allow_html=True)

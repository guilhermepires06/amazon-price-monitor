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

# URL RAW do banco no GitHub (ATUALMENTE NÃO ESTÁ SENDO USADA)
GITHUB_DB_URL = "https://raw.githubusercontent.com/guilhermepires06/amazon-price-monitor/main/scraping.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# =============================================================================
# AJUSTE DE SCHEMA (image_url)
# =============================================================================


def ensure_schema():
    """Garante que a tabela products tenha a coluna image_url."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        # coluna já existe
        pass
    conn.close()


# Garante schema no banco local inicial
ensure_schema()

# =============================================================================
# (OPCIONAL) SINCRONIZAÇÃO COM GITHUB - **NÃO USADA NO APP**
# =============================================================================


def sync_db_from_github():
    """
    Baixa o scraping.db diretamente do GitHub (URL RAW) e sobrescreve o arquivo local.

    IMPORTANTE:
    - NÃO está sendo chamado em nenhum lugar do painel.
    - Se você rodar isso manualmente, ele vai APAGAR alterações locais.
    """
    if not GITHUB_DB_URL:
        return

    try:
        resp = requests.get(GITHUB_DB_URL, timeout=20)
        resp.raise_for_status()
        with open(DB_NAME, "wb") as f:
            f.write(resp.content)

        # garante que a coluna image_url exista no banco recém-baixado
        ensure_schema()

        print("[sync_db_from_github] Banco atualizado a partir do GitHub.")
    except Exception as e:
        print(f"[sync_db_from_github] Erro ao baixar banco do GitHub: {e}")


# =============================================================================
# CACHE – HTML
# =============================================================================


@st.cache_data(show_spinner=False, ttl=600)
def cached_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


# =============================================================================
# FUNÇÕES DE BANCO
# =============================================================================


def get_data():
    """
    Lê products e prices do banco LOCAL.

    ATENÇÃO:
    - NÃO sincroniza com o GitHub aqui.
    - Assim, tudo que você alterar via scraper permanece no arquivo scraping.db.
    """
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


def update_product_image(product_id: int, image_url: str | None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE products SET image_url = ? WHERE id = ?",
        (image_url, product_id),
    )
    conn.commit()
    conn.close()


def get_latest_price(df_prices: pd.DataFrame, product_id: int):
    df_prod = df_prices[df_prices["product_id"] == product_id]
    df_prod = df_prod.dropna(subset=["price"])
    if df_prod.empty:
        return None
    return df_prod["price"].iloc[-1]


# =============================================================================
# FUNÇÕES DE SCRAPING
# =============================================================================


def get_product_image(url: str) -> str | None:
    """Tenta achar a imagem principal da Amazon."""
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
    [data-testid="stSidebar"] {
        background-color: #020617;
        color: #e5e7eb;
        border-right: 1px solid #1f2933;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #e5e7eb !important;
    }

    .card-title {
        font-size: 0.85rem;
        font-weight: 600;
        color: #e5e7eb;
        margin-bottom: 0.4rem;
        min-height: 2.4em;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }

    .card-price {
        font-size: 0.95rem;
        font-weight: 700;
        margin-top: 0.2rem;
        color: #a5b4fc;
        margin-bottom: 0.3rem;
    }

    .card-img-box {
        height: 140px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 0.4rem;
    }

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

    a { color: #38bdf8 !important; }

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
# SIDEBAR – APENAS INFORMAÇÃO (SEM CADASTRO)
# =============================================================================

with st.sidebar:
    st.markdown("## Produtos monitorados")
    st.write(
        "Os produtos deste painel são gerenciados pelo scraper e pelo "
        "banco de dados `scraping.db`. Esta interface é apenas para "
        "visualização dos preços e gráficos."
    )
    st.markdown("---")
    st.markdown(
        "Este painel lê diretamente o banco local **`scraping.db`**.  \n"
        "Adições/remoções de produtos devem ser feitas fora da interface."
    )

# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

df_products, df_prices = get_data()

st.title("💹 Monitor de Preços")

# Última atualização
if not df_prices.empty and "date_local" in df_prices.columns:
    last_dt = df_prices["date_local"].max()
    last_str = last_dt.strftime("%d/%m %H:%M") if pd.notna(last_dt) else "--/-- --:--"
else:
    last_str = "--/-- --:--"

col_title, col_last = st.columns([4, 1])
with col_last:
    st.markdown(
        f"""
        <div class="last-update-pill">
            <span>🕒 Última atualização:</span>
            <strong>{last_str}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

if df_products.empty:
    st.warning("Nenhum produto cadastrado no banco. Cadastre produtos no scraping.db.")
    st.stop()

sns.set_style("whitegrid")

st.markdown("## Produtos monitorados")

# ----------------------------------------------------------------------------- #
# PARA CADA PRODUTO: CARD + GRÁFICO MENOR + INSIGHTS
# ----------------------------------------------------------------------------- #

for _, product in df_products.iterrows():
    df_prod = df_prices[df_prices["product_id"] == product["id"]].copy()

    with st.container():
        st.markdown('<div class="detail-card">', unsafe_allow_html=True)

        st.markdown(f"### {product['name']}")

        # colunas mais compactas
        col_img, col_graph = st.columns([1, 1.8])

        with col_img:
            st.write("**Produto**")
            img_url = product.get("image_url") or get_product_image(product["url"])
            if img_url:
                st.image(img_url, width=220)   # imagem um pouco menor
            else:
                st.info("Sem imagem disponível.")
            st.markdown(f"[Ver na Amazon]({product['url']})")

            st.markdown("#### Ajustar imagem manualmente")
            manual_img = st.text_input(
                "URL direta da imagem:",
                value=product.get("image_url") or "",
                key=f"manual_img_{product['id']}",
            )

            if st.button("Salvar imagem", key=f"save_img_{product['id']}"):
                if manual_img.strip():
                    update_product_image(product["id"], manual_img.strip())
                    st.success("Imagem atualizada com sucesso.")
                else:
                    update_product_image(product["id"], None)
                    st.info("Imagem removida.")
                st.rerun()

        with col_graph:
            st.write("**Histórico de Preços**")

            if df_prod.empty:
                st.info("Sem histórico de preços para este produto ainda.")
            else:
                # gráfico menor
                fig, ax = plt.subplots(figsize=(6, 2.5))
                sns.lineplot(data=df_prod, x="date_local", y="price", marker="o", ax=ax)
                ax.set_xlabel("Data/Hora (BR)")
                ax.set_ylabel("Preço (R$)")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
                plt.xticks(rotation=30)
                st.pyplot(fig)

                st.markdown("### 📌 Insights")
                df_prod_valid = df_prod.dropna(subset=["price"])
                if len(df_prod_valid) >= 2:
                    first_price = df_prod_valid["price"].iloc[0]
                    last_price = df_prod_valid["price"].iloc[-1]
                    max_price = df_prod_valid["price"].max()
                    min_price = df_prod_valid["price"].min()
                    mean_price = df_prod_valid["price"].mean()

                    diff_abs = last_price - first_price
                    diff_percent = (diff_abs / first_price) * 100 if first_price != 0 else 0

                    if diff_abs > 0:
                        tendencia = "subiu"
                        badge_class = "positive"
                    elif diff_abs < 0:
                        tendencia = "caiu"
                        badge_class = "negative"
                    else:
                        tendencia = "se manteve estável"
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
                            Mín: R$ {min_price:.2f}</span>
                        <span class="metric-badge">
                            Máx: R$ {max_price:.2f}</span>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.write(
                        f"**1. Tendência geral:** o preço {tendencia} de "
                        f"R$ {first_price:.2f} para R$ {last_price:.2f} "
                        f"({diff_abs:+.2f} R$, {diff_percent:+.1f}%)."
                    )
                    st.write(
                        f"**2. Faixa de variação:** mínimo registrado R$ {min_price:.2f}, "
                        f"máximo R$ {max_price:.2f} e preço médio de R$ {mean_price:.2f}."
                    )
                    if last_price == min_price:
                        st.write(
                            "**3. Momento de compra:** o preço atual é o mais baixo do histórico — "
                            "excelente momento para considerar a compra."
                        )
                    elif last_price == max_price:
                        st.write(
                            "**3. Momento de compra:** o preço atual está no topo histórico — "
                            "pode valer a pena aguardar uma queda."
                        )
                    else:
                        st.write(
                            "**3. Momento de compra:** o preço atual está dentro da faixa histórica, "
                            "sem ser o mínimo nem o máximo."
                        )
                else:
                    st.write(
                        "Ainda não há pontos suficientes no histórico para gerar análises detalhadas. "
                        "Deixe o coletor rodando por mais tempo."
                    )

        st.markdown("</div>", unsafe_allow_html=True)

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
# FUNÇÕES DE BANCO
# =============================================================================


def get_data():
    conn = sqlite3.connect(DB_NAME)
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    df_prices = pd.read_sql_query("SELECT * FROM prices", conn)
    conn.close()

    if "date" in df_prices.columns:
        df_prices["date"] = pd.to_datetime(df_prices["date"])
        df_prices = df_prices.sort_values("date")
        # corrigindo fuso (antes ficava +1h)
        df_prices["date_local"] = df_prices["date"] - pd.Timedelta(hours=4)
    else:
        df_prices["date_local"] = pd.NaT

    return df_products, df_prices


def add_product_to_db(name: str, url: str):
    name = name.strip()
    url = url.strip()

    if not url:
        return False, "Informe uma URL válida."

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM products WHERE url = ?", (url,))
    if cursor.fetchone():
        conn.close()
        return False, "Este produto já está cadastrado."

    image_url = get_product_image(url)

    cursor.execute(
        "INSERT INTO products (name, url, image_url) VALUES (?, ?, ?)",
        (name, url, image_url),
    )
    conn.commit()
    conn.close()
    return True, "Produto cadastrado com sucesso!"


def update_product_image(product_id: int, image_url: str | None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE products SET image_url = ? WHERE id = ?",
        (image_url, product_id),
    )
    conn.commit()
    conn.close()


def delete_product_from_db(product_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM prices WHERE product_id = ?", (product_id,))
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
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


def fetch_product_title(url: str) -> str | None:
    try:
        html = cached_html(url)
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find(id="productTitle")
    if title_tag:
        return title_tag.get_text(strip=True)
    return None


def scrape_single_product(product_id: int, url: str):
    """Coleta o preço de UM produto e grava na tabela prices."""
    try:
        html = cached_html(url)
    except Exception:
        return

    soup = BeautifulSoup(html, "html.parser")

    price_whole = soup.find("span", class_="a-price-whole")
    price_fraction = soup.find("span", class_="a-price-fraction")
    if price_whole and price_fraction:
        full_price_str = f"{price_whole.text.strip()},{price_fraction.text.strip()}"
    elif price_whole:
        full_price_str = price_whole.text.strip()
    else:
        full_price_str = None

    price = extract_price(full_price_str)

    old_price_tag = soup.find("span", class_="a-text-price")
    old_price_str = old_price_tag.get_text(strip=True) if old_price_tag else None
    old_price = extract_price(old_price_str)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO prices (product_id, price, old_price)
        VALUES (?, ?, ?)
        """,
        (product_id, price, old_price),
    )
    conn.commit()
    conn.close()


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

    /* PILL DE ÚLTIMA ATUALIZAÇÃO ------------------------------------------- */
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

    /* CARD DE DETALHES (MESMO TAMANHO DO CARD NORMAL) ---------------------- */
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
    border-radius: 1rem;
    border: 1px solid rgba(148,163,184,0.6);
    box-shadow: 0 14px 38px rgba(15,23,42,0.95);
    padding: 0.9rem 1rem 1.2rem 1rem;

    /* --- O QUE FOI ALTERADO --- */
    max-width: 9150px !important;    /* 30% mais largo */
    width: 100%;
    min-height: 420px;              /* pouquinho mais alto p/ gráfico */
    /* -------------------------- */

    overflow: hidden;
}

    div[data-testid="stVerticalBlock"]:has(.detail-card-flag)::before {
        content: "";
        position: absolute;
        inset: 0;
        background: radial-gradient(circle at top right, rgba(56,189,248,0.14), transparent 60%);
        opacity: 0.9;
        pointer-events: none;
    }

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
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# SIDEBAR – CADASTRO
# =============================================================================

with st.sidebar:
    st.markdown('<div class="sidebar-box">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-title">➕ Adicionar produto da Amazon</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sidebar-sub">'
        "Cole a URL de um produto da Amazon e, se quiser, personalize o nome. "
        "O sistema tentará buscar automaticamente o título e a imagem."
        "</div>",
        unsafe_allow_html=True,
    )

    new_url = st.text_input("URL do produto na Amazon")
    new_name = st.text_input("Nome do produto (opcional)")

    if st.button("Adicionar produto"):
        if not new_url.strip():
            st.error("Informe a URL do produto.")
        else:
            name_to_use = new_name.strip()
            if not name_to_use:
                st.info("Buscando título automaticamente na Amazon...")
                auto_title = fetch_product_title(new_url)
                name_to_use = auto_title or "Produto Amazon"

            ok, msg = add_product_to_db(name_to_use, new_url)
            if ok:
                st.success(msg)
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM products WHERE url = ?",
                    (new_url.strip(),),
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    product_id = row[0]
                    scrape_single_product(product_id, new_url.strip())
                    st.session_state["selected_product_id"] = product_id
                st.rerun()
            else:
                st.warning(msg)

    st.markdown("---")
    st.caption(
        "Este painel lê o banco **`scraping.db`**, "
        "atualizado automaticamente pelo GitHub Actions."
    )
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

df_products, df_prices = get_data()

# Última atualização
if not df_prices.empty and "date_local" in df_prices.columns:
    last_dt = df_prices["date_local"].max()
    last_str = last_dt.strftime("%d/%m %H:%M") if pd.notna(last_dt) else "--/-- --:--"
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
    st.warning("Nenhum produto cadastrado. Adicione um produto na barra lateral.")
    st.stop()

sns.set_style("whitegrid")

# ----------------------------------------------------------------------------- #
# DETALHES (CARD CENTRAL DO TAMANHO DE UM CARD NORMAL)
# ----------------------------------------------------------------------------- #

selected_id = st.session_state.get("selected_product_id")

if selected_id is not None and selected_id in df_products["id"].values:
    product = df_products[df_products["id"] == selected_id].iloc[0]
    df_prod = df_prices[df_prices["product_id"] == selected_id].copy()

    st.markdown("### Detalhes do produto selecionado")

    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_center:
        with st.container():
            st.markdown('<div class="detail-card-flag"></div>', unsafe_allow_html=True)

            top_cols = st.columns([5, 1])
            with top_cols[0]:
                st.markdown(f"**{product['name']}**")
            with top_cols[1]:
                if st.button("✕ Fechar", key="close_detail"):
                    st.session_state["selected_product_id"] = None
                    st.rerun()

            img_col, info_col = st.columns([1, 1], gap="small")
            with img_col:
                img_url = product.get("image_url") or get_product_image(product["url"])
                if img_url:
                    st.image(img_url, width=160)
                else:
                    st.info("Sem imagem disponível.")
            with info_col:
                st.markdown(f"[Ver na Amazon]({product['url']})")

                manual_img = st.text_input(
                    "URL da imagem",
                    value=product.get("image_url") or "",
                    key=f"manual_img_{product['id']}",
                )

                save_col, del_col = st.columns(2)
                with save_col:
                    if st.button("Salvar imagem", key=f"save_img_{product['id']}"):
                        if manual_img.strip():
                            update_product_image(product["id"], manual_img.strip())
                            st.success("Imagem atualizada.")
                        else:
                            update_product_image(product["id"], None)
                            st.info("Imagem removida.")
                        st.rerun()
                with del_col:
                    if st.button("🗑 Excluir produto", key=f"del_prod_detail_{product['id']}"):
                        delete_product_from_db(product["id"])
                        st.success("Produto removido.")
                        st.session_state["selected_product_id"] = None
                        st.rerun()

            st.markdown("---")
            st.write("**Histórico de preços**")

            if df_prod.empty:
                st.info("Sem histórico ainda.")
            else:
                fig, ax = plt.subplots(figsize=(4, 2.2))  # tamanho compatível com card
                sns.lineplot(data=df_prod, x="date_local", y="price", marker="o", ax=ax)
                ax.set_xlabel("Data/Hora", fontsize=8)
                ax.set_ylabel("Preço (R$)", fontsize=8)
                ax.tick_params(axis="both", labelsize=8)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m\n%H:%M"))
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

st.markdown('<h2 class="section-title">Produtos monitorados</h2>', unsafe_allow_html=True)

cols = st.columns(3, gap="large")

for idx, (_, product) in enumerate(df_products.iterrows()):
    col = cols[idx % 3]

    with col:
        with st.container():
            st.markdown('<div class="product-card-flag"></div>', unsafe_allow_html=True)

            st.markdown(
                f'<div class="product-title">{product["name"]}</div>',
                unsafe_allow_html=True,
            )

            img_url = product.get("image_url")
            if not img_url:
                img_url = get_product_image(product["url"])

            st.markdown('<div class="product-image-wrapper">', unsafe_allow_html=True)
            if img_url:
                st.image(img_url, use_column_width=False, width=230)
            else:
                st.markdown(
                    '<div class="product-image-placeholder">Imagem indisponível</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

            latest_price = get_latest_price(df_prices, product["id"])
            st.markdown('<div class="product-card-footer">', unsafe_allow_html=True)
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

            st.markdown('<div class="product-actions-row">', unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Ver detalhes", key=f"view_{product['id']}"):
                    st.session_state["selected_product_id"] = product["id"]
                    st.rerun()
            with b2:
                if st.button("🗑 Excluir", key=f"del_{product['id']}"):
                    delete_product_from_db(product["id"])
                    if st.session_state.get("selected_product_id") == product["id"]:
                        st.session_state["selected_product_id"] = None
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

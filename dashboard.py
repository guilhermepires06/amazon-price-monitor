import sqlite3
import json
import re
from datetime import datetime
import os
import base64

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
# CONFIG GITHUB – ENVIO DO scraping.db
# =============================================================================

GITHUB_REPO = "guilhermepires06/amazon-price-monitor"
GITHUB_FILE_PATH = "scraping.db"
GITHUB_BRANCH = "main"



GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "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").strip()

def upload_db_to_github(commit_message: str = "Atualiza scraping.db via app"):
    """
    Envia o arquivo scraping.db para o GitHub, sobrescrevendo o existente.
    Usa a API:
      PUT /repos/{owner}/{repo}/contents/{path}
    """
    if not GITHUB_TOKEN:
        st.warning("⚠️ Token GitHub não configurado (variável GITHUB_TOKEN).")
        return

    if not os.path.exists(DB_NAME):
        st.warning("⚠️ Arquivo scraping.db inexistente.")
        return

    try:
        with open(DB_NAME, "rb") as f:
            content = f.read()
    except Exception as e:
        st.error(f"Erro abrindo scraping.db: {e}")
        return

    b64_content = base64.b64encode(content).decode("utf-8")

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    sha = None
    try:
        resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
        if resp.status_code == 200:
            sha = resp.json().get("sha")
        elif resp.status_code == 401:
            st.error("❌ Token inválido (401 Bad Credentials).")
            return
        elif resp.status_code == 404:
            pass
    except Exception as e:
        st.warning(f"Falha ao consultar GitHub: {e}")

    data = {
        "message": commit_message,
        "content": b64_content,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        data["sha"] = sha

    try:
        put_resp = requests.put(api_url, headers=headers, json=data)
        if put_resp.status_code not in (200, 201):
            try:
                err = put_resp.json()
            except Exception:
                err = put_resp.text
            st.error(f"❌ Erro ao enviar scraping.db ({put_resp.status_code}): {err}")
        else:
            st.success("✅ scraping.db enviado ao GitHub com sucesso!")
    except Exception as e:
        st.error(f"Falha ao enviar scraping.db: {e}")


# =============================================================================
# AJUSTE DE SCHEMA
# =============================================================================

def ensure_schema():
    """Garante que tabela products e prices possuem colunas image_url e old_price."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE prices ADD COLUMN old_price REAL")
    except sqlite3.OperationalError:
        pass

    conn.commit()
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

    try:
        upload_db_to_github(f"Adiciona produto: {name}")
    except Exception:
        pass

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

    try:
        upload_db_to_github(f"Atualiza imagem do produto ID {product_id}")
    except Exception:
        pass


def delete_product_from_db(product_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM prices WHERE product_id = ?", (product_id,))
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()

    try:
        upload_db_to_github(f"Remove produto ID {product_id}")
    except Exception:
        pass


def get_latest_price(df_prices: pd.DataFrame, product_id: int):
    df_prod = df_prices[df_prices["product"] == product_id] \
        if "product" in df_prices.columns \
        else df_prices[df_prices["product_id"] == product_id]

    df_prod = df_prod.dropna(subset=["price"])
    if df_prod.empty:
        return None

    return df_prod["price"].iloc[-1]


# =============================================================================
# FUNÇÕES DE SCRAPING
# =============================================================================

def get_product_image(url: str) -> str | None:
    """Tenta achar a imagem principal."""
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
    if img and img.get("data-a-dynamic-image"):
        try:
            dyn = json.loads(img["data-a-dynamic-image"])
            urls = list(dyn.keys())
            return urls[0] if urls else None
        except Exception:
            pass

    meta = soup.find("meta", {"property": "og:image"})
    if meta and meta.get("content"):
        return meta["content"]

    any_img = soup.find("img", src=lambda x: x and "images/I/" in x)
    if any_img and any_img.get("src"):
        return any_img["src"]

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
    """Coleta preço de um produto e grava na tabela prices."""
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

    if price is None:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO prices (product_id, price, old_price)
            VALUES (?, ?, ?)
            """,
            (product_id, price, old_price),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
    finally:
        conn.close()

    try:
        upload_db_to_github(f"Atualiza preço do produto ID {product_id}")
    except Exception:
        pass


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
        background: linear-gradient(180deg, #020617 0%, #020617 40%, #020617 100%);
        color: #e5e7eb;
        border-right: 1px solid #1f2937;
    }

    .sidebar-box {
        padding: 0.9rem 1rem;
        background: rgba(15,23,42,0.85);
        border-radius: 0.75rem;
        border: 1px solid rgba(148,163,184,0.35);
        box-shadow: 0 10px 30px rgba(15,23,42,0.75);
    }

    .sidebar-title {
        font-size: 1.1rem;
        font-weight: 700;
    }

    .sidebar-sub {
        font-size: 0.80rem;
        color: #9ca3af;
    }

    h1, h2, h3, h4, h5, h6 {
        color: #e5e7eb !important;
    }

    .main-title {
        font-size: 1.9rem;
        font-weight: 800;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    .last-update-pill {
        padding: 0.35rem 0.9rem;
        border-radius: 999px;
        border: 1px solid rgba(148,163,184,0.5);
        background: rgba(15,23,42,0.9);
    }

    /* ===== CARDS DOS PRODUTOS ===== */

    .product-card-flag {
        display: none;
    }

    div[data-testid="stVerticalBlock"]:has(.product-card-flag) {
        position: relative;
        background: radial-gradient(circle at top left, #020617, #020617 40%, #020617 100%);
        border-radius: 1rem;
        border: 1px solid rgba(148,163,184,0.45);
        padding: 0.9rem 1rem;
        min-height: 320px;
        box-shadow: 0 12px 35px rgba(15,23,42,0.9);
        margin-bottom: 1.7rem;
        transition: all 0.18s ease-out;
    }

    .product-title {
        font-size: 0.90rem;
        font-weight: 600;
        color: #e5e7eb;
        min-height: 2.6em;
    }

    .product-image-wrapper {
        width: 100%;
        text-align: center;
        margin-top: 0.3rem;
        margin-bottom: 0.5rem;
    }

    .product-image-wrapper img {
        max-width: 230px;
        max-height: 170px;
        width: 100%;
        object-fit: contain;
        border-radius: 0.75rem;
    }

    .product-card-footer {
        border-top: 1px dashed rgba(55,65,81,0.8);
        margin-top: auto;
        padding-top: 0.35rem;
    }

    .product-price-badge {
        padding: 0.2rem 0.55rem;
        border-radius: 999px;
        border: 1px solid rgba(129,140,248,0.6);
        background: rgba(15,23,42,0.9);
        color: #ede9fe;
        font-size: 0.76rem;
    }

    /* ===== DETALHES DO PRODUTO ===== */

    .detail-card-flag {
        display: none;
    }

    div[data-testid="stVerticalBlock"]:has(.detail-card-flag) {
        position: relative;
        background: radial-gradient(circle at top left, #020617, #020617 40%, #020617 100%);
        border-radius: 1.2rem;
        border: 1px solid rgba(148,163,184,0.6);
        padding: 1.6rem 2rem;
        min-height: 620px;
        max-width: 1800px !important;
        box-shadow: 0 18px 45px rgba(15,23,42,0.95);
        margin-bottom: 2rem;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# SIDEBAR – CADASTRO + ASSINATURA
# =============================================================================

with st.sidebar:
    st.markdown('<div class="sidebar-box">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-title">➕ Adicionar produto da Amazon</div>', unsafe_allow_html=True)

    new_url = st.text_input("URL do produto na Amazon")
    new_name = st.text_input("Nome do produto (opcional)")

    if st.button("Adicionar produto"):
        if not new_url.strip():
            st.error("Informe a URL do produto.")
        else:
            name_to_use = new_name.strip()
            if not name_to_use:
                st.info("Buscando título automaticamente...")
                auto_title = fetch_product_title(new_url)
                name_to_use = auto_title or "Produto Amazon"

            ok, msg = add_product_to_db(name_to_use, new_url)

            if ok:
                st.success(msg)

                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM products WHERE url = ?", (new_url.strip(),))
                row = cursor.fetchone()
                conn.close()

                if row:
                    product_id = row[0]
                    scrape_single_product(product_id, new_url.strip())
                    st.session_state["selected_product_id"] = product_id

                st.rerun()
            else:
                st.warning(msg)

    # Assinatura
    st.markdown(
        """
        <div style="margin-top: 1rem; padding: 0.85rem; text-align: center;
        border: 1px solid rgba(148,163,184,0.25); border-radius: 10px;
        background: rgba(30,41,59,0.55); color:#cbd5e1;">
            Sistema desenvolvido por:<br>
            <strong>Eduardo Feres</strong><br>
            <strong>Guilherme Pires</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# CONTEÚDO PRINCIPAL
# =============================================================================

df_products, df_prices = get_data()

if not df_prices.empty:
    last_dt = df_prices["date_local"].max()
    last_str = last_dt.strftime("%d/%m %H:%M")
else:
    last_str = "--/-- --:--"

header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.markdown(
        """
        <div class="main-title">
            <span>💹</span> Monitor de Preços
        </div>
        """,
        unsafe_allow_html=True,
    )

with header_col2:
    st.markdown(
        f"""
        <div style="display:flex; justify-content:flex-end;">
            <div class="last-update-pill">🕒 {last_str}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

if df_products.empty:
    st.warning("Nenhum produto cadastrado ainda.")
    st.stop()

sns.set_style("whitegrid")
# =============================================================================
# CARD DE DETALHES – CENTRALIZADO
# =============================================================================

selected_id = st.session_state.get("selected_product_id")

if selected_id is not None and selected_id in df_products["id"].values:
    product = df_products[df_products["id"] == selected_id].iloc[0]
    df_prod = df_prices[df_prices["product_id"] == selected_id].copy()

    st.markdown("### Detalhes do produto selecionado")

    left, center, right = st.columns([1, 2, 1])

    with center:
        with st.container():
            st.markdown('<div class="detail-card-flag"></div>', unsafe_allow_html=True)

            top_cols = st.columns([5, 1])
            with top_cols[0]:
                st.markdown(f"**{product['name']}**")
            with top_cols[1]:
                if st.button("✕ Fechar"):
                    st.session_state["selected_product_id"] = None
                    st.rerun()

            # IMAGEM + INFO
            img_col, info_col = st.columns([1, 1])

            with img_col:
                img_url = product.get("image_url") or get_product_image(product["url"])
                if img_url:
                    st.image(img_url, width=170)
                else:
                    st.info("Sem imagem disponível.")

            with info_col:
                st.markdown(f"[Ver na Amazon]({product['url']})")

                manual_img = st.text_input(
                    "URL da imagem",
                    value=product.get("image_url") or "",
                    key=f"manual_img_{product['id']}",
                )

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Salvar imagem", key=f"save_img_{product['id']}"):
                        update_product_image(product["id"], manual_img or None)
                        st.success("Imagem atualizada!")
                        st.rerun()

                with c2:
                    if st.button("🗑 Excluir", key=f"del_prod_{product['id']}"):
                        delete_product_from_db(product["id"])
                        st.session_state["selected_product_id"] = None
                        st.success("Produto removido!")
                        st.rerun()

            st.markdown("---")
            st.write("**Histórico de preços**")

            # GRÁFICO
            if df_prod.empty:
                st.info("Nenhum preço coletado ainda.")
            else:
                fig, ax = plt.subplots(figsize=(4, 2))

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
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m\n%H:%M"))

                plt.tight_layout()
                st.pyplot(fig)

                df_valid = df_prod.dropna(subset=["price"])
                if len(df_valid) >= 2:
                    first = df_valid["price"].iloc[0]
                    last = df_valid["price"].iloc[-1]
                    maxv = df_valid["price"].max()
                    minv = df_valid["price"].min()
                    diff = last - first

                    if diff > 0:
                        trend = "subiu"
                        badge = "positive"
                    elif diff < 0:
                        trend = "caiu"
                        badge = "negative"
                    else:
                        trend = "estável"
                        badge = "neutral"

                    st.markdown(
                        f"""
                        <span class="metric-badge {badge}">Tendência: {trend}</span>
                        <span class="metric-badge">Atual: R$ {last:.2f}</span>
                        <span class="metric-badge">Mín: R$ {minv:.2f}</span>
                        <span class="metric-badge">Máx: R$ {maxv:.2f}</span>
                        """,
                        unsafe_allow_html=True,
                    )

# =============================================================================
# GRID DE CARDS – PRODUTOS MONITORADOS
# =============================================================================

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

            img_url = product.get("image_url") or get_product_image(product["url"])

            st.markdown('<div class="product-image-wrapper">', unsafe_allow_html=True)
            if img_url:
                st.image(img_url, width=230)
            else:
                st.markdown(
                    '<div class="product-image-placeholder">Sem imagem</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

            latest_price = get_latest_price(df_prices, product["id"])

            st.markdown('<div class="product-card-footer">', unsafe_allow_html=True)
            if latest_price:
                st.markdown(
                    f'<span class="product-price-badge">💰 R$ {latest_price:.2f}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<span class="product-price-badge">Sem preço</span>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown('<div class="product-actions-row">', unsafe_allow_html=True)
            a, b = st.columns(2)

            with a:
                if st.button("Ver detalhes", key=f"view_{product['id']}"):
                    st.session_state["selected_product_id"] = product["id"]
                    st.rerun()

            with b:
                if st.button("🗑 Excluir", key=f"delete_{product['id']}"):
                    delete_product_from_db(product["id"])
                    if st.session_state.get("selected_product_id") == product["id"]:
                        st.session_state["selected_product_id"] = None
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)


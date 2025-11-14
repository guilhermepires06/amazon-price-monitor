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
        df_prices["date_local"] = df_prices["date"] - pd.Timedelta(hours=3)
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
        min-height: 2.4em;        /* 2 linhas fixas */
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
        height: 140px;            /* área fixa da imagem */
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 0.4rem;
    }

    .detail-card {
        padding: 1.25rem;
        border-radius: 1rem;
        background: #020617;
        border: 1px solid rgba(148,163,184,0.5);
        box-shadow: 0 18px 45px rgba(15,23,42,0.9);
    }

    .metric-badge {
        display: inline-block;
        padding: 0.25rem 0.7rem;
        border-radius: 999px;
        background: #0f172a;
        font-size: 0.75rem;
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
# SIDEBAR – CADASTRO
# =============================================================================

with st.sidebar:
    st.markdown("## ➕ Adicionar produto da Amazon")
    st.write(
        "Cole a URL de um produto da Amazon e, se quiser, personalize o nome. "
        "O sistema tentará buscar automaticamente o título e a imagem."
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
    st.warning("Nenhum produto cadastrado. Adicione um produto na barra lateral.")
    st.stop()

sns.set_style("whitegrid")

# ----------------------------------------------------------------------------- #
# BLOCO DE DETALHES
# ----------------------------------------------------------------------------- #

selected_id = st.session_state.get("selected_product_id")

if selected_id is not None and selected_id in df_products["id"].values:
    product = df_products[df_products["id"] == selected_id].iloc[0]
    df_prod = df_prices[df_prices["product_id"] == selected_id].copy()

    st.markdown("## Detalhes do produto selecionado")
    with st.container():
        st.markdown('<div class="detail-card">', unsafe_allow_html=True)

        top_cols = st.columns([6, 1])
        with top_cols[0]:
            st.markdown(f"### {product['name']}")
        with top_cols[1]:
            if st.button("Fechar detalhes"):
                st.session_state["selected_product_id"] = None
                st.rerun()

        col_img, col_graph = st.columns([1, 2])

        with col_img:
            st.write("**Produto**")
            img_url = product.get("image_url") or get_product_image(product["url"])
            if img_url:
                st.image(img_url, width=260)
            else:
                st.info("Sem imagem disponível.")
            st.markdown(f"[Ver na Amazon]({product['url']})")

            st.markdown("#### Ajustar imagem manualmente")
            manual_img = st.text_input(
                "URL direta da imagem:",
                value=product.get("image_url") or "",
                key=f"manual_img_{product['id']}",
            )

            save_col, del_col = st.columns(2)
            with save_col:
                if st.button("Salvar imagem", key=f"save_img_{product['id']}"):
                    if manual_img.strip():
                        update_product_image(product["id"], manual_img.strip())
                        st.success("Imagem atualizada com sucesso.")
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

        with col_graph:
            st.write("**Histórico de Preços**")

            if df_prod.empty:
                st.info("Sem histórico de preços para este produto ainda.")
            else:
                fig, ax = plt.subplots(figsize=(8, 3))
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

# ----------------------------------------------------------------------------- #
# GRID DE CARDS – PRODUTOS MONITORADOS  (ÚNICO GRID!)
# ----------------------------------------------------------------------------- #

st.markdown("## Produtos monitorados")

cols = st.columns(3, gap="large")

for idx, (_, product) in enumerate(df_products.iterrows()):
    col = cols[idx % 3]

    with col:
        # card único, menor, com tudo dentro
        with st.container(border=True):
            # título
            st.markdown(
                f'<div class="card-title">{product["name"]}</div>',
                unsafe_allow_html=True,
            )

            # imagem
            img_url = product.get("image_url")
            if not img_url:
                img_url = get_product_image(product["url"])

            st.markdown('<div class="card-img-box">', unsafe_allow_html=True)
            if img_url:
                st.image(img_url, width=150)
            else:
                st.markdown(
                    """
                    <div style="
                        width: 150px;
                        height: 120px;
                        background: #111827;
                        border-radius: 8px;
                        border: 1px solid #334155;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-size: 0.75rem;
                        color: #64748b;">
                        Sem imagem
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

            # preço
            latest_price = get_latest_price(df_prices, product["id"])
            if latest_price is not None:
                st.markdown(
                    f'<div class="card-price">R$ {latest_price:.2f}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="card-price">Sem preço ainda</div>',
                    unsafe_allow_html=True,
                )

            # botões
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

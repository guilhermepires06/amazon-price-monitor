import sqlite3
from datetime import datetime, timezone
import time
import json
import re

import requests
from bs4 import BeautifulSoup

from utils import extract_price  # já existe no seu projeto

# =============================================================================
# CONFIG
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
# FUNÇÕES AUXILIARES
# =============================================================================

def get_conn():
    return sqlite3.connect(DB_NAME)


def ensure_schema():
    """
    Garante que as tabelas básicas existam.
    Não apaga nada, só cria se não existir.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            image_url TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            price REAL,
            date TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
        """
    )

    conn.commit()
    conn.close()


def fetch_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[ERRO] Falha ao buscar HTML para {url}: {e}")
        return None


def parse_price_from_html(html: str) -> float | None:
    """
    Tenta extrair o preço da página da Amazon.
    Usa alguns seletores comuns e cai no extract_price no fim.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. Preços padrão
    span = soup.find("span", id="priceblock_ourprice") or soup.find(
        "span", id="priceblock_dealprice"
    )
    if span and span.get_text(strip=True):
        price = extract_price(span.get_text(strip=True))
        if price is not None:
            return price

    # 2. Qualquer span com classe a-offscreen (Amazon usa muito isso)
    span = soup.select_one("span.a-offscreen")
    if span and span.get_text(strip=True):
        price = extract_price(span.get_text(strip=True))
        if price is not None:
            return price

    # 3. Extrai qualquer coisa parecida com R$ 1.234,56
    text = soup.get_text(" ", strip=True)
    match = re.search(r"R\$\s*[\d\.\,]+", text)
    if match:
        price = extract_price(match.group(0))
        if price is not None:
            return price

    # Nada encontrado
    return None


# =============================================================================
# LÓGICA PRINCIPAL
# =============================================================================

def run_scraper():
    ensure_schema()

    conn = get_conn()
    cur = conn.cursor()

    # Lê todos os produtos cadastrados
    df_products = None
    try:
        import pandas as pd

        df_products = pd.read_sql_query("SELECT * FROM products", conn)
    except Exception as e:
        print(f"[ERRO] Não foi possível ler tabela products: {e}")
        conn.close()
        return

    if df_products.empty:
        print("[INFO] Nenhum produto cadastrado em products.")
        conn.close()
        return

    # Timestamp único da rodada em UTC
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_str = now_utc.isoformat()  # será convertido no dashboard

    print(f"[INFO] Iniciando rodada de scraping em {now_str} (UTC)")
    sucessos = 0
    falhas = 0

    for _, prod in df_products.iterrows():
        pid = int(prod["id"])
        name = prod["name"]
        url = prod["url"]

        print(f"[INFO] Coletando produto {pid} - {name}")

        html = fetch_html(url)
        if html is None:
            price = None
            falhas += 1
            print(f"[WARN] HTML não carregado para {name}. Gravando price=NULL.")
        else:
            price = parse_price_from_html(html)
            if price is None:
                falhas += 1
                print(f"[WARN] Não consegui extrair preço de {name}. Gravando price=NULL.")
            else:
                sucessos += 1
                print(f"[OK] Preço extraído para {name}: {price}")

        # Insere SEMPRE um registro para este produto nesta rodada
        cur.execute(
            "INSERT INTO prices (product_id, price, date) VALUES (?, ?, ?)",
            (pid, price, now_str),
        )
        conn.commit()

        # Pequeno delay para não forçar a Amazon demais
        time.sleep(2)

    conn.close()
    print(
        f"[INFO] Rodada finalizada. Sucessos: {sucessos} | Falhas: {falhas} | Timestamp: {now_str} (UTC)"
    )


if __name__ == "__main__":
    run_scraper()

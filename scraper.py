import time
import json
import os

import requests
from bs4 import BeautifulSoup

from database import connect, create_tables
from utils import extract_price

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def load_products():
    """Carrega a lista de produtos a partir de products.json."""
    with open("products.json", "r", encoding="utf-8") as f:
        return json.load(f)


def insert_products(products):
    """Insere produtos no banco se ainda não existirem."""
    conn = connect()
    cursor = conn.cursor()

    for p in products:
        cursor.execute("SELECT id FROM products WHERE name = ?", (p["name"],))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO products (name, url) VALUES (?, ?)",
                (p["name"], p["url"]),
            )

    conn.commit()
    conn.close()


def save_price(product_id: int, price: float | None, old_price: float | None):
    """Salva um registro de preço na tabela prices."""
    conn = connect()
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


def scrape_once():
    """Executa o scraping de TODOS os produtos uma vez."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, url FROM products")
    products = cursor.fetchall()
    conn.close()

    if not products:
        print("[SCRAPER] Nenhum produto encontrado no banco.")
        return

    for product_id, name, url in products:
        print(f"[SCRAPER] Coletando dados de: {name}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
        except Exception as e:
            print(f"[ERRO] Falha ao acessar {url}: {e}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")

        # Preço atual (preço principal)
        price_whole = soup.find("span", class_="a-price-whole")
        price_fraction = soup.find("span", class_="a-price-fraction")

        if price_whole and price_fraction:
            full_price_str = f"{price_whole.text.strip()},{price_fraction.text.strip()}"
        elif price_whole:
            full_price_str = price_whole.text.strip()
        else:
            full_price_str = None

        price = extract_price(full_price_str)

        # Preço antigo (geralmente riscado)
        old_price_tag = soup.find("span", class_="a-text-price")
        old_price_str = old_price_tag.get_text(strip=True) if old_price_tag else None
        old_price = extract_price(old_price_str)

        print(f" → Preço atual: {price} | Preço antigo: {old_price}")
        save_price(product_id, price, old_price)


def run_forever(interval_hours: int = 1):
    """
    Loop infinito: executa o scraping a cada `interval_hours` horas.

    Use isso LOCALMENTE (por exemplo: python scraper.py)
    se quiser deixar rodando em background.
    """
    create_tables()
    insert_products(load_products())

    while True:
        print("\n=== INICIANDO SCRAPING ===")
        scrape_once()
        print(f"→ Aguardando {interval_hours} horas...\n")
        time.sleep(interval_hours * 60 * 60)


def run_once_for_actions():
    """
    Modo especial para GitHub Actions:
    - Cria tabelas
    - Garante produtos cadastrados
    - Roda scraping UMA vez
    - Encerra
    """
    print("[SCRAPER] Modo GitHub Actions (execução única).")
    create_tables()
    insert_products(load_products())
    scrape_once()
    print("[SCRAPER] Execução concluída.")


if __name__ == "__main__":
    # Se estiver rodando dentro do GitHub Actions,
    # existe a variável de ambiente GITHUB_ACTIONS=true
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        run_once_for_actions()
    else:
        # Modo normal (local): loop infinito
        run_forever(interval_hours=1)

import sqlite3
import time
from datetime import datetime, timezone
import statistics

import requests
from bs4 import BeautifulSoup

from utils import extract_price

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

# Ativa ou não o filtro de outlier por histórico
USE_OUTLIER_FILTER = True


# =============================================================================
# BANCO / SCHEMA
# =============================================================================

def get_conn():
    return sqlite3.connect(DB_NAME)


def ensure_schema():
    """
    Garante que as tabelas products e prices existam.
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


# =============================================================================
# FUNÇÕES AUXILIARES DE HISTÓRICO / OUTLIER
# =============================================================================

def get_price_stats(conn: sqlite3.Connection, product_id: int):
    """
    Busca últimos preços válidos de um produto e calcula estatísticas básicas.

    Retorna dict com:
        {
            "count": int,
            "median": float,
            "mean": float,
            "min": float,
            "max": float,
        }
    ou None se não tiver dados suficientes.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT price
        FROM prices
        WHERE product_id = ? AND price IS NOT NULL
        ORDER BY date DESC
        LIMIT 30
        """,
        (product_id,),
    )
    rows = [r[0] for r in cur.fetchall() if r[0] is not None and r[0] > 0]

    if len(rows) < 3:
        return None

    try:
        med = statistics.median(rows)
        mean = statistics.mean(rows)
    except statistics.StatisticsError:
        return None

    return {
        "count": len(rows),
        "median": med,
        "mean": mean,
        "min": min(rows),
        "max": max(rows),
    }


def is_price_outlier(new_price: float, stats: dict,
                     up_factor: float = 3.0,
                     down_factor: float = 0.33) -> bool:
    """
    Decide se o novo preço é muito diferente do histórico.

    Regra:
      - se new_price > median * up_factor  -> outlier (pico absurdo)
      - se new_price < median * down_factor -> outlier (queda absurda)
    """
    if stats is None:
        # sem histórico suficiente -> não dá pra julgar
        return False

    median = stats["median"]

    if median <= 0:
        return False

    if new_price > median * up_factor:
        return True

    if new_price < median * down_factor:
        return True

    return False


# =============================================================================
# FUNÇÕES DE SCRAPING
# =============================================================================

def fetch_html(url: str, timeout: int = 25) -> str | None:
    """
    Faz GET na página da Amazon e retorna o HTML em texto.
    Retorna None em caso de erro.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[ERRO] Falha ao buscar HTML de {url}: {e}")
        return None


def _parse_price_from_price_block(block) -> float | None:
    """
    Dado um 'bloco de preço' (div/span com a estrutura da Amazon),
    monta o valor usando:
        - span.a-price-whole (parte inteira, com ponto de milhar)
        - span.a-price-fraction (centavos)
    e converte com extract_price.

    Retorna float ou None.
    """
    if block is None:
        return None

    whole_span = block.select_one("span.a-price-whole")
    if not whole_span:
        return None

    fraction_span = block.select_one("span.a-price-fraction")

    # Pega apenas dígitos da parte inteira (remove ponto, vírgula e espaços)
    whole_raw = whole_span.get_text(strip=True)
    whole_digits = "".join(ch for ch in whole_raw if ch.isdigit())

    if not whole_digits:
        return None

    if fraction_span:
        fraction_raw = fraction_span.get_text(strip=True)
        fraction_digits = "".join(ch for ch in fraction_raw if ch.isdigit())
        if not fraction_digits:
            fraction_digits = "00"
    else:
        fraction_digits = "00"

    # Monta string estilo BR: R$ 2116,05
    price_str = f"R$ {whole_digits},{fraction_digits}"

    price = extract_price(price_str)
    if price is not None and price > 1:
        return price

    return None


def parse_price_from_html(html: str) -> float | None:
    """
    Tenta extrair o preço a partir do HTML usando vários seletores.

    NOVA LÓGICA:
      - Coleta TODOS os preços possíveis em uma lista (candidates).
      - No final, retorna o MENOR preço válido encontrado.
      - Isso evita pegar preço antigo de 2k quando o atual é 158.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[float] = []

    # 1) Blocos de preço principais (desktop / corePrice)
    price_containers_selectors = [
        "#corePriceDisplay_desktop_feature_div",
        "#corePrice_feature_div",
        "#price",
        "div[data-feature-name='corePrice']",
    ]

    for sel in price_containers_selectors:
        block = soup.select_one(sel)
        price = _parse_price_from_price_block(block)
        if price is not None and price > 1:
            candidates.append(price)

    # 2) Qualquer .a-price na página (todos os blocos)
    for block in soup.select("span.a-price, div.a-price"):
        price = _parse_price_from_price_block(block)
        if price is not None and price > 1:
            candidates.append(price)

    # 3) IDs clássicos da Amazon (fallback antigo)
    for span_id in [
        "priceblock_ourprice",
        "priceblock_dealprice",
        "priceblock_saleprice",
        "corePrice_feature_div",
    ]:
        span = soup.find("span", id=span_id)
        if span and span.get_text(strip=True):
            price = extract_price(span.get_text())
            if price is not None and price > 1:
                candidates.append(price)

    # 4) Estrutura nova: .a-price .a-offscreen
    span = soup.select_one(".a-price .a-offscreen")
    if span and span.get_text(strip=True):
        price = extract_price(span.get_text())
        if price is not None and price > 1:
            candidates.append(price)

    # 5) Qualquer span com a classe a-offscreen
    span = soup.select_one("span.a-offscreen")
    if span and span.get_text(strip=True):
        price = extract_price(span.get_text())
        if price is not None and price > 1:
            candidates.append(price)

    # 6) Fallback extremo: usa todo o texto da página
    text = soup.get_text(" ", strip=True)
    price = extract_price(text)
    if price is not None and price > 1:
        candidates.append(price)

    if not candidates:
        return None

    best_price = min(candidates)
    return best_price


def get_price_with_retries(url: str,
                           attempts: int = 3,
                           delay: int = 4) -> float | None:
    """
    Tenta extrair o preço de uma URL da Amazon com algumas tentativas.
    Só retorna preço > 1. Se falhar, retorna None.
    """
    for i in range(1, attempts + 1):
        print(f"  [INFO] Tentativa {i} para {url}")
        html = fetch_html(url)
        if not html:
            time.sleep(delay)
            continue

        price = parse_price_from_html(html)
        if price is not None and price > 1:
            print(f"  [OK] Preço encontrado bruto (menor da página): R$ {price:.2f}")
            return float(round(price, 2))

        print("  [WARN] Preço não encontrado ou inválido, tentando de novo...")
        time.sleep(delay)

    print("  [ERRO] Não foi possível obter preço válido após várias tentativas.")
    return None


# =============================================================================
# LÓGICA PRINCIPAL
# =============================================================================

def run_scraper():
    ensure_schema()

    conn = get_conn()
    cur = conn.cursor()

    # lê todos os produtos cadastrados
    cur.execute("SELECT id, name, url FROM products")
    products = cur.fetchall()

    if not products:
        print("[INFO] Nenhum produto cadastrado na tabela products.")
        conn.close()
        return

    # timestamp único da rodada, em UTC
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_str = now_utc.isoformat()
    print(f"[INFO] Iniciando rodada de scraping em {now_str} (UTC)")
    print(f"[INFO] Total de produtos: {len(products)}")

    sucessos = 0
    falhas = 0
    outliers = 0

    for pid, name, url in products:
        print(f"\n[PRODUTO] ID {pid} - {name}")
        price = get_price_with_retries(url)

        if price is None:
            falhas += 1
            print(f"[WARN] Produto '{name}': preço não será registrado nesta rodada (None).")
            continue

        # filtro de outlier baseado no histórico
        if USE_OUTLIER_FILTER:
            stats = get_price_stats(conn, pid)
            if is_price_outlier(price, stats):
                outliers += 1
                if stats:
                    print(
                        f"[OUTLIER] Preço {price:.2f} muito diferente da mediana "
                        f"{stats['median']:.2f} (histórico {stats['count']} pontos). "
                        "Valor IGNORADO, não será salvo no banco."
                    )
                else:
                    print(
                        "[OUTLIER] Preço marcado como outlier, mas sem estatísticas "
                        "detalhadas disponíveis. Valor IGNORADO."
                    )
                continue

        # insere registro na tabela prices
        cur.execute(
            "INSERT INTO prices (product_id, price, date) VALUES (?, ?, ?)",
            (pid, price, now_str),
        )
        conn.commit()
        sucessos += 1
        print(f"[SAVE] Gravado no banco: product_id={pid}, price={price:.2f}, date={now_str}")

    conn.close()
    print(
        f"\n[RESUMO] Rodada finalizada."
        f" Sucessos: {sucessos}"
        f" | Falhas (sem preço): {falhas}"
        f" | Outliers ignorados: {outliers}"
        f" | Timestamp: {now_str} (UTC)"
    )


if __name__ == "__main__":
    run_scraper()

import sqlite3

DB_NAME = "scraping.db"


def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()

    # garante que a tabela products exista
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url  TEXT NOT NULL,
            image_url TEXT
        )
        """
    )

    # se já existir sem image_url, adiciona a coluna
    cur.execute("PRAGMA table_info(products)")
    cols = [row[1] for row in cur.fetchall()]
    if "image_url" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN image_url TEXT")

    conn.commit()


def insert_product(conn, name: str, url: str):
    cur = conn.cursor()

    # verifica se já existe produto com essa URL
    cur.execute("SELECT id FROM products WHERE url = ?", (url,))
    row = cur.fetchone()
    if row:
        print(f"[INFO] Produto já existe ({row[0]}): {name}")
        return row[0]

    cur.execute(
        "INSERT INTO products (name, url, image_url) VALUES (?, ?, NULL)",
        (name, url),
    )
    conn.commit()
    prod_id = cur.lastrowid
    print(f"[OK] Produto inserido (id={prod_id}): {name}")
    return prod_id


def main():
    conn = sqlite3.connect(DB_NAME)
    ensure_schema(conn)

    produtos = [
        (
            "Placa de Vídeo MSI RTX 5060 Shadow 2X OC, 8GB, GDDR7-912-V537-037",
            "https://www.amazon.com.br/Placa-Video-MSI-Shadow-GDDR7-912-V537-037/dp/B09729YMPT/",
        ),
        (
            "Redragon TECLADO MECÂNICO GAMER FIZZ RGB PRETO SWITCH MARROM",
            "https://www.amazon.com.br/Redragon-TECLADO-MECANICO-SWITCH-MARROM/dp/B0B6KFCC19/",
        ),
        (
            "Placa Mãe MSI B650 GAMING PLUS WIFI (AM5/4xDDR5/HDMI/DisplayPort/M.2/USB 3.2)",
            "https://www.amazon.com.br/MSI-Placa-m%C3%A3e-B650-GAMING-Socket/dp/B0C3R2TXHJ/",
        ),
    ]

    for name, url in produtos:
        insert_product(conn, name, url)

    # só pra conferir o resultado
    cur = conn.cursor()
    cur.execute("SELECT id, name, url FROM products ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall()
    print("\n[DEBUG] Últimos produtos cadastrados:")
    for r in rows:
        print(f"  id={r[0]} | {r[1]}")

    conn.close()


if __name__ == "__main__":
    main()

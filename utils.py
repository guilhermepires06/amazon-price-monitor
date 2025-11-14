import re
from typing import Optional


def extract_price(text: str | None) -> Optional[float]:
    """
    Extrai um preço em formato brasileiro de um texto.
    • Prioriza padrões 'R$ 1.234,56'
    • Ignora valores 0 ou muito pequenos (<= 1)
    • Se houver vários preços, usa o MAIOR (geralmente é o preço do produto)
    """
    if not text:
        return None

    # Normaliza espaços
    text = re.sub(r"\s+", " ", text)

    candidates: list[float] = []

    # 1) Padrões explícitos com "R$"
    #    Ex: "R$ 3.379,00", "por R$3.199,90", etc.
    for match in re.findall(r"R\$\s*([\d\.\,]+)", text):
        cleaned = match.replace(".", "").replace(",", ".")
        try:
            value = float(cleaned)
            if value > 1:  # ignora 0,00 / 0 / 0.5 etc.
                candidates.append(value)
        except ValueError:
            continue

    if candidates:
        # Na Amazon, o preço do produto em si quase sempre é o maior valor
        return max(candidates)

    # 2) Fallback: qualquer número com vírgula/ponto
    #    (caso o texto não traga "R$")
    for match in re.findall(r"\d+(?:[\.,]\d+)?", text):
        cleaned = match.replace(".", "").replace(",", ".")
        try:
            value = float(cleaned)
            if value > 1:
                return value
        except ValueError:
            continue

    return None

import re


def extract_price(text: str | None):
 
    if not text:
        return None

    # Remove currency symbol and spaces
    text = text.replace("R$", "").replace(" ", "").strip()

    # Replace thousands separator and decimal comma
    text = text.replace(".", "").replace(",", ".")

    # Find a number with optional decimals
    match = re.search(r"\d+(\.\d+)?", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None

    return None

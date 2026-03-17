import re
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime


IGNORED_NAME_PATTERNS = (
    r"^vl\.?\s*total",
    r"^valor\s+total",
)


def _is_valid_product_name(raw_name: str) -> bool:
    if not raw_name:
        return False

    name = raw_name.strip().lower()
    return not any(re.match(pattern, name) for pattern in IGNORED_NAME_PATTERNS)


def _parse_brl_value(raw_value: str | None) -> float | None:
    if not raw_value:
        return None

    normalized = (
        raw_value.strip()
        .replace("R$", "")
        .replace(".", "")
        .replace(",", ".")
    )

    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return None

    return float(match.group(0))


def _extract_totals(soup: BeautifulSoup) -> dict:
    totals = {
        "items_count": None,
        "gross_total": None,
        "discount_total": 0.0,
        "amount_paid": None,
    }

    total_container = soup.select_one("#totalNota")
    if not total_container:
        return totals

    for row in total_container.select("#linhaTotal, #linhaForma"):
        label_tag = row.find("label")
        value_tag = row.find("span", class_="totalNumb")

        if not label_tag or not value_tag:
            continue

        label = label_tag.get_text(" ", strip=True).lower()
        value_text = value_tag.get_text(" ", strip=True)

        if "qtd. total de itens" in label:
            parsed_value = _parse_brl_value(value_text)
            totals["items_count"] = int(parsed_value) if parsed_value is not None else None
        elif "valor total" in label:
            totals["gross_total"] = _parse_brl_value(value_text)
        elif "descontos" in label:
            discount_value = _parse_brl_value(value_text)
            totals["discount_total"] = discount_value if discount_value is not None else 0.0
        elif "valor a pagar" in label:
            totals["amount_paid"] = _parse_brl_value(value_text)

    return totals


def scrape_nfce(url: str):

    items = []

    data_compra = None

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1280, "height": 720}
        )

        page = context.new_page()

        print("Abrindo NFCe...")

        page.goto(
            url,
            timeout=60000,
            wait_until="networkidle"
        )

        # aguarda renderização dos itens
        page.wait_for_timeout(4000)

        html = page.content()

        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    info = soup.select_one("#infos") or soup.select_one("#conteudo")

    if info:
        texto = info.get_text(" ", strip=True)

        match = re.search(
            r"Emissão:\s*(\d{2}/\d{2}/\d{4}\s\d{2}:\d{2}:\d{2})",
            texto
        )

        if match:
            data_str = match.group(1)
            data_compra = datetime.strptime(data_str, "%d/%m/%Y %H:%M:%S")

    totais = _extract_totals(soup)
    item_rows = soup.select('tr[id^="Item"]')

    print("Produtos encontrados:", len(item_rows))

    for item_row in item_rows:
        produto = item_row.select_one("td span.txtTit")
        if not produto:
            continue

        nome = produto.get_text(" ", strip=True)

        if not _is_valid_product_name(nome):
            continue

        preco_tag = item_row.select_one("td[align='right'] .valor") or item_row.select_one(".valor")
        preco = _parse_brl_value(preco_tag.get_text(" ", strip=True) if preco_tag else None)

        if preco is None:
            continue

        desconto = 0.0
        desconto_texto = item_row.get_text(" ", strip=True)
        desconto_match = re.search(r"desconto\D*(-?\d+[\.,]\d+)", desconto_texto, re.I)
        if desconto_match:
            desconto = _parse_brl_value(desconto_match.group(1)) or 0.0

        final_price = preco - desconto

        items.append({
            "name": nome,
            "price": preco,
            "discount": desconto,
            "final_price": final_price
        })

    return {
    "items": items,
    "data_compra": data_compra,
    "totals": totais
    }
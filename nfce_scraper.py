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


def _normalize_whitespace(raw_text: str | None) -> str:
    if not raw_text:
        return ""
    return re.sub(r"\s+", " ", raw_text).strip()


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


def _extract_store_info(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    mercado_nome = None
    mercado_endereco = None

    info = soup.select_one("#infos") or soup.select_one("#conteudo")
    info_text = _normalize_whitespace(info.get_text(" ", strip=True) if info else "")

    if info_text:
        nome_match = re.search(r"(?:Raz[aã]o\s+Social|Nome\s+Empresarial)\s*:\s*(.*?)(?:\s+CNPJ\s*:|\s+IE\s*:|$)", info_text, re.I)
        if nome_match:
            mercado_nome = _normalize_whitespace(nome_match.group(1))

        endereco_match = re.search(
            r"Endere[cç]o\s*:\s*(.*?)(?:\s+Bairro\s*:|\s+CEP\s*:|\s+Munic[ií]pio\s*:|\s+UF\s*:|\s+Fone\s*:|$)",
            info_text,
            re.I,
        )
        if endereco_match:
            mercado_endereco = _normalize_whitespace(endereco_match.group(1))

    if not mercado_nome:
        for selector in ("#u20", "#u15", "#u13", ".txtCenter"):
            tag = soup.select_one(selector)
            if not tag:
                continue
            text = _normalize_whitespace(tag.get_text(" ", strip=True))
            if not text:
                continue
            if "danfe" in text.lower() or "documento auxiliar" in text.lower():
                continue
            mercado_nome = text
            break

    if not mercado_endereco:
        for selector in ("#u18", "#u17", "#u16", ".txtTopo"):
            tag = soup.select_one(selector)
            if not tag:
                continue
            text = _normalize_whitespace(tag.get_text(" ", strip=True))
            if not text:
                continue
            if re.search(r"\d{5}-?\d{3}", text) or re.search(r"\b(?:rua|av\.?|avenida|travessa|rodovia|alameda|estrada)\b", text, re.I):
                mercado_endereco = text
                break

    return mercado_nome, mercado_endereco


def _normalize_unit(raw_unit: str | None) -> str:
    if not raw_unit:
        return "UN"

    unit = _normalize_whitespace(raw_unit).upper().replace(".", "")
    tokens = re.findall(r"[A-Z]+", unit)

    if "KG" in tokens or re.search(r"\bKG\b", unit):
        return "KG"

    if any(token in {"UN", "UND", "UNID", "UNIDADE", "UNIT"} for token in tokens):
        return "UN"

    # Mantem apenas UN ou KG para padronizar a API.
    return "UN"


def _extract_item_measurement(item_row, item_total: float | None) -> tuple[int | float, str, float | None]:
    row_text = _normalize_whitespace(item_row.get_text(" ", strip=True))

    quantity = None
    unit = None
    unit_price = None

    qty_tag = item_row.select_one(".Rqtd")
    if qty_tag:
        quantity = _parse_brl_value(qty_tag.get_text(" ", strip=True))

    unit_tag = item_row.select_one(".RUN")
    if unit_tag:
        unit = _normalize_whitespace(unit_tag.get_text(" ", strip=True))

    unit_price_tag = item_row.select_one(".RvlUnit")
    if unit_price_tag:
        unit_price = _parse_brl_value(unit_price_tag.get_text(" ", strip=True))

    if quantity is None:
        qty_match = re.search(r"(?:qtd(?:e)?\.?|qtde\.?)\s*:?\s*([\d.,]+)", row_text, re.I)
        if qty_match:
            quantity = _parse_brl_value(qty_match.group(1))

    if not unit:
        # Na NFC-e, frequentemente aparece como "UN: KG" ou "Unit: UN".
        unit_match = re.search(r"\b(?:UN|UNIT)\s*:?\s*([A-Za-z]{1,8})", row_text, re.I)
        if unit_match:
            unit = unit_match.group(1)

    if unit_price is None:
        unit_price_match = re.search(r"(?:vl\.?\s*unit\.?|valor\s*unit[aá]rio)\s*:?\s*R?\$?\s*(-?\d+[\.,]\d+)", row_text, re.I)
        if unit_price_match:
            unit_price = _parse_brl_value(unit_price_match.group(1))

    unit = _normalize_unit(unit)

    if quantity is None and item_total and unit_price and unit_price > 0:
        quantity = item_total / unit_price

    if unit == "UN":
        if quantity is None or quantity <= 0:
            quantity = 1
        else:
            quantity = int(round(quantity))
            if quantity <= 0:
                quantity = 1
    else:  # KG
        if quantity is None or quantity <= 0:
            quantity = 1.0
        else:
            quantity = float(quantity)

    if unit_price is None and item_total and quantity:
        unit_price = float(item_total) / float(quantity)

    return quantity, unit, unit_price


def _extract_item_discount(item_row) -> float:
    def _candidates_from_text(raw_text: str) -> list[float]:
        text = _normalize_whitespace(raw_text)
        if not text:
            return []

        candidates = []

        patterns = (
            # Ex.: "Desconto: R$ 1,23" / "Desc.: 1,23"
            r"(?:desconto|desc\.?|vl\.?\s*desc(?:onto)?|vdesc)\s*:?\s*R?\$?\s*(-?\d+[\.,]\d+)",
            # Ex.: "R$ 1,23 desconto"
            r"R?\$?\s*(-?\d+[\.,]\d+)\s*(?:de\s*)?(?:desconto|desc\.?)",
        )

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                value = _parse_brl_value(match.group(1))
                if value is not None and value > 0:
                    candidates.append(value)

        return candidates

    candidates = []

    # Seletores comuns para desconto por item em layouts NFC-e.
    for selector in (
        ".valorDesc",
        ".RvlDesc",
        ".vDesc",
        ".txtDesc",
        "span[id*='Desc']",
        "span[class*='Desc']",
        "label[id*='Desc']",
        "label[class*='Desc']",
    ):
        for tag in item_row.select(selector):
            value = _parse_brl_value(tag.get_text(" ", strip=True))
            if value is not None and value > 0:
                candidates.append(value)

    candidates.extend(_candidates_from_text(item_row.get_text(" ", strip=True)))

    # Em algumas páginas o desconto do item fica em uma linha de detalhe logo abaixo do item.
    for sibling in item_row.find_next_siblings("tr", limit=4):
        sibling_id = (sibling.get("id") or "").strip()
        if sibling_id.startswith("Item"):
            break

        for selector in (
            ".valorDesc",
            ".RvlDesc",
            ".vDesc",
            ".txtDesc",
            "span[id*='Desc']",
            "span[class*='Desc']",
            "label[id*='Desc']",
            "label[class*='Desc']",
        ):
            for tag in sibling.select(selector):
                value = _parse_brl_value(tag.get_text(" ", strip=True))
                if value is not None and value > 0:
                    candidates.append(value)

        candidates.extend(_candidates_from_text(sibling.get_text(" ", strip=True)))

    if not candidates:
        return 0.0

    # Evita escolher desconto percentual por engano; em geral o valor monetário é o maior candidato.
    return max(candidates)


def _extract_totals_and_payment(soup: BeautifulSoup) -> dict:
    totals = {
        "items_count": None,
        "gross_total": None,
        "discount_total": 0.0,
        "amount_paid": None,
        "forma_pagamento": None
    }

    container = soup.select_one("#totalNota")
    if not container:
        return totals

    payment_entries = []

    # Extrai linhas de total
    for row in container.select("#linhaTotal, #linhaForma"):
        label_tag = row.find("label")
        value_tag = row.find("span", class_="totalNumb")
        if not label_tag:
            continue
        label_text = label_tag.get_text(" ", strip=True).lower()
        label_text_normalized = _normalize_whitespace(label_tag.get_text(" ", strip=True))

        if "qtd. total de itens" in label_text and value_tag:
            totals["items_count"] = int(_parse_brl_value(value_tag.get_text()))
        elif "valor total" in label_text and value_tag:
            totals["gross_total"] = _parse_brl_value(value_tag.get_text())
        elif "descontos" in label_text and value_tag:
            totals["discount_total"] = _parse_brl_value(value_tag.get_text()) or 0.0
        elif "valor a pagar" in label_text and value_tag:
            totals["amount_paid"] = _parse_brl_value(value_tag.get_text())
        elif "forma de pagamento" in label_text:
            # próximo #linhaTotal com class tx contém o tipo de pagamento
            prox = row.find_next_sibling("div", id="linhaTotal")
            if prox and prox.find("label", class_="tx"):
                totals["forma_pagamento"] = prox.find("label", class_="tx").get_text(" ", strip=True)

        # Captura formas de pagamento em layouts onde cada forma é uma linha tx.
        if "forma de pagamento" not in label_text and label_tag.get("class") and "tx" in label_tag.get("class"):
            if value_tag:
                payment_entries.append(f"{label_text_normalized}: {_normalize_whitespace(value_tag.get_text(' ', strip=True))}")
            else:
                payment_entries.append(label_text_normalized)

    if payment_entries and not totals["forma_pagamento"]:
        # Remove duplicidade preservando ordem.
        seen = set()
        unique_entries = []
        for entry in payment_entries:
            if entry in seen:
                continue
            seen.add(entry)
            unique_entries.append(entry)
        totals["forma_pagamento"] = " | ".join(unique_entries)

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

    mercado_nome, mercado_endereco = _extract_store_info(soup)

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

    totais = _extract_totals_and_payment(soup)

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

        desconto = _extract_item_discount(item_row)

        quantidade, unidade, valor_unitario = _extract_item_measurement(item_row, preco)

        final_price = preco - desconto

        items.append({
            "name": nome,
            "price": preco,
            "discount": desconto,
            "final_price": final_price,
            "quantity": quantidade,
            "unit": unidade,
            "unit_price": valor_unitario,
        })

    return {
    "items": items,
    "data_compra": data_compra,
    "totals": totais,
    "mercado_nome": mercado_nome,
    "mercado_endereco": mercado_endereco,
    "forma_pagamento": totais.get("forma_pagamento"),
    }
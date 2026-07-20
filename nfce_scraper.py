import os
import re
import threading
import time
from playwright.sync_api import sync_playwright
import urllib.parse
import urllib.error
import urllib.request
from bs4 import BeautifulSoup
from datetime import datetime


IGNORED_NAME_PATTERNS = (
    r"^vl\.?\s*total",
    r"^valor\s+total",
)


class SefazBlockedError(Exception):
    pass


class SefazNotFoundError(Exception):
    pass


class SefazTemporaryError(Exception):
    pass


SEFAZ_MIN_INTERVAL_SECONDS = float(os.getenv("SEFAZ_MIN_INTERVAL_SECONDS", "0"))
SEFAZ_MAX_RETRIES = int(os.getenv("SEFAZ_MAX_RETRIES", "2"))
SEFAZ_RETRY_BASE_SECONDS = float(os.getenv("SEFAZ_RETRY_BASE_SECONDS", "5"))
_sefaz_request_lock = threading.Lock()
_last_sefaz_request_at = 0.0


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


def _first_text(root, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        tag = root.select_one(selector)
        if not tag:
            continue
        text = _normalize_whitespace(tag.get_text(" ", strip=True))
        if text:
            return text
    return ""


def _find_item_nodes(soup: BeautifulSoup):
    nodes = []
    seen = set()

    # Layouts de NFC-e variam por UF: alguns usam tr#Item..., outros div/li#Item...
    for selector in (
        '[id^="Item"]',
        '[id*="Item"]',
        '#tabResult tr',
        '#tabResult li',
        '#tabResult > div',
    ):
        for node in soup.select(selector):
            key = id(node)
            if key in seen:
                continue

            text = _normalize_whitespace(node.get_text(" ", strip=True))
            if not text:
                continue

            lowered = text.lower()
            if not any(token in lowered for token in ("qtd", "qtde", "vl", "valor", "un", "unit")):
                continue

            seen.add(key)
            nodes.append(node)

    return nodes


def _extract_item_name(item_row) -> str:
    name = _first_text(
        item_row,
        (
            ".txtTit",
            "[class*='txtTit']",
            ".produto",
            ".nome",
            ".descricao",
            "td:first-child span",
            "td:first-child",
        ),
    )

    if name:
        return name

    row_text = _normalize_whitespace(item_row.get_text(" ", strip=True))
    name_match = re.match(
        r"(.+?)(?:\s+(?:c[oó]digo|cod\.?|qtd(?:e)?\.?|qtde\.?|un(?:it)?\.?|vl\.?|valor)\s*:)",
        row_text,
        re.I,
    )
    if name_match:
        return _normalize_whitespace(name_match.group(1))

    return ""


def _extract_item_total(item_row) -> float | None:
    value_text = _first_text(
        item_row,
        (
            "td[align='right'] .valor",
            ".valor",
            "[class*='valor']",
            "[class*='Valor']",
            "td[align='right']",
        ),
    )
    value = _parse_brl_value(value_text)
    if value is not None:
        return value

    row_text = _normalize_whitespace(item_row.get_text(" ", strip=True))
    total_match = re.search(
        r"(?:vl\.?\s*total|valor\s*total|total)\s*:?\s*R?\$?\s*(-?\d+[\.,]\d+)",
        row_text,
        re.I,
    )
    if total_match:
        return _parse_brl_value(total_match.group(1))

    money_values = re.findall(r"R?\$?\s*(-?\d+[\.,]\d{2})", row_text)
    if money_values:
        return _parse_brl_value(money_values[-1])

    return None


def _save_debug_html(html: str, reason: str) -> None:
    try:
        with open("last_nfce_debug.html", "w", encoding="utf-8") as debug_file:
            debug_file.write(html)
        print(f"HTML de debug salvo em last_nfce_debug.html ({reason})")
    except OSError as error:
        print(f"Nao foi possivel salvar HTML de debug: {error}")


def _detect_blocked_page(soup: BeautifulSoup) -> str | None:
    html_text = str(soup).lower()
    page_text = _normalize_whitespace(soup.get_text(" ", strip=True)).lower()
    support_id_match = re.search(r"support id\s*(?:is|:)?\s*:?\s*(\d+)", page_text, re.I)
    support_id_text = f" Support ID: {support_id_match.group(1)}." if support_id_match else ""

    has_nfce_content = (
        bool(_find_item_nodes(soup))
        or bool(soup.select_one("#totalNota, .txtTit, [id^='Item']"))
        or "documento auxiliar da nota fiscal de consumidor eletrônica" in page_text
        or "danfe nfc-e" in page_text
    )

    if has_nfce_content:
        return None

    if "recaptcharesponse" in html_text or "google.com/recaptcha" in html_text:
        return (
            "A SEFAZ retornou uma pagina intermediaria com reCAPTCHA. "
            "A consulta automatica da NFC-e do RJ foi bloqueada antes de exibir os produtos."
            f"{support_id_text}"
        )

    if "queremos saber se é humano ou robô" in page_text or "codigo da imagem" in page_text or "código da imagem" in page_text:
        return (
            "A SEFAZ solicitou captcha de imagem antes de exibir os produtos. "
            "A consulta automatica da NFC-e do RJ foi bloqueada."
            f"{support_id_text}"
        )

    if "/tspd/" in html_text or "apm_do_not_touch" in html_text:
        return (
            "A SEFAZ retornou uma pagina de protecao anti-bot antes de exibir os produtos."
            f"{support_id_text}"
        )

    if "captcha" in page_text:
        return f"A SEFAZ solicitou captcha antes de exibir os produtos.{support_id_text}"

    return None


def _throttle_sefaz_request() -> None:
    global _last_sefaz_request_at

    if SEFAZ_MIN_INTERVAL_SECONDS <= 0:
        return

    elapsed = time.monotonic() - _last_sefaz_request_at
    wait_seconds = SEFAZ_MIN_INTERVAL_SECONDS - elapsed
    if wait_seconds > 0:
        print(f"Aguardando {wait_seconds:.1f}s antes da proxima consulta SEFAZ.")
        time.sleep(wait_seconds)
    _last_sefaz_request_at = time.monotonic()


def _is_retryable_status(status: int | None) -> bool:
    return status in {408, 429, 500, 502, 503, 504}


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return _is_retryable_status(error.code)
    return isinstance(error, (TimeoutError, urllib.error.URLError))


def _retry_delay(attempt: int) -> float:
    return SEFAZ_RETRY_BASE_SECONDS * (2 ** max(attempt - 1, 0))


def _raise_for_empty_or_error_page(soup: BeautifulSoup) -> None:
    page_text = _normalize_whitespace(soup.get_text(" ", strip=True))
    lowered = page_text.lower()
    title = _normalize_whitespace(soup.title.get_text(" ", strip=True) if soup.title else "")

    if "error 404" in lowered or "404 - not found" in lowered or title.lower() == "error":
        raise SefazNotFoundError(
            "A SEFAZ retornou 404 para esta NFC-e. A URL/chave nao foi encontrada no endpoint consultado."
        )


def _encode_nfce_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)

    if not query:
        return url

    encoded_query = urllib.parse.urlencode(query)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, encoded_query, parsed.fragment)
    )


def _fetch_html_direct(url: str) -> tuple[str, int | None]:
    encoded_url = _encode_nfce_url(url)
    request = urllib.request.Request(
        encoded_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = getattr(response, "status", None)
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            raw_body = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        final_url = error.geturl()
        content_type = error.headers.get("Content-Type", "") if error.headers else ""
        raw_body = error.read()

    print("Status SEFAZ direto:", status)
    print("URL final direta:", final_url)
    print("Content-Type SEFAZ:", content_type)

    charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "iso-8859-1"

    html = raw_body.decode(charset, errors="replace")

    if status == 404:
        _save_debug_html(html, "sefaz 404 consulta direta")
        raise SefazNotFoundError(
            "A SEFAZ retornou 404 para esta NFC-e na consulta direta. "
            "A URL/chave nao foi encontrada no endpoint consultado."
        )

    if _is_retryable_status(status):
        raise SefazTemporaryError(f"A SEFAZ retornou status temporario {status} na consulta direta.")

    return html, status


def _fetch_html_playwright(url: str) -> tuple[str, int | None]:
    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="pt-BR",
                viewport={"width": 1280, "height": 720}
            )

            page = context.new_page()
            response = page.goto(
                _encode_nfce_url(url),
                timeout=60000,
                wait_until="domcontentloaded"
            )

            print("Status SEFAZ Playwright:", response.status if response else "sem resposta")
            print("URL final Playwright:", page.url)

            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                print("Network idle nao ocorreu dentro do tempo; seguindo com HTML renderizado.")

            try:
                page.wait_for_function(
                    """
                    () => {
                        const text = document.body ? document.body.innerText : "";
                        return document.querySelector('[id^="Item"], #tabResult, .txtTit')
                            || text.includes("Documento Auxiliar da Nota Fiscal")
                            || text.includes("DANFE NFC-e")
                            || text.includes("QR Code");
                    }
                    """,
                    timeout=30000,
                )
            except Exception:
                print("A pagina renderizada nao apresentou conteudo conhecido da NFC-e dentro do tempo.")

            try:
                page.wait_for_selector('[id^="Item"], #tabResult, .txtTit', timeout=10000)
            except Exception:
                print("Nenhum seletor conhecido de itens apareceu dentro do tempo.")

            page.wait_for_timeout(1500)

            html = page.content()
            status = response.status if response else None
        finally:
            browser.close()

    if status == 404:
        _save_debug_html(html, "sefaz 404 playwright")
        raise SefazNotFoundError(
            "A SEFAZ retornou 404 para esta NFC-e no navegador. "
            "A URL/chave nao foi encontrada no endpoint consultado."
        )

    if _is_retryable_status(status):
        raise SefazTemporaryError(f"A SEFAZ retornou status temporario {status} no navegador.")

    return html, status


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


def parse_nfce_html(html: str):
    items = []
    data_compra = None

    soup = BeautifulSoup(html, "html.parser")

    blocked_reason = _detect_blocked_page(soup)
    if blocked_reason:
        _save_debug_html(html, "bloqueio sefaz")
        raise SefazBlockedError(blocked_reason)

    _raise_for_empty_or_error_page(soup)

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

    item_rows = _find_item_nodes(soup)

    print("Produtos encontrados:", len(item_rows))

    if not item_rows:
        page_text = _normalize_whitespace(soup.get_text(" ", strip=True))
        print("Titulo da pagina:", _normalize_whitespace(soup.title.get_text(" ", strip=True) if soup.title else ""))
        print("Amostra da pagina:", page_text[:500])
        _save_debug_html(html, "zero itens")

    for item_row in item_rows:
        nome = _extract_item_name(item_row)

        if not _is_valid_product_name(nome):
            continue

        preco = _extract_item_total(item_row)

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


def _scrape_nfce_once(url: str):
    print("Abrindo NFCe...")
    _throttle_sefaz_request()

    try:
        html, _ = _fetch_html_direct(url)
    except Exception as error:
        if isinstance(error, (SefazNotFoundError, SefazTemporaryError)):
            raise
        print(f"Falha na consulta direta da SEFAZ: {error}")
        html = ""

    direct_soup = BeautifulSoup(html, "html.parser")
    direct_blocked_reason = _detect_blocked_page(direct_soup) if html else None

    if direct_blocked_reason:
        _save_debug_html(html, "bloqueio sefaz consulta direta")
        print("Consulta direta retornou pagina intermediaria da SEFAZ; tentando Playwright.")
        html, _ = _fetch_html_playwright(url)
    elif not _normalize_whitespace(direct_soup.get_text(" ", strip=True)):
        print("Consulta direta retornou pagina vazia; tentando Playwright.")
        html, _ = _fetch_html_playwright(url)

    return parse_nfce_html(html)


def scrape_nfce(url: str):
    with _sefaz_request_lock:
        last_error = None
        for attempt in range(1, SEFAZ_MAX_RETRIES + 2):
            try:
                print(f"Tentativa SEFAZ {attempt}/{SEFAZ_MAX_RETRIES + 1}")
                return _scrape_nfce_once(url)
            except SefazTemporaryError as error:
                last_error = error
            except Exception as error:
                if not _is_retryable_error(error):
                    raise
                last_error = error

            if attempt <= SEFAZ_MAX_RETRIES:
                delay = _retry_delay(attempt)
                print(f"Falha temporaria na SEFAZ: {last_error}. Nova tentativa em {delay:.1f}s.")
                time.sleep(delay)

        raise SefazTemporaryError(f"A consulta da SEFAZ falhou apos retries: {last_error}")

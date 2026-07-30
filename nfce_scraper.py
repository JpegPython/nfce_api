import os
import re
import http.cookiejar
import threading
import time
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
import urllib.parse
import urllib.error
import urllib.request
from bs4 import BeautifulSoup
from datetime import datetime

from nfce_errors import (
    NFCeDocumentMismatchError,
    NFCeParseError,
    SefazBlockedError,
    SefazIpBlockedError,
    SefazNotFoundError,
    SefazTemporaryError,
)
from nfce_parser import parse_nfce_xml, reconcile_nfce_result
from nfce_qr import validate_nfce_qr_url


IGNORED_NAME_PATTERNS = (
    r"^vl\.?\s*total",
    r"^valor\s+total",
    r"^valor\s+a\s+pagar",
    r"^descontos?$",
    r"^forma\s+de\s+pagamento",
    r"^qtd\.?\s+total",
)

SEFAZ_MIN_INTERVAL_SECONDS = float(os.getenv("SEFAZ_MIN_INTERVAL_SECONDS", "0"))
SEFAZ_MAX_RETRIES = int(os.getenv("SEFAZ_MAX_RETRIES", "2"))
SEFAZ_RETRY_BASE_SECONDS = float(os.getenv("SEFAZ_RETRY_BASE_SECONDS", "5"))
NFCE_MAX_HTML_CHARS = int(
    os.getenv("NFCE_MAX_HTML_CHARS", str(5 * 1024 * 1024))
)
NFCE_MAX_RESPONSE_BYTES = int(
    os.getenv("NFCE_MAX_RESPONSE_BYTES", str(8 * 1024 * 1024))
)
NFCE_SAVE_DEBUG_HTML = (
    os.getenv("NFCE_SAVE_DEBUG_HTML", "false").strip().lower() == "true"
)
_sefaz_request_lock = threading.Lock()
_last_sefaz_request_at = 0.0
_sefaz_cookie_jar = http.cookiejar.CookieJar()
_sefaz_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_sefaz_cookie_jar)
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
        "[data-item]",
        ".item",
        ".item-produto",
        ".produto-item",
        '#tabResult tr',
        '#tabResult li',
        '#tabResult > div',
        "table tbody tr",
    ):
        for node in soup.select(selector):
            key = id(node)
            if key in seen:
                continue

            text = _normalize_whitespace(node.get_text(" ", strip=True))
            if not text:
                continue

            lowered = text.lower()
            if not re.search(
                r"\b(?:qtd(?:e)?|vl|valor|un(?:idade)?|unit)\b",
                lowered,
                re.I,
            ):
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
            "[data-name]",
            "[data-produto]",
            "td.descricao",
            "td.produto",
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
            ".RvlTotal",
            ".vProd",
            "[data-total]",
            ".valor",
            "[class*='valor']",
            "[class*='Valor']",
            "td[align='right']",
            "td:last-child",
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
    if not NFCE_SAVE_DEBUG_HTML:
        print(f"HTML de debug nao persistido ({reason}).")
        return

    try:
        with open("last_nfce_debug.html", "w", encoding="utf-8") as debug_file:
            debug_file.write(html)
        print(f"HTML de debug salvo em last_nfce_debug.html ({reason})")
    except OSError as error:
        print(f"Nao foi possivel salvar HTML de debug: {error}")


def _detect_blocked_page(soup: BeautifulSoup) -> SefazBlockedError | None:
    html_text = str(soup).lower()
    page_text = _normalize_whitespace(soup.get_text(" ", strip=True)).lower()
    support_id_match = re.search(
        r"(?:support id|numero de id|número de id)\s*(?:is|e|é|:)?\s*:?\s*<?(\d+)",
        page_text,
        re.I,
    )
    support_id_text = f" Support ID: {support_id_match.group(1)}." if support_id_match else ""

    has_nfce_items = (
        bool(_find_item_nodes(soup))
        or bool(soup.select_one(".txtTit, [id^='Item'], [data-item]"))
    )
    has_nfce_content = (
        has_nfce_items
        or bool(soup.select_one("#totalNota"))
        or "documento auxiliar da nota fiscal de consumidor eletrônica" in page_text
        or "danfe nfc-e" in page_text
    )

    if has_nfce_items:
        return None

    if (
        "enderecos ip" in page_text
        or "endereços ip" in page_text
        or "endereco ip atual" in page_text
        or "endereço ip atual" in page_text
        or "bloqueia acessos provenientes" in page_text
    ):
        return SefazIpBlockedError(
            "A SEFAZ-RJ bloqueou o endereco IP usado na consulta. "
            "Abra a NFC-e no celular ou tente outra rede."
            f"{support_id_text}"
        )

    if "recaptcharesponse" in html_text or "google.com/recaptcha" in html_text:
        return SefazBlockedError(
            "A SEFAZ retornou uma pagina intermediaria com reCAPTCHA. "
            "A NFC-e precisa ser aberta no navegador ou WebView do usuario."
            f"{support_id_text}"
        )

    if "queremos saber se é humano ou robô" in page_text or "codigo da imagem" in page_text or "código da imagem" in page_text:
        return SefazBlockedError(
            "A SEFAZ solicitou captcha de imagem antes de exibir os produtos. "
            "A validacao precisa ser concluida pelo usuario."
            f"{support_id_text}"
        )

    if "/tspd/" in html_text or "apm_do_not_touch" in html_text:
        return SefazBlockedError(
            "A SEFAZ retornou uma pagina de protecao TSPD antes de exibir os produtos. "
            "A NFC-e precisa ser aberta pelo usuario."
            f"{support_id_text}"
        )

    if "captcha" in page_text:
        return SefazBlockedError(
            f"A SEFAZ solicitou captcha antes de exibir os produtos.{support_id_text}"
        )

    if has_nfce_content:
        return None

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

    temporary_markers = (
        "servico temporariamente indisponivel",
        "serviço temporariamente indisponível",
        "tente novamente mais tarde",
        "gateway timeout",
        "service unavailable",
    )
    if any(marker in lowered for marker in temporary_markers):
        raise SefazTemporaryError(
            "A pagina de consulta da SEFAZ esta temporariamente indisponivel."
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


def _read_response_body(response) -> bytes:
    raw_body = response.read(NFCE_MAX_RESPONSE_BYTES + 1)
    if len(raw_body) > NFCE_MAX_RESPONSE_BYTES:
        raise NFCeParseError("A resposta da SEFAZ excede o tamanho maximo permitido.")
    return raw_body


def _decode_response_body(raw_body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
    xml_encoding_match = re.match(
        br"\s*<\?xml[^>]+encoding=[\"']([\w.-]+)[\"']",
        raw_body[:200],
        re.I,
    )

    encodings = []
    if charset_match:
        encodings.append(charset_match.group(1))
    if xml_encoding_match:
        encodings.append(xml_encoding_match.group(1).decode("ascii", errors="ignore"))
    encodings.extend(("utf-8-sig", "iso-8859-1"))

    for encoding in dict.fromkeys(encodings):
        try:
            return raw_body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw_body.decode("utf-8", errors="replace")


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
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        },
    )

    try:
        with _sefaz_opener.open(request, timeout=60) as response:
            status = getattr(response, "status", None)
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            raw_body = _read_response_body(response)
    except urllib.error.HTTPError as error:
        status = error.code
        final_url = error.geturl()
        content_type = error.headers.get("Content-Type", "") if error.headers else ""
        raw_body = _read_response_body(error)

    print("Status SEFAZ direto:", status)
    print("URL final direta:", final_url)
    print("Content-Type SEFAZ:", content_type)

    html = _decode_response_body(raw_body, content_type)

    if status == 404:
        _save_debug_html(html, "sefaz 404 consulta direta")
        raise SefazNotFoundError(
            "A SEFAZ retornou 404 para esta NFC-e na consulta direta. "
            "A URL/chave nao foi encontrada no endpoint consultado."
        )

    if _is_retryable_status(status):
        raise SefazTemporaryError(f"A SEFAZ retornou status temporario {status} na consulta direta.")

    return html, status


def _fetch_html_playwright_once(url: str) -> tuple[str, int | None]:
    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="pt-BR",
                viewport={"width": 1280, "height": 720}
            )

            browser_cookies = []
            for cookie in _sefaz_cookie_jar:
                browser_cookie = {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path or "/",
                    "secure": cookie.secure,
                }
                if cookie.expires:
                    browser_cookie["expires"] = cookie.expires
                browser_cookies.append(browser_cookie)
            if browser_cookies:
                try:
                    context.add_cookies(browser_cookies)
                except Exception as error:
                    print(f"Nao foi possivel reaproveitar cookies no navegador: {error}")

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
                        return document.querySelector(
                            '[id^="Item"], #tabResult, .txtTit, [data-item], table tbody tr'
                        )
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
                page.wait_for_selector(
                    '[id^="Item"], #tabResult, .txtTit, [data-item], table tbody tr',
                    timeout=10000,
                )
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


def _fetch_html_playwright(url: str) -> tuple[str, int | None]:
    try:
        return _fetch_html_playwright_once(url)
    except (SefazNotFoundError, SefazTemporaryError):
        raise
    except PlaywrightError as error:
        raise SefazTemporaryError(
            f"O navegador nao conseguiu carregar a NFC-e: {error}"
        ) from error


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

    aliases = {
        "UND": "UN",
        "UNID": "UN",
        "UNIDADE": "UN",
        "UNIT": "UN",
        "PC": "UN",
        "PCA": "UN",
        "LITRO": "L",
        "LITROS": "L",
        "GR": "G",
    }
    supported = {
        "UN",
        "KG",
        "G",
        "L",
        "LT",
        "ML",
        "CX",
        "PCT",
        "DZ",
        "M",
        "M2",
        "M3",
    }

    for token in tokens:
        normalized = aliases.get(token, token)
        if normalized in supported:
            return normalized

    return "UN"


def _extract_item_measurement(item_row, item_total: float | None) -> tuple[int | float, str, float | None]:
    row_text = _normalize_whitespace(item_row.get_text(" ", strip=True))

    quantity = None
    unit = None
    unit_price = None

    qty_tag = item_row.select_one(".Rqtd")
    qty_tag = qty_tag or item_row.select_one("[data-quantity], [data-quantidade]")
    if qty_tag:
        quantity = _parse_brl_value(qty_tag.get_text(" ", strip=True))

    unit_tag = item_row.select_one(".RUN")
    unit_tag = unit_tag or item_row.select_one("[data-unit], [data-unidade]")
    if unit_tag:
        unit = _normalize_whitespace(unit_tag.get_text(" ", strip=True))

    unit_price_tag = item_row.select_one(".RvlUnit")
    unit_price_tag = unit_price_tag or item_row.select_one(
        "[data-unit-price], [data-valor-unitario]"
    )
    if unit_price_tag:
        unit_price = _parse_brl_value(unit_price_tag.get_text(" ", strip=True))

    if quantity is None:
        qty_match = re.search(r"(?:qtd(?:e)?\.?|qtde\.?)\s*:?\s*([\d.,]+)", row_text, re.I)
        if qty_match:
            quantity = _parse_brl_value(qty_match.group(1))

    if not unit:
        # Na NFC-e, frequentemente aparece como "UN: KG" ou "Unit: UN".
        unit_match = re.search(
            r"\b(?:UN(?:IDADE)?|UNIT)\s*:?\s*([A-Za-z0-9]{1,8})",
            row_text,
            re.I,
        )
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
    else:
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

        sibling_text = _normalize_whitespace(sibling.get_text(" ", strip=True))
        if re.search(
            r"(?:total\s+de\s+descontos|valor\s+total|valor\s+a\s+pagar)",
            sibling_text,
            re.I,
        ):
            continue

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

        candidates.extend(_candidates_from_text(sibling_text))

    if not candidates:
        return 0.0

    # Evita escolher desconto percentual por engano; em geral o valor monetário é o maior candidato.
    return max(candidates)


def _extract_totals_and_payment(soup: BeautifulSoup) -> dict:
    totals = {
        "items_count": None,
        "gross_total": None,
        "discount_total": None,
        "amount_paid": None,
        "forma_pagamento": None
    }

    container = soup.select_one("#totalNota") or soup

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

    page_text = _normalize_whitespace(container.get_text(" ", strip=True))

    def _labeled_value(pattern: str) -> float | None:
        match = re.search(
            rf"{pattern}\s*:?\s*R?\$?\s*(-?\d{{1,3}}(?:\.\d{{3}})*,\d{{2}}|-?\d+[.,]\d{{2}})",
            page_text,
            re.I,
        )
        return _parse_brl_value(match.group(1)) if match else None

    if totals["items_count"] is None:
        count_match = re.search(
            r"(?:qtd\.?\s*total\s*de\s*itens|quantidade\s*de\s*itens)\s*:?\s*(\d+)",
            page_text,
            re.I,
        )
        if count_match:
            totals["items_count"] = int(count_match.group(1))

    if totals["gross_total"] is None:
        totals["gross_total"] = _labeled_value(
            r"(?:valor\s+total(?:\s+dos\s+produtos)?|total\s+bruto)"
        )
    if totals["discount_total"] is None:
        totals["discount_total"] = _labeled_value(
            r"(?:descontos?|valor\s+do\s+desconto)"
        )
    if totals["amount_paid"] is None:
        totals["amount_paid"] = _labeled_value(
            r"(?:valor\s+a\s+pagar|valor\s+pago|valor\s+da\s+nota)"
        )
    if not totals["forma_pagamento"]:
        payment_match = re.search(
            r"forma\s+de\s+pagamento\s*:?\s*"
            r"(dinheiro|pix|cart[aã]o\s+de\s+cr[eé]dito|"
            r"cart[aã]o\s+de\s+d[eé]bito|vale\s+\w+|outros?)",
            page_text,
            re.I,
        )
        if payment_match:
            totals["forma_pagamento"] = _normalize_whitespace(
                payment_match.group(1)
            )

    return totals


def _extract_access_keys(soup: BeautifulSoup) -> set[str]:
    keys = set()
    page_text = _normalize_whitespace(soup.get_text(" ", strip=True))

    for match in re.finditer(
        r"chave\s+de\s+acesso.{0,80}?((?:\d[\s.\-/]*){44})",
        page_text,
        re.I,
    ):
        candidate = re.sub(r"\D", "", match.group(1))
        if len(candidate) == 44:
            keys.add(candidate)

    raw_html = str(soup)
    for match in re.finditer(r"(\d{44})(?=(?:\||%7[cC]))", raw_html):
        keys.add(match.group(1))

    return keys


def _validate_html_access_key(
    soup: BeautifulSoup,
    expected_access_key: str | None,
) -> None:
    if not expected_access_key:
        return

    html_access_keys = _extract_access_keys(soup)
    if html_access_keys and expected_access_key not in html_access_keys:
        raise NFCeDocumentMismatchError(
            "O HTML recebido pertence a uma NFC-e diferente da chave informada no QR Code."
        )


def parse_nfce_html(html: str, expected_access_key: str | None = None):
    if not html or not html.strip():
        raise NFCeParseError("O HTML recebido esta vazio.")

    if len(html) > NFCE_MAX_HTML_CHARS:
        raise NFCeParseError("O HTML recebido excede o tamanho maximo permitido.")

    items = []
    data_compra = None

    soup = BeautifulSoup(html, "html.parser")

    blocked_error = _detect_blocked_page(soup)
    if blocked_error:
        _save_debug_html(html, "bloqueio sefaz")
        raise blocked_error

    _raise_for_empty_or_error_page(soup)
    _validate_html_access_key(soup, expected_access_key)

    mercado_nome, mercado_endereco = _extract_store_info(soup)

    info = soup.select_one("#infos") or soup.select_one("#conteudo")
    page_text = _normalize_whitespace(soup.get_text(" ", strip=True))
    info_text = _normalize_whitespace(
        info.get_text(" ", strip=True) if info else page_text
    )

    date_match = re.search(
        r"(?:data\s+de\s+)?emiss[aã]o\s*:?\s*"
        r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}(?::\d{2})?)",
        info_text,
        re.I,
    )
    if date_match:
        data_str = date_match.group(1)
        date_format = (
            "%d/%m/%Y %H:%M:%S"
            if data_str.count(":") == 2
            else "%d/%m/%Y %H:%M"
        )
        data_compra = datetime.strptime(data_str, date_format)

    totais = _extract_totals_and_payment(soup)

    item_rows = _find_item_nodes(soup)

    print("Produtos encontrados:", len(item_rows))

    if not item_rows:
        page_text = _normalize_whitespace(soup.get_text(" ", strip=True))
        print("Titulo da pagina:", _normalize_whitespace(soup.title.get_text(" ", strip=True) if soup.title else ""))
        print("Amostra da pagina:", page_text[:500])
        _save_debug_html(html, "zero itens")
        raise NFCeParseError(
            "A pagina foi carregada, mas nenhum item da NFC-e foi identificado."
        )

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

    if not items:
        _save_debug_html(html, "itens sem dados validos")
        raise NFCeParseError(
            "Os itens da NFC-e foram encontrados, mas nenhum possui dados validos."
        )

    return reconcile_nfce_result(
        {
            "items": items,
            "data_compra": data_compra,
            "totals": totais,
            "mercado_nome": mercado_nome,
            "mercado_endereco": mercado_endereco,
            "forma_pagamento": totais.get("forma_pagamento"),
        },
        source_format="html",
    )


def _looks_like_nfce_xml(document: str) -> bool:
    prefix = document.lstrip("\ufeff \t\r\n")[:500]
    if not prefix.startswith("<"):
        return False
    return bool(
        re.search(
            r"<(?:\w+:)?(?:nfeProc|NFe|infNFe)\b",
            prefix,
            re.I,
        )
    )


def parse_nfce_document(document: str, expected_access_key: str | None = None):
    if _looks_like_nfce_xml(document):
        return parse_nfce_xml(
            document,
            expected_access_key=expected_access_key,
        )
    return parse_nfce_html(
        document,
        expected_access_key=expected_access_key,
    )


def _scrape_nfce_once(url: str, expected_access_key: str | None = None):
    print("Abrindo NFCe...")
    _throttle_sefaz_request()

    try:
        html, _ = _fetch_html_direct(url)
    except Exception as error:
        if isinstance(error, (SefazNotFoundError, SefazTemporaryError)):
            raise
        print(f"Falha na consulta direta da SEFAZ: {error}")
        html = ""

    if html:
        if not _looks_like_nfce_xml(html):
            direct_soup = BeautifulSoup(html, "html.parser")
            direct_blocked_error = _detect_blocked_page(direct_soup)
            if direct_blocked_error:
                _save_debug_html(html, "bloqueio sefaz consulta direta")
                raise direct_blocked_error

        try:
            return parse_nfce_document(
                html,
                expected_access_key=expected_access_key,
            )
        except NFCeParseError as error:
            print(
                "Resposta direta nao pode ser extraida; "
                f"tentando pagina renderizada: {error}"
            )
    else:
        print("Consulta direta retornou pagina vazia; tentando pagina renderizada.")

    rendered_document, _ = _fetch_html_playwright(url)
    return parse_nfce_document(
        rendered_document,
        expected_access_key=expected_access_key,
    )


def scrape_nfce(url: str):
    qr_data = validate_nfce_qr_url(url)

    with _sefaz_request_lock:
        last_error = None
        for attempt in range(1, SEFAZ_MAX_RETRIES + 2):
            try:
                print(f"Tentativa SEFAZ {attempt}/{SEFAZ_MAX_RETRIES + 1}")
                result = _scrape_nfce_once(
                    qr_data.canonical_url,
                    expected_access_key=qr_data.access_key,
                )
                result["access_key"] = qr_data.access_key
                result["qr"] = qr_data.as_dict()
                return result
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

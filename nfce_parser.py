import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation

from nfce_errors import (
    NFCeDocumentMismatchError,
    NFCeParseError,
)


PAYMENT_METHODS = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartao de credito",
    "04": "Cartao de debito",
    "05": "Credito loja",
    "10": "Vale alimentacao",
    "11": "Vale refeicao",
    "12": "Vale presente",
    "13": "Vale combustivel",
    "15": "Boleto bancario",
    "16": "Deposito bancario",
    "17": "PIX",
    "18": "Transferencia bancaria",
    "19": "Programa de fidelidade",
    "90": "Sem pagamento",
    "99": "Outros",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _descendants(root: ET.Element, name: str):
    return (element for element in root.iter() if _local_name(element.tag) == name)


def _first(root: ET.Element | None, name: str) -> ET.Element | None:
    if root is None:
        return None
    return next(_descendants(root, name), None)


def _text(root: ET.Element | None, name: str) -> str | None:
    element = _first(root, name)
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _decimal(raw_value: str | None) -> Decimal | None:
    if raw_value is None:
        return None
    try:
        value = Decimal(raw_value.replace(",", "."))
    except InvalidOperation as error:
        raise NFCeParseError(f"Valor numerico invalido no XML: {raw_value!r}.") from error
    if not value.is_finite():
        raise NFCeParseError("O XML contem um valor numerico nao finito.")
    return value


def _number(raw_value: str | None) -> float | None:
    value = _decimal(raw_value)
    return float(value) if value is not None else None


def _parse_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None

    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        for date_format in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue
    return None


def _format_address(address: ET.Element | None) -> str | None:
    if address is None:
        return None

    street = _text(address, "xLgr")
    number = _text(address, "nro")
    complement = _text(address, "xCpl")
    district = _text(address, "xBairro")
    city = _text(address, "xMun")
    state = _text(address, "UF")
    postal_code = _text(address, "CEP")

    parts = []
    first_line = ", ".join(part for part in (street, number) if part)
    if first_line:
        parts.append(first_line)
    parts.extend(part for part in (complement, district) if part)

    city_state = " - ".join(part for part in (city, state) if part)
    if city_state:
        parts.append(city_state)
    if postal_code:
        parts.append(f"CEP {postal_code}")

    return ", ".join(parts) or None


def _normalize_xml_unit(raw_unit: str | None) -> str:
    unit = re.sub(r"[^A-Za-z]", "", raw_unit or "").upper()
    if unit in {"UND", "UNID", "UNIDADE", "PC", "PCA"}:
        return "UN"
    return unit[:8] or "UN"


def _parse_xml_item(detail: ET.Element) -> dict | None:
    product = _first(detail, "prod")
    if product is None:
        return None

    name = (_text(product, "xProd") or "").strip()
    if not name:
        return None

    quantity = _number(_text(product, "qCom"))
    unit_price = _number(_text(product, "vUnCom"))
    gross_total = _number(_text(product, "vProd"))
    discount = _number(_text(product, "vDesc")) or 0.0

    if gross_total is None and quantity is not None and unit_price is not None:
        gross_total = quantity * unit_price
    if gross_total is None:
        return None

    return {
        "name": name,
        "code": _text(product, "cProd"),
        "gtin": _text(product, "cEAN"),
        "price": gross_total,
        "discount": discount,
        "final_price": gross_total - discount,
        "quantity": 1.0 if quantity is None else quantity,
        "unit": _normalize_xml_unit(_text(product, "uCom")),
        "unit_price": unit_price,
    }


def _parse_xml_payments(root: ET.Element) -> str | None:
    entries = []
    for detail in _descendants(root, "detPag"):
        payment_code = _text(detail, "tPag")
        payment_value = _number(_text(detail, "vPag"))
        label = PAYMENT_METHODS.get(payment_code or "", f"Codigo {payment_code}")
        if payment_value is None:
            entries.append(label)
        else:
            entries.append(f"{label}: R$ {payment_value:.2f}")

    return " | ".join(dict.fromkeys(entries)) or None


def _xml_access_key(info: ET.Element) -> str | None:
    identifier = (info.attrib.get("Id") or "").strip()
    match = re.fullmatch(r"NFe(\d{44})", identifier, re.I)
    return match.group(1) if match else None


def parse_nfce_xml(xml: str, expected_access_key: str | None = None) -> dict:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise NFCeParseError("O XML retornado pela SEFAZ esta malformado.") from error

    info = _first(root, "infNFe")
    if info is None:
        raise NFCeParseError("O XML nao contem o grupo infNFe da NFC-e.")

    access_key = _xml_access_key(info) or _text(root, "chNFe")
    if not access_key:
        raise NFCeParseError("O XML nao informa a chave de acesso da NFC-e.")
    if expected_access_key and access_key != expected_access_key:
        raise NFCeDocumentMismatchError(
            "O XML recebido pertence a uma NFC-e diferente da chave informada no QR Code."
        )

    model = _text(_first(info, "ide"), "mod")
    if model != "65":
        raise NFCeParseError("O XML recebido nao pertence a uma NFC-e modelo 65.")

    items = []
    for detail in _descendants(info, "det"):
        item = _parse_xml_item(detail)
        if item:
            items.append(item)

    if not items:
        raise NFCeParseError("Nenhum produto valido foi encontrado no XML da NFC-e.")

    issuer = _first(info, "emit")
    totals_group = _first(info, "ICMSTot")
    totals = {
        "items_count": len(items),
        "gross_total": _number(_text(totals_group, "vProd")),
        "discount_total": _number(_text(totals_group, "vDesc")) or 0.0,
        "amount_paid": _number(_text(totals_group, "vNF")),
        "forma_pagamento": _parse_xml_payments(info),
    }

    return reconcile_nfce_result(
        {
            "items": items,
            "data_compra": _parse_datetime(_text(_first(info, "ide"), "dhEmi")),
            "totals": totals,
            "mercado_nome": _text(issuer, "xNome") or _text(issuer, "xFant"),
            "mercado_endereco": _format_address(_first(issuer, "enderEmit")),
            "forma_pagamento": totals["forma_pagamento"],
            "access_key": access_key,
        },
        source_format="xml",
    )


def _finite_number(raw_value, field_name: str) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise NFCeParseError(f"O campo {field_name} nao possui um numero valido.") from error
    if not math.isfinite(value):
        raise NFCeParseError(f"O campo {field_name} possui um numero nao finito.")
    return value


def reconcile_nfce_result(result: dict, source_format: str) -> dict:
    items = result.get("items") or []
    if not items:
        raise NFCeParseError("A NFC-e nao possui itens validos para reconciliacao.")

    warnings = []
    gross_items = 0.0
    discount_items = 0.0

    for index, item in enumerate(items, start=1):
        name = (item.get("name") or "").strip()
        if not name:
            raise NFCeParseError(f"O item {index} nao possui descricao.")

        price = _finite_number(item.get("price"), f"items[{index}].price")
        discount = _finite_number(
            item.get("discount", 0.0),
            f"items[{index}].discount",
        )
        quantity = _finite_number(
            item.get("quantity", 1.0),
            f"items[{index}].quantity",
        )

        if price < 0 or discount < 0 or quantity <= 0:
            raise NFCeParseError(f"O item {index} possui valores negativos ou quantidade zero.")
        if discount - price > 0.01:
            raise NFCeParseError(f"O desconto do item {index} e maior que seu valor.")

        item["name"] = name
        item["price"] = round(price, 2)
        item["discount"] = round(discount, 2)
        item["final_price"] = round(price - discount, 2)
        item["quantity"] = quantity
        item["unit"] = (item.get("unit") or "UN").strip().upper()[:8]
        if item.get("unit_price") is None:
            item["unit_price"] = price / quantity
        else:
            item["unit_price"] = _finite_number(
                item["unit_price"],
                f"items[{index}].unit_price",
            )
            if item["unit_price"] < 0:
                raise NFCeParseError(f"O item {index} possui valor unitario negativo.")

        gross_items += price
        discount_items += discount

    totals = result.get("totals") or {}
    gross_total = totals.get("gross_total")
    discount_total = totals.get("discount_total")
    amount_paid = totals.get("amount_paid")

    if gross_total is None:
        gross_total = gross_items
    else:
        gross_total = _finite_number(gross_total, "totals.gross_total")

    if discount_total is None:
        discount_total = discount_items
    else:
        discount_total = _finite_number(discount_total, "totals.discount_total")

    if amount_paid is None:
        amount_paid = gross_total - discount_total
    else:
        amount_paid = _finite_number(amount_paid, "totals.amount_paid")

    if min(gross_total, discount_total, amount_paid) < 0:
        raise NFCeParseError("Os totais da NFC-e nao podem ser negativos.")
    if discount_total - gross_total > 0.01:
        raise NFCeParseError("O desconto total e maior que o valor bruto da NFC-e.")

    expected_count = totals.get("items_count")
    if expected_count is not None and int(expected_count) != len(items):
        warnings.append(
            f"Quantidade declarada ({int(expected_count)}) difere dos itens extraidos ({len(items)})."
        )

    tolerance = max(0.05, abs(gross_total) * 0.005)
    if abs(gross_items - gross_total) > tolerance:
        warnings.append(
            "A soma bruta dos itens difere do total da nota; frete, acrescimos ou arredondamento podem explicar."
        )
    if discount_items and abs(discount_items - discount_total) > tolerance:
        warnings.append("A soma dos descontos por item difere do desconto total da nota.")
    if abs((gross_total - discount_total) - amount_paid) > tolerance:
        warnings.append(
            "O valor pago inclui diferencas alem do total bruto e descontos extraidos."
        )

    totals.update(
        {
            "items_count": len(items),
            "gross_total": round(gross_total, 2),
            "discount_total": round(discount_total, 2),
            "amount_paid": round(amount_paid, 2),
        }
    )
    result["totals"] = totals
    result["forma_pagamento"] = result.get("forma_pagamento") or totals.get(
        "forma_pagamento"
    )
    result["extraction"] = {
        "source_format": source_format,
        "reconciled": True,
        "warnings": warnings,
    }
    return result

import base64
import binascii
import re
import urllib.parse
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

from nfce_errors import InvalidQrCodeError


MAX_QR_URL_LENGTH = 4096
RJ_NFCE_HOSTS = {
    "consultadfe.fazenda.rj.gov.br",
    "www4.fazenda.rj.gov.br",
}
RJ_NFCE_PATH = "/consultanfce/qrcode"
CURRENT_RJ_NFCE_HOST = "consultadfe.fazenda.rj.gov.br"
CURRENT_RJ_NFCE_PATH = "/consultaNFCe/QRCode"


@dataclass(frozen=True)
class NFCeQrData:
    access_key: str
    version: int
    environment: int
    emission_type: str
    source_host: str
    payload: str
    canonical_url: str

    def as_dict(self) -> dict:
        return asdict(self)


def calculate_access_key_digit(access_key_without_digit: str) -> int:
    weight = 2
    total = 0

    for digit in reversed(access_key_without_digit):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1

    result = 11 - (total % 11)
    return 0 if result >= 10 else result


def validate_access_key(access_key: str) -> None:
    if not re.fullmatch(r"\d{44}", access_key):
        raise InvalidQrCodeError("A chave de acesso deve conter exatamente 44 digitos.")

    if access_key[:2] != "33":
        raise InvalidQrCodeError("O QR Code nao pertence a uma NFC-e emitida no RJ.")

    if access_key[20:22] != "65":
        raise InvalidQrCodeError("O documento informado nao e uma NFC-e modelo 65.")

    expected_digit = calculate_access_key_digit(access_key[:43])
    if int(access_key[-1]) != expected_digit:
        raise InvalidQrCodeError("O digito verificador da chave de acesso e invalido.")


def _extract_payload(query: str) -> str:
    payload_values = []

    for parameter in query.split("&"):
        key, separator, raw_value = parameter.partition("=")
        if not separator:
            continue
        if urllib.parse.unquote_plus(key) == "p":
            # unquote preserva '+' de assinaturas Base64 da versao 3.
            payload_values.append(urllib.parse.unquote(raw_value))

    if len(payload_values) != 1 or not payload_values[0]:
        raise InvalidQrCodeError(
            "A URL deve possuir exatamente um parametro 'p' preenchido."
        )

    return payload_values[0].strip()


def _validate_environment(raw_environment: str) -> int:
    if raw_environment not in {"1", "2"}:
        raise InvalidQrCodeError(
            "O ambiente deve ser producao (1) ou homologacao (2)."
        )
    return int(raw_environment)


def _validate_offline_fields(fields: list[str], version: int) -> None:
    if not re.fullmatch(r"\d{2}", fields[3]):
        raise InvalidQrCodeError("O dia de emissao em contingencia e invalido.")

    day = int(fields[3])
    if day < 1 or day > 31:
        raise InvalidQrCodeError("O dia de emissao em contingencia e invalido.")

    try:
        total_value = Decimal(fields[4])
    except InvalidOperation as error:
        raise InvalidQrCodeError(
            "O valor total da NFC-e em contingencia e invalido."
        ) from error

    if not total_value.is_finite() or total_value < 0:
        raise InvalidQrCodeError("O valor total da NFC-e e invalido ou negativo.")

    if version == 2:
        if not fields[5] or not re.fullmatch(r"\d{1,6}", fields[6]):
            raise InvalidQrCodeError(
                "Os parametros de contingencia da versao 2 sao invalidos."
            )
        if not re.fullmatch(r"[0-9A-Fa-f]{40}", fields[7]):
            raise InvalidQrCodeError("O hash do QR Code versao 2 e invalido.")
        return

    destination_type = fields[5]
    destination_id = fields[6]

    if not destination_type and destination_id:
        raise InvalidQrCodeError(
            "A identificacao do destinatario foi informada sem o tipo."
        )

    if destination_type:
        if destination_type not in {"1", "2", "3"}:
            raise InvalidQrCodeError(
                "O tipo de identificacao do destinatario e invalido."
            )
        if destination_type == "1" and not re.fullmatch(r"\d{14}", destination_id):
            raise InvalidQrCodeError("O CNPJ do destinatario e invalido.")
        if destination_type == "2" and not re.fullmatch(r"\d{11}", destination_id):
            raise InvalidQrCodeError("O CPF do destinatario e invalido.")
        if (
            destination_type == "3"
            and destination_id
            and not 3 <= len(destination_id) <= 14
        ):
            raise InvalidQrCodeError(
                "A identificacao do destinatario estrangeiro e invalida."
            )

    if not fields[7]:
        raise InvalidQrCodeError(
            "Os parametros de contingencia da versao 3 estao incompletos."
        )

    try:
        base64.b64decode(fields[7], validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidQrCodeError(
            "A assinatura do QR Code versao 3 e invalida."
        ) from error


def _validate_version_fields(fields: list[str], version: int, offline: bool) -> None:
    expected_length = 8 if offline else (5 if version == 2 else 3)
    if len(fields) != expected_length:
        raise InvalidQrCodeError(
            f"O QR Code versao {version} possui uma quantidade invalida de parametros."
        )

    if version == 2 and not offline:
        if not re.fullmatch(r"\d{1,6}", fields[3]):
            raise InvalidQrCodeError("O identificador CSC da versao 2 e invalido.")
        if not re.fullmatch(r"[0-9A-Fa-f]{40}", fields[4]):
            raise InvalidQrCodeError("O hash do QR Code versao 2 e invalido.")

    if offline:
        _validate_offline_fields(fields, version)


def validate_nfce_qr_url(url: str) -> NFCeQrData:
    raw_url = (url or "").strip()
    if not raw_url:
        raise InvalidQrCodeError("O QR Code nao contem uma URL.")
    if len(raw_url) > MAX_QR_URL_LENGTH:
        raise InvalidQrCodeError("A URL do QR Code excede o tamanho permitido.")

    try:
        parsed = urllib.parse.urlsplit(raw_url)
        port = parsed.port
    except ValueError as error:
        raise InvalidQrCodeError("A URL contida no QR Code e invalida.") from error

    if parsed.scheme.lower() not in {"http", "https"}:
        raise InvalidQrCodeError("A URL da NFC-e deve usar HTTP ou HTTPS.")
    if parsed.username or parsed.password:
        raise InvalidQrCodeError("A URL da NFC-e nao pode conter credenciais.")
    if port not in {None, 80, 443}:
        raise InvalidQrCodeError("A URL da NFC-e utiliza uma porta nao permitida.")

    host = (parsed.hostname or "").lower()
    if host not in RJ_NFCE_HOSTS:
        raise InvalidQrCodeError(
            "A URL nao pertence ao servico oficial de NFC-e da SEFAZ-RJ."
        )

    if parsed.path.rstrip("/").lower() != RJ_NFCE_PATH:
        raise InvalidQrCodeError(
            "A URL nao corresponde a consulta de QR Code da NFC-e do RJ."
        )

    payload = _extract_payload(parsed.query)
    fields = payload.split("|")
    if len(fields) < 3:
        raise InvalidQrCodeError("O QR Code possui menos parametros do que o esperado.")

    access_key = fields[0]
    validate_access_key(access_key)

    if fields[1] not in {"2", "3"}:
        raise InvalidQrCodeError("A versao do QR Code deve ser 2 ou 3.")

    version = int(fields[1])
    environment = _validate_environment(fields[2])
    offline = access_key[34] == "9"
    _validate_version_fields(fields, version, offline)

    canonical_query = urllib.parse.urlencode({"p": payload})
    canonical_url = urllib.parse.urlunsplit(
        (
            "https",
            CURRENT_RJ_NFCE_HOST,
            CURRENT_RJ_NFCE_PATH,
            canonical_query,
            "",
        )
    )

    return NFCeQrData(
        access_key=access_key,
        version=version,
        environment=environment,
        emission_type="offline" if offline else "online",
        source_host=host,
        payload=payload,
        canonical_url=canonical_url,
    )

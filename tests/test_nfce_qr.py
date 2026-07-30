import urllib.parse
import unittest

from nfce_errors import InvalidQrCodeError
from nfce_qr import (
    calculate_access_key_digit,
    validate_nfce_qr_url,
)


VALID_ACCESS_KEY = "33260710697697000317651050004583541251457065"
VALID_V2_HASH = "B823A57C59CC50191AE34A5DFA5A892DA2BE99FF"
CURRENT_URL = "https://consultadfe.fazenda.rj.gov.br/consultaNFCe/QRCode"


def access_key_with(model="65", emission_type="1", state="33"):
    digits = list(VALID_ACCESS_KEY[:43])
    digits[0:2] = state
    digits[20:22] = model
    digits[34] = emission_type
    body = "".join(digits)
    return f"{body}{calculate_access_key_digit(body)}"


def qr_url(payload, host="consultadfe.fazenda.rj.gov.br", scheme="https"):
    return f"{scheme}://{host}/consultaNFCe/QRCode?p={payload}"


class NFCeQrValidationTests(unittest.TestCase):
    def test_accepts_rj_version_2_online(self):
        payload = f"{VALID_ACCESS_KEY}|2|1|1|{VALID_V2_HASH}"
        result = validate_nfce_qr_url(qr_url(payload))

        self.assertEqual(result.access_key, VALID_ACCESS_KEY)
        self.assertEqual(result.version, 2)
        self.assertEqual(result.environment, 1)
        self.assertEqual(result.emission_type, "online")

    def test_accepts_version_3_online(self):
        result = validate_nfce_qr_url(qr_url(f"{VALID_ACCESS_KEY}|3|1"))

        self.assertEqual(result.version, 3)
        self.assertEqual(result.emission_type, "online")

    def test_accepts_legacy_rj_host_but_canonicalizes_to_current_https(self):
        payload = f"{VALID_ACCESS_KEY}|3|1"
        result = validate_nfce_qr_url(
            qr_url(payload, host="www4.fazenda.rj.gov.br", scheme="http")
        )

        self.assertTrue(result.canonical_url.startswith(f"{CURRENT_URL}?p="))

    def test_accepts_encoded_payload(self):
        payload = f"{VALID_ACCESS_KEY}|3|1"
        encoded_payload = urllib.parse.quote(payload, safe="")
        result = validate_nfce_qr_url(qr_url(encoded_payload))

        self.assertEqual(result.payload, payload)

    def test_accepts_version_3_offline_without_recipient(self):
        access_key = access_key_with(emission_type="9")
        signature = "+/8="
        payload = f"{access_key}|3|1|30|10.00|||{signature}"
        result = validate_nfce_qr_url(qr_url(payload))

        self.assertEqual(result.emission_type, "offline")
        self.assertTrue(result.payload.endswith(signature))
        self.assertIn("%2B%2F8%3D", result.canonical_url)

    def test_accepts_version_2_offline(self):
        access_key = access_key_with(emission_type="9")
        payload = (
            f"{access_key}|2|1|30|10.00|DIGEST_VALUE|1|{VALID_V2_HASH}"
        )
        result = validate_nfce_qr_url(qr_url(payload))

        self.assertEqual(result.version, 2)
        self.assertEqual(result.emission_type, "offline")

    def test_rejects_external_host(self):
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(
                qr_url(f"{VALID_ACCESS_KEY}|3|1", host="example.com")
            )

    def test_rejects_wrong_state(self):
        access_key = access_key_with(state="35")
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(qr_url(f"{access_key}|3|1"))

    def test_rejects_document_other_than_model_65(self):
        access_key = access_key_with(model="55")
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(qr_url(f"{access_key}|3|1"))

    def test_rejects_invalid_check_digit(self):
        invalid_key = f"{VALID_ACCESS_KEY[:43]}0"
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(qr_url(f"{invalid_key}|3|1"))

    def test_rejects_online_layout_for_offline_access_key(self):
        access_key = access_key_with(emission_type="9")
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(qr_url(f"{access_key}|3|1"))

    def test_rejects_duplicate_payload_parameter(self):
        payload = f"{VALID_ACCESS_KEY}|3|1"
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(f"{qr_url(payload)}&p={payload}")

    def test_rejects_unexpected_path_and_port(self):
        payload = f"{VALID_ACCESS_KEY}|3|1"

        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(
                f"https://consultadfe.fazenda.rj.gov.br/outro?p={payload}"
            )
        with self.assertRaises(InvalidQrCodeError):
            validate_nfce_qr_url(
                f"https://consultadfe.fazenda.rj.gov.br:8000"
                f"/consultaNFCe/QRCode?p={payload}"
            )


if __name__ == "__main__":
    unittest.main()

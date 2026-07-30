import unittest
from unittest.mock import patch

from nfce_errors import (
    NFCeDocumentMismatchError,
    SefazBlockedError,
    SefazIpBlockedError,
)
from nfce_scraper import _scrape_nfce_once, parse_nfce_html


RECAPTCHA_HTML = """
<html>
  <head>
    <script src="https://www.google.com/recaptcha/api.js"></script>
  </head>
  <body>
    <form><input id="recaptchaResponse" /></form>
  </body>
</html>
"""

IP_BLOCK_HTML = """
<html>
  <body>
    Nosso servico de seguranca bloqueia acessos provenientes desses enderecos IP.
    Seu endereco IP atual pode estar listado. Numero de ID e: 123456.
  </body>
</html>
"""

ACCESS_KEY_HTML = """
<html>
  <body>
    <div>Chave de acesso: 3326 0710 6976 9700 0317 6510 5000 4583 5412 5145 7065</div>
  </body>
</html>
"""

NFCE_HTML = """
<html>
  <body>
    <div id="infos">Emissao: 30/07/2026 18:20:30</div>
    <div id="Item1">
      <span class="txtTit">PRODUTO TESTE</span>
      <span class="Rqtd">Qtd.: 1</span>
      <span class="RUN">UN: UN</span>
      <span class="RvlUnit">Vl. Unit.: 5,00</span>
      <span class="valor">5,00</span>
    </div>
    <div id="totalNota">
      <div id="linhaTotal">
        <label>Qtd. total de itens</label>
        <span class="totalNumb">1</span>
      </div>
      <div id="linhaTotal">
        <label>Valor total</label>
        <span class="totalNumb">5,00</span>
      </div>
      <div id="linhaTotal">
        <label>Valor a pagar</label>
        <span class="totalNumb">5,00</span>
      </div>
    </div>
  </body>
</html>
"""


class NFCeBlockingTests(unittest.TestCase):
    def test_recaptcha_requires_user_action(self):
        with self.assertRaises(SefazBlockedError) as raised:
            parse_nfce_html(RECAPTCHA_HTML)

        self.assertEqual(raised.exception.code, "SEFAZ_ACTION_REQUIRED")
        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.action_required)

    def test_ip_block_has_specific_error_code(self):
        with self.assertRaises(SefazIpBlockedError) as raised:
            parse_nfce_html(IP_BLOCK_HTML)

        self.assertEqual(raised.exception.code, "SEFAZ_IP_BLOCKED")
        self.assertIn("123456", raised.exception.message)

    def test_rejects_html_from_another_access_key(self):
        with self.assertRaises(NFCeDocumentMismatchError) as raised:
            parse_nfce_html(
                ACCESS_KEY_HTML,
                expected_access_key="33260710699697000317651050004583541251457061",
            )

        self.assertEqual(raised.exception.code, "NFCE_DOCUMENT_MISMATCH")

    @patch("nfce_scraper._fetch_html_playwright")
    @patch("nfce_scraper._fetch_html_direct")
    def test_blocked_direct_request_does_not_start_playwright(
        self,
        fetch_direct,
        fetch_playwright,
    ):
        fetch_direct.return_value = (RECAPTCHA_HTML, 200)

        with self.assertRaises(SefazBlockedError):
            _scrape_nfce_once("https://consultadfe.fazenda.rj.gov.br/example")

        fetch_playwright.assert_not_called()

    @patch("nfce_scraper._fetch_html_playwright")
    @patch("nfce_scraper._fetch_html_direct")
    def test_unparseable_direct_response_uses_rendered_page_automatically(
        self,
        fetch_direct,
        fetch_playwright,
    ):
        fetch_direct.return_value = ("<html><body>Carregando...</body></html>", 200)
        fetch_playwright.return_value = (NFCE_HTML, 200)

        result = _scrape_nfce_once(
            "https://consultadfe.fazenda.rj.gov.br/example"
        )

        self.assertEqual(result["items"][0]["name"], "PRODUTO TESTE")
        self.assertEqual(result["extraction"]["source_format"], "html")
        fetch_playwright.assert_called_once()


if __name__ == "__main__":
    unittest.main()

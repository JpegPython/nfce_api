import unittest

from nfce_errors import NFCeDocumentMismatchError, NFCeParseError
from nfce_parser import parse_nfce_xml, reconcile_nfce_result


ACCESS_KEY = "33260710697697000317651050004583541251457065"

NFCE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{ACCESS_KEY}" versao="4.00">
      <ide>
        <mod>65</mod>
        <dhEmi>2026-07-30T18:20:30-03:00</dhEmi>
      </ide>
      <emit>
        <xNome>Mercado Teste LTDA</xNome>
        <enderEmit>
          <xLgr>Rua Principal</xLgr>
          <nro>10</nro>
          <xBairro>Centro</xBairro>
          <xMun>Rio de Janeiro</xMun>
          <UF>RJ</UF>
          <CEP>20000000</CEP>
        </enderEmit>
      </emit>
      <det nItem="1">
        <prod>
          <cProd>ABC</cProd>
          <cEAN>SEM GTIN</cEAN>
          <xProd>ARROZ TESTE</xProd>
          <qCom>2.0000</qCom>
          <uCom>UN</uCom>
          <vUnCom>5.00</vUnCom>
          <vProd>10.00</vProd>
          <vDesc>1.00</vDesc>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <cProd>DEF</cProd>
          <xProd>LEGUME TESTE</xProd>
          <qCom>0.5000</qCom>
          <uCom>KG</uCom>
          <vUnCom>20.00</vUnCom>
          <vProd>10.00</vProd>
        </prod>
      </det>
      <total>
        <ICMSTot>
          <vProd>20.00</vProd>
          <vDesc>1.00</vDesc>
          <vNF>19.00</vNF>
        </ICMSTot>
      </total>
      <pag>
        <detPag>
          <tPag>17</tPag>
          <vPag>19.00</vPag>
        </detPag>
      </pag>
    </infNFe>
  </NFe>
</nfeProc>
"""


class NFCeXmlParserTests(unittest.TestCase):
    def test_parses_namespaced_nfce_xml(self):
        result = parse_nfce_xml(NFCE_XML, expected_access_key=ACCESS_KEY)

        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["quantity"], 2.0)
        self.assertEqual(result["items"][1]["unit"], "KG")
        self.assertEqual(result["totals"]["amount_paid"], 19.0)
        self.assertIn("PIX", result["forma_pagamento"])
        self.assertEqual(result["extraction"]["source_format"], "xml")
        self.assertEqual(result["extraction"]["warnings"], [])

    def test_rejects_xml_from_another_document(self):
        with self.assertRaises(NFCeDocumentMismatchError):
            parse_nfce_xml(
                NFCE_XML,
                expected_access_key="33260710699697000317651050004583541251457061",
            )

    def test_rejects_malformed_xml(self):
        with self.assertRaises(NFCeParseError):
            parse_nfce_xml("<nfeProc><NFe>", expected_access_key=ACCESS_KEY)

    def test_reconciliation_reports_nonfatal_total_difference(self):
        result = reconcile_nfce_result(
            {
                "items": [
                    {
                        "name": "PRODUTO",
                        "price": 10,
                        "discount": 0,
                        "quantity": 1,
                        "unit": "UN",
                        "unit_price": 10,
                    }
                ],
                "totals": {
                    "items_count": 1,
                    "gross_total": 12,
                    "discount_total": 0,
                    "amount_paid": 12,
                },
            },
            source_format="html",
        )

        self.assertTrue(result["extraction"]["warnings"])

    def test_reconciliation_rejects_impossible_discount(self):
        with self.assertRaises(NFCeParseError):
            reconcile_nfce_result(
                {
                    "items": [
                        {
                            "name": "PRODUTO",
                            "price": 10,
                            "discount": 11,
                            "quantity": 1,
                        }
                    ]
                },
                source_format="html",
            )


if __name__ == "__main__":
    unittest.main()

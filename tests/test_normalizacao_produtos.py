import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from normalizacao_produtos import (
    bootstrap_unlinked_products,
    build_parser,
    category_for,
    main,
    reference_measurement,
)


class CategoryForTests(unittest.TestCase):
    def test_prioritizes_product_type_over_flavor_or_ambiguous_words(self):
        cases = {
            "AGUA SANITARIA SUPER GLOBO 1L": "Limpeza",
            "BISC.NESTLE NESFIT LIMAO E CEREAIS 160G": "Biscoitos e snacks",
            "PAO HAMBURGUER PLUS VITA 520 G TIPO BRIOCHE": "Padaria",
            "PEITO DE PERU DEFUMADO SADIA kg": "Frios e laticinios",
            "CAFE CAPPUCCINO 3 COR.AVELA 200G": "Mercearia",
            "FILE TILAPIA CONG.BOMAR PCT 500 G": "Peixes e frutos do mar",
            "BATATA CONG. MCCAIN 1,05 kg FININHAS": "Congelados",
            "CR.DENTAL COLGATE TOTAL GENGIVAS REFOR.180G": "Higiene e cuidado pessoal",
            "AMENDOIM AGTAL SALGADO 200G": "Biscoitos e snacks",
            "BATATA E.CHIPS PALHA TRADICIONAL 215G": "Biscoitos e snacks",
            "CONC.LIQ.DAFRUTA MARACUJA PET LV 1L PG 750ML": "Bebidas",
            "DIFUSOR GLADE LAVANDA 100ML": "Limpeza",
            "ERVILHAS PREDILECTA LT 170G": "Mercearia",
            "FACA TRAMONTINA CARNE 8 DYNAMIC R.8108": "Utilidades",
            "LA ACO BOMBRIL 6 UN": "Limpeza",
            "LUVA LIMPPANO VELOUTE GRANDE": "Limpeza",
            "MATE LEAO 250G": "Bebidas",
            "OSSO PALITO 6MM LE PETS C12": "Pet",
            "POLPA SEMPRE VIVA MANGA 400G": "Hortifruti",
            "TOALHA DE PAPEL ABSOLUTO 360 FOLHAS": "Utilidades",
        }

        for product_name, expected_category in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(category_for(product_name), expected_category)


class ReferenceMeasurementTests(unittest.TestCase):
    def test_normalizes_mass_volume_count_and_bulk_units(self):
        cases = (
            ("LEITE INTEGRAL 1L", "UN", ("ML", 1000.0)),
            ("BATATA CONGELADA 1,05KG", "UN", ("G", 1050.0)),
            ("OVOS BRANCOS C30", "UN", ("UN", 30.0)),
            ("QUEIJO MUSSARELA GRANEL KG", "KG", ("KG", 1.0)),
            ("SACOLA CINZA", "UN", ("UN", 1.0)),
        )

        for product_name, sale_unit, expected in cases:
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    reference_measurement(product_name, sale_unit),
                    expected,
                )


class BootstrapUnlinkedProductsTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            """
            CREATE TABLE categorias_produto (
                id INTEGER PRIMARY KEY,
                nome VARCHAR NOT NULL UNIQUE,
                descricao VARCHAR
            );
            CREATE TABLE produtos_canonicos (
                id INTEGER PRIMARY KEY,
                nome VARCHAR NOT NULL UNIQUE,
                categoria_id INTEGER,
                marca VARCHAR,
                unidade_referencia VARCHAR,
                quantidade_referencia FLOAT
            );
            CREATE TABLE produtos (
                id INTEGER PRIMARY KEY,
                nome VARCHAR NOT NULL UNIQUE,
                produto_canonico_id INTEGER
            );
            CREATE TABLE itens_compra (
                id INTEGER PRIMARY KEY,
                produto_id INTEGER,
                unidade VARCHAR
            );
            """
        )
        self.connection.execute(
            "INSERT INTO produtos_canonicos (id, nome) VALUES (1, 'EXISTENTE')"
        )
        self.connection.executemany(
            "INSERT INTO produtos (id, nome, produto_canonico_id) VALUES (?, ?, ?)",
            (
                (1, "ACUCAR UNIAO 1kg", None),
                (2, "ACUCAR UNIAO 1KG", None),
                (3, "PRODUTO JA VINCULADO", 1),
            ),
        )
        self.connection.executemany(
            "INSERT INTO itens_compra (produto_id, unidade) VALUES (?, 'UN')",
            ((1,), (2,), (3,)),
        )

    def tearDown(self):
        self.connection.close()

    def test_links_unlinked_products_without_changing_existing_links(self):
        result = bootstrap_unlinked_products(self.connection)

        self.assertEqual(
            result,
            {"created": 1, "linked": 2, "skipped": 0, "metadata_updated": 1},
        )
        rows = self.connection.execute(
            "SELECT id, produto_canonico_id FROM produtos ORDER BY id"
        ).fetchall()
        self.assertEqual(rows[0][1], rows[1][1])
        self.assertEqual(rows[2], (3, 1))

        canonical = self.connection.execute(
            """
            SELECT pc.nome, c.nome, pc.marca,
                   pc.unidade_referencia, pc.quantidade_referencia
            FROM produtos_canonicos pc
            JOIN categorias_produto c ON c.id = pc.categoria_id
            WHERE pc.id = ?
            """,
            (rows[0][1],),
        ).fetchone()
        self.assertEqual(
            canonical,
            ("ACUCAR UNIAO 1KG", "Mercearia", "UNIAO", "G", 1000.0),
        )
        existing = self.connection.execute(
            """
            SELECT c.nome, pc.unidade_referencia, pc.quantidade_referencia
            FROM produtos_canonicos pc
            JOIN categorias_produto c ON c.id = pc.categoria_id
            WHERE pc.id = 1
            """
        ).fetchone()
        self.assertEqual(existing, ("Outros", "UN", 1.0))

    def test_dry_run_rolls_back_all_changes(self):
        result = bootstrap_unlinked_products(self.connection, dry_run=True)

        self.assertEqual(
            result,
            {"created": 1, "linked": 2, "skipped": 0, "metadata_updated": 1},
        )
        linked = self.connection.execute(
            "SELECT COUNT(*) FROM produtos WHERE produto_canonico_id IS NOT NULL"
        ).fetchone()[0]
        canonical_count = self.connection.execute(
            "SELECT COUNT(*) FROM produtos_canonicos"
        ).fetchone()[0]
        category_count = self.connection.execute(
            "SELECT COUNT(*) FROM categorias_produto"
        ).fetchone()[0]
        self.assertEqual(linked, 1)
        self.assertEqual(canonical_count, 1)
        self.assertEqual(category_count, 0)


class CommandLineTests(unittest.TestCase):
    def test_accepts_bootstrap_in_dry_run_mode(self):
        args = build_parser().parse_args(["bootstrap", "--dry-run"])

        self.assertEqual(args.command, "bootstrap")
        self.assertTrue(args.dry_run)

    def test_bootstrap_dry_run_does_not_persist_schema_data(self):
        with tempfile.TemporaryDirectory() as directory:
            database_file = os.path.join(directory, "market.db")
            with sqlite3.connect(database_file) as connection:
                connection.executescript(
                    """
                    CREATE TABLE produtos (
                        id INTEGER PRIMARY KEY,
                        nome VARCHAR NOT NULL UNIQUE,
                        produto_canonico_id INTEGER
                    );
                    CREATE TABLE itens_compra (
                        id INTEGER PRIMARY KEY,
                        produto_id INTEGER,
                        unidade VARCHAR
                    );
                    INSERT INTO produtos (id, nome) VALUES (1, 'ACUCAR UNIAO 1KG');
                    INSERT INTO itens_compra (produto_id, unidade) VALUES (1, 'UN');
                    """
                )

            with patch.dict(
                os.environ,
                {"DATABASE_URL": f"sqlite:///{database_file}"},
            ), patch.object(
                sys,
                "argv",
                ["normalizacao_produtos.py", "bootstrap", "--dry-run"],
            ), redirect_stdout(io.StringIO()):
                main()

            with sqlite3.connect(database_file) as connection:
                linked = connection.execute(
                    "SELECT COUNT(*) FROM produtos WHERE produto_canonico_id IS NOT NULL"
                ).fetchone()[0]
                canonical_count = connection.execute(
                    "SELECT COUNT(*) FROM produtos_canonicos"
                ).fetchone()[0]
                category_count = connection.execute(
                    "SELECT COUNT(*) FROM categorias_produto"
                ).fetchone()[0]

            self.assertEqual(linked, 0)
            self.assertEqual(canonical_count, 0)
            self.assertEqual(category_count, 0)


if __name__ == "__main__":
    unittest.main()

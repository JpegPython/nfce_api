import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import Compra, ItemCompra
from nfce_service import salvar_compra


ACCESS_KEY = "33260710697697000317651050004583541251457065"


def sample_item(name="PRODUTO TESTE"):
    return {
        "name": name,
        "price": 10.0,
        "discount": 1.0,
        "final_price": 9.0,
        "quantity": 2.0,
        "unit": "UN",
        "unit_price": 5.0,
    }


class NFCePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session_patch = patch(
            "nfce_service.SessionLocal",
            self.session_factory,
        )
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()
        self.engine.dispose()

    def test_same_access_key_is_idempotent(self):
        first = salvar_compra(
            [sample_item()],
            datetime(2026, 7, 30, 18, 20),
            chave_acesso=ACCESS_KEY,
        )
        second = salvar_compra(
            [sample_item()],
            datetime(2026, 7, 30, 18, 20),
            chave_acesso=ACCESS_KEY,
        )

        db = self.session_factory()
        try:
            self.assertFalse(first.already_imported)
            self.assertTrue(second.already_imported)
            self.assertEqual(first.compra_id, second.compra_id)
            self.assertEqual(db.query(Compra).count(), 1)
            self.assertEqual(db.query(ItemCompra).count(), 1)
        finally:
            db.close()

    def test_invalid_item_rolls_back_purchase_and_all_items(self):
        with self.assertRaises(ValueError):
            salvar_compra(
                [sample_item(), sample_item(name="")],
                datetime(2026, 7, 30, 18, 20),
                chave_acesso=ACCESS_KEY,
            )

        db = self.session_factory()
        try:
            self.assertEqual(db.query(Compra).count(), 0)
            self.assertEqual(db.query(ItemCompra).count(), 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

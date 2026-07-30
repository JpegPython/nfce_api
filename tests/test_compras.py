import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from main import listar_compras
from models import Compra


class ListarComprasTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_lists_newest_purchases_first_and_uses_id_as_tiebreaker(self):
        db = self.session_factory()
        try:
            db.add_all(
                [
                    Compra(id=1, data=datetime(2026, 7, 29, 10, 0)),
                    Compra(id=2, data=datetime(2026, 7, 30, 10, 0)),
                    Compra(id=3, data=datetime(2026, 7, 30, 10, 0)),
                ]
            )
            db.commit()
        finally:
            db.close()

        with patch("main.SessionLocal", self.session_factory):
            resultado = listar_compras()

        self.assertEqual(
            [compra["compra_id"] for compra in resultado],
            [3, 2, 1],
        )


if __name__ == "__main__":
    unittest.main()

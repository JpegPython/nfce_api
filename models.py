from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from database import Base
from datetime import datetime

class Compra(Base):
    __tablename__ = "compras"

    id = Column(Integer, primary_key=True)
    valor_total = Column(Float)
    data = Column(DateTime)
    itens = relationship("ItemCompra")


class Produto(Base):
    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True)
    nome = Column(String)


class ItemCompra(Base):
    __tablename__ = "itens_compra"

    id = Column(Integer, primary_key=True)

    compra_id = Column(Integer, ForeignKey("compras.id"))
    produto_id = Column(Integer, ForeignKey("produtos.id"))

    preco = Column(Float)
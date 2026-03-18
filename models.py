from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class Compra(Base):
    __tablename__ = "compras"

    id = Column(Integer, primary_key=True)
    valor_bruto = Column(Float)          # Valor total sem desconto
    desconto_total = Column(Float)       # Total de descontos
    valor_pago = Column(Float)           # Valor final pago
    data = Column(DateTime, default=datetime.utcnow)

    mercado_nome = Column(String)        # Nome do mercado
    mercado_endereco = Column(String)    # Endereço do mercado
    forma_pagamento = Column(String)     # Ex: Cartão, Dinheiro

    itens = relationship("ItemCompra", back_populates="compra")


class Produto(Base):
    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True)   # Evita duplicação de produtos

    itens = relationship("ItemCompra", back_populates="produto")


class ItemCompra(Base):
    __tablename__ = "itens_compra"

    id = Column(Integer, primary_key=True)

    compra_id = Column(Integer, ForeignKey("compras.id"))
    produto_id = Column(Integer, ForeignKey("produtos.id"))

    quantidade = Column(Float, nullable=True)
    unidade = Column(String, nullable=True)       # UN, KG, LT...
    valor_unitario = Column(Float, nullable=True)
    preco_total = Column(Float, nullable=True)    # valor total do item (unit * qtde)
    desconto = Column(Float, default=0.0)

    compra = relationship("Compra", back_populates="itens")
    produto = relationship("Produto", back_populates="itens")
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
    produto_canonico_id = Column(Integer, ForeignKey("produtos_canonicos.id"), nullable=True)

    itens = relationship("ItemCompra", back_populates="produto")
    produto_canonico = relationship("ProdutoCanonico", back_populates="produtos")


class CategoriaProduto(Base):
    __tablename__ = "categorias_produto"

    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    descricao = Column(String, nullable=True)

    produtos_canonicos = relationship("ProdutoCanonico", back_populates="categoria")


class ProdutoCanonico(Base):
    __tablename__ = "produtos_canonicos"

    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    categoria_id = Column(Integer, ForeignKey("categorias_produto.id"), nullable=True)
    marca = Column(String, nullable=True)
    unidade_referencia = Column(String, nullable=True)
    quantidade_referencia = Column(Float, nullable=True)

    categoria = relationship("CategoriaProduto", back_populates="produtos_canonicos")
    produtos = relationship("Produto", back_populates="produto_canonico")


class SugestaoNormalizacaoProduto(Base):
    __tablename__ = "sugestoes_normalizacao_produtos"

    id = Column(Integer, primary_key=True)
    produto_origem_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    produto_destino_id = Column(Integer, ForeignKey("produtos.id"), nullable=False)
    nome_sugerido = Column(String, nullable=False)
    categoria_sugerida = Column(String, nullable=True)
    confianca = Column(Float, nullable=False)
    motivo = Column(String, nullable=True)
    status = Column(String, default="pendente")
    criado_em = Column(DateTime, default=datetime.utcnow)


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

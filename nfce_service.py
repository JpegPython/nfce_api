from database import SessionLocal
from models import Compra, Produto, ItemCompra
from sqlalchemy.exc import NoResultFound
from datetime import datetime

def salvar_compra(itens, data_compra, totals=None, mercado_nome=None, mercado_endereco=None, forma_pagamento=None):
    """
    Salva uma compra no banco com itens detalhados.

    :param itens: lista de dicts com keys:
        - name (str)
        - price (float)
        - discount (float)
        - final_price (float)
        - quantity (float, opcional)
        - unit (str, opcional)
        - unit_price (float, opcional)
    :param data_compra: datetime da compra
    :param totals: dict com 'gross_total', 'discount_total', 'amount_paid'
    :param mercado_nome: str nome do mercado
    :param mercado_endereco: str endereço do mercado
    :param forma_pagamento: str forma de pagamento
    :return: id da compra
    """

    if not itens:
        raise ValueError("Nao e permitido salvar compra sem itens.")

    db = SessionLocal()
    totals = totals or {}

    valor_bruto = totals.get("gross_total") or sum(item.get("price", 0.0) for item in itens)
    desconto_total = totals.get("discount_total") or sum(item.get("discount", 0.0) for item in itens)
    valor_pago = totals.get("amount_paid") or (valor_bruto - desconto_total)

    compra = Compra(
        valor_bruto=valor_bruto,
        desconto_total=desconto_total,
        valor_pago=valor_pago,
        data=data_compra,
        mercado_nome=mercado_nome,
        mercado_endereco=mercado_endereco,
        forma_pagamento=forma_pagamento
    )

    db.add(compra)
    db.commit()
    db.refresh(compra)
    compra_id = compra.id

    for item in itens:
        produto_nome = item.get("name")
        produto = db.query(Produto).filter_by(nome=produto_nome).one_or_none()
        if not produto:
            produto = Produto(nome=produto_nome)
            db.add(produto)
            db.flush()  # garante que produto.id esteja disponível

        quantidade = item.get("quantity") or 1.0
        unidade = item.get("unit") or "UN"
        valor_unitario = item.get("unit_price") or item.get("price", 0.0)
        preco_total = item.get("final_price") or (valor_unitario * quantidade)
        desconto = item.get("discount") or 0.0

        item_compra = ItemCompra(
            compra_id=compra_id,
            produto_id=produto.id,
            quantidade=quantidade,
            unidade=unidade,
            valor_unitario=valor_unitario,
            preco_total=preco_total,
            desconto=desconto
        )

        db.add(item_compra)

    db.commit()
    db.close()

    return compra_id
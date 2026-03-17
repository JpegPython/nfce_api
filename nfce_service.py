from database import SessionLocal
from models import Compra, Produto, ItemCompra
from datetime import datetime

def salvar_compra(itens, data_compra, totals=None):

    db = SessionLocal()

    totals = totals or {}
    total = totals.get("amount_paid")

    if total is None:
        total = sum(item.get("final_price", item["price"]) for item in itens)

    compra = Compra(
        valor_total=total,
        data=data_compra
    )

    db.add(compra)
    db.commit()
    db.refresh(compra)

    compra_id = compra.id

    for item in itens:

        produto = Produto(nome=item["name"])

        db.add(produto)
        db.flush()

        item_compra = ItemCompra(
            compra_id=compra_id,
            produto_id=produto.id,
            preco=item["price"]
        )

        db.add(item_compra)

    db.commit()
    db.close()

    return compra_id
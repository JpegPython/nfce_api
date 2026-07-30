from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import Compra, ItemCompra, Produto


@dataclass(frozen=True)
class SavePurchaseResult:
    compra_id: int
    already_imported: bool


def _value_or_default(value, default):
    return default if value is None else value


def buscar_compra_por_chave(chave_acesso: str | None) -> dict | None:
    if not chave_acesso:
        return None

    db = SessionLocal()
    try:
        compra = (
            db.query(Compra)
            .filter(Compra.chave_acesso == chave_acesso)
            .one_or_none()
        )
        if compra is None:
            return None

        items = []
        for item in compra.itens:
            final_price = item.preco_total or 0.0
            discount = item.desconto or 0.0
            items.append(
                {
                    "name": item.produto.nome if item.produto else "Produto sem nome",
                    "price": final_price + discount,
                    "discount": discount,
                    "final_price": final_price,
                    "quantity": item.quantidade or 1.0,
                    "unit": item.unidade or "UN",
                    "unit_price": item.valor_unitario or 0.0,
                }
            )

        return {
            "compra_id": compra.id,
            "data": compra.data,
            "items": items,
            "totals": {
                "items_count": len(items),
                "gross_total": compra.valor_bruto or 0.0,
                "discount_total": compra.desconto_total or 0.0,
                "amount_paid": compra.valor_pago or 0.0,
            },
            "mercado_nome": compra.mercado_nome,
            "mercado_endereco": compra.mercado_endereco,
            "forma_pagamento": compra.forma_pagamento,
            "access_key": compra.chave_acesso,
            "already_imported": True,
            "extraction": {
                "source_format": "database",
                "reconciled": True,
                "warnings": [],
            },
        }
    finally:
        db.close()


def salvar_compra(
    itens,
    data_compra,
    totals=None,
    mercado_nome=None,
    mercado_endereco=None,
    forma_pagamento=None,
    chave_acesso=None,
) -> SavePurchaseResult:
    if not itens:
        raise ValueError("Nao e permitido salvar compra sem itens.")

    totals = totals or {}
    valor_bruto = _value_or_default(
        totals.get("gross_total"),
        sum(item.get("price", 0.0) for item in itens),
    )
    desconto_total = _value_or_default(
        totals.get("discount_total"),
        sum(item.get("discount", 0.0) for item in itens),
    )
    valor_pago = _value_or_default(
        totals.get("amount_paid"),
        valor_bruto - desconto_total,
    )

    db = SessionLocal()
    try:
        with db.begin():
            if chave_acesso:
                existing = (
                    db.query(Compra)
                    .filter(Compra.chave_acesso == chave_acesso)
                    .one_or_none()
                )
                if existing:
                    return SavePurchaseResult(existing.id, True)

            compra = Compra(
                valor_bruto=valor_bruto,
                desconto_total=desconto_total,
                valor_pago=valor_pago,
                data=data_compra or datetime.utcnow(),
                mercado_nome=mercado_nome,
                mercado_endereco=mercado_endereco,
                forma_pagamento=forma_pagamento,
                chave_acesso=chave_acesso,
            )
            db.add(compra)
            db.flush()

            for item in itens:
                produto_nome = (item.get("name") or "").strip()
                if not produto_nome:
                    raise ValueError("Nao e permitido salvar item sem nome de produto.")

                produto = (
                    db.query(Produto)
                    .filter(Produto.nome == produto_nome)
                    .one_or_none()
                )
                if not produto:
                    produto = Produto(nome=produto_nome)
                    db.add(produto)
                    db.flush()

                quantidade = _value_or_default(item.get("quantity"), 1.0)
                unidade = _value_or_default(item.get("unit"), "UN")
                valor_unitario = _value_or_default(
                    item.get("unit_price"),
                    item.get("price", 0.0),
                )
                preco_total = _value_or_default(
                    item.get("final_price"),
                    valor_unitario * quantidade,
                )
                desconto = _value_or_default(item.get("discount"), 0.0)

                db.add(
                    ItemCompra(
                        compra_id=compra.id,
                        produto_id=produto.id,
                        quantidade=quantidade,
                        unidade=unidade,
                        valor_unitario=valor_unitario,
                        preco_total=preco_total,
                        desconto=desconto,
                    )
                )

        return SavePurchaseResult(compra.id, False)
    except IntegrityError:
        db.rollback()
        existing = buscar_compra_por_chave(chave_acesso)
        if existing:
            return SavePurchaseResult(existing["compra_id"], True)
        raise
    finally:
        db.close()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from nfce_scraper import scrape_nfce
from nfce_service import salvar_compra

from database import Base, engine, SessionLocal
from models import Compra, ItemCompra, Produto

# Criação das tabelas no banco
Base.metadata.create_all(bind=engine)

app = FastAPI()


class NFCeRequest(BaseModel):
    url: str


@app.post("/nfce")
def read_nfce(data: NFCeRequest):
    resultado = scrape_nfce(data.url)

    items = resultado.get("items") or []
    if len(items) == 0:
        raise HTTPException(
            status_code=422,
            detail="Ocorreu um erro na leitura da nota: nenhum item foi identificado.",
        )

    compra_id = salvar_compra(
        itens=items,
        data_compra=resultado["data_compra"],
        totals=resultado.get("totals"),
        mercado_nome=resultado.get("mercado_nome"),
        mercado_endereco=resultado.get("mercado_endereco"),
        forma_pagamento=resultado.get("forma_pagamento")
    )

    return {
        "compra_id": compra_id,
        "data": resultado["data_compra"],
        "items": resultado["items"],
        "totals": resultado.get("totals"),
        "mercado_nome": resultado.get("mercado_nome"),
        "mercado_endereco": resultado.get("mercado_endereco"),
        "forma_pagamento": resultado.get("forma_pagamento")
    }


@app.get("/compras")
def listar_compras():
    db = SessionLocal()
    compras = db.query(Compra).all()
    resultado = []

    for compra in compras:
        itens = db.query(ItemCompra).filter(ItemCompra.compra_id == compra.id).all()
        lista_itens = []

        for item in itens:
            produto = db.query(Produto).filter(Produto.id == item.produto_id).first()
            lista_itens.append({
                "produto": produto.nome if produto else None,
                "quantidade": item.quantidade or 1.0,
                "unidade": item.unidade or "UN",
                "valor_unitario": item.valor_unitario or 0.0,
                "preco_total": item.preco_total or 0.0,
                "desconto": item.desconto or 0.0,
            })

        resultado.append({
            "compra_id": compra.id,
            "data": compra.data,
            "valor_bruto": compra.valor_bruto or 0.0,
            "desconto_total": compra.desconto_total or 0.0,
            "valor_pago": compra.valor_pago or 0.0,
            "mercado_nome": compra.mercado_nome,
            "mercado_endereco": compra.mercado_endereco,
            "forma_pagamento": compra.forma_pagamento,
            "itens": lista_itens
        })

    db.close()
    return resultado
    



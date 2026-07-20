from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from nfce_scraper import (
    SefazBlockedError,
    SefazNotFoundError,
    SefazTemporaryError,
    parse_nfce_html,
    scrape_nfce,
)
from nfce_service import salvar_compra

from database import SessionLocal, ensure_database_schema
from models import Compra, ItemCompra, Produto

# Criação das tabelas no banco
ensure_database_schema()

app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


class NFCeRequest(BaseModel):
    url: str


class NFCeHtmlRequest(BaseModel):
    html: str
    source_url: str | None = None


def _salvar_resultado_nfce(resultado):
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


@app.post("/nfce")
def read_nfce(data: NFCeRequest):
    try:
        resultado = scrape_nfce(data.url)
    except SefazBlockedError as error:
        raise HTTPException(status_code=424, detail=str(error))
    except SefazNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except SefazTemporaryError as error:
        raise HTTPException(status_code=503, detail=str(error))

    return _salvar_resultado_nfce(resultado)


@app.post("/nfce/html")
def read_nfce_html(data: NFCeHtmlRequest):
    try:
        resultado = parse_nfce_html(data.html)
    except SefazBlockedError as error:
        raise HTTPException(status_code=424, detail=str(error))
    except SefazNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _salvar_resultado_nfce(resultado)


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


@app.get("/gastos-mensais")
def listar_gastos_mensais():
    db = SessionLocal()

    try:
        # SQLite: agrupa por ano-mes usando strftime para manter ordenacao correta.
        rows = (
            db.query(
                func.strftime("%Y-%m", Compra.data).label("ano_mes"),
                func.sum(Compra.valor_pago).label("total"),
            )
            .group_by(func.strftime("%Y-%m", Compra.data))
            .order_by(func.strftime("%Y-%m", Compra.data))
            .all()
        )

        if not rows:
            return []

        resultado = []
        for ano_mes, total in rows:
            ano, mes = ano_mes.split("-")
            resultado.append(
                {
                    "mes": f"{mes}/{ano}",
                    "total": float(total or 0.0),
                }
            )

        return resultado
    finally:
        db.close()



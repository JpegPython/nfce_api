from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func

from database import SessionLocal, ensure_database_schema
from models import Compra, ItemCompra, Produto
from nfce_errors import NFCeError, NFCeParseError
from nfce_qr import validate_nfce_qr_url
from nfce_scraper import NFCE_MAX_HTML_CHARS, parse_nfce_document, scrape_nfce
from nfce_service import buscar_compra_por_chave, salvar_compra


ensure_database_schema()

app = FastAPI()


@app.exception_handler(NFCeError)
async def nfce_error_handler(_request: Request, error: NFCeError):
    return JSONResponse(
        status_code=error.status_code,
        content={
            "detail": error.message,
            **error.as_dict(),
        },
    )


@app.get("/health")
def health_check():
    return {"status": "ok"}


class NFCeRequest(BaseModel):
    url: str


class NFCeHtmlRequest(BaseModel):
    html: str = Field(min_length=1, max_length=NFCE_MAX_HTML_CHARS)
    source_url: str


def _salvar_resultado_nfce(resultado):
    items = resultado.get("items") or []
    if not items:
        raise NFCeParseError(
            "Ocorreu um erro na leitura da nota: nenhum item foi identificado."
        )

    save_result = salvar_compra(
        itens=items,
        data_compra=resultado["data_compra"],
        totals=resultado.get("totals"),
        mercado_nome=resultado.get("mercado_nome"),
        mercado_endereco=resultado.get("mercado_endereco"),
        forma_pagamento=resultado.get("forma_pagamento"),
        chave_acesso=resultado.get("access_key"),
    )

    return {
        "compra_id": save_result.compra_id,
        "already_imported": save_result.already_imported,
        "data": resultado["data_compra"],
        "items": resultado["items"],
        "totals": resultado.get("totals"),
        "mercado_nome": resultado.get("mercado_nome"),
        "mercado_endereco": resultado.get("mercado_endereco"),
        "forma_pagamento": resultado.get("forma_pagamento"),
        "access_key": resultado.get("access_key"),
        "qr": resultado.get("qr"),
        "extraction": resultado.get("extraction"),
    }


@app.post("/nfce/validate")
def validate_nfce(data: NFCeRequest):
    return validate_nfce_qr_url(data.url).as_dict()


@app.post("/nfce")
def read_nfce(data: NFCeRequest):
    qr_data = validate_nfce_qr_url(data.url)
    existing = buscar_compra_por_chave(qr_data.access_key)
    if existing:
        existing["qr"] = qr_data.as_dict()
        return existing

    resultado = scrape_nfce(data.url)
    return _salvar_resultado_nfce(resultado)


@app.post("/nfce/html")
def read_nfce_html(data: NFCeHtmlRequest):
    qr_data = validate_nfce_qr_url(data.source_url)
    existing = buscar_compra_por_chave(qr_data.access_key)
    if existing:
        existing["qr"] = qr_data.as_dict()
        return existing

    resultado = parse_nfce_document(
        data.html,
        expected_access_key=qr_data.access_key,
    )
    resultado["access_key"] = qr_data.access_key
    resultado["qr"] = qr_data.as_dict()
    return _salvar_resultado_nfce(resultado)


@app.get("/compras")
def listar_compras():
    db = SessionLocal()
    compras = (
        db.query(Compra)
        .order_by(Compra.data.desc(), Compra.id.desc())
        .all()
    )
    resultado = []

    for compra in compras:
        itens = db.query(ItemCompra).filter(ItemCompra.compra_id == compra.id).all()
        lista_itens = []

        for item in itens:
            produto = db.query(Produto).filter(Produto.id == item.produto_id).first()
            lista_itens.append(
                {
                    "produto": produto.nome if produto else None,
                    "quantidade": item.quantidade or 1.0,
                    "unidade": item.unidade or "UN",
                    "valor_unitario": item.valor_unitario or 0.0,
                    "preco_total": item.preco_total or 0.0,
                    "desconto": item.desconto or 0.0,
                }
            )

        resultado.append(
            {
                "compra_id": compra.id,
                "data": compra.data,
                "valor_bruto": compra.valor_bruto or 0.0,
                "desconto_total": compra.desconto_total or 0.0,
                "valor_pago": compra.valor_pago or 0.0,
                "mercado_nome": compra.mercado_nome,
                "mercado_endereco": compra.mercado_endereco,
                "forma_pagamento": compra.forma_pagamento,
                "chave_acesso": compra.chave_acesso,
                "itens": lista_itens,
            }
        )

    db.close()
    return resultado


@app.get("/gastos-mensais")
def listar_gastos_mensais():
    db = SessionLocal()

    try:
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

from fastapi import FastAPI
from pydantic import BaseModel

from nfce_scraper import scrape_nfce
from nfce_service import salvar_compra

from database import Base, engine, SessionLocal
from models import Compra, ItemCompra, Produto

Base.metadata.create_all(bind=engine)

app = FastAPI()


class NFCeRequest(BaseModel):
    url: str


@app.post("/nfce")
def read_nfce(data: NFCeRequest):

    resultado = scrape_nfce(data.url)
    compra_id = salvar_compra(
        resultado["items"],
        resultado["data_compra"],
        resultado.get("totals")
    )

    return {
        "compra_id": compra_id,
        "data": resultado["data_compra"],
        "items": resultado["items"],
        "totals": resultado.get("totals")
    }


@app.get("/compras")
def listar_compras():

    db = SessionLocal()

    compras = db.query(Compra).all()

    resultado = []

    for compra in compras:

        itens = db.query(ItemCompra).filter(
            ItemCompra.compra_id == compra.id
        ).all()

        lista_itens = []

        for item in itens:

            produto = db.query(Produto).filter(
                Produto.id == item.produto_id
            ).first()

            lista_itens.append({
                "produto": produto.nome,
                "preco": item.preco
            })

        resultado.append({
            "compra_id": compra.id,
            "data": compra.data,
            "total": compra.valor_total,
            "itens": lista_itens
        })

    db.close()

    return resultado
    



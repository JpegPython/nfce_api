from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from sqlalchemy import inspect, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./market.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def ensure_database_schema():
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    produto_columns = {column["name"] for column in inspector.get_columns("produtos")}

    if "produto_canonico_id" not in produto_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE produtos "
                    "ADD COLUMN produto_canonico_id INTEGER "
                    "REFERENCES produtos_canonicos(id)"
                )
            )

    compra_columns = {column["name"] for column in inspector.get_columns("compras")}
    if "chave_acesso" not in compra_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE compras ADD COLUMN chave_acesso VARCHAR(44)")
            )

    compra_inspector = inspect(engine)
    unique_constraints = compra_inspector.get_unique_constraints("compras")
    has_unique_access_key = any(
        constraint.get("column_names") == ["chave_acesso"]
        for constraint in unique_constraints
    )

    if not has_unique_access_key:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_compras_chave_acesso ON compras (chave_acesso)"
                )
            )

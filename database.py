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

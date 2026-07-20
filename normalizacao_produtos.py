import argparse
import os
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

CATEGORIES = (
    "Hortifruti",
    "Carnes e aves",
    "Peixes e frutos do mar",
    "Congelados",
    "Frios e laticinios",
    "Bebidas",
    "Mercearia",
    "Padaria",
    "Biscoitos e snacks",
    "Doces e chocolates",
    "Limpeza",
    "Higiene e cuidado pessoal",
    "Pet",
    "Utilidades",
    "Outros",
)

ABBREVIATIONS = {
    "B": "BEBIDA",
    "BEB": "BEBIDA",
    "BL": "BEBIDA LACTEA",
    "LACTEA": "LACTEA",
    "CR": "CREME",
    "COND": "CONDIMENTO",
    "QJO": "QUEIJO",
    "FGO": "FRANGO",
    "LING": "LINGUICA",
    "LIMP": "LIMPADOR",
    "LIQ": "LIQUIDO",
    "DESO": "DESODORIZADOR",
    "ACHOC": "ACHOCOLATADO",
    "AMAC": "AMACIANTE",
    "BISC": "BISCOITO",
    "ESP": "ESPECIAL",
    "E": "EXTRA",
}

BRANDS = {
    "ADRIA",
    "BAUDUCCO",
    "BATAVO",
    "BEM BRASIL",
    "BIC",
    "BORGES",
    "COMBRASIL",
    "COMFORT",
    "C VALE",
    "DOWNY",
    "ELEGE",
    "ETTI",
    "GAROTO",
    "GRAN MESTRI",
    "HELLMANNS",
    "HEINEKEN",
    "INTIMUS",
    "ITAMBE",
    "KITANO",
    "LACTA",
    "LEAO",
    "LIMPOL",
    "MARILAN",
    "MCCAIN",
    "MUNDIAL",
    "NESCAU",
    "NESFIT",
    "NESTLE",
    "NUTELLA",
    "PIRACANJUBA",
    "PIRAKIDS",
    "PIRAQUE",
    "POMAROLA",
    "PREDILECTA",
    "PRESIDENT",
    "PERDIGAO",
    "QUALY",
    "QUAKER",
    "QUERO",
    "RED BULL",
    "SADIA",
    "SEARA",
    "SCOTCH BRITE",
    "SMIRNOFF",
    "TODDYNHO",
    "UNIAO",
    "VEJA",
    "WHISKAS",
    "YOKI",
}

VARIANT_TOKENS = {
    "ACAI",
    "AVELA",
    "AZUL",
    "BRANCO",
    "CACAU",
    "CARNE",
    "CHOCOLATE",
    "DESNATADO",
    "FRANGO",
    "GINSENG",
    "HORTELA",
    "INTEGRAL",
    "LARANJA",
    "LAR",
    "LAVANDA",
    "LIMAO",
    "MORANGO",
    "NATURAL",
    "ORIGINAL",
    "PICANHA",
    "TRADICIONAL",
    "VERDE",
    "VERMELHO",
}

NOISE_TOKENS = {
    "BJ",
    "BDJ",
    "C",
    "COM",
    "DESC",
    "EMB",
    "ECON",
    "GRANEL",
    "KG",
    "L",
    "LT",
    "ML",
    "G",
    "LV",
    "N",
    "P",
    "PC",
    "PCT",
    "PG",
    "REFIL",
    "SACHE",
    "SACHET",
    "UN",
    "UND",
    "UNIDADE",
    "UM",
}


def database_path() -> str:
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "", 1)

    data_db = Path("data/market.db")
    if data_db.exists():
        return str(data_db)

    return "market.db"


def ensure_sqlite_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS categorias_produto (
            id INTEGER NOT NULL,
            nome VARCHAR NOT NULL,
            descricao VARCHAR,
            PRIMARY KEY (id),
            UNIQUE (nome)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS produtos_canonicos (
            id INTEGER NOT NULL,
            nome VARCHAR NOT NULL,
            categoria_id INTEGER,
            marca VARCHAR,
            unidade_referencia VARCHAR,
            quantidade_referencia FLOAT,
            PRIMARY KEY (id),
            UNIQUE (nome),
            FOREIGN KEY(categoria_id) REFERENCES categorias_produto (id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sugestoes_normalizacao_produtos (
            id INTEGER NOT NULL,
            produto_origem_id INTEGER NOT NULL,
            produto_destino_id INTEGER NOT NULL,
            nome_sugerido VARCHAR NOT NULL,
            categoria_sugerida VARCHAR,
            confianca FLOAT NOT NULL,
            motivo VARCHAR,
            status VARCHAR,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            FOREIGN KEY(produto_origem_id) REFERENCES produtos (id),
            FOREIGN KEY(produto_destino_id) REFERENCES produtos (id)
        )
        """
    )

    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(produtos)").fetchall()
    }
    if "produto_canonico_id" not in columns:
        connection.execute(
            "ALTER TABLE produtos "
            "ADD COLUMN produto_canonico_id INTEGER "
            "REFERENCES produtos_canonicos(id)"
        )


def normalize_text(raw_text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", raw_text)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    text = ascii_text.upper()
    text = re.sub(r"([A-Z])\.([A-Z])", r"\1 \2", text)
    text = re.sub(r"[^A-Z0-9,./]+", " ", text)
    text = text.replace(".", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokens(raw_text: str) -> list[str]:
    normalized = normalize_text(raw_text)
    raw_tokens = re.findall(r"[A-Z]+|\d+(?:[,.]\d+)?", normalized)
    expanded = []

    for token in raw_tokens:
        expanded.extend(ABBREVIATIONS.get(token, token).split())

    return expanded


def measurement_signature(raw_text: str) -> str | None:
    text = normalize_text(raw_text).replace(",", ".")
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(KG|G|ML|L)\b", text)
    if not matches:
        return None

    value, unit = matches[-1]
    amount = float(value)
    if unit == "KG":
        amount *= 1000
        unit = "G"
    elif unit == "L":
        amount *= 1000
        unit = "ML"

    if amount.is_integer():
        amount_text = str(int(amount))
    else:
        amount_text = f"{amount:.2f}".rstrip("0").rstrip(".")

    return f"{amount_text}{unit}"


def reference_measurement(
    raw_text: str,
    sale_unit: str | None,
) -> tuple[str, float]:
    text = normalize_text(raw_text).replace(",", ".")
    measurements = re.findall(r"(\d+(?:\.\d+)?)\s*(KG|G|ML|L)\b", text)

    if measurements:
        value, unit = measurements[-1]
        amount = float(value)
        if unit == "KG":
            return "G", amount * 1000
        if unit == "L":
            return "ML", amount * 1000
        return unit, amount

    count_match = re.search(r"\bC\s*/?\s*(\d+)\b", text)
    if not count_match:
        count_match = re.search(r"\b(\d+)\s*(?:UN|UND|UNIDADES)\b", text)
    if count_match:
        return "UN", float(count_match.group(1))

    normalized_sale_unit = normalize_text(sale_unit or "UN")
    if normalized_sale_unit in {"KG", "G", "L", "ML", "UN"}:
        return normalized_sale_unit, 1.0
    return "UN", 1.0


def package_signature(raw_text: str) -> str | None:
    text = normalize_text(raw_text)
    match = re.search(r"\bC\s*/?\s*(\d+)\b", text)
    if match:
        return f"C{match.group(1)}"
    return None


def compact_key(raw_text: str) -> str:
    useful = []
    for token in tokens(raw_text):
        if token.isdigit():
            continue
        if token in NOISE_TOKENS:
            continue
        useful.append(token)
    return " ".join(useful)


def bag_key(raw_text: str) -> str:
    useful = []
    for token in tokens(raw_text):
        if token.isdigit():
            continue
        if token in NOISE_TOKENS:
            continue
        useful.append(token)
    return " ".join(sorted(set(useful)))


def brand_for(raw_text: str) -> str | None:
    text = f" {normalize_text(raw_text)} "
    for brand in sorted(BRANDS, key=len, reverse=True):
        if f" {brand} " in text:
            return brand
    return None


def variant_tokens_for(raw_text: str) -> set[str]:
    return set(tokens(raw_text)) & VARIANT_TOKENS


def category_for(raw_text: str) -> str:
    text = f" {compact_key(raw_text)} "

    rules = (
        ("Pet", r"\b(RACAO|WHISKAS|GATITOS|OSSO|PETS)\b"),
        ("Limpeza", r"\b(AGUA SANITARIA|ALVEJANTE|AMACIANTE|LAVA|LIMPADOR|MULTIUSO|ESPONJA|PATO|VANISH|VEJA|ROUPAS|DIFUSOR|LA ACO|LUVA)\b"),
        ("Higiene e cuidado pessoal", r"\b(INTIMUS|APAR|GILLETTE|SAB|SABONETE|PAPEL HIG|DENTAL|ENXAG BUCAL|DESOD|HASTES FLEXIVEIS|TOALHAS UMEDECIDAS)\b"),
        ("Biscoitos e snacks", r"\b(BISCOITO|BISC|TORRADA|COOKIE|WAFER|BATATA PALHA|CHIPS|PRINGLES|CEREAIS|MAGIC TOAST|AMENDOIM)\b"),
        ("Padaria", r"\b(PAO|BOLO|PANETONE|ROSQUINHA)\b"),
        ("Frios e laticinios", r"\b(LEITE|LACTEA|IOG|QUEIJO|QJO|MUSSARELA|REQUEIJAO|MARGARINA|MANTEIGA|CREME DE LEITE|CREME DE RICOTA|CHANTILLY|PEITO DE PERU|PRESUNTO)\b"),
        ("Peixes e frutos do mar", r"\b(TILAPIA|ATUM|SALMAO|SARDINHA|CAMARAO|BACALHAU|PEIXE)\b"),
        ("Congelados", r"\b(BATATA (CG|CONG)|CHICKEN CRISPY|QUINOA BURGER|POLPA (CG|CONG))\b"),
        ("Carnes e aves", r"\b(FRANGO|FGO|FILE|PEITO|COXA|LINGUICA|CALABRESA|BOV|PATINHO|PICANHA|HAMBURGUER|SALSICHA)\b"),
        ("Bebidas", r"\b(AGUA|BEBIDA|GUARAVITON|GUARAVITA|ENERGETICO|RED BULL|DRINK|SMIRNOFF|CERVEJA|CHA|MATE|ICE TEA|SUCO|DAFRUTA|ACHOCOLATADO|NESCAU|TODDYNHO|VODKA)\b"),
        ("Mercearia", r"\b(ARROZ|FEIJAO|ACUCAR|AVEIA|AZEITE|CAFE|CACAU|MILHO|ERVILHA|ERVILHAS|MOLHO|KETCHUP|MASSA|MAIONESE|FARINHA|OVOS|CONDIMENTO|KITANO|LENTILHA|TAPIOCA|TEMPERO|PAPRICA|LEMON PEPPER|SAL)\b"),
        ("Doces e chocolates", r"\b(CHOCOLATE|BOMBOM|SNICKERS|NUTELLA|AVELA|LACTA|GAROTO|PACOCA|COBERTURA)\b"),
        ("Hortifruti", r"\b(ALFACE|ALHO|BANANA|BATATA INGLESA|BATATA LISA|BROCOLIS|CEBOLA|CENOURA|CHEIRO VERDE|COENTRO|COUVE|LIMAO|MACA|MAMAO|MANDIOCA|POLPA|TANGERINA|TOMATE|POKAN|PONKAN)\b"),
        ("Utilidades", r"\b(FACA|SACOLA|TOALHA DE PAPEL|TOALHA PAPEL)\b"),
    )

    for category, pattern in rules:
        if re.search(pattern, text):
            return category

    return "Outros"


def suggested_name(name_a: str, name_b: str) -> str:
    keys = [compact_key(name_a), compact_key(name_b)]
    chosen = min(keys, key=len)
    measure = measurement_signature(name_a) or measurement_signature(name_b)
    if measure and measure not in chosen:
        chosen = f"{chosen} {measure}"
    return chosen


def should_suggest(name_a: str, name_b: str) -> tuple[float, str] | None:
    compact_a = compact_key(name_a)
    compact_b = compact_key(name_b)
    bag_a = bag_key(name_a)
    bag_b = bag_key(name_b)
    measure_a = measurement_signature(name_a)
    measure_b = measurement_signature(name_b)
    package_a = package_signature(name_a)
    package_b = package_signature(name_b)
    brand_a = brand_for(name_a)
    brand_b = brand_for(name_b)
    variants_a = variant_tokens_for(name_a)
    variants_b = variant_tokens_for(name_b)

    if brand_a and brand_b and brand_a != brand_b:
        return None

    if brand_a != brand_b:
        brandless_ratio = SequenceMatcher(None, compact_a, compact_b).ratio()
        if brandless_ratio < 0.93:
            return None

    different_variants = variants_a.symmetric_difference(variants_b)
    if different_variants and compact_a != compact_b and bag_a != bag_b:
        return None

    if measure_a and measure_b and measure_a != measure_b:
        return None

    if package_a != package_b:
        return None

    if bag_a and bag_a == bag_b:
        return 0.98, "mesmos tokens relevantes apos normalizacao"

    ratio = SequenceMatcher(None, compact_a, compact_b).ratio()

    if measure_a == measure_b and ratio >= 0.86:
        return round(ratio, 2), "nome muito parecido e mesma embalagem"

    if not measure_a and not measure_b and ratio >= 0.9:
        return round(ratio, 2), "nome muito parecido sem embalagem informada"

    if category_for(name_a) == "Hortifruti" and category_for(name_b) == "Hortifruti" and ratio >= 0.82:
        return round(ratio, 2), "hortifruti com nome muito parecido"

    return None


def seed_categories(connection: sqlite3.Connection) -> None:
    connection.executemany(
        "INSERT OR IGNORE INTO categorias_produto (nome) VALUES (?)",
        [(category,) for category in CATEGORIES],
    )


def load_products(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT
            p.id,
            p.nome,
            COUNT(i.id) AS ocorrencias,
            MIN(i.valor_unitario) AS menor_preco,
            MAX(i.valor_unitario) AS maior_preco,
            GROUP_CONCAT(DISTINCT i.unidade) AS unidades
        FROM produtos p
        LEFT JOIN itens_compra i ON i.produto_id = p.id
        GROUP BY p.id, p.nome
        ORDER BY p.nome
        """
    ).fetchall()

    return [
        {
            "id": row[0],
            "nome": row[1],
            "ocorrencias": row[2],
            "menor_preco": row[3],
            "maior_preco": row[4],
            "unidades": row[5],
        }
        for row in rows
    ]


def generate_suggestions(products: list[dict]) -> list[dict]:
    suggestions = []

    for index, product_a in enumerate(products):
        for product_b in products[index + 1 :]:
            result = should_suggest(product_a["nome"], product_b["nome"])
            if not result:
                continue

            confidence, reason = result
            destination, origin = sorted(
                (product_a, product_b),
                key=lambda product: (-product["ocorrencias"], len(product["nome"]), product["nome"]),
            )

            suggestions.append(
                {
                    "produto_origem_id": origin["id"],
                    "produto_destino_id": destination["id"],
                    "nome_origem": origin["nome"],
                    "nome_destino": destination["nome"],
                    "nome_sugerido": suggested_name(origin["nome"], destination["nome"]),
                    "categoria_sugerida": category_for(destination["nome"]),
                    "confianca": confidence,
                    "motivo": reason,
                }
            )

    suggestions.sort(key=lambda item: (-item["confianca"], item["categoria_sugerida"], item["nome_sugerido"]))
    return suggestions


def persist_suggestions(connection: sqlite3.Connection, suggestions: list[dict], min_confidence: float) -> int:
    connection.execute("DELETE FROM sugestoes_normalizacao_produtos WHERE status = 'pendente'")

    filtered = [suggestion for suggestion in suggestions if suggestion["confianca"] >= min_confidence]
    connection.executemany(
        """
        INSERT INTO sugestoes_normalizacao_produtos (
            produto_origem_id,
            produto_destino_id,
            nome_sugerido,
            categoria_sugerida,
            confianca,
            motivo,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pendente')
        """,
        [
            (
                suggestion["produto_origem_id"],
                suggestion["produto_destino_id"],
                suggestion["nome_sugerido"],
                suggestion["categoria_sugerida"],
                suggestion["confianca"],
                suggestion["motivo"],
            )
            for suggestion in filtered
        ],
    )
    return len(filtered)


def print_report(products: list[dict], suggestions: list[dict], limit: int) -> None:
    print(f"Produtos analisados: {len(products)}")
    print(f"Sugestoes geradas: {len(suggestions)}")
    print()

    for suggestion in suggestions[:limit]:
        print(
            f"[{suggestion['confianca']:.2f}] {suggestion['categoria_sugerida']} - "
            f"{suggestion['motivo']}"
        )
        print(f"  destino #{suggestion['produto_destino_id']}: {suggestion['nome_destino']}")
        print(f"  origem  #{suggestion['produto_origem_id']}: {suggestion['nome_origem']}")
        print(f"  canonico sugerido: {suggestion['nome_sugerido']}")
        print()


def list_suggestions(connection: sqlite3.Connection, status: str, limit: int) -> None:
    rows = connection.execute(
        """
        SELECT
            s.id,
            s.confianca,
            s.categoria_sugerida,
            s.motivo,
            p_destino.nome AS nome_destino,
            p_origem.nome AS nome_origem,
            s.nome_sugerido
        FROM sugestoes_normalizacao_produtos s
        JOIN produtos p_destino ON p_destino.id = s.produto_destino_id
        JOIN produtos p_origem ON p_origem.id = s.produto_origem_id
        WHERE s.status = ?
        ORDER BY s.confianca DESC, s.nome_sugerido
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()

    if not rows:
        print(f"Nenhuma sugestao com status '{status}'.")
        return

    for row in rows:
        print(f"#{row[0]} [{row[1]:.2f}] {row[2]} - {row[3]}")
        print(f"  base:      {row[4]}")
        print(f"  sugerido:  {row[5]}")
        print(f"  canonico:  {row[6]}")
        print()


def get_or_create_category(connection: sqlite3.Connection, category_name: str | None) -> int | None:
    if not category_name:
        return None

    connection.execute(
        "INSERT OR IGNORE INTO categorias_produto (nome) VALUES (?)",
        (category_name,),
    )
    row = connection.execute(
        "SELECT id FROM categorias_produto WHERE nome = ?",
        (category_name,),
    ).fetchone()
    return row[0] if row else None


def get_or_create_canonical_product(
    connection: sqlite3.Connection,
    name: str,
    category_name: str | None,
) -> int:
    category_id = get_or_create_category(connection, category_name)
    brand = brand_for(name)

    connection.execute(
        """
        INSERT OR IGNORE INTO produtos_canonicos (
            nome,
            categoria_id,
            marca
        )
        VALUES (?, ?, ?)
        """,
        (name, category_id, brand),
    )
    row = connection.execute(
        "SELECT id FROM produtos_canonicos WHERE nome = ?",
        (name,),
    ).fetchone()
    return row[0]


def _bootstrap_unlinked_products(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT
            p.id,
            p.nome,
            COALESCE(MIN(i.unidade), 'UN') AS unidade_venda
        FROM produtos p
        LEFT JOIN itens_compra i ON i.produto_id = p.id
        WHERE p.produto_canonico_id IS NULL
        GROUP BY p.id, p.nome
        ORDER BY p.id
        """
    ).fetchall()

    result = {
        "created": 0,
        "linked": 0,
        "skipped": 0,
        "metadata_updated": 0,
    }

    for product_id, raw_name, sale_unit in rows:
        canonical_name = normalize_text(raw_name or "")
        if not canonical_name:
            result["skipped"] += 1
            continue

        category_id = get_or_create_category(connection, category_for(raw_name))
        brand = brand_for(raw_name)
        reference_unit, reference_quantity = reference_measurement(
            raw_name,
            sale_unit,
        )
        canonical_row = connection.execute(
            "SELECT id FROM produtos_canonicos WHERE nome = ?",
            (canonical_name,),
        ).fetchone()

        if canonical_row:
            canonical_id = canonical_row[0]
        else:
            cursor = connection.execute(
                """
                INSERT INTO produtos_canonicos (
                    nome,
                    categoria_id,
                    marca,
                    unidade_referencia,
                    quantidade_referencia
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    canonical_name,
                    category_id,
                    brand,
                    reference_unit,
                    reference_quantity,
                ),
            )
            canonical_id = cursor.lastrowid
            result["created"] += 1

        cursor = connection.execute(
            """
            UPDATE produtos
            SET produto_canonico_id = ?
            WHERE id = ? AND produto_canonico_id IS NULL
            """,
            (canonical_id, product_id),
        )
        result["linked"] += cursor.rowcount

    canonical_rows = connection.execute(
        """
        SELECT
            pc.id,
            pc.nome,
            pc.categoria_id,
            pc.marca,
            MIN(p.nome) AS nome_produto,
            COALESCE(MIN(i.unidade), 'UN') AS unidade_venda
        FROM produtos_canonicos pc
        LEFT JOIN produtos p ON p.produto_canonico_id = pc.id
        LEFT JOIN itens_compra i ON i.produto_id = p.id
        WHERE pc.categoria_id IS NULL
           OR pc.unidade_referencia IS NULL
           OR pc.quantidade_referencia IS NULL
        GROUP BY pc.id, pc.nome, pc.categoria_id, pc.marca
        ORDER BY pc.id
        """
    ).fetchall()

    for (
        canonical_id,
        canonical_name,
        category_id,
        brand,
        product_name,
        sale_unit,
    ) in canonical_rows:
        metadata_source = product_name or canonical_name
        if category_id is None:
            category_id = get_or_create_category(
                connection,
                category_for(metadata_source),
            )
        detected_brand = brand or brand_for(canonical_name) or brand_for(metadata_source)
        reference_unit, reference_quantity = reference_measurement(
            canonical_name,
            sale_unit,
        )
        connection.execute(
            """
            UPDATE produtos_canonicos
            SET categoria_id = COALESCE(categoria_id, ?),
                marca = COALESCE(marca, ?),
                unidade_referencia = COALESCE(unidade_referencia, ?),
                quantidade_referencia = COALESCE(quantidade_referencia, ?)
            WHERE id = ?
            """,
            (
                category_id,
                detected_brand,
                reference_unit,
                reference_quantity,
                canonical_id,
            ),
        )
        result["metadata_updated"] += 1

    return result


def bootstrap_unlinked_products(
    connection: sqlite3.Connection,
    dry_run: bool = False,
) -> dict[str, int]:
    if not dry_run:
        return _bootstrap_unlinked_products(connection)

    connection.execute("SAVEPOINT bootstrap_unlinked_products")
    try:
        result = _bootstrap_unlinked_products(connection)
    finally:
        connection.execute("ROLLBACK TO bootstrap_unlinked_products")
        connection.execute("RELEASE bootstrap_unlinked_products")
    return result


def approve_suggestion(connection: sqlite3.Connection, suggestion_id: int) -> bool:
    row = connection.execute(
        """
        SELECT
            s.id,
            s.produto_origem_id,
            s.produto_destino_id,
            s.nome_sugerido,
            s.categoria_sugerida,
            p_origem.produto_canonico_id,
            p_destino.produto_canonico_id
        FROM sugestoes_normalizacao_produtos s
        JOIN produtos p_origem ON p_origem.id = s.produto_origem_id
        JOIN produtos p_destino ON p_destino.id = s.produto_destino_id
        WHERE s.id = ? AND s.status = 'pendente'
        """,
        (suggestion_id,),
    ).fetchone()

    if not row:
        print(f"Sugestao #{suggestion_id} nao encontrada ou nao esta pendente.")
        return False

    _, origin_id, destination_id, suggested_name_value, category_name, origin_canonical_id, destination_canonical_id = row
    existing_ids = {
        canonical_id
        for canonical_id in (origin_canonical_id, destination_canonical_id)
        if canonical_id is not None
    }

    if len(existing_ids) > 1:
        print(
            f"Sugestao #{suggestion_id} tem produtos ja vinculados a canonicos diferentes; "
            "revise manualmente."
        )
        return False

    if existing_ids:
        canonical_id = existing_ids.pop()
    else:
        canonical_id = get_or_create_canonical_product(
            connection,
            suggested_name_value,
            category_name,
        )

    connection.execute(
        """
        UPDATE produtos
        SET produto_canonico_id = ?
        WHERE id IN (?, ?)
        """,
        (canonical_id, origin_id, destination_id),
    )
    connection.execute(
        "UPDATE sugestoes_normalizacao_produtos SET status = 'aprovada' WHERE id = ?",
        (suggestion_id,),
    )
    print(f"Sugestao #{suggestion_id} aprovada; produto canonico #{canonical_id}.")
    return True


def reject_suggestion(connection: sqlite3.Connection, suggestion_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE sugestoes_normalizacao_produtos
        SET status = 'rejeitada'
        WHERE id = ? AND status = 'pendente'
        """,
        (suggestion_id,),
    )
    if cursor.rowcount == 0:
        print(f"Sugestao #{suggestion_id} nao encontrada ou nao esta pendente.")
        return False

    print(f"Sugestao #{suggestion_id} rejeitada.")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera e revisa sugestoes de normalizacao de produtos.")
    parser.add_argument(
        "command",
        nargs="?",
        default="generate",
        choices=("generate", "list", "approve", "reject", "bootstrap"),
    )
    parser.add_argument("ids", nargs="*", type=int)
    parser.add_argument("--min-confidence", type=float, default=0.86)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--status", default="pendente")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    with sqlite3.connect(database_path()) as connection:
        ensure_sqlite_schema(connection)

        if args.command == "bootstrap":
            result = bootstrap_unlinked_products(connection, dry_run=args.dry_run)
            if not args.dry_run:
                connection.commit()
            mode = "Simulacao" if args.dry_run else "Normalizacao"
            print(
                f"{mode}: {result['linked']} produtos vinculados, "
                f"{result['created']} canonicos criados e "
                f"{result['skipped']} ignorados."
            )
            return

        seed_categories(connection)

        if args.command == "list":
            list_suggestions(connection, args.status, args.limit)
            return

        if args.command in {"approve", "reject"}:
            if not args.ids:
                raise SystemExit(f"Informe pelo menos um ID para {args.command}.")

            for suggestion_id in args.ids:
                if args.command == "approve":
                    approve_suggestion(connection, suggestion_id)
                else:
                    reject_suggestion(connection, suggestion_id)
            connection.commit()
            return

        products = load_products(connection)
        suggestions = generate_suggestions(products)

        persisted = 0
        if not args.dry_run:
            persisted = persist_suggestions(connection, suggestions, args.min_confidence)

        connection.commit()

    print_report(products, suggestions, args.limit)
    if args.dry_run:
        print("Dry run: nenhuma sugestao foi gravada.")
    else:
        print(f"Sugestoes pendentes gravadas: {persisted}")


if __name__ == "__main__":
    main()

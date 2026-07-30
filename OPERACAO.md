# Operacao da NFC-e API

## Atualizar com backup

Execute a partir da raiz do repositorio:

```bash
git pull --ff-only origin main
docker compose stop nfce-api
mkdir -p backups
cp -p data/market.db backups/market-AAAAMMDDTHHMMSSZ.db
```

Valide o banco original e o backup com o Python disponivel no host:

```bash
python3 -c 'import sqlite3; print(sqlite3.connect("data/market.db").execute("PRAGMA integrity_check").fetchone()[0])'
python3 -c 'import sqlite3; print(sqlite3.connect("backups/market-AAAAMMDDTHHMMSSZ.db").execute("PRAGMA integrity_check").fetchone()[0])'
```

Os dois comandos devem retornar `ok`. Depois, reconstrua e teste:

```bash
docker compose build nfce-api
docker compose run --rm --no-deps nfce-api \
  python -m unittest discover -s tests -v
```

Somente depois dos testes, inicie a API:

```bash
docker compose up -d nfce-api
docker compose ps
docker compose logs --tail=100 nfce-api
```

Valide o endpoint usando o endereco configurado em `API_BIND_ADDRESS` e
`API_HOST_PORT`:

```bash
set -a
. ./.env
set +a
curl --fail "http://${API_BIND_ADDRESS}:${API_HOST_PORT}/health"
```

A resposta esperada e:

```json
{"status":"ok"}
```

## Restaurar um backup

Pare a API e preserve o banco que sera substituido:

```bash
docker compose stop nfce-api
cp -p data/market.db backups/market-pre-restore-AAAAMMDDTHHMMSSZ.db
cp -p backups/market-AAAAMMDDTHHMMSSZ.db data/market.db
docker compose up -d nfce-api
```

Depois da restauracao, valide `/health`, os logs e `PRAGMA integrity_check`.

## Dados que nao devem ser versionados

- `.env`: IPs, portas, credenciais e configuracoes locais;
- `data/`: banco SQLite ativo;
- `backups/`: snapshots do banco;
- `last_nfce_debug.html`: resposta fiscal de diagnostico;
- `sefaz_*_sample.html`: amostras fiscais locais.

Esses caminhos tambem ficam fora do contexto de build quando podem conter
dados privados.

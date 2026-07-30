# nfce_api
API para leitura de dados de NFe, para organizaçao e analise de dados domésticos

## Configuracao local

Crie o arquivo de configuracao local a partir do exemplo:

```bash
cp .env.example .env
```

O `.env` e ignorado pelo Git. Guarde nele IPs privados, portas, URLs de banco,
senhas e outros valores especificos do ambiente. O `.env.example` deve conter
somente valores seguros para documentacao.

O Docker Compose carrega o `.env` automaticamente. As configuracoes disponiveis
sao:

```bash
API_BIND_ADDRESS=127.0.0.1
API_HOST_PORT=8001
DATABASE_URL=sqlite:////app/data/market.db
SEFAZ_MIN_INTERVAL_SECONDS=15
SEFAZ_MAX_RETRIES=2
SEFAZ_RETRY_BASE_SECONDS=5
NFCE_MAX_HTML_CHARS=5242880
NFCE_MAX_RESPONSE_BYTES=8388608
NFCE_SAVE_DEBUG_HTML=false
```

## Politica do scanner NFC-e

Antes de acessar a internet, a API valida se o QR Code:

- usa um host oficial da SEFAZ-RJ;
- aponta para `/consultaNFCe/QRCode`;
- contem uma chave de 44 digitos com DV valido, UF `33` e modelo `65`;
- usa ambiente `1` ou `2`;
- segue o leiaute online ou offline das versoes 2 e 3.

URLs antigas do host `www4.fazenda.rj.gov.br` sao aceitas, mas convertidas para
o endpoint HTTPS atual antes da consulta. Outros hosts, portas e caminhos sao
rejeitados com `400` e codigo `INVALID_QR`.

`POST /nfce/validate` executa somente essa validacao, sem consultar a SEFAZ:

```json
{
  "url": "https://consultadfe.fazenda.rj.gov.br/consultaNFCe/QRCode?p=..."
}
```

`POST /nfce` consulta a SEFAZ de forma serializada: se chegarem varias notas ao
mesmo tempo, uma espera a outra. Isso reduz bloqueios por rajada.

A extracao normal continua exigindo somente o scan. A API executa
automaticamente, nesta ordem:

1. valida e canonicaliza o QR Code;
2. verifica se a chave ja foi importada, sem consultar novamente a SEFAZ;
3. faz a consulta HTTP direta, preservando cookies entre consultas;
4. tenta XML estruturado quando a resposta o disponibiliza;
5. tenta o HTML retornado pelo portal do RJ;
6. se a resposta for uma pagina vazia, uma casca JavaScript ou um leiaute ainda
   nao extraivel, carrega a mesma URL no Chromium e tenta novamente.

O navegador e um fallback automatico e nao aparece para o usuario. Ele nao e
usado para contornar captcha: quando a propria SEFAZ exige validacao humana, a
API encerra com o erro estruturado correspondente.

Retries sao aplicados apenas para erros temporarios, como `429`, `5xx` e falhas
de rede. `404` nao entra em retry: a API retorna `404` porque a SEFAZ informou
que aquela URL/chave nao foi encontrada.

Quando a SEFAZ exigir captcha, reCAPTCHA ou protecao TSPD, a API interrompe a
consulta sem tentar Playwright ou novos retries e retorna `424`. Bloqueios
especificos de IP usam o codigo `SEFAZ_IP_BLOCKED`; os demais usam
`SEFAZ_ACTION_REQUIRED`.

```json
{
  "detail": "A NFC-e precisa ser aberta no navegador ou WebView do usuario.",
  "code": "SEFAZ_ACTION_REQUIRED",
  "message": "A NFC-e precisa ser aberta no navegador ou WebView do usuario.",
  "retryable": false,
  "action_required": true
}
```

O campo `detail` continua sendo texto para manter compatibilidade com o app
atual. O caminho assistido e usar `POST /nfce/html` com o HTML da pagina ja
carregada e validada no navegador ou WebView do usuario. `source_url` e
obrigatoria e passa pela mesma validacao antes de o HTML ser processado. Quando
o HTML expoe a chave de acesso, ela tambem e comparada com a chave do QR Code;
uma divergencia retorna `422` com codigo `NFCE_DOCUMENT_MISMATCH`:

```json
{
  "source_url": "https://consultadfe.fazenda.rj.gov.br/consultaNFCe/QRCode?p=...",
  "html": "<html>...</html>"
}
```

O tamanho do HTML e limitado por `NFCE_MAX_HTML_CHARS`.
Respostas baixadas da SEFAZ sao limitadas por `NFCE_MAX_RESPONSE_BYTES`.

## Parsing e integridade

O parser XML e independente de namespace e le os grupos oficiais `infNFe`,
`det/prod`, `ICMSTot`, `emit` e `pag`. O parser HTML permanece como fallback
para os leiautes do portal da SEFAZ-RJ e reconhece itens por IDs, classes,
atributos de dados e tabelas.

Antes de salvar, a API valida valores finitos, quantidades positivas, descontos
e totais. Diferencas nao fatais de soma ou arredondamento sao retornadas em
`extraction.warnings`; dados impossiveis sao rejeitados.

A chave de acesso e unica em `compras`. Um novo scan da mesma NFC-e retorna a
compra existente com `already_imported: true`, sem nova consulta ao portal. A
compra e todos os itens sao gravados na mesma transacao, evitando registros
parciais.

Por padrao, paginas fiscais nao sao gravadas em disco. O HTML de diagnostico so
e salvo quando `NFCE_SAVE_DEBUG_HTML=true`.

## Normalizacao de produtos

Os nomes originais recebidos da nota fiscal permanecem em `produtos`. Cada
produto bruto e vinculado a um registro em `produtos_canonicos`, que guarda
categoria, marca e unidade/quantidade de referencia para analises.

A carga inicial e conservadora: produtos diferentes nao sao fundidos
automaticamente. Apenas nomes que ficam exatamente iguais apos a normalizacao
textual reutilizam o mesmo canonico; equivalencias aproximadas continuam no
fluxo de sugestoes para revisao.

Simular a normalizacao sem gravar no banco:

```bash
python normalizacao_produtos.py bootstrap --dry-run
```

Aplicar a normalizacao aos produtos ainda nao vinculados e completar metadados
ausentes dos canonicos existentes:

```bash
python normalizacao_produtos.py bootstrap
```

O comando e idempotente: execucoes posteriores processam apenas produtos novos
ou metadados ainda incompletos.

Executar os testes:

```bash
python -m unittest discover -s tests -v
```

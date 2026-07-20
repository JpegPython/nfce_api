# nfce_api
API para leitura de dados de NFe, para organizaçao e analise de dados domésticos

## Politica do scanner NFC-e

`POST /nfce` consulta a SEFAZ de forma serializada: se chegarem varias notas ao
mesmo tempo, uma espera a outra. Isso reduz bloqueios por rajada.

Variaveis de controle no Docker:

```bash
SEFAZ_MIN_INTERVAL_SECONDS=15
SEFAZ_MAX_RETRIES=2
SEFAZ_RETRY_BASE_SECONDS=5
```

Retries sao aplicados apenas para erros temporarios, como `429`, `5xx` e falhas
de rede. `404` nao entra em retry: a API retorna `404` porque a SEFAZ informou
que aquela URL/chave nao foi encontrada.

Quando a SEFAZ exigir captcha, a API retorna `424`. O caminho assistido e usar
`POST /nfce/html` com o HTML da pagina ja carregada/resolvida no navegador ou
WebView do usuario.

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

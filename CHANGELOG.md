# Historico de alteracoes

## 2026-07-30 - Ordenacao das compras

- `GET /compras` agora retorna as compras da mais recente para a mais antiga;
- compras com a mesma data usam o maior `id` como desempate deterministico;
- teste de regressao cobre os dois criterios de ordenacao.

## 2026-07-30 - Importacao NFC-e robustecida

Versao base: commit `3d03d14`.

### Seguranca e validacao

- validacao estrita do QR Code antes de qualquer acesso externo;
- allowlist dos hosts oficiais da SEFAZ-RJ e canonicalizacao para HTTPS;
- validacao da chave de acesso, UF, modelo, ambiente e leiautes 2/3;
- limites configuraveis para HTML enviado e respostas baixadas;
- paginas fiscais de debug desativadas por padrao.

### Importacao

- novo endpoint `POST /nfce/validate`;
- parser de XML NFC-e independente de namespace;
- parser HTML ampliado para diferentes leiautes da SEFAZ-RJ;
- fallback automatico de HTTP direto para Chromium em respostas nao extraiveis;
- erros estruturados para captcha, bloqueio de IP, indisponibilidade, documento
  divergente e formato nao suportado.

### Integridade e persistencia

- chave de acesso unica em `compras`;
- importacao idempotente, sem nova consulta para notas ja cadastradas;
- compra e itens persistidos na mesma transacao;
- reconciliacao de quantidades, descontos e totais, com avisos para diferencas
  nao fatais;
- atualizacao compativel do schema SQLite durante o startup.

### Operacao

- 31 testes executados com sucesso na imagem Docker;
- implantacao validada por `GET /health`;
- procedimento de backup, atualizacao e restauracao documentado em
  `OPERACAO.md`.

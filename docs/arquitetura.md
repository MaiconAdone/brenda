# Arquitetura

```
Fontes oficiais ──► bronze (arquivos brutos + linhagem)
                     │  Polars: tipagem, filtro do piloto, descarte de colunas
                     ▼
                   silver (Parquet normalizado, testes de qualidade)
                     │  Splink (DuckDB): resolução de entidades
                     ▼
                   gold (entidades, ligações com score, tabelas de sinais)
                     │                         │
                     ▼                         ▼
           grafo (NetworkX/Neo4j)        sinais ► score ► alertas ► dossiê
                     │                                   │
                     └──────────► API FastAPI ◄──────────┘
                                       │
                                  UI Streamlit
```

Tudo é orquestrado como *assets* do Dagster.

## Decisões

### Orquestrador: Dagster (não Prefect)

- O modelo de *software-defined assets* corresponde às camadas bronze/silver/gold: cada
  tabela é um asset, com dependências explícitas.
- Linhagem e metadados (URL, hash, contagem de linhas) ficam visíveis por asset na UI, o que
  serve diretamente ao princípio de auditabilidade.
- Partições (por mês e por município) dão ingestão incremental e reprocessamento seletivo.
- *Asset checks* implementam os testes de qualidade de dados da camada silver.

Prefect é mais simples para tarefas soltas, mas não tem a noção de asset como cidadão de
primeira classe, que é o centro deste projeto.

### Armazenamento: Parquet + DuckDB

Arquivos Parquet por camada em `data/`; DuckDB lê direto deles. Sem servidor de banco, e
qualquer pessoa reproduz o resultado com `make ingest`.

### Grafo: NetworkX no piloto, Neo4j para consulta

- A fonte da verdade são as tabelas gold. O grafo é **derivado** e pode ser reconstruído.
- Sinais de grafo e Louvain/Leiden rodam em NetworkX/igraph, o que basta na escala de um município.
- O Neo4j (no Docker Compose) atende às consultas de subgrafo de 2 saltos da interface e à
  exploração por jornalistas. Avaliar o desempenho na Fase 6 (estado inteiro).

### Grafo (Fase 2)

Construído por `lupa.graph.modelo.construir` e gravado em `data/gold/grafo/` (um Parquet por
rótulo de nó e por tipo de aresta). Toda aresta tem `fonte` (link ou arquivo oficial).

| Nó | `id` | Origem |
|---|---|---|
| `Pessoa` | `id_pessoa` (HMAC) ou `tse-<ano>-<sq>` para candidato sem CPF publicado | sócios, fornecedores PF, doadores, candidatos, sancionados |
| `Empresa` | CNPJ raiz (8) | Receita; fornecedores, doadores e sócios PJ |
| `Estabelecimento` | CNPJ (14) | Receita |
| `Endereco` | hash do endereço normalizado, sem complemento (nível do prédio) | Receita |
| `Telefone` / `Email` | HMAC com sal (o contato não é guardado) | Receita |
| `OrgaoPublico` | CNPJ | PNCP |
| `Licitacao` / `Contrato` | número de controle PNCP | PNCP |
| `Candidatura` | `<ano>-<sq_candidato>` | TSE |
| `Sancao` | `<cadastro>-<código>` | CEIS/CNEP (só de quem já está no grafo) |

| Aresta | De → Para | Propriedades |
|---|---|---|
| `SOCIO_DE` | Pessoa/Empresa → Empresa | qualificação, data de entrada, faixa etária |
| `UNIDADE_DE` | Estabelecimento → Empresa | |
| `LOCALIZADA_EM` | Estabelecimento → Endereco | complemento |
| `TEM_TELEFONE` / `TEM_EMAIL` | Estabelecimento → Telefone/Email | |
| `PROMOVIDA_POR` | Licitacao → OrgaoPublico | |
| `VENCEU` | Empresa/Pessoa → Licitacao | nº de itens, valor homologado, CNPJ do estabelecimento |
| `PARTE_DE` | Empresa/Pessoa → Contrato | valor global |
| `CELEBRADO_POR` | Contrato → OrgaoPublico | |
| `ORIGINADO_DE` | Contrato → Licitacao | |
| `CANDIDATO_EM` | Pessoa → Candidatura | |
| `DOOU_PARA` | Pessoa/Empresa → Candidatura | valor somado, nº de lançamentos |
| `FORNECEU_CAMPANHA` | Pessoa/Empresa → Candidatura | valor somado, nº de lançamentos |
| `SANCIONADA` | Empresa/Pessoa → Sancao | |
| `MESMA_ENTIDADE_PROVAVEL` | Pessoa → Pessoa | probabilidade, evidência, peso por comparação |

Diferenças em relação ao modelo inicial do projeto:

- `CONTRATADA_POR` foi desdobrada em `PARTE_DE` + `CELEBRADO_POR`, passando pelo nó
  `Contrato`, para guardar os dados do contrato.
- `PARTICIPOU_DE` não existe: o PNCP só publica os vencedores (ver `docs/fontes.md`).
- `SERVIDOR_DE` não existe: não há fonte aberta de servidores municipais de Mauá.
- `FORNECEU_CAMPANHA` foi acrescentada (despesas de campanha do TSE).

Consultas: `lupa.graph.consultas` (NetworkX) e `src/lupa/graph/consultas.cypher` (Neo4j).
Carga no Neo4j: `uv run python -m lupa.graph neo4j` (apaga e recarrega o banco).

### Identidade de pessoas

As fontes publicam CPF parcial (6 dígitos centrais). A identidade determinística é
`HMAC(sal, miolo + nome normalizado)`; ligações entre fontes com dados divergentes passam
pelo Splink e viram arestas `MESMA_ENTIDADE_PROVAVEL` com score. Ver `docs/lgpd.md`.

### Sinais e score

- Cada sinal é uma subclasse de `lupa.signals.base.Sinal`, registrada com `@registrar`, num
  módulo por categoria (`lupa/signals/catalogo/`) e com teste próprio. As tabelas de entrada
  (silver, grafo e derivadas) vêm de `lupa.signals.dados.carregar`.
- O score (`lupa.scoring.regras`) é uma soma ponderada por confiança, com teto por categoria;
  sinais `somente_combinado` são descartados quando estão sozinhos.

### Dossiê

HTML com Jinja2, convertido em PDF com WeasyPrint (Fase 4). O mesmo template é usado na UI e
no PDF.

## Módulos

| Pasta | Responsabilidade |
|---|---|
| `src/lupa/ingestion` | Um módulo por fonte: download idempotente, retry, rate limit, linhagem |
| `src/lupa/cleaning` | Documentos, nomes, endereços |
| `src/lupa/entity_resolution` | Modelos Splink |
| `src/lupa/graph` | Modelo de nós/arestas, carga Neo4j, consultas |
| `src/lupa/signals` | Catálogo de sinais |
| `src/lupa/scoring` | Agregação |
| `src/lupa/ml` | Anomalias, comunidades |
| `src/lupa/dossier` | Geração de dossiê |
| `src/lupa/api` / `ui` | FastAPI / Streamlit |
| `pipelines` | Definições do Dagster |

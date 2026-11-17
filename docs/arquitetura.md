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

### Identidade de pessoas

As fontes publicam CPF parcial (6 dígitos centrais). A identidade determinística é
`HMAC(sal, miolo + nome normalizado)`; ligações entre fontes com dados divergentes passam
pelo Splink e viram arestas `MESMA_ENTIDADE_PROVAVEL` com score. Ver `docs/lgpd.md`.

### Sinais e score

- Cada sinal é uma subclasse de `lupa.signals.base.Sinal`, registrada com `@registrar`, em seu
  próprio arquivo e com teste próprio.
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

# Lupa Cidadã

Plataforma **independente, de código aberto e auditável** que usa **somente dados públicos
abertos** do governo brasileiro para apontar **indícios** de irregularidade em contratações
públicas: empresas de fachada, laranjas, conflitos de interesse e conluio em licitações.

> **Alertas são indícios estatísticos para orientar apuração e não constituem acusação.**

## Princípios

1. Só dados públicos e abertos, obtidos de fontes oficiais.
2. Indício não é acusação: a linguagem é sempre "alerta", "indício", "sinal de risco".
3. Todo alerta mostra as regras que dispararam, os dados usados e os links para a fonte.
4. Revisão humana obrigatória antes de qualquer exportação ou divulgação.
5. LGPD: CPF mascarado (`***.123.456-**`), pseudonimização, minimização e log de acesso.
6. Reprodutível: clone, configure e rode.
7. Pessoas nunca são ligadas só pelo nome; toda ligação tem score de confiança.

Ver [docs/etica.md](docs/etica.md) e [docs/lgpd.md](docs/lgpd.md).

## Começando

Requisitos: [uv](https://docs.astral.sh/uv/), Docker (opcional), `make` (opcional).

```bash
make setup              # ou: uv sync --extra api --extra ui --extra orquestracao --extra ml
cp .env.example .env    # preencha LUPA_SALT e PORTAL_TRANSPARENCIA_API_KEY
```

Defina o município do piloto em [config/piloto.yaml](config/piloto.yaml) (`municipio_ibge`, `uf`).

```bash
make test       # testes (somente dados sintéticos)
make check      # lint + tipos + testes
make run        # Neo4j, API (:8000), UI (:8501) e Dagster (:3000) via Docker Compose
make ingest     # materializa todos os assets de ingestão
```

Ingestão sem Dagster: `uv run python -m lupa.ingestion [ibge pncp receita_cnpj sancoes bolsa_familia tse]`.

Entidades e grafo: `uv run python -m lupa.graph` (gera `data/gold/`). Para carregar no Neo4j
com o Docker Compose no ar: `uv run python -m lupa.graph neo4j`.

Modelos de apoio (anomalias e núcleos de fornecedoras, extra `ml`): `uv run python -m lupa.ml`,
antes dos sinais. Avaliação por precisão@k: `uv run python -m lupa.ml avaliacao`.

Sinais e score: `uv run python -m lupa.signals [--top 50]` gera os alertas e o relatório
`data/gold/relatorios/top50.md`, mais `campanhas_top50.md` para candidaturas (uso interno,
revisão humana obrigatória).

Interface: `uv run streamlit run src/lupa/ui/app.py` (http://localhost:8501). Ranking com
filtros, ficha da entidade com evidências, cronologia e conexões até 2 saltos, registro de
revisão humana e geração do dossiê em PDF, liberada só depois de uma revisão aprovada. Pela linha
de comando: `uv run python -m lupa.dossier Empresa:<cnpj raiz> -o dossie.pdf --usuario <nome>`.

Sem `make`: `uv run pytest`, `uv run ruff check .`, `uv run mypy`,
`uv run uvicorn lupa.api.main:app`, `uv run streamlit run src/lupa/ui/app.py`,
`uv run dagster dev -m pipelines.definitions`.

## Documentação

| Documento | Conteúdo |
|---|---|
| [arquitetura.md](docs/arquitetura.md) | Camadas, decisões e justificativas |
| [fontes.md](docs/fontes.md) | Fontes, formatos, limites e linhagem |
| [metodologia.md](docs/metodologia.md) | Sinais, score, avaliação e mudanças de pesos |
| [etica.md](docs/etica.md) | Linguagem, explicabilidade e revisão humana |
| [lgpd.md](docs/lgpd.md) | Finalidade, base legal e salvaguardas |

## Roteiro

- [x] Fase 0: fundação
- [x] Fase 1: ingestão (IBGE, PNCP, CNPJ, CEIS/CNEP, Bolsa Família, TSE) — piloto Mauá/SP
- [x] Fase 2: entidades (Splink) e grafo (NetworkX; carga no Neo4j)
- [x] Fase 3: 19 sinais (inclui cruzamentos com o Bolsa Família), score por regras e relatório dos 50 principais alertas
- [x] Fase 4: interface (Streamlit), revisão humana, grafo de 2 saltos e dossiê em PDF
- [x] Fase 5: anomalias (Isolation Forest + SHAP), núcleos de fornecedoras (Louvain) e precisão@k
- [ ] Fase 6: escala

## Licença

[AGPL-3.0-or-later](LICENSE). Versões modificadas, inclusive oferecidas como serviço, devem
manter o código aberto.

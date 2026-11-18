"""Etapas da Fase 2: entidades → grafo → Neo4j (opcional).

Leem a camada silver e gravam na gold::

    gold/entidades/pessoas.parquet    uma linha por pessoa
    gold/entidades/ligacoes.parquet   MESMA_ENTIDADE_PROVAVEL (Splink)
    gold/grafo/nos/*.parquet          nós por rótulo
    gold/grafo/arestas/*.parquet      arestas por tipo
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

from lupa.config import carregar_regras
from lupa.entity_resolution import pessoas
from lupa.graph import neo4j
from lupa.graph.modelo import Grafo, construir
from lupa.ingestion.arquivos import gravar_parquet
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.etapas import Resultado
from lupa.qualidade import verificar_grafo, verificar_tabela


def etapa_entidades(ctx: Contexto) -> Resultado:
    """Pessoas consolidadas e ligações prováveis entre elas (Splink)."""
    consolidadas = pessoas.consolidar(pessoas.mencoes(ctx))
    ligacoes = pessoas.resolver(consolidadas, carregar_regras().resolucao_entidades)
    destino = ctx.gold("entidades")
    gravar_parquet(consolidadas, destino / "pessoas.parquet")
    gravar_parquet(ligacoes, destino / "ligacoes.parquet")
    tabelas = {"entidades/pessoas": consolidadas, "entidades/ligacoes": ligacoes}
    falhas = [f for nome, df in tabelas.items() for f in verificar_tabela(nome, df)]
    return Resultado(tabelas, falhas)


def etapa_grafo(ctx: Contexto) -> Resultado:
    """Nós e arestas do grafo na camada gold."""
    base = ctx.gold("entidades")
    g = construir(
        ctx,
        pl.read_parquet(base / "pessoas.parquet"),
        pl.read_parquet(base / "ligacoes.parquet"),
    )
    g.gravar(ctx.gold("grafo"))
    tabelas = {
        **{f"grafo/nos/{r}": df for r, df in g.nos.items()},
        **{f"grafo/arestas/{t}": df for t, df in g.arestas.items()},
    }
    return Resultado(tabelas, verificar_grafo(g.nos, g.arestas))


def etapa_neo4j(ctx: Contexto) -> Resultado:
    """Carrega o grafo gold no Neo4j (precisa do servidor no ar)."""
    neo4j.carregar_no_servidor(Grafo.ler(ctx.gold("grafo")))
    return Resultado({}, [])


ETAPAS: dict[str, Callable[[Contexto], Resultado]] = {
    "entidades": etapa_entidades,
    "grafo": etapa_grafo,
    "neo4j": etapa_neo4j,
}

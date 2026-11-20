"""Avaliação por precisão@k a partir das revisões humanas.

- **precisão@k:** entre as ``k`` primeiras entidades de cada ranking que já têm decisão
  final (aprovado ou descartado), a fração aprovada. A ``cobertura`` diz quantas das
  ``k`` foram revisadas: precisão com cobertura baixa ainda diz pouco.
- **por sinal:** entre as entidades revisadas em que o sinal disparou, a fração aprovada.
  Sinal com precisão baixa e amostra razoável é candidato a recalibrar ou desativar
  (ver ``docs/etica.md``).

Usa a última decisão de cada entidade, mesmo que os sinais tenham mudado depois: a
pergunta "o caso procedia?" continua respondida.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import polars as pl

from lupa import revisao
from lupa.ingestion.arquivos import gravar_parquet
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.etapas import Resultado
from lupa.revisao import Revisao

KS = (10, 20, 50)
FINAIS = ("aprovado", "descartado")


def _decisoes(revisoes: Mapping[str, Revisao]) -> pl.DataFrame:
    return pl.DataFrame(
        [(e, r.situacao == "aprovado") for e, r in revisoes.items() if r.situacao in FINAIS],
        schema={"entidade": pl.String, "aprovado": pl.Boolean},
        orient="row",
    )


def precisao_k(
    scores: pl.DataFrame, revisoes: Mapping[str, Revisao], ks: Sequence[int] = KS
) -> pl.DataFrame:
    """Precisão e cobertura nas ``k`` primeiras posições de cada ranking."""
    decisoes = _decisoes(revisoes)
    linhas = []
    for ranking in sorted(set(scores["ranking"])):
        r = scores.filter(pl.col("ranking") == ranking).sort("posicao")
        for k in ks:
            topo = r.head(k).join(decisoes, on="entidade", how="inner")
            n = topo.height
            aprovados = int(topo["aprovado"].sum())
            linhas.append({
                "ranking": ranking, "k": k, "revisadas": n, "aprovadas": aprovados,
                "precisao": aprovados / n if n else None, "cobertura": n / min(k, r.height or 1),
            })  # fmt: skip
    return pl.DataFrame(
        linhas,
        schema={"ranking": pl.String, "k": pl.Int64, "revisadas": pl.Int64,
                "aprovadas": pl.Int64, "precisao": pl.Float64, "cobertura": pl.Float64},
    )  # fmt: skip


def precisao_por_sinal(alertas: pl.DataFrame, revisoes: Mapping[str, Revisao]) -> pl.DataFrame:
    """Precisão de cada sinal entre as entidades revisadas."""
    return (
        alertas.select("sinal_id", "entidade")
        .unique()
        .join(_decisoes(revisoes), on="entidade", how="inner")
        .group_by("sinal_id")
        .agg(revisadas=pl.len(), aprovadas=pl.col("aprovado").sum().cast(pl.Int64))
        .with_columns(precisao=pl.col("aprovadas") / pl.col("revisadas"))
        .sort("sinal_id")
    )


def etapa_avaliacao(ctx: Contexto) -> Resultado:
    """Calcula e grava a avaliação em ``gold/avaliacao/``."""
    pasta = ctx.gold("alertas")
    scores = pl.read_parquet(pasta / "scores.parquet")
    alertas = pl.read_parquet(pasta / "alertas.parquet")
    revs = revisao.atuais(ctx.raiz)
    tabelas = {
        "avaliacao/precisao_k": precisao_k(scores, revs),
        "avaliacao/por_sinal": precisao_por_sinal(alertas, revs),
    }
    for nome, df in tabelas.items():
        gravar_parquet(df, ctx.gold("avaliacao") / f"{nome.split('/')[1]}.parquet")
    return Resultado(tabelas, [])

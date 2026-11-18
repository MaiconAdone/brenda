"""Executa os sinais ativos, calcula o score e grava alertas e relatório na gold.

Saídas::

    gold/alertas/alertas.parquet   um alerta por linha, evidências em JSON
    gold/alertas/scores.parquet    score por entidade, com decomposição
    gold/relatorios/top50.md       relatório dos 50 principais alertas (revisão humana)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict

import polars as pl

import lupa.signals.catalogo  # noqa: F401  (registra os sinais)
from lupa.config import ConfigRegras, carregar_regras
from lupa.ingestion.arquivos import gravar_parquet
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.etapas import Resultado
from lupa.scoring import ScoreEntidade, calcular_scores
from lupa.signals import dados
from lupa.signals.base import Alerta, Sinal, sinais
from lupa.signals.relatorio import gerar_relatorio

log = logging.getLogger("lupa.sinais")


def rodar_sinais(
    t: Mapping[str, pl.DataFrame], regras: ConfigRegras, catalogo: Mapping[str, type[Sinal]]
) -> list[Alerta]:
    """Avalia todos os sinais ativos do catálogo."""
    alertas: list[Alerta] = []
    for sinal_id, classe in sorted(catalogo.items()):
        cfg = regras.sinais.get(sinal_id)
        if cfg is not None and not cfg.ativo:
            continue
        novos = classe(cfg.parametros if cfg else None).evidencias(t)
        log.info("%s: %d alertas", sinal_id, len(novos))
        alertas.extend(novos)
    return alertas


def _tabela_alertas(alertas: list[Alerta]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "sinal_id": [a.sinal_id for a in alertas],
            "entidade": [a.entidade_id for a in alertas],
            "confianca": [a.confianca for a in alertas],
            "n_evidencias": [len(a.evidencias) for a in alertas],
            "evidencias": [
                json.dumps([asdict(e) for e in a.evidencias], ensure_ascii=False, default=str)
                for a in alertas
            ],
        },
        schema={
            "sinal_id": pl.String, "entidade": pl.String, "confianca": pl.Float64,
            "n_evidencias": pl.Int64, "evidencias": pl.String,
        },
    )  # fmt: skip


def _tabela_scores(scores: list[ScoreEntidade]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "posicao": list(range(1, len(scores) + 1)),
            "entidade": [s.entidade_id for s in scores],
            "score": [s.total for s in scores],
            "por_categoria": [json.dumps(dict(s.por_categoria)) for s in scores],
            "sinais": [sorted({a.sinal_id for a in s.alertas}) for s in scores],
            "descartados": [list(s.descartados) for s in scores],
        },
        schema={
            "posicao": pl.Int64, "entidade": pl.String, "score": pl.Float64,
            "por_categoria": pl.String, "sinais": pl.List(pl.String),
            "descartados": pl.List(pl.String),
        },
    )  # fmt: skip


def etapa_sinais(ctx: Contexto, top: int = 50) -> Resultado:
    """Sinais → score → alertas e relatório dos ``top`` principais."""
    piloto = ctx.config.piloto
    t = dados.carregar(ctx.raiz, piloto.uf, piloto.nome_municipio)
    regras = carregar_regras()
    catalogo = sinais()
    alertas = rodar_sinais(t, regras, catalogo)
    scores = calcular_scores(alertas, catalogo, regras)
    tab_alertas, tab_scores = _tabela_alertas(alertas), _tabela_scores(scores)
    gravar_parquet(tab_alertas, ctx.gold("alertas") / "alertas.parquet")
    gravar_parquet(tab_scores, ctx.gold("alertas") / "scores.parquet")
    texto = gerar_relatorio(scores, catalogo, t, top=top, gerado_em=ctx.hoje())
    (ctx.gold("relatorios") / f"top{top}.md").write_text(texto, encoding="utf-8")
    return Resultado({"alertas/alertas": tab_alertas, "alertas/scores": tab_scores}, [])

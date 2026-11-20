"""Executa os sinais ativos, calcula o score e grava alertas e relatório na gold.

Saídas::

    gold/alertas/alertas.parquet   um alerta por linha, evidências em JSON
    gold/alertas/scores.parquet    score por entidade, com decomposição
    gold/relatorios/top50.md       50 principais fornecedores e pessoas (revisão humana)
    gold/relatorios/campanhas_top50.md   50 principais candidaturas (revisão humana)

Candidaturas ficam num ranking à parte: os sinais de campanha (L07, L08) medem outra coisa
e, misturados, tirariam espaço dos fornecedores.
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


RANKINGS = {
    "fornecedores": "principais alertas de fornecedores e pessoas",
    "campanhas": "principais alertas de candidaturas",
}


def ranking_de(entidade: str) -> str:
    """Ranking em que a entidade aparece: ``campanhas`` para candidaturas."""
    return "campanhas" if entidade.startswith("Candidatura:") else "fornecedores"


def separar(scores: list[ScoreEntidade]) -> dict[str, list[ScoreEntidade]]:
    """Scores por ranking, mantendo a ordem."""
    grupos: dict[str, list[ScoreEntidade]] = {nome: [] for nome in RANKINGS}
    for s in scores:
        grupos[ranking_de(s.entidade_id)].append(s)
    return grupos


def _tabela_scores(scores: list[ScoreEntidade]) -> pl.DataFrame:
    linhas = [(nome, i, s) for nome, g in separar(scores).items() for i, s in enumerate(g, 1)]
    scores = [s for _, _, s in linhas]
    return pl.DataFrame(
        {
            "ranking": [nome for nome, _, _ in linhas],
            "posicao": [i for _, i, _ in linhas],
            "entidade": [s.entidade_id for s in scores],
            "score": [s.total for s in scores],
            "por_categoria": [json.dumps(dict(s.por_categoria)) for s in scores],
            "sinais": [sorted({a.sinal_id for a in s.alertas}) for s in scores],
            "descartados": [list(s.descartados) for s in scores],
        },
        schema={
            "ranking": pl.String, "posicao": pl.Int64, "entidade": pl.String, "score": pl.Float64,
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
    arquivos = {"fornecedores": f"top{top}.md", "campanhas": f"campanhas_top{top}.md"}
    for nome, grupo in separar(scores).items():
        texto = gerar_relatorio(
            grupo, catalogo, t, top=top, gerado_em=ctx.hoje(), titulo=RANKINGS[nome]
        )
        (ctx.gold("relatorios") / arquivos[nome]).write_text(texto, encoding="utf-8")
    return Resultado({"alertas/alertas": tab_alertas, "alertas/scores": tab_scores}, [])

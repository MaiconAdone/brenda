"""Ponta a ponta com a silver sintética: entidades → grafo → sinais → relatório."""

from __future__ import annotations

import polars as pl

from lupa import AVISO_LEGAL
from lupa.graph import etapas
from lupa.ingestion.contexto import Contexto
from lupa.signals.executar import etapa_sinais


def test_sinais_ponta_a_ponta(silver: Contexto) -> None:
    etapas.etapa_entidades(silver)
    etapas.etapa_grafo(silver)
    resultado = etapa_sinais(silver, top=10)

    alertas = pl.read_parquet(silver.gold("alertas") / "alertas.parquet")
    scores = pl.read_parquet(silver.gold("alertas") / "scores.parquet")
    assert resultado.resumo() == {
        "alertas/alertas": alertas.height,
        "alertas/scores": scores.height,
    }
    # 11222333 e 44555666 têm o mesmo sócio e fornecem ao mesmo órgão (K04);
    # 44555666 tem sanção (CEIS) em vigor, mas sem abrangência sobre o município.
    assert {"Empresa:11222333", "Empresa:44555666"} <= set(alertas["entidade"])
    assert "K04" in set(alertas["sinal_id"])
    for _, grupo in scores.group_by("ranking"):
        assert grupo["posicao"].to_list() == list(range(1, grupo.height + 1))
    assert (silver.gold("relatorios") / "campanhas_top10.md").exists()

    relatorio = (silver.gold("relatorios") / "top10.md").read_text(encoding="utf-8")
    assert AVISO_LEGAL in relatorio
    assert "não revisado" in relatorio
    assert "https://pncp.gov.br/" in relatorio or "https://arquivos.receitafederal" in relatorio


def test_candidaturas_ficam_em_ranking_separado() -> None:
    from lupa.scoring import ScoreEntidade
    from lupa.signals.executar import separar

    def score(entidade: str, total: float) -> ScoreEntidade:
        return ScoreEntidade(entidade, total, {}, ())

    grupos = separar(
        [score("Candidatura:2024-1", 30), score("Empresa:1", 20), score("Pessoa:p", 10)]
    )
    assert [s.entidade_id for s in grupos["fornecedores"]] == ["Empresa:1", "Pessoa:p"]
    assert [s.entidade_id for s in grupos["campanhas"]] == ["Candidatura:2024-1"]

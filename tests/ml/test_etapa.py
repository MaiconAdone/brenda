"""Etapa de ML sobre o pipeline sintético completo."""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from lupa.graph import etapas
from lupa.ingestion.contexto import Contexto
from lupa.ml.etapas import etapa_ml
from tests.grafo.conftest import _sal, ctx, silver  # noqa: F401  (fixtures reaproveitadas)


def test_etapa_ml_grava_tabelas(silver: Contexto) -> None:  # noqa: F811
    etapas.etapa_entidades(silver)
    etapas.etapa_grafo(silver)
    resultado = etapa_ml(silver)
    assert set(resultado.tabelas) == {"ml/features", "ml/anomalias", "ml/nucleos"}
    for nome in ("features", "anomalias", "nucleos"):
        assert (silver.gold("ml") / f"{nome}.parquet").exists()
    # Poucas fornecedoras sintéticas: o modelo não roda, mas a tabela existe vazia.
    assert resultado.tabelas["ml/features"].height > 0

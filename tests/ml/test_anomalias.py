"""Isolation Forest + SHAP sobre features sintéticas."""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("shap")

from lupa.ml.anomalias import ConfigAnomalias, detectar, matriz
from lupa.ml.features import FEATURES, formatar


def _populacao(n: int = 80) -> dict[str, list[float | None]]:
    rng = np.random.default_rng(7)
    return {
        "n_vitorias": rng.integers(1, 4, n).astype(float).tolist(),
        "valor_total": rng.uniform(10_000, 200_000, n).tolist(),
        "frac_dispensa": rng.uniform(0, 0.2, n).tolist(),
        "razao_preco": rng.uniform(0.7, 1.0, n).tolist(),
        "dias_ate_1a_vitoria": rng.uniform(1_000, 6_000, n).tolist(),
        "valor_por_capital": rng.uniform(0.1, 2, n).tolist(),
        "aditivo_max": [0.0] * n,
        "empresas_mesmo_contato": [1.0] * n,
    }


def _tabela(extra: list[dict[str, float]]) -> pl.DataFrame:
    base = _populacao()
    n = len(base["n_vitorias"])
    df = pl.DataFrame({"entidade": [f"Empresa:{i:08d}" for i in range(n)], **base})
    linhas = [
        {"entidade": f"Empresa:9999999{i}", **{f: df[f].median() for f in FEATURES}, **e}
        for i, e in enumerate(extra)
    ]
    return pl.concat([df, pl.DataFrame(linhas, schema=df.schema)])


def test_marca_o_lado_de_risco_e_explica() -> None:
    feats = _tabela([
        # nova, cara, com aditivo e contato compartilhado: risco
        {"dias_ate_1a_vitoria": 20, "aditivo_max": 0.4, "empresas_mesmo_contato": 6,
         "valor_por_capital": 300},
        # muito antiga e muito barata: atípica, mas no lado seguro
        {"dias_ate_1a_vitoria": 30_000, "razao_preco": 0.1},
    ])  # fmt: skip
    a = detectar(feats, ConfigAnomalias(percentil=0.98)).sort("escore", descending=True)
    topo = a.row(0, named=True)
    assert topo["entidade"] == "Empresa:99999990" and topo["anomala"]
    explicadas = {e["feature"] for e in json.loads(topo["explicacao"])}
    assert explicadas & {"dias_ate_1a_vitoria", "aditivo_max", "empresas_mesmo_contato",
                         "valor_por_capital"}  # fmt: skip
    segura = a.filter(pl.col("entidade") == "Empresa:99999991").row(0, named=True)
    assert not segura["anomala"]


def test_deterministico_e_pequeno_demais() -> None:
    feats = _tabela([])
    assert detectar(feats).equals(detectar(feats))
    assert detectar(feats.head(10)).is_empty()


def test_matriz_corta_lado_seguro_e_preenche_nulos() -> None:
    feats = _tabela([]).with_columns(razao_preco=pl.lit(None, pl.Float64))
    x = matriz(feats)
    assert not np.isnan(x).any()
    coluna = list(FEATURES).index("dias_ate_1a_vitoria")
    assert x[:, coluna].max() <= np.median(feats["dias_ate_1a_vitoria"].to_numpy()) + 1e-9


def test_formatar() -> None:
    assert formatar("valor_total", 1234.5) == "R$ 1.234,50"
    assert formatar("frac_dispensa", 0.5) == "50%"
    assert formatar("dias_ate_1a_vitoria", 45.0) == "45 dias"
    assert formatar("razao_preco", None) == "sem dado"

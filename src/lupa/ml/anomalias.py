"""Fornecedoras com perfil atípico: Isolation Forest explicado com SHAP.

O modelo só aponta quem foge do padrão das demais fornecedoras do piloto; não sabe o
que é irregular. Por isso o sinal M01 é ``somente_combinado`` (não conta sozinho no
score) e cada alerta traz as features que mais pesaram, com o valor da empresa e a
mediana das demais, calculadas com SHAP (``TreeExplainer``).

Determinístico: semente fixa e features em ordem fixa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import polars as pl

from lupa.ml.features import FEATURES

MIN_EMPRESAS = 30


@dataclass(frozen=True)
class ConfigAnomalias:
    """Parâmetros do modelo."""

    n_arvores: int = 300
    semente: int = 42
    percentil: float = 0.98  # acima disto a empresa é marcada como anômala
    n_explicacoes: int = 3


def matriz(feats: pl.DataFrame) -> np.ndarray:
    """Matriz do modelo: log1p nas features de cauda longa e nulos pela mediana."""
    colunas = []
    for nome, f in FEATURES.items():
        c = pl.col(nome).cast(pl.Float64)
        if f.log:
            c = c.clip(lower_bound=0).log1p()
        c = c.fill_null(c.median()).fill_null(0.0)
        # Corta o lado seguro na mediana: só o lado de risco pode parecer atípico.
        c = c.clip(lower_bound=c.median()) if f.risco == "alto" else c.clip(upper_bound=c.median())
        colunas.append(c.alias(nome))
    return feats.select(colunas).to_numpy()


def detectar(feats: pl.DataFrame, cfg: ConfigAnomalias | None = None) -> pl.DataFrame:
    """Escore de anomalia, percentil e explicação de cada empresa.

    Colunas: ``entidade``, ``escore`` (maior = mais atípico), ``percentil`` (0 a 1),
    ``anomala`` e ``explicacao`` (JSON com as features que mais pesaram).
    """
    from shap import TreeExplainer
    from sklearn.ensemble import IsolationForest

    cfg = cfg or ConfigAnomalias()
    vazio = pl.DataFrame(
        schema={"entidade": pl.String, "escore": pl.Float64, "percentil": pl.Float64,
                "anomala": pl.Boolean, "explicacao": pl.String}
    )  # fmt: skip
    if feats.height < MIN_EMPRESAS:
        return vazio
    x = matriz(feats)
    modelo = IsolationForest(
        n_estimators=cfg.n_arvores, random_state=cfg.semente, contamination="auto"
    ).fit(x)
    escore = -modelo.score_samples(x)
    percentil = (np.argsort(np.argsort(escore)) + 1) / len(escore)
    # SHAP explica o comprimento do caminho: valor negativo = caminho curto = mais atípico.
    contribuicoes = -np.asarray(TreeExplainer(modelo).shap_values(x))
    medianas = {n: feats[n].median() for n in FEATURES}
    nomes = list(FEATURES)
    explicacoes = []
    for i in range(feats.height):
        ordem = np.argsort(-contribuicoes[i])[: cfg.n_explicacoes]
        explicacoes.append(
            json.dumps(
                [
                    {"feature": nomes[j], "valor": feats[nomes[j]][i],
                     "mediana": medianas[nomes[j]],
                     "contribuicao": round(float(contribuicoes[i, j]), 4)}
                    for j in ordem if contribuicoes[i, j] > 0
                ],
                ensure_ascii=False,
            )
        )  # fmt: skip
    return pl.DataFrame(
        {
            "entidade": feats["entidade"],
            "escore": escore,
            "percentil": percentil,
            "anomala": percentil >= cfg.percentil,
            "explicacao": explicacoes,
        },
        schema=vazio.schema,
    )

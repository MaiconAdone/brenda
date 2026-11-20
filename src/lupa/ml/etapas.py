"""Etapa da Fase 5: features, anomalias e núcleos de fornecedoras.

Lê silver e grafo gold e grava::

    gold/ml/features.parquet    features por fornecedora
    gold/ml/anomalias.parquet   escore, percentil e explicação SHAP (sinal M01)
    gold/ml/nucleos.parquet     núcleos de fornecedoras ligadas (sinal M02)

Precisa do extra ``ml`` (scikit-learn e SHAP) só para as anomalias; os núcleos usam
apenas o NetworkX.
"""

from __future__ import annotations

import logging

from lupa.ingestion.arquivos import gravar_parquet
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.etapas import Resultado
from lupa.ml import anomalias, comunidades
from lupa.ml.features import features_empresas
from lupa.signals import dados

log = logging.getLogger("lupa.ml")


def etapa_ml(ctx: Contexto) -> Resultado:
    """Calcula e grava as tabelas de ML usadas pelos sinais M01 e M02."""
    piloto = ctx.config.piloto
    t = dados.carregar(ctx.raiz, piloto.uf, piloto.nome_municipio)
    feats = features_empresas(t)
    anom = anomalias.detectar(feats)
    nucleos = comunidades.detectar(t)
    tabelas = {"ml/features": feats, "ml/anomalias": anom, "ml/nucleos": nucleos}
    for nome, df in tabelas.items():
        gravar_parquet(df, ctx.gold("ml") / f"{nome.split('/')[1]}.parquet")
    log.info(
        "%d fornecedoras; %d atípicas; %d núcleos com %d empresas",
        feats.height, int(anom["anomala"].sum()), nucleos["nucleo"].n_unique(), nucleos.height,
    )  # fmt: skip
    return Resultado(tabelas, [])

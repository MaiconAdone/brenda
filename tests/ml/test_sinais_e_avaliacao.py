"""Sinais M01/M02, regra de combinação no score e precisão@k."""

from __future__ import annotations

import json

import polars as pl
import pytest

import lupa.signals.catalogo  # noqa: F401  (registra os sinais)
from lupa.config import carregar_regras
from lupa.ml.avaliacao import precisao_k, precisao_por_sinal
from lupa.revisao import Revisao
from lupa.scoring import calcular_scores
from lupa.signals.base import Alerta, sinais

A, B, C = "Empresa:11111111", "Empresa:22222222", "Empresa:33333333"


def _tabelas() -> dict[str, pl.DataFrame]:
    explicacao = json.dumps([{"feature": "aditivo_max", "valor": 0.4, "mediana": 0.0,
                              "contribuicao": 0.3}])  # fmt: skip
    return {
        "meta/coletas": pl.DataFrame({"fonte": ["pncp"], "coletado_em": ["2026-03-15"]}),
        "derivado/nomes": pl.DataFrame(
            {"entidade": [A, B, C], "nome": ["EMP A", "EMP B", "EMP C"]}
        ),
        "ml/anomalias": pl.DataFrame({
            "entidade": [A, B], "escore": [0.7, 0.4], "percentil": [1.0, 0.5],
            "anomala": [True, False], "explicacao": [explicacao, "[]"],
        }),
        "ml/nucleos": pl.DataFrame({
            "entidade": [A, B, C], "nucleo": [0, 0, 0], "membros": [[A, B, C]] * 3,
            "lacos": [["socio|" + B], ["socio|" + A, "contato|" + C], ["contato|" + B]],
            "orgao_comum": ["ORGAO X"] * 3, "n_no_orgao": [3] * 3,
            "valor_nucleo": [1_000_000.0] * 3, "co_vitorias": [1] * 3,
        }),
    }  # fmt: skip


def _rodar(sinal_id: str) -> dict[str, Alerta]:
    return {a.entidade_id: a for a in sinais()[sinal_id]({}).evidencias(_tabelas())}


def test_m01_explica_com_shap() -> None:
    alertas = _rodar("M01")
    assert set(alertas) == {A}
    descricao = alertas[A].evidencias[0].descricao
    assert "maior acréscimo em contrato: 40% (mediana 0%)" in descricao
    assert alertas[A].confianca == pytest.approx(1.0)


def test_m02_descreve_o_nucleo() -> None:
    alertas = _rodar("M02")
    assert set(alertas) == {A, B, C}
    texto = alertas[B].evidencias[0].descricao
    assert "núcleo de 3 fornecedoras" in texto and "sócio em comum com EMP A" in texto
    assert "venceu junto 1 licitação" in texto


def test_m01_sozinho_nao_pontua() -> None:
    catalogo = sinais()
    regras = carregar_regras()
    so_modelo = calcular_scores(_rodar("M01").values(), catalogo, regras)
    assert so_modelo[0].total == 0 and so_modelo[0].descartados == ("M01",)
    junto = calcular_scores([*_rodar("M01").values(), *_rodar("M02").values()], catalogo, regras)
    a = next(s for s in junto if s.entidade_id == A)
    assert {x.sinal_id for x in a.alertas} == {"M01", "M02"} and a.total > 8


def _rev(entidade: str, situacao: str) -> Revisao:
    return Revisao(entidade, situacao, "Ana", "Justificativa sintética longa o bastante.",
                   ("K04",), 10.0)  # fmt: skip


def test_precisao_k_e_por_sinal() -> None:
    scores = pl.DataFrame({
        "ranking": ["fornecedores"] * 4, "posicao": [1, 2, 3, 4],
        "entidade": [A, B, C, "Empresa:4"],
    })  # fmt: skip
    revs = {A: _rev(A, "aprovado"), B: _rev(B, "descartado"), C: _rev(C, "em_revisao")}
    pk = precisao_k(scores, revs, ks=[2, 4]).sort("k")
    assert pk["precisao"].to_list() == [0.5, 0.5]
    assert pk["cobertura"].to_list() == [1.0, 0.5]
    alertas = pl.DataFrame({"sinal_id": ["K04", "K04", "M01"], "entidade": [A, B, A]})
    ps = precisao_por_sinal(alertas, revs)
    assert dict(zip(ps["sinal_id"], ps["precisao"], strict=True)) == {"K04": 0.5, "M01": 1.0}

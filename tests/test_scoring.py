from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import polars as pl
import pytest

from lupa.config import carregar_regras
from lupa.scoring import calcular_scores
from lupa.signals.base import Alerta, Categoria, Evidencia, Fonte, Sinal, registrar

FONTE = Fonte("sintetica", "https://exemplo.invalid", "2026-01-01")


def _sinal(id_: str, categoria: Categoria, combinado: bool = False) -> type[Sinal]:
    class _S(Sinal):
        id: ClassVar[str] = id_
        nome: ClassVar[str] = f"Sinal {id_}"
        descricao: ClassVar[str] = "Sinal sintético de teste."
        peso_padrao: ClassVar[float] = 1.0
        somente_combinado: ClassVar[bool] = combinado

        def evidencias(self, tabelas: Mapping[str, pl.DataFrame]) -> list[Alerta]:
            return []

    _S.categoria = categoria
    return _S


CATALOGO = {
    "X01": _sinal("X01", Categoria.FACHADA),
    "X02": _sinal("X02", Categoria.FACHADA),
    "X03": _sinal("X03", Categoria.CONFLITO_INTERESSE),
    "X04": _sinal("X04", Categoria.CONFLITO_INTERESSE, combinado=True),
}


def _alerta(sinal: str, entidade: str, confianca: float = 1.0) -> Alerta:
    return Alerta(sinal, entidade, (Evidencia("fato sintético", {}, (FONTE,), confianca),))


def test_teto_por_categoria(dir_config: Path) -> None:
    regras = carregar_regras(dir_config / "regras.yaml")
    [score] = calcular_scores([_alerta("X01", "E1"), _alerta("X02", "E1")], CATALOGO, regras)
    assert score.por_categoria == {"fachada": 15}  # 10 + 8 limitado ao teto 15
    assert score.total == 15


def test_confianca_pondera(dir_config: Path) -> None:
    regras = carregar_regras(dir_config / "regras.yaml")
    [score] = calcular_scores([_alerta("X01", "E1", confianca=0.5)], CATALOGO, regras)
    assert score.total == 5


def test_sinal_inativo_ignorado(dir_config: Path) -> None:
    regras = carregar_regras(dir_config / "regras.yaml")
    assert calcular_scores([_alerta("X03", "E1")], CATALOGO, regras) == []


def test_somente_combinado_nao_dispara_sozinho(dir_config: Path) -> None:
    regras = carregar_regras(dir_config / "regras.yaml")
    [sozinho] = calcular_scores([_alerta("X04", "E1")], CATALOGO, regras)
    assert sozinho.total == 0
    assert sozinho.descartados == ("X04",)

    [combinado] = calcular_scores([_alerta("X04", "E2"), _alerta("X01", "E2")], CATALOGO, regras)
    assert combinado.total == 13


def test_ordenacao(dir_config: Path) -> None:
    regras = carregar_regras(dir_config / "regras.yaml")
    scores = calcular_scores([_alerta("X02", "B"), _alerta("X01", "A")], CATALOGO, regras)
    assert [s.entidade_id for s in scores] == ["A", "B"]


def test_evidencia_exige_fonte() -> None:
    with pytest.raises(ValueError, match="fonte"):
        Evidencia("sem fonte", {}, ())


def test_registro_valida_id() -> None:
    with pytest.raises(ValueError):
        registrar(_sinal("invalido", Categoria.FACHADA))

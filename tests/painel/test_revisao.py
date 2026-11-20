"""Regras da revisão humana."""

from __future__ import annotations

from pathlib import Path

import pytest

from lupa import revisao
from lupa.revisao import Revisao, RevisaoInvalidaError, RevisaoPendenteError

JUSTIFICATIVA = "Conferi as fontes no PNCP e na Receita; o indício procede."


def _rev(situacao: str = "aprovado", **extra: object) -> Revisao:
    dados: dict[str, object] = {
        "entidade": "Empresa:11111111", "situacao": situacao, "revisor": "Ana",
        "justificativa": JUSTIFICATIVA, "sinais": ("F01", "K04"), "score": 20.0, **extra,
    }  # fmt: skip
    return Revisao(**dados)  # type: ignore[arg-type]


def test_registra_e_le_historico(tmp_path: Path) -> None:
    revisao.registrar(tmp_path, _rev("em_revisao", justificativa=""))
    revisao.registrar(tmp_path, _rev())
    historico = revisao.historico(tmp_path, "Empresa:11111111")
    assert [r.situacao for r in historico] == ["em_revisao", "aprovado"]
    assert revisao.atuais(tmp_path)["Empresa:11111111"].sinais == ("F01", "K04")


@pytest.mark.parametrize(
    "rev",
    [
        _rev(revisor="  "),
        _rev(justificativa="curta"),
        _rev("descartado", justificativa=""),
        _rev("nao_revisado"),
        _rev("inventado"),
    ],
)
def test_revisao_invalida(tmp_path: Path, rev: Revisao) -> None:
    with pytest.raises(RevisaoInvalidaError):
        revisao.registrar(tmp_path, rev)
    assert revisao.ler(tmp_path) == []


def test_exportacao_exige_aprovacao_vigente() -> None:
    with pytest.raises(RevisaoPendenteError, match="não revisado"):
        revisao.exigir_aprovacao(None, ["F01"])
    with pytest.raises(RevisaoPendenteError, match="Em revisão"):
        revisao.exigir_aprovacao(_rev("em_revisao"), ["F01", "K04"])
    with pytest.raises(RevisaoPendenteError, match="sinais mudaram"):
        revisao.exigir_aprovacao(_rev(), ["F01", "K04", "E04"])
    assert revisao.exigir_aprovacao(_rev(), ["K04", "F01"]).revisor == "Ana"

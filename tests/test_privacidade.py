from __future__ import annotations

import pytest

from lupa.privacidade import SalAusenteError, id_pessoa
from tests.conftest import SAL_TESTE, gerar_cpf


def test_id_estavel_entre_formatos() -> None:
    cpf = gerar_cpf("123456789")
    a = id_pessoa(cpf, "Maria Teste", SAL_TESTE)
    b = id_pessoa("***456789**", "MARIA  TESTE", SAL_TESTE)
    assert a == b
    assert len(a) == 32


def test_homonimo_com_miolo_diferente_nao_e_mesma_pessoa() -> None:
    a = id_pessoa("***456789**", "Maria Teste", SAL_TESTE)
    b = id_pessoa("***456780**", "Maria Teste", SAL_TESTE)
    assert a != b


def test_mesmo_miolo_nome_diferente_nao_e_mesma_pessoa() -> None:
    a = id_pessoa("***456789**", "Maria Teste", SAL_TESTE)
    b = id_pessoa("***456789**", "Joana Teste", SAL_TESTE)
    assert a != b


def test_sal_muda_id() -> None:
    a = id_pessoa("***456789**", "Maria Teste", SAL_TESTE)
    b = id_pessoa("***456789**", "Maria Teste", SAL_TESTE + "-outro")
    assert a != b


def test_exige_sal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUPA_SALT", raising=False)
    with pytest.raises(SalAusenteError):
        id_pessoa("***456789**", "Maria Teste")

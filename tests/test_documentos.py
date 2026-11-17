from __future__ import annotations

import pytest

from lupa.cleaning.documentos import (
    cnpj_raiz,
    cnpj_valido,
    cpf_valido,
    mascarar_cpf,
    miolo_cpf,
)
from lupa.cleaning.texto import normalizar_nome
from tests.conftest import gerar_cpf


def test_cpf_sintetico_valido() -> None:
    cpf = gerar_cpf("123456789")
    assert cpf_valido(cpf)
    assert not cpf_valido(cpf[:-1] + str((int(cpf[-1]) + 1) % 10))
    assert not cpf_valido("111.111.111-11")


def test_mascara_padrao_interface() -> None:
    cpf = gerar_cpf("123456789")
    assert mascarar_cpf(cpf) == "***.456.789-**"
    assert mascarar_cpf(f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}") == "***.456.789-**"


def test_mascara_aceita_formato_receita() -> None:
    assert miolo_cpf("***456789**") == "456789"
    assert mascarar_cpf("***456789**") == "***.456.789-**"


def test_mascara_rejeita_lixo() -> None:
    with pytest.raises(ValueError):
        mascarar_cpf("12345")


def test_cnpj() -> None:
    assert cnpj_valido("11.222.333/0001-81")
    assert not cnpj_valido("11.222.333/0001-82")
    assert cnpj_raiz("11.222.333/0001-81") == "11222333"


def test_normalizar_nome() -> None:
    assert normalizar_nome("  José   da Silva-Júnior ") == "JOSE DA SILVA JUNIOR"
    assert normalizar_nome("JOSÉ DA SILVA JÚNIOR") == normalizar_nome("jose da silva junior")

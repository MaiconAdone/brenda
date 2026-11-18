from __future__ import annotations

from datetime import date

import polars as pl

from lupa.qualidade import Especificacao, verificar


def test_detecta_violacoes() -> None:
    df = pl.DataFrame(
        {
            "id": ["a", "a", "b"],
            "cnpj": ["11222333000181", "11222333000182", None],
            "valor": [1.0, -2.0, 3.0],
            "doc": ["***.456.789-**", "12345678909", "123.456.789-09"],
            "data": [date(2024, 1, 1), date(1800, 1, 1), None],
        }
    )
    esp = Especificacao(
        chave=("id",),
        nao_nulos=("cnpj",),
        cnpj=("cnpj",),
        nao_negativos=("valor",),
        sem_cpf_aberto=("doc",),
        datas=("data",),
    )
    regras = {(f.regra, f.linhas) for f in verificar(df, "t", esp)}
    assert regras == {
        ("chave_unica", 1),
        ("nao_nulo", 1),
        ("cnpj_valido", 1),
        ("nao_negativo", 1),
        ("sem_cpf_aberto", 2),
        ("data_plausivel", 1),
    }


def test_tabela_vazia_e_coluna_ausente() -> None:
    falhas = verificar(pl.DataFrame({"x": []}), "t", Especificacao(nao_nulos=("y",)))
    assert {f.regra for f in falhas} == {"minimo_linhas", "coluna_ausente"}
    assert verificar(pl.DataFrame({"x": []}), "t", Especificacao(minimo_linhas=0)) == []

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


def test_verificar_grafo_acha_ponta_inexistente_e_cpf() -> None:
    from lupa.qualidade import verificar_grafo

    nos = {
        "Pessoa": pl.DataFrame({"id": ["p1", "p1"], "documento": ["12345678909", None]}),
        "Empresa": pl.DataFrame({"id": ["e1"]}),
    }
    arestas = {
        "SOCIO_DE": pl.DataFrame({
            "origem": ["p1", "p9"], "destino": ["e1", "e1"],
            "rotulo_origem": ["Pessoa", "Pessoa"], "rotulo_destino": ["Empresa", "Empresa"],
            "fonte": ["f", None],
        })
    }  # fmt: skip
    regras = {(f.tabela, f.regra, f.detalhe): f.linhas for f in verificar_grafo(nos, arestas)}
    assert regras == {
        ("grafo/nos/Pessoa", "chave", "id"): 1,
        ("grafo/nos/Pessoa", "sem_cpf_aberto", "documento"): 1,
        ("grafo/arestas/SOCIO_DE", "nao_nulo", "fonte"): 1,
        ("grafo/arestas/SOCIO_DE", "ponta_inexistente", "origem:Pessoa"): 1,
    }

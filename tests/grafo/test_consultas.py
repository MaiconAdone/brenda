"""Consultas NetworkX sobre um grafo sintético montado à mão."""

from __future__ import annotations

import polars as pl
import pytest

from lupa.graph import consultas as c
from lupa.graph.modelo import Grafo


def _arestas(
    tipo: str, ro: str, rd: str, pares: list[tuple[str, str]], **extra: list[object]
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "origem": [o for o, _ in pares],
            "destino": [d for _, d in pares],
            "rotulo_origem": [ro] * len(pares),
            "rotulo_destino": [rd] * len(pares),
            "fonte": ["sintetico"] * len(pares),
            **extra,
        }
    )


@pytest.fixture
def rede() -> c.Rede:
    g = Grafo()
    g.adicionar_nos("Pessoa", pl.DataFrame({"id": ["p1", "p2", "p3"], "nome": ["A", "B", "B2"]}))
    g.adicionar_nos("Empresa", pl.DataFrame({"id": ["e1", "e2", "e3"], "nome": ["E1", "E2", "E3"]}))
    g.adicionar_nos("OrgaoPublico", pl.DataFrame({"id": ["o1"], "nome": ["ORGAO"]}))
    g.adicionar_nos("Licitacao", pl.DataFrame({"id": ["l1", "l2", "l3"]}))
    g.adicionar_arestas(
        "SOCIO_DE",
        _arestas("SOCIO_DE", "Pessoa", "Empresa", [("p1", "e1"), ("p1", "e2"), ("p2", "e2")]),
    )
    g.adicionar_arestas(
        "PROMOVIDA_POR",
        _arestas(
            "PROMOVIDA_POR", "Licitacao", "OrgaoPublico", [("l1", "o1"), ("l2", "o1"), ("l3", "o1")]
        ),
    )
    g.adicionar_arestas(
        "VENCEU",
        _arestas("VENCEU", "Empresa", "Licitacao", [("e1", "l1"), ("e1", "l2"), ("e2", "l3")]),
    )
    g.adicionar_arestas("MESMA_ENTIDADE_PROVAVEL",
                        _arestas("MESMA_ENTIDADE_PROVAVEL", "Pessoa", "Pessoa", [("p2", "p3")],
                                 probabilidade=[0.93]))  # fmt: skip
    return c.carregar_networkx(g)


def test_socios_e_empresas(rede: c.Rede) -> None:
    assert c.socios_de(rede, "Empresa:e2") == ["Pessoa:p1", "Pessoa:p2"]
    assert c.empresas_de(rede, "Pessoa:p1") == ["Empresa:e1", "Empresa:e2"]
    assert c.socios_em_comum(rede, "Empresa:e1", "Empresa:e2") == ["Pessoa:p1"]
    assert c.socios_em_comum(rede, "Empresa:e1", "Empresa:e3") == []


def test_fornecedores_do_orgao(rede: c.Rede) -> None:
    df = c.fornecedores_do_orgao(rede, "OrgaoPublico:o1")
    assert df.rows() == [("Empresa:e1", "E1", 2), ("Empresa:e2", "E2", 1)]


def test_mesma_pessoa_provavel(rede: c.Rede) -> None:
    assert c.mesma_pessoa_provavel(rede, "Pessoa:p3") == [("Pessoa:p2", 0.93)]
    assert c.mesma_pessoa_provavel(rede, "Pessoa:p1") == []


def test_vizinhanca_e_caminho(rede: c.Rede) -> None:
    viz = c.vizinhanca(rede, "Pessoa:p2", saltos=1)
    assert set(viz.nodes) == {"Pessoa:p2", "Empresa:e2", "Pessoa:p3"}
    assert c.menor_caminho(rede, "Pessoa:p3", "Empresa:e1") == [
        "Pessoa:p3", "Pessoa:p2", "Empresa:e2", "Pessoa:p1", "Empresa:e1",
    ]  # fmt: skip
    assert c.menor_caminho(rede, "Pessoa:p3", "Empresa:e1", max_saltos=3) is None
    assert c.menor_caminho(rede, "Pessoa:p1", "Empresa:e3") is None


def test_vizinhanca_nao_expande_hubs(rede: c.Rede) -> None:
    viz = c.vizinhanca(rede, "Licitacao:l1", saltos=2, grau_max=2)
    # o1 tem grau 3 > 2: entra, mas l2 e l3 (vizinhos dele) não.
    assert "OrgaoPublico:o1" in viz and "Licitacao:l3" not in viz


def test_filtro_de_rotulos() -> None:
    g = Grafo()
    g.adicionar_nos("Pessoa", pl.DataFrame({"id": ["p1"]}))
    g.adicionar_nos("Telefone", pl.DataFrame({"id": ["t1"]}))
    rede = c.carregar_networkx(g, rotulos=["Pessoa"])
    assert list(rede.nodes) == ["Pessoa:p1"]

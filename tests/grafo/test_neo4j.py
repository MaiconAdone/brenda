"""Carga no Neo4j com uma sessão falsa que registra o Cypher enviado."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from lupa.graph import neo4j
from lupa.graph.modelo import Grafo


class SessaoFalsa:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parametros: Any) -> None:
        self.chamadas.append((query, parametros))


def _grafo() -> Grafo:
    g = Grafo()
    g.adicionar_nos("Empresa", pl.DataFrame({"id": [f"e{i}" for i in range(5)], "nome": ["X"] * 5}))
    g.adicionar_nos("Pessoa", pl.DataFrame({"id": ["p1"], "nome": ["A"]}))
    g.adicionar_arestas(
        "SOCIO_DE",
        pl.DataFrame({
            "origem": ["p1", "e0"], "destino": ["e1", "e2"],
            "rotulo_origem": ["Pessoa", "Empresa"], "rotulo_destino": ["Empresa", "Empresa"],
            "fonte": ["f", "f"], "qualificacao": ["49", "22"],
        }),
    )  # fmt: skip
    return g


def test_carga_em_lotes() -> None:
    sessao = SessaoFalsa()
    contagem = neo4j.carregar(_grafo(), sessao, lote=2)
    assert contagem == {"nos/Empresa": 5, "nos/Pessoa": 1, "arestas/SOCIO_DE": 2}
    queries = [q for q, _ in sessao.chamadas]
    assert queries[0].startswith("MATCH (n) CALL (n) { DETACH DELETE n }")
    assert any("CREATE CONSTRAINT empresa_id" in q for q in queries)
    lotes_empresa = [p for q, p in sessao.chamadas if "MERGE (n:Empresa" in q]
    assert [len(p["linhas"]) for p in lotes_empresa] == [2, 2, 1]
    # Uma consulta por par de rótulos, com as propriedades num struct.
    arestas = [(q, p) for q, p in sessao.chamadas if "CREATE (a)-[r:SOCIO_DE]" in q]
    assert len(arestas) == 2
    assert "MATCH (a:Pessoa" in arestas[0][0] and "MATCH (a:Empresa" in arestas[1][0]
    assert arestas[0][1]["linhas"] == [
        {"origem": "p1", "destino": "e1", "props": {"fonte": "f", "qualificacao": "49"}}
    ]


def test_sem_limpar() -> None:
    sessao = SessaoFalsa()
    neo4j.carregar(_grafo(), sessao, limpar=False)
    assert not any("DETACH DELETE" in q for q, _ in sessao.chamadas)


def test_rotulo_invalido_e_recusado() -> None:
    g = Grafo()
    g.adicionar_nos("Empresa`) DETACH DELETE", pl.DataFrame({"id": ["x"]}))
    with pytest.raises(ValueError, match="Identificador inválido"):
        neo4j.carregar(g, SessaoFalsa())

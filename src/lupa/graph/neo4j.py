"""Carga do grafo gold no Neo4j.

A carga é completa e idempotente: por padrão apaga o banco e recarrega tudo, em lotes
com ``UNWIND``. Rótulos e tipos vêm do código (não de dados), e mesmo assim são
validados antes de entrar no Cypher.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from typing import Any, Protocol

import polars as pl

from lupa.graph.modelo import Grafo

log = logging.getLogger("lupa.grafo")

_IDENTIFICADOR = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class Sessao(Protocol):
    """Parte da sessão do driver ``neo4j`` usada aqui."""

    def run(self, query: str, **parametros: Any) -> Any: ...


def _ident(nome: str) -> str:
    if not _IDENTIFICADOR.match(nome):
        raise ValueError(f"Identificador inválido para Cypher: {nome!r}")
    return nome


def _lotes(df: pl.DataFrame, tamanho: int) -> Iterator[list[dict[str, Any]]]:
    for inicio in range(0, df.height, tamanho):
        yield df.slice(inicio, tamanho).to_dicts()


def carregar(grafo: Grafo, sessao: Sessao, lote: int = 5000, limpar: bool = True) -> dict[str, int]:
    """Carrega nós e arestas; devolve quantos de cada foram enviados."""
    if limpar:
        sessao.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
    contagem: dict[str, int] = {}
    for rotulo, df in grafo.nos.items():
        r = _ident(rotulo)
        sessao.run(
            f"CREATE CONSTRAINT {r.lower()}_id IF NOT EXISTS FOR (n:{r}) REQUIRE n.id IS UNIQUE"
        )
        for linhas in _lotes(df, lote):
            sessao.run(f"UNWIND $linhas AS l MERGE (n:{r} {{id: l.id}}) SET n += l", linhas=linhas)
        contagem[f"nos/{rotulo}"] = df.height
    for tipo, df in grafo.arestas.items():
        t = _ident(tipo)
        props = [
            c
            for c in df.columns
            if c not in ("origem", "destino", "rotulo_origem", "rotulo_destino")
        ]
        for (ro, rd), grupo in df.group_by("rotulo_origem", "rotulo_destino", maintain_order=True):
            consulta = (
                f"UNWIND $linhas AS l "
                f"MATCH (a:{_ident(str(ro))} {{id: l.origem}}) "
                f"MATCH (b:{_ident(str(rd))} {{id: l.destino}}) "
                f"CREATE (a)-[r:{t}]->(b) SET r += l.props"
            )
            dados = grupo.select("origem", "destino", props=pl.struct(props))
            for linhas in _lotes(dados, lote):
                sessao.run(consulta, linhas=linhas)
        contagem[f"arestas/{tipo}"] = df.height
    log.info("Neo4j: %s", contagem)
    return contagem


def carregar_no_servidor(grafo: Grafo, banco: str | None = None) -> dict[str, int]:
    """Conecta usando ``NEO4J_URI``, ``NEO4J_USER`` e ``NEO4J_PASSWORD`` e carrega."""
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    usuario = os.environ.get("NEO4J_USER", "neo4j")
    senha = os.environ.get("NEO4J_PASSWORD", "")
    with GraphDatabase.driver(uri, auth=(usuario, senha)) as driver:
        driver.verify_connectivity()
        with driver.session(database=banco) as sessao:
            return carregar(grafo, sessao)

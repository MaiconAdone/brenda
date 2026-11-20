"""Validação das consultas SQL e Cypher que os agentes enviam.

Duas camadas para SQL: (1) aqui, o texto é analisado (sqlglot) e só passa um único SELECT
sobre as visões permitidas, sem funções de arquivo/rede; (2) a conexão DuckDB roda com
acesso externo desligado fora de ``silver/`` e ``gold/`` e configuração travada
(``lupa.ferramentas.dados``). Para Cypher não há analisador completo em Python; a
validação é por palavras-chave e a sessão do Neo4j é aberta em modo de leitura.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import sqlglot
import sqlglot.expressions as exp
from sqlglot.errors import ParseError


class ConsultaRecusadaError(ValueError):
    """A consulta não é somente leitura ou usa algo fora do permitido."""


# Funções que leem arquivos, rede ou ambiente.
_FUNCOES_PROIBIDAS = re.compile(
    r"^(read_|scan_|parquet_|sniff_|glob|query|query_table|getenv|current_setting|"
    r"duckdb_|pragma_|load|install|sqlite_|postgres_|mysql_|iceberg_|delta_|http)",
    re.IGNORECASE,
)
_TIPOS_PERMITIDOS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def validar_sql(sql: str, visoes: Iterable[str]) -> exp.Expression:
    """Devolve a árvore da consulta se ela for um SELECT seguro; senão levanta erro."""
    permitidas = {v.lower() for v in visoes}
    try:
        comandos = [c for c in sqlglot.parse(sql, read="duckdb") if c is not None]
    except ParseError as erro:
        raise ConsultaRecusadaError(f"SQL inválido: {erro}") from erro
    if len(comandos) != 1:
        raise ConsultaRecusadaError("Envie exatamente um comando SQL.")
    arvore = comandos[0]
    if not isinstance(arvore, _TIPOS_PERMITIDOS):
        raise ConsultaRecusadaError(
            f"Só consultas SELECT são permitidas (recebido: {type(arvore).__name__})."
        )
    for no in arvore.walk():
        if isinstance(no, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                           exp.Alter, exp.Command, exp.Copy, exp.Attach, exp.Set,
                           exp.Pragma, exp.Into)):  # fmt: skip
            raise ConsultaRecusadaError(f"Operação não permitida: {type(no).__name__}.")
    ctes = {c.alias_or_name.lower() for c in arvore.find_all(exp.CTE)}
    for tabela in arvore.find_all(exp.Table):
        nome = tabela.name.lower()
        if not isinstance(tabela.this, exp.Identifier):
            raise ConsultaRecusadaError("Funções de tabela não são permitidas; use as visões.")
        if tabela.db or tabela.catalog:
            raise ConsultaRecusadaError("Use só o nome da visão, sem esquema ou catálogo.")
        if nome not in permitidas and nome not in ctes:
            raise ConsultaRecusadaError(
                f"Tabela {tabela.name!r} não existe ou não é permitida. "
                "Veja as visões em descrever_tabelas."
            )
    for funcao in arvore.find_all(exp.Func):
        nome = funcao.sql_name() if not isinstance(funcao, exp.Anonymous) else funcao.name
        if _FUNCOES_PROIBIDAS.match(nome or ""):
            raise ConsultaRecusadaError(f"Função não permitida: {nome}.")
    return arvore


_CYPHER_ESCRITA = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|CALL|USE|ALTER|"
    r"GRANT|DENY|REVOKE|START|STOP|TERMINATE|SHOW)\b",
    re.IGNORECASE,
)


def validar_cypher(cypher: str) -> str:
    """Aceita só consultas de leitura (MATCH/OPTIONAL MATCH/WITH/WHERE/RETURN...).

    ``CALL`` também é recusado (procedimentos podem escrever ou ler arquivos).
    """
    sem_textos = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", cypher)
    sem_comentarios = re.sub(r"//[^\n]*|/\*.*?\*/", " ", sem_textos, flags=re.DOTALL)
    if ";" in sem_comentarios.strip().rstrip(";"):
        raise ConsultaRecusadaError("Envie uma única consulta Cypher.")
    achado = _CYPHER_ESCRITA.search(sem_comentarios)
    if achado:
        raise ConsultaRecusadaError(
            f"Cypher somente leitura: {achado.group(1).upper()} não é permitido."
        )
    if not re.search(r"\bMATCH\b", sem_comentarios, re.IGNORECASE):
        raise ConsultaRecusadaError("A consulta deve começar com MATCH.")
    if not re.search(r"\bRETURN\b", sem_comentarios, re.IGNORECASE):
        raise ConsultaRecusadaError("A consulta deve ter RETURN.")
    return cypher.strip().rstrip(";")

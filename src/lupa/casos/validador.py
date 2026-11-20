"""Validador automático de evidências (código, não LLM).

Uma evidência só entra no caso se a referência puder ser refeita e der o mesmo resultado:

- ``registro_banco``: a consulta SQL é executada de novo e o hash do resultado tem de bater;
- ``consulta_grafo``: a mesma ferramenta de grafo é chamada com os mesmos argumentos;
- ``documento``: o documento tem de estar no cache com o mesmo SHA-256 e URL, e o trecho
  citado tem de aparecer no texto extraído.

O que não passa é descartado e o descarte fica no log de eventos.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from lupa.ferramentas.documentos import normalizar_trecho
from lupa.ferramentas.sql_seguro import ConsultaRecusadaError


class _Dados(Protocol):
    def consultar_tabelas(self, sql: str, limite: int | None = None) -> Any: ...
    def vizinhanca_grafo(self, no: str, saltos: int = 1, grau_max: int = 50,
                         tipos: Any = None) -> Any: ...  # fmt: skip
    def caminho_grafo(self, a: str, b: str, max_saltos: int = 4, grau_max: int = 200) -> Any: ...
    def consultar_grafo(self, cypher: str, limite: int | None = None) -> Any: ...


class _Documentos(Protocol):
    def metadados(self, documento_id: str) -> Any: ...
    def texto(self, documento_id: str) -> str: ...


@dataclass(frozen=True)
class Veredito:
    """Resultado da validação."""

    valida: bool
    motivo: str = ""


class Validador:
    """Confere referências contra a base de dados e o cache de documentos."""

    def __init__(self, dados: _Dados, documentos: _Documentos) -> None:
        self.dados = dados
        self.documentos = documentos

    def validar(self, tipo: str, referencia: dict[str, Any], trecho: str) -> Veredito:
        """Confere uma evidência."""
        if not isinstance(referencia, dict) or referencia.get("tipo") != tipo:
            return Veredito(False, f"A referência não é do tipo {tipo!r} (copie a 'referencia' "
                                   "devolvida pela ferramenta).")  # fmt: skip
        verificador: dict[str, Callable[[dict[str, Any], str], Veredito]] = {
            "registro_banco": self._banco,
            "consulta_grafo": self._grafo,
            "documento": self._documento,
        }
        if tipo not in verificador:
            return Veredito(False, f"Tipo de evidência desconhecido: {tipo!r}.")
        try:
            return verificador[tipo](referencia, trecho)
        except (ConsultaRecusadaError, ValueError, KeyError, TypeError) as erro:
            return Veredito(False, f"Não foi possível refazer a referência: {erro}")

    def _banco(self, ref: dict[str, Any], _trecho: str) -> Veredito:
        sql, esperado = ref.get("sql"), ref.get("sha_resultado")
        if not sql or not esperado:
            return Veredito(False, "Referência de banco sem 'sql' ou 'sha_resultado'.")
        atual = self.dados.consultar_tabelas(sql, ref.get("limite")).referencia["sha_resultado"]
        if atual != esperado:
            return Veredito(False, "O resultado da consulta não confere com o citado.")
        return Veredito(True)

    def _grafo(self, ref: dict[str, Any], _trecho: str) -> Veredito:
        ferramenta, args = ref.get("ferramenta"), ref.get("argumentos")
        if ferramenta not in ("vizinhanca_grafo", "caminho_grafo", "consultar_grafo") or not (
            isinstance(args, dict)
        ):
            return Veredito(False, "Referência de grafo sem ferramenta ou argumentos válidos.")
        atual = getattr(self.dados, ferramenta)(**args).referencia["sha_resultado"]
        if atual != ref.get("sha_resultado"):
            return Veredito(False, "O resultado da consulta ao grafo não confere com o citado.")
        return Veredito(True)

    def _documento(self, ref: dict[str, Any], trecho: str) -> Veredito:
        doc = self.documentos.metadados(str(ref.get("documento_id", "")))
        if doc is None:
            return Veredito(False, "Documento não está no cache (leia-o com ler_documento antes).")
        if doc.sha256 != ref.get("sha256") or doc.url != ref.get("url"):
            return Veredito(False, "URL ou hash do documento não confere com o cache.")
        if normalizar_trecho(trecho) not in normalizar_trecho(
            self.documentos.texto(doc.documento_id)
        ):
            return Veredito(False, "O trecho citado não aparece no documento (cite literalmente).")
        return Veredito(True)


def revalidar(arquivo: Any, validador: Validador) -> list[str]:
    """Revalida todas as evidências válidas (ex.: depois de atualizar os dados).

    Devolve os ids invalidados. Hipóteses sustentadas que perdem a base voltam a
    ``proposta`` (ver ``ArquivoCasos.invalidar_evidencia``).
    """
    invalidas = []
    for ev in arquivo.evidencias_validas():
        veredito = validador.validar(ev["tipo"], ev["referencia"], ev["trecho_relevante"])
        if not veredito.valida:
            arquivo.invalidar_evidencia("sistema", ev["id"], veredito.motivo)
            invalidas.append(ev["id"])
    return invalidas

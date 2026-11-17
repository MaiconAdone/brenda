"""Contrato comum a todos os sinais de risco.

Um sinal não acusa ninguém: ele aponta um padrão nos dados públicos e mostra
exatamente quais registros, de quais fontes, o fizeram disparar.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

import polars as pl


class Categoria(StrEnum):
    """Categorias do catálogo de sinais (cada uma tem teto próprio no score)."""

    FACHADA = "fachada"
    LARANJA = "laranja"
    CONFLITO_INTERESSE = "conflito_interesse"
    CONLUIO = "conluio"
    EXECUCAO_CONTRATUAL = "execucao_contratual"
    ANOMALIA = "anomalia"


@dataclass(frozen=True)
class Fonte:
    """Referência verificável ao dado original."""

    nome: str
    url: str
    coletado_em: str


@dataclass(frozen=True)
class Evidencia:
    """Um fato observado nos dados que sustenta o alerta.

    ``confianca`` (0 a 1) reflete a certeza das ligações usadas, por exemplo o score
    do Splink quando a evidência depende de identificar a mesma pessoa em duas bases.
    """

    descricao: str
    dados: Mapping[str, Any]
    fontes: tuple[Fonte, ...]
    confianca: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confianca <= 1.0:
            raise ValueError("confianca deve estar entre 0 e 1.")
        if not self.fontes:
            raise ValueError("Toda evidência precisa de ao menos uma fonte.")


@dataclass(frozen=True)
class Alerta:
    """Disparo de um sinal para uma entidade (empresa, pessoa, licitação...)."""

    sinal_id: str
    entidade_id: str
    evidencias: tuple[Evidencia, ...] = field(default_factory=tuple)

    @property
    def confianca(self) -> float:
        """Maior confiança entre as evidências (0 se não houver nenhuma)."""
        return max((e.confianca for e in self.evidencias), default=0.0)


_ID_VALIDO = re.compile(r"^[A-Z]\d{2}$")


class Sinal(ABC):
    """Classe base de um sinal de risco.

    Subclasses definem os metadados como atributos de classe e implementam
    :meth:`evidencias`, que recebe as tabelas da camada silver/gold e devolve um
    alerta por entidade que dispara o sinal.
    """

    id: ClassVar[str]
    nome: ClassVar[str]
    descricao: ClassVar[str]
    categoria: ClassVar[Categoria]
    peso_padrao: ClassVar[float]
    somente_combinado: ClassVar[bool] = False
    limitacoes: ClassVar[str] = ""

    def __init__(self, parametros: Mapping[str, Any] | None = None) -> None:
        self.parametros: dict[str, Any] = dict(parametros or {})

    @abstractmethod
    def evidencias(self, tabelas: Mapping[str, pl.DataFrame]) -> list[Alerta]:
        """Avalia o sinal e devolve os alertas com suas evidências."""


_REGISTRO: dict[str, type[Sinal]] = {}


def registrar[S: type[Sinal]](classe: S) -> S:
    """Decorador que valida os metadados e inclui o sinal no catálogo."""
    for atributo in ("id", "nome", "descricao", "categoria", "peso_padrao"):
        if not hasattr(classe, atributo):
            raise TypeError(f"{classe.__name__} não define '{atributo}'.")
    if not _ID_VALIDO.match(classe.id):
        raise ValueError(f"id de sinal inválido: {classe.id!r} (esperado ex.: 'F01').")
    if classe.id in _REGISTRO and _REGISTRO[classe.id] is not classe:
        raise ValueError(f"Sinal {classe.id} já registrado.")
    _REGISTRO[classe.id] = classe
    return classe


def sinais() -> dict[str, type[Sinal]]:
    """Catálogo de sinais registrados, por id."""
    return dict(_REGISTRO)

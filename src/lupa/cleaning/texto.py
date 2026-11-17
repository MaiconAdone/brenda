"""Normalização de textos (nomes de pessoas e razões sociais)."""

from __future__ import annotations

import re
import unicodedata

_ESPACOS = re.compile(r"\s+")
_NAO_ALFANUM = re.compile(r"[^A-Z0-9 ]")


def normalizar_nome(nome: str) -> str:
    """Caixa alta, sem acentos, sem pontuação e com espaços simples.

    >>> normalizar_nome("  José   da Silva-Júnior ")
    'JOSE DA SILVA JUNIOR'
    """
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    limpo = _NAO_ALFANUM.sub(" ", sem_acento.upper())
    return _ESPACOS.sub(" ", limpo).strip()

"""Retira nomes de pessoas físicas de textos prontos (ex.: descrição das evidências).

Os sinais descrevem sócios como ``NOME (***.123.456-**)``. Para o agente, a pessoa vira
``[pessoa ***.123.456-**]``: dá para seguir a mesma pessoa pelo CPF mascarado e pelo
``id_pessoa`` sem expor o nome. Nomes conhecidos sem CPF ao lado (ex.: lista de sócios
que entraram na empresa) são trocados por ``[pessoa <id curto>]``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_NOME_COM_CPF = re.compile(r"[A-ZÀ-Ý][A-ZÀ-Ý'´`^~.\- ]{2,}?\s*\((\*\*\*\.\d{3}\.\d{3}-\*\*)\)")


def anonimizar_texto(texto: str, nomes: Mapping[str, str] | None = None) -> str:
    """Troca ``NOME (CPF mascarado)`` e os ``nomes`` conhecidos (nome -> id) por marcadores."""
    saida = _NOME_COM_CPF.sub(lambda m: f"[pessoa {m.group(1)}]", texto)
    for nome, ident in sorted((nomes or {}).items(), key=lambda kv: -len(kv[0])):
        if len(nome) >= 5:
            saida = re.sub(re.escape(nome), f"[pessoa {ident[:12]}]", saida, flags=re.IGNORECASE)
    return saida


def anonimizar(valor: Any, nomes: Mapping[str, str] | None = None) -> Any:
    """Aplica ``anonimizar_texto`` em todos os textos de uma estrutura (dict/list/str)."""
    if isinstance(valor, str):
        return anonimizar_texto(valor, nomes)
    if isinstance(valor, Mapping):
        return {k: anonimizar(v, nomes) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [anonimizar(v, nomes) for v in valor]
    return valor

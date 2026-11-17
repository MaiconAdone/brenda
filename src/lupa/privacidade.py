"""Identificação pseudonimizada de pessoas físicas (LGPD: minimização).

Pessoas são identificadas por um hash com sal secreto de (miolo do CPF + nome
normalizado). O miolo sozinho não identifica ninguém: a Receita publica só 6 dos 11
dígitos, e o mesmo miolo aparece em vários CPFs. Por isso o nome entra no hash, e
duas pessoas só são consideradas a mesma quando miolo e nome coincidem. Ligações mais
fracas passam pela resolução de entidades, sempre com score de confiança.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from lupa.cleaning.documentos import miolo_cpf
from lupa.cleaning.texto import normalizar_nome


class SalAusenteError(RuntimeError):
    """``LUPA_SALT`` não foi definido no ambiente."""


def _sal(sal: str | None) -> bytes:
    valor = sal if sal is not None else os.environ.get("LUPA_SALT", "")
    if len(valor) < 16:
        raise SalAusenteError("Defina LUPA_SALT (mínimo 16 caracteres) no .env.")
    return valor.encode("utf-8")


def id_pessoa(cpf: str, nome: str, sal: str | None = None) -> str:
    """Identificador estável e não reversível de uma pessoa física."""
    chave = f"{miolo_cpf(cpf)}|{normalizar_nome(nome)}".encode()
    return hmac.new(_sal(sal), chave, hashlib.sha256).hexdigest()[:32]

"""Regra de linguagem do projeto aplicada ao texto escrito pelos agentes.

Ver ``docs/etica.md``: "alerta", "indício", "sinal de risco"; nunca "fraude", "fraudador",
"corrupto", "culpado". O filtro vale para o que o agente afirma (enunciados, objeções,
resultados), não para trechos citados de documentos, que são transcritos como estão.
"""

from __future__ import annotations

import re
import unicodedata

# Radicais proibidos (comparados sem acento e sem caixa).
TERMOS_PROIBIDOS: tuple[str, ...] = (
    "fraud",  # fraude, fraudou, fraudador, fraudulento
    "corrupt",  # corrupto, corrupção
    "corrupc",
    "culpad",
    "criminos",
    "ladrao",
    "ladroes",
    "bandid",
    "desviou",
    "roubou",
    "cometeu crime",
)


class LinguagemProibidaError(ValueError):
    """O texto afirma culpa em vez de descrever um indício."""


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def termos_encontrados(texto: str) -> list[str]:
    """Radicais proibidos presentes em ``texto``."""
    limpo = _sem_acento(texto)
    return [t for t in TERMOS_PROIBIDOS if re.search(rf"\b{re.escape(t)}", limpo)]


def exigir_linguagem(texto: str, campo: str) -> None:
    """Recusa texto com linguagem de acusação."""
    achados = termos_encontrados(texto)
    if achados:
        raise LinguagemProibidaError(
            f"{campo}: use 'indício' ou 'alerta' e descreva o padrão observado; "
            f"termos não permitidos: {', '.join(achados)}."
        )

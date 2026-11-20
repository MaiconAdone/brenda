"""Interface Streamlit (``app.py``) e utilidades de exibição."""

from __future__ import annotations

import re

_MD = re.compile(r"([\\`*_\[\]<>#|$~])")


def md(texto: str) -> str:
    """Escapa o texto para Markdown (CPF mascarado tem ``*``; valores em reais têm ``$``)."""
    return _MD.sub(r"\\\1", texto)

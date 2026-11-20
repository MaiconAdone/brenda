"""Log de acesso (LGPD): quem consultou ou exportou o quê, e quando.

Usado pela API e pela interface. Não grava termos de busca nem filtros, para não
registrar dados pessoais digitados por quem consulta.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def arquivo_log() -> Path:
    """Arquivo do log, em ``LUPA_LOG_DIR`` (padrão ``logs/``)."""
    return Path(os.environ.get("LUPA_LOG_DIR", "logs")) / "acesso.jsonl"


def registrar(acao: str, usuario: str, **detalhes: object) -> None:
    """Acrescenta um registro ao log."""
    registro = {
        "em": datetime.now(UTC).isoformat(),
        "acao": acao,
        "usuario": usuario or "anonimo",
        **detalhes,
    }
    arquivo = arquivo_log()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")

"""Contexto compartilhado pela ingestão: configuração, diretórios e linhagem."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from lupa.config import ConfigPiloto, carregar_piloto
from lupa.linhagem import Linhagem

log = logging.getLogger("lupa.ingestion")


@dataclass
class Contexto:
    """Tudo que uma etapa de ingestão precisa saber sobre o ambiente."""

    raiz: Path
    config: ConfigPiloto
    agora: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def linhagem(self) -> Linhagem:
        """Livro de linhagem do projeto."""
        return Linhagem(self.raiz / "linhagem.jsonl")

    def hoje(self) -> date:
        """Data de referência da coleta."""
        return self.config.piloto.data_referencia or self.agora().date()

    def bronze(self, *partes: str) -> Path:
        """Diretório (criado se preciso) dentro de ``bronze/``."""
        return self._dir("bronze", *partes)

    def silver(self, *partes: str) -> Path:
        """Diretório (criado se preciso) dentro de ``silver/``."""
        return self._dir("silver", *partes)

    def temporario(self) -> Path:
        """Diretório de trabalho para arquivos descompactados."""
        return self._dir("tmp")

    def _dir(self, *partes: str) -> Path:
        caminho = self.raiz.joinpath(*partes)
        caminho.mkdir(parents=True, exist_ok=True)
        return caminho


def contexto_padrao() -> Contexto:
    """Contexto a partir de ``LUPA_DATA_DIR`` e ``config/piloto.yaml``."""
    return Contexto(raiz=Path(os.environ.get("LUPA_DATA_DIR", "data")), config=carregar_piloto())

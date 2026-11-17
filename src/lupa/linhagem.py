"""Registro de linhagem: de onde veio cada arquivo, quando e com que hash."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class RegistroLinhagem:
    """Metadados de um arquivo coletado de uma fonte oficial."""

    fonte: str
    url: str
    arquivo: str
    sha256: str
    bytes: int
    coletado_em: str

    def como_json(self) -> str:
        """Serializa em uma linha JSON."""
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def sha256_arquivo(caminho: Path, bloco: int = 1 << 20) -> str:
    """Hash SHA-256 de um arquivo, lido em blocos (serve para arquivos de vários GB)."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        while pedaco := f.read(bloco):
            h.update(pedaco)
    return h.hexdigest()


class Linhagem:
    """Livro de registros de linhagem em JSON Lines (um registro por coleta)."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho

    def registrar(
        self, fonte: str, url: str, arquivo: Path, coletado_em: datetime | None = None
    ) -> RegistroLinhagem:
        """Calcula o hash do arquivo e acrescenta o registro ao livro."""
        registro = RegistroLinhagem(
            fonte=fonte,
            url=url,
            arquivo=arquivo.as_posix(),
            sha256=sha256_arquivo(arquivo),
            bytes=arquivo.stat().st_size,
            coletado_em=(coletado_em or datetime.now(UTC)).isoformat(),
        )
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.caminho.open("a", encoding="utf-8") as f:
            f.write(registro.como_json() + "\n")
        return registro

    def registros(self) -> list[RegistroLinhagem]:
        """Todos os registros, na ordem de coleta."""
        if not self.caminho.exists():
            return []
        with self.caminho.open(encoding="utf-8") as f:
            return [RegistroLinhagem(**json.loads(linha)) for linha in f if linha.strip()]

    def ja_coletado(self, sha256: str) -> bool:
        """Indica se um conteúdo com esse hash já foi registrado (ingestão idempotente)."""
        return any(r.sha256 == sha256 for r in self.registros())

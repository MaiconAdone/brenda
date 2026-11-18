"""Utilitários de arquivo: JSON Lines, CSV em latin-1 dentro de ZIP e Parquet."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import polars as pl


def gravar_jsonl(caminho: Path, registros: Iterable[dict[str, Any]]) -> int:
    """Grava registros em JSON Lines de forma atômica. Devolve a quantidade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    n = 0
    with temporario.open("w", encoding="utf-8") as f:
        for registro in registros:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            n += 1
    temporario.replace(caminho)
    return n


def ler_jsonl(caminho: Path) -> Iterator[dict[str, Any]]:
    """Lê um arquivo JSON Lines."""
    with caminho.open(encoding="utf-8") as f:
        for linha in f:
            if linha.strip():
                yield json.loads(linha)


def ler_jsonls(caminhos: Iterable[Path]) -> list[dict[str, Any]]:
    """Concatena vários arquivos JSON Lines."""
    return [r for c in sorted(caminhos) for r in ler_jsonl(c)]


def extrair_utf8(
    zip_path: Path, destino: Path, membro: str | None = None, codificacao: str = "latin-1"
) -> Path:
    """Extrai um membro de um ZIP convertendo para UTF-8 (o Polars só lê UTF-8).

    Sem ``membro``, extrai o primeiro arquivo que não seja PDF.
    """
    with zipfile.ZipFile(zip_path) as z:
        nome = membro or next(n for n in z.namelist() if not n.lower().endswith(".pdf"))
        destino.parent.mkdir(parents=True, exist_ok=True)
        # latin-1 tem 1 byte por caractere, então converter em blocos é seguro.
        with z.open(nome) as origem, destino.open("wb") as saida:
            while pedaco := origem.read(1 << 22):
                saida.write(pedaco.decode(codificacao).encode("utf-8"))
    return destino


def membros_zip(zip_path: Path) -> list[str]:
    """Nomes dos arquivos dentro de um ZIP."""
    with zipfile.ZipFile(zip_path) as z:
        return z.namelist()


def ler_csv_texto(caminho: Path, colunas: list[str] | None, cabecalho: bool) -> pl.LazyFrame:
    """Lê CSV ``;`` como texto (sem inferir tipos), de forma preguiçosa."""
    return pl.scan_csv(
        caminho,
        separator=";",
        has_header=cabecalho,
        new_columns=colunas,
        infer_schema=False,
        quote_char='"',
        truncate_ragged_lines=True,
        encoding="utf8",
    )


def gravar_parquet(df: pl.DataFrame, caminho: Path) -> Path:
    """Grava Parquet de forma atômica."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    df.write_parquet(temporario, compression="zstd")
    temporario.replace(caminho)
    return caminho

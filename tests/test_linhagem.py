from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from lupa.linhagem import Linhagem, sha256_arquivo


def test_registra_e_detecta_reingestao(tmp_path: Path) -> None:
    arquivo = tmp_path / "bronze" / "exemplo.csv"
    arquivo.parent.mkdir()
    arquivo.write_bytes(b"cnpj;nome\n11222333000181;EMPRESA SINTETICA\n")
    livro = Linhagem(tmp_path / "linhagem.jsonl")

    reg = livro.registrar(
        "teste", "https://exemplo.invalid/dados.csv", arquivo, datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert reg.sha256 == hashlib.sha256(arquivo.read_bytes()).hexdigest()
    assert reg.sha256 == sha256_arquivo(arquivo, bloco=4)
    assert reg.coletado_em.startswith("2026-01-01")
    assert livro.ja_coletado(reg.sha256)
    assert not livro.ja_coletado("0" * 64)
    assert livro.registros() == [reg]


def test_livro_vazio(tmp_path: Path) -> None:
    assert Linhagem(tmp_path / "nao_existe.jsonl").registros() == []

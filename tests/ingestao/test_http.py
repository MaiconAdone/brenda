from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from lupa.ingestion.http import ClienteHttp

Fabrica = Callable[[Callable[[httpx.Request], httpx.Response]], ClienteHttp]


def test_retry_em_503(cliente_falso: Fabrica) -> None:
    chamadas = {"n": 0}

    def manipulador(req: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        return httpx.Response(503) if chamadas["n"] < 3 else httpx.Response(200, json={"ok": 1})

    assert cliente_falso(manipulador).get_json("https://x.invalid/a") == {"ok": 1}
    assert chamadas["n"] == 3


def test_400_nao_repete(cliente_falso: Fabrica) -> None:
    chamadas = {"n": 0}

    def manipulador(req: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        return httpx.Response(400)

    with pytest.raises(httpx.HTTPStatusError):
        cliente_falso(manipulador).get("https://x.invalid/a")
    assert chamadas["n"] == 1


def test_204_e_404_sao_vazios(cliente_falso: Fabrica) -> None:
    cliente = cliente_falso(lambda r: httpx.Response(204 if "a" in r.url.path else 404))
    assert cliente.get_json("https://x.invalid/a") is None
    assert cliente.get_json("https://x.invalid/b") is None


def test_download_idempotente_e_retomavel(cliente_falso: Fabrica, tmp_path: Path) -> None:
    conteudo = b"0123456789" * 10
    pedidos: list[str | None] = []

    def manipulador(req: httpx.Request) -> httpx.Response:
        intervalo = req.headers.get("range")
        pedidos.append(intervalo)
        if intervalo:
            inicio = int(intervalo.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=conteudo[inicio:])
        return httpx.Response(200, content=conteudo)

    cliente = cliente_falso(manipulador)
    destino = tmp_path / "arq.zip"
    (tmp_path / "arq.zip.part").write_bytes(conteudo[:40])

    assert cliente.baixar("https://x.invalid/arq.zip", destino, tamanho_esperado=100)
    assert destino.read_bytes() == conteudo
    assert pedidos == ["bytes=40-"]

    assert not cliente.baixar("https://x.invalid/arq.zip", destino, tamanho_esperado=100)
    assert len(pedidos) == 1


def test_download_incompleto_falha(cliente_falso: Fabrica, tmp_path: Path) -> None:
    cliente = cliente_falso(lambda r: httpx.Response(200, content=b"abc"))
    with pytest.raises(OSError, match="incompleto"):
        cliente.baixar("https://x.invalid/a", tmp_path / "a", tamanho_esperado=10)

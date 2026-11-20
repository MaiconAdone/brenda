"""Fixtures da ingestão: contexto em diretório temporário e HTTP simulado."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from lupa.config import ConfigPiloto
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from tests.conftest import SAL_TESTE, gravar_ibge_sintetico


@pytest.fixture(autouse=True)
def _sal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUPA_SALT", SAL_TESTE)


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry instantâneo nos testes."""
    for nome in ("get", "requisitar", "_baixar_parcial"):
        monkeypatch.setattr(getattr(ClienteHttp, nome).retry, "wait", wait_none())


@pytest.fixture
def ctx(tmp_path: Path) -> Contexto:
    """Piloto sintético em Mauá/SP, período de 1 ano até 15/03/2026."""
    config = ConfigPiloto.model_validate(
        {
            "piloto": {
                "municipio_ibge": "3529401",
                "uf": "SP",
                "nome_municipio": "MAUA",
                "municipio_receita": "6689",
                "periodo_anos": 1,
                "data_referencia": date(2026, 3, 15),
            },
            "ingestao": {
                "pncp": {"modalidades": [6, 8], "intervalo_min_s": 0},
                "tse": {"anos": [2024]},
            },
            "finalidade": "Teste sintético de interesse público.",
        }
    )
    gravar_ibge_sintetico(tmp_path / "data")
    return Contexto(
        raiz=tmp_path / "data", config=config, agora=lambda: datetime(2026, 3, 15, 12, tzinfo=UTC)
    )


Manipulador = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def cliente_falso() -> Callable[[Manipulador], ClienteHttp]:
    """Fábrica de ``ClienteHttp`` com ``httpx.MockTransport``."""

    def fabrica(manipulador: Manipulador) -> ClienteHttp:
        return ClienteHttp(transport=httpx.MockTransport(manipulador))

    return fabrica


def zip_csv(caminho: Path, membros: dict[str, str], codificacao: str = "latin-1") -> Path:
    """Cria um ZIP com CSVs codificados como as fontes oficiais."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for nome, conteudo in membros.items():
            z.writestr(nome, conteudo.encode(codificacao))
    caminho.write_bytes(buffer.getvalue())
    return caminho


def linha(*campos: str) -> str:
    """Linha CSV ``;`` com aspas, no padrão da Receita/TSE/CGU."""
    return ";".join(f'"{c}"' for c in campos) + "\n"

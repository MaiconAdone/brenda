from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lupa.config import (
    ConfigPiloto,
    PilotoNaoDefinidoError,
    carregar_piloto,
    carregar_regras,
)

RAIZ = Path(__file__).resolve().parents[1]


def test_config_do_repositorio_e_valida() -> None:
    piloto = carregar_piloto(RAIZ / "config" / "piloto.yaml")
    regras = carregar_regras(RAIZ / "config" / "regras.yaml")
    assert piloto.piloto.periodo_anos == 4
    assert regras.sinais["C04"].somente_combinado
    categorias = set(regras.categorias)
    assert {"fachada", "laranja", "conflito_interesse", "conluio"} <= categorias


def test_piloto_sintetico(dir_config: Path) -> None:
    cfg = carregar_piloto(dir_config / "piloto.yaml")
    assert cfg.piloto.exigir_definido() == ("4205407", "SC")
    assert cfg.piloto.periodo() == (date(2022, 3, 1), date(2026, 3, 1))


def test_piloto_nao_definido() -> None:
    cfg = ConfigPiloto.model_validate({"piloto": {}, "finalidade": "x" * 30})
    with pytest.raises(PilotoNaoDefinidoError):
        cfg.piloto.exigir_definido()


def test_uf_incoerente() -> None:
    cfg = ConfigPiloto.model_validate(
        {"piloto": {"municipio_ibge": "4205407", "uf": "SP"}, "finalidade": "x" * 30}
    )
    with pytest.raises(ValueError, match="não pertence"):
        cfg.piloto.exigir_definido()


def test_codigo_ibge_invalido() -> None:
    with pytest.raises(ValidationError):
        ConfigPiloto.model_validate({"piloto": {"municipio_ibge": "123"}, "finalidade": "x" * 30})

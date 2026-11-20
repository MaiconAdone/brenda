"""Fixtures sintéticas. Nunca use dados reais nos testes."""

from __future__ import annotations

from pathlib import Path

import pytest

SAL_TESTE = "sal-sintetico-apenas-para-testes"


def gerar_cpf(base9: str) -> str:
    """Gera um CPF sintético válido a partir de 9 dígitos."""

    def dv(d: str) -> str:
        soma = sum(int(x) * p for x, p in zip(d, range(len(d) + 1, 1, -1), strict=True))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    d1 = dv(base9)
    return base9 + d1 + dv(base9 + d1)


@pytest.fixture
def dir_config(tmp_path: Path) -> Path:
    """Diretório de configuração sintético."""
    (tmp_path / "piloto.yaml").write_text(
        "piloto:\n  municipio_ibge: 4205407\n  uf: SC\n  periodo_anos: 4\n"
        "  data_referencia: 2026-03-01\n"
        "finalidade: Interesse público, finalidade de teste sintético.\n",
        encoding="utf-8",
    )
    (tmp_path / "regras.yaml").write_text(
        "categorias:\n  fachada: {teto: 15}\n  conflito_interesse: {teto: 30}\n"
        "sinais:\n  X01: {peso: 10}\n  X02: {peso: 8}\n  X03: {peso: 5, ativo: false}\n"
        "  X04: {peso: 3, somente_combinado: true}\n",
        encoding="utf-8",
    )
    return tmp_path


MUNICIPIOS_SINTETICOS = {
    "3529401": "Mauá",
    "3547809": "Santo André",
    "3548708": "São Bernardo do Campo",
}


def gravar_ibge_sintetico(raiz: Path) -> None:
    """Silver ``ibge/municipios`` mínima (define os municípios do escopo nos testes)."""
    import polars as pl

    from lupa.cleaning.texto import normalizar_nome

    destino = raiz / "silver" / "ibge"
    destino.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "municipio_ibge": list(MUNICIPIOS_SINTETICOS),
        "nome": list(MUNICIPIOS_SINTETICOS.values()),
        "nome_normalizado": [normalizar_nome(n) for n in MUNICIPIOS_SINTETICOS.values()],
        "uf": ["SP"] * len(MUNICIPIOS_SINTETICOS),
    }).write_parquet(destino / "municipios.parquet")  # fmt: skip

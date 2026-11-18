"""Resolução de entidades de pessoas com dados sintéticos."""

from __future__ import annotations

import polars as pl
import pytest

from lupa.config import ConfigResolucao
from lupa.entity_resolution import pessoas
from lupa.ingestion.contexto import Contexto
from tests.grafo.conftest import CARLOS, JOSE, JOSE_VARIANTE, MARIA_2020, MARIA_HOMONIMA


@pytest.fixture
def resolvido(silver: Contexto) -> tuple[pl.DataFrame, pl.DataFrame]:
    consolidadas = pessoas.consolidar(pessoas.mencoes(silver))
    return consolidadas, pessoas.resolver(consolidadas, ConfigResolucao())


def _ligados(ligacoes: pl.DataFrame, a: str, b: str) -> bool:
    par = (min(a, b), max(a, b))
    return ligacoes.filter((pl.col("id_a") == par[0]) & (pl.col("id_b") == par[1])).height == 1


def test_mencoes_e_consolidacao(silver: Contexto) -> None:
    m = pessoas.mencoes(silver)
    assert set(m["origem"]) == {"socio", "doador_campanha", "fornecedor_campanha", "candidato"}
    consolidadas = pessoas.consolidar(m)
    jose = consolidadas.filter(pl.col("no_id") == JOSE.id).row(0, named=True)
    # Mesmo id em sócio e doador vira uma pessoa só, com as duas origens.
    assert jose["origens"] == "doador_campanha;socio"
    assert (jose["idade_min"], jose["idade_max"]) == (41, 50)
    assert jose["documento"] == "***.456.789-**"
    # Candidato sem CPF ganha id por candidatura; idade vem do ano de nascimento.
    sem_cpf = consolidadas.filter(pl.col("no_id") == "tse-2024-200").row(0, named=True)
    assert (sem_cpf["idade_min"], sem_cpf["idade_max"]) == (45, 46)


def test_idades_incompativeis_viram_nulas() -> None:
    m = pl.DataFrame(
        {
            "no_id": ["x", "x"],
            "origem": ["socio", "candidato"],
            "nome_normalizado": ["A B", "A B"],
            "documento": [None, None],
            "idade_min": [21, 50],
            "idade_max": [30, 51],
        },
        schema=pessoas.ESQUEMA_MENCAO,
    )
    linha = pessoas.consolidar(m).row(0, named=True)
    assert linha["idade_min"] is None and linha["idade_max"] is None


def test_mesmo_miolo_e_nome_parecido_liga(resolvido: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    _, ligacoes = resolvido
    assert _ligados(ligacoes, JOSE.id, JOSE_VARIANTE.id)
    linha = ligacoes.filter(pl.col("id_a") == min(JOSE.id, JOSE_VARIANTE.id)).row(0, named=True)
    assert "miolo do CPF igual" in linha["evidencia"]
    assert linha["probabilidade"] >= 0.8
    assert linha["mw_miolo"] > 0


def test_candidato_sem_cpf_liga_pelo_ano(resolvido: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    _, ligacoes = resolvido
    assert _ligados(ligacoes, MARIA_2020.id, "tse-2024-200")


def test_homonimo_com_idade_incompativel_nao_liga(
    resolvido: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    _, ligacoes = resolvido
    assert not _ligados(ligacoes, MARIA_HOMONIMA.id, "tse-2024-200")
    assert not _ligados(ligacoes, MARIA_HOMONIMA.id, MARIA_2020.id)


def test_nunca_liga_so_pelo_nome(resolvido: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    """Doador com miolo × candidato sem CPF e sem idade do outro lado: só o nome coincide."""
    _, ligacoes = resolvido
    assert not _ligados(ligacoes, CARLOS.id, "tse-2024-300")


def test_poucas_pessoas_nao_quebra() -> None:
    vazio = pessoas.consolidar(pl.DataFrame(schema=pessoas.ESQUEMA_MENCAO))
    assert pessoas.resolver(vazio, ConfigResolucao()).is_empty()

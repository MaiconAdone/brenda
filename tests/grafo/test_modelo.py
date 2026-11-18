"""Construção do grafo a partir da silver sintética."""

from __future__ import annotations

import polars as pl
import pytest

from lupa.graph import etapas
from lupa.graph.modelo import Grafo
from lupa.ingestion.contexto import Contexto
from lupa.qualidade import verificar_grafo
from tests.grafo.conftest import JOSE, MARIA_2020, ORGAO


@pytest.fixture
def grafo(silver: Contexto) -> Grafo:
    assert etapas.etapa_entidades(silver).falhas == []
    resultado = etapas.etapa_grafo(silver)
    assert resultado.falhas == []
    return Grafo.ler(silver.gold("grafo"))


def _arestas(g: Grafo, tipo: str) -> set[tuple[str, str]]:
    return set(g.arestas[tipo].select("origem", "destino").iter_rows())


def test_integridade_e_fontes(grafo: Grafo) -> None:
    assert verificar_grafo(grafo.nos, grafo.arestas) == []
    for df in grafo.arestas.values():
        assert df["fonte"].null_count() == 0


def test_empresas_socios_e_enderecos(grafo: Grafo) -> None:
    socios = _arestas(grafo, "SOCIO_DE")
    assert (JOSE.id, "11222333") in socios and (JOSE.id, "44555666") in socios
    assert ("99888777", "11222333") in socios  # sócio PJ vira aresta Empresa → Empresa
    holding = grafo.nos["Empresa"].filter(pl.col("id") == "99888777")
    assert holding["nome"].to_list() == ["HOLDING SA"]
    # Mesmo prédio (complemento diferente) → um único nó de endereço.
    assert grafo.nos["Endereco"].height == 1
    assert grafo.arestas["LOCALIZADA_EM"]["complemento"].n_unique() == 2
    simples = grafo.nos["Empresa"].filter(pl.col("id") == "11222333")["opcao_simples"]
    assert simples.to_list() == ["S"]


def test_contatos_ficam_como_hash(grafo: Grafo) -> None:
    assert grafo.nos["Telefone"].height == 1  # telefone compartilhado
    assert grafo.nos["Email"].height == 2
    tudo = "".join(df.write_csv() for df in [*grafo.nos.values(), *grafo.arestas.values()])
    assert "1140001111" not in tudo and "sintetica.test" not in tudo


def test_licitacoes_contratos_e_vencedores(grafo: Grafo) -> None:
    venceu = grafo.arestas["VENCEU"]
    ctrl1 = venceu.filter(pl.col("destino") == "CTRL-1").row(0, named=True)
    assert ctrl1["origem"] == "11222333" and ctrl1["n_itens"] == 2
    assert ctrl1["valor_homologado"] == 90.0
    assert ctrl1["fonte"].endswith("/CTRL-1")
    assert _arestas(grafo, "PROMOVIDA_POR") == {("CTRL-1", ORGAO), ("CTRL-2", ORGAO)}
    assert _arestas(grafo, "ORIGINADO_DE") == {("CT-1", "CTRL-1")}
    assert _arestas(grafo, "PARTE_DE") == {("11222333", "CT-1")}
    assert _arestas(grafo, "CELEBRADO_POR") == {("CT-1", ORGAO)}


def test_campanhas(grafo: Grafo) -> None:
    candidaturas = grafo.nos["Candidatura"]
    assert candidaturas.height == 3  # 2º turno não duplica
    assert candidaturas.filter(pl.col("id") == "2024-200")["resultado"].to_list() == ["ELEITO"]
    assert (MARIA_2020.id, "2020-100") in _arestas(grafo, "CANDIDATO_EM")
    assert ("tse-2024-200", "2024-200") in _arestas(grafo, "CANDIDATO_EM")
    doou = grafo.arestas["DOOU_PARA"].filter(pl.col("origem") == JOSE.id).row(0, named=True)
    assert (doou["destino"], doou["valor"], doou["n_lancamentos"]) == ("2020-100", 300.0, 2)
    assert ("44555666", "2020-100") in _arestas(grafo, "FORNECEU_CAMPANHA")


def test_sancoes_so_de_quem_esta_no_grafo(grafo: Grafo) -> None:
    assert _arestas(grafo, "SANCIONADA") == {("44555666", "CEIS-1")}
    assert grafo.nos["Sancao"]["id"].to_list() == ["CEIS-1"]


def test_mesma_entidade_provavel_tem_score(grafo: Grafo) -> None:
    mesma = grafo.arestas["MESMA_ENTIDADE_PROVAVEL"]
    assert mesma.height >= 2
    assert mesma["probabilidade"].min() >= 0.8  # type: ignore[operator]
    assert mesma["evidencia"].null_count() == 0


def test_gravar_remove_tabelas_antigas(silver: Contexto, grafo: Grafo) -> None:
    destino = silver.gold("grafo")
    (destino / "nos" / "Obsoleto.parquet").write_bytes(b"")
    grafo.gravar(destino)
    assert not (destino / "nos" / "Obsoleto.parquet").exists()

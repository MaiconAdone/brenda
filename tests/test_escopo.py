"""Escopo com vários municípios: correspondência entre bases, PNCP e vitórias por contrato."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from lupa import escopo
from lupa.config import ConfigPiloto
from lupa.ingestion import pncp
from lupa.ingestion.contexto import Contexto
from lupa.signals.dados import derivar
from tests.conftest import gravar_ibge_sintetico


def _ctx(tmp_path: Path, **piloto: object) -> Contexto:
    config = ConfigPiloto.model_validate(
        {"piloto": {"uf": "SP", **piloto}, "finalidade": "Teste sintético de interesse público."}
    )
    gravar_ibge_sintetico(tmp_path)
    return Contexto(raiz=tmp_path, config=config)


def test_municipios_e_correspondencia_por_nome(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, municipios=["3547809", "3548708"])
    m = escopo.municipios(ctx)
    assert m["chave"].to_list() == ["SANTOANDRE", "SAOBERNARDODOCAMPO"]
    receita = [("7075", "SANTO ANDRE"), ("7077", "SAO BERNARDO DO CAMPO"), ("6689", "MAUA")]
    assert escopo.correspondencia(m, receita, "Receita") == {"3547809": "7075", "3548708": "7077"}
    with pytest.raises(ValueError, match="sem correspondência"):
        escopo.correspondencia(m, [("7075", "SANTO ANDRE")], "Receita")


def test_municipio_fora_da_uf_ou_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="não encontrados"):
        escopo.municipios(_ctx(tmp_path, municipios=["3500105"]))


def test_chave_ignora_pontuacao_e_nome_antigo() -> None:
    assert escopo.chave("Santa Bárbara d'Oeste") == escopo.chave("SANTA BARBARA D OESTE")
    assert escopo.chave("EMBU") == escopo.chave("Embu das Artes")


def test_consultas_do_pncp_por_municipio_ou_uf(tmp_path: Path) -> None:
    varios = _ctx(tmp_path, municipios=["3529401", "3547809"])
    assert pncp.alvos_consulta(varios) == [
        ("3529401", {"codigoMunicipioIbge": "3529401"}),
        ("3547809", {"codigoMunicipioIbge": "3547809"}),
    ]
    estado = _ctx(tmp_path, estado_inteiro=True)
    assert pncp.alvos_consulta(estado) == [("UF-SP", {"uf": "SP"})]
    filtro = pncp._filtro_escopo(varios)
    assert filtro({"unidadeOrgao": {"codigoIbge": "3547809", "ufSigla": "SP"}})
    assert not filtro({"unidadeOrgao": {"codigoIbge": "3550308", "ufSigla": "SP"}})
    assert pncp._filtro_escopo(estado)({"unidadeOrgao": {"ufSigla": "SP"}})


def test_vitorias_vindas_de_contratos() -> None:
    """Licitação sem resultados coletados (coleta seletiva): a vitória vem do contrato."""
    contratacoes = pl.DataFrame({
        "numero_controle_pncp": ["L1", "L2"], "orgao_cnpj": ["1", "1"],
        "orgao_nome": ["PREFEITURA"] * 2, "orgao_esfera": ["M"] * 2, "modalidade_id": [6, 6],
        "municipio_ibge": ["3547809"] * 2, "amparo_legal": ["Art. 28"] * 2,
        "objeto": ["A", "B"], "url_fonte": ["https://pncp.gov.br/app/editais/L1",
                                            "https://pncp.gov.br/app/editais/L2"],
        "data_publicacao": [datetime(2025, 1, 1)] * 2,
    })  # fmt: skip
    resultados = pl.DataFrame({
        "numero_controle_pncp": ["L1"], "fornecedor_tipo_pessoa": ["PJ"],
        "fornecedor_documento": ["11111111000100"], "fornecedor_id_pessoa": [None],
        "fornecedor_nome_normalizado": ["EMPRESA A"], "valor_total_homologado": [100.0],
        "data_resultado": [datetime(2025, 1, 10)],
    }, schema_overrides={"fornecedor_id_pessoa": pl.String})  # fmt: skip
    contratos = pl.DataFrame({
        "numero_controle_pncp": ["C1", "C2"], "numero_controle_compra": ["L1", "L2"],
        "orgao_cnpj": ["1", "1"], "orgao_nome": ["PREFEITURA"] * 2,
        "municipio_ibge": ["3547809"] * 2, "objeto": ["A", "B"],
        "fornecedor_tipo_pessoa": ["PJ", "PJ"],
        "fornecedor_documento": ["11111111000100", "22222222000100"],
        "fornecedor_id_pessoa": [None, None], "fornecedor_nome_normalizado": ["A", "B"],
        "valor_inicial": [100.0, 250.0], "valor_global": [100.0, 250.0],
        "data_assinatura": [date(2025, 2, 1)] * 2, "url_fonte": ["u1", "u2"],
    }, schema_overrides={"fornecedor_id_pessoa": pl.String})  # fmt: skip
    v = derivar({"pncp/contratacoes": contratacoes, "pncp/resultados": resultados,
                 "pncp/contratos": contratos})["derivado/vitorias"]  # fmt: skip
    linhas = {r["entidade"]: r for r in v.iter_rows(named=True)}
    assert set(linhas) == {"Empresa:11111111", "Empresa:22222222"}
    assert linhas["Empresa:11111111"]["origem"] == "resultado"  # L1 tem resultado: não duplica
    b = linhas["Empresa:22222222"]
    assert b["origem"] == "contrato" and b["valor"] == 250.0 and b["modalidade_id"] == 6
    assert b["url_fonte"].endswith("/L2") and b["municipio_ibge"] == "3547809"


def test_codigos_homonimos_aceita_mesmo_nome_em_outra_uf() -> None:
    municipios = pl.DataFrame(
        {"municipio_ibge": ["3547809"], "nome": ["Santo André"]}
    ).with_columns(chave=escopo.chave_expr("nome"))
    pares = [("7057", "SANTO ANDRE"), ("2011", "SANTO ANDRE"), ("7107", "SAO PAULO")]
    assert escopo.codigos_homonimos(municipios, pares, "Receita") == ["2011", "7057"]
    with pytest.raises(ValueError, match="sem correspondência"):
        escopo.codigos_homonimos(municipios, [("7107", "SAO PAULO")], "Receita")

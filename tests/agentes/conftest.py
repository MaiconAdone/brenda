"""Base sintética mínima para as ferramentas dos agentes. Nenhum dado real."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from lupa.casos.repositorio import ArquivoCasos
from lupa.casos.validador import Validador
from lupa.ferramentas.dados import Dados
from lupa.ferramentas.documentos import Documentos
from lupa.ingestion.http import ClienteHttp

ID_ANA = "a" * 32
ID_BETO = "b" * 32
EMPRESA = "Empresa:11111111"


def _p(raiz: Path, rel: str, df: pl.DataFrame) -> None:
    caminho = raiz / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(caminho)


def _arestas(origem: list[str], destino: list[str], ro: str, rd: str) -> pl.DataFrame:
    return pl.DataFrame({"origem": origem, "destino": destino, "rotulo_origem": ro,
                         "rotulo_destino": rd})  # fmt: skip


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    """Camadas silver/gold sintéticas com dados pessoais fictícios a esconder."""
    r = tmp_path / "data"
    _p(r, "silver/receita_cnpj/empresas.parquet", pl.DataFrame({
        "cnpj_basico": ["11111111", "22222222", "33333333"],
        "razao_social": ["ALFA SERVICOS LTDA", "BETA COMERCIO LTDA", "ANA FICTICIA 12345678909"],
        "capital_social": [1000.0, 50000.0, 5000.0],
    }))  # fmt: skip
    _p(r, "silver/receita_cnpj/simples.parquet", pl.DataFrame({
        "cnpj_basico": ["11111111", "33333333"], "opcao_simples": ["S", "S"],
        "opcao_mei": ["N", "S"],
    }))  # fmt: skip
    _p(r, "silver/receita_cnpj/estabelecimentos.parquet", pl.DataFrame({
        "cnpj_basico": ["11111111", "33333333"], "cnpj": ["11111111000191", "33333333000100"],
        "identificador_matriz_filial": ["1", "1"], "situacao_cadastral": ["02", "02"],
        "data_situacao_cadastral": [date(2020, 1, 1)] * 2,
        "data_inicio_atividade": [date(2023, 1, 1), date(2022, 5, 1)],
        "cnae_fiscal_principal": ["8630501", "4711301"], "cnae_fiscal_secundaria": [None, None],
        "logradouro": ["RUA DAS FLORES", "RUA DA CASA DA ANA"], "numero": ["10", "99"],
        "complemento": [None, None], "bairro": ["CENTRO", "JARDIM"], "cep": ["09000000", "09111111"],
        "uf": ["SP", "SP"], "municipio": ["7075", "7075"],
        "telefone_1": ["11999990000", "11988887777"], "correio_eletronico": ["a@x.test", "ana@x.test"],
    }))  # fmt: skip
    _p(r, "silver/receita_cnpj/socios.parquet", pl.DataFrame({
        "cnpj_basico": ["11111111", "11111111", "22222222"],
        "identificador_socio": ["2", "2", "2"],
        "nome_socio": ["ANA FICTICIA", "BETO EXEMPLO", "ANA FICTICIA"],
        "qualificacao_socio": ["49", "22", "49"],
        "data_entrada_sociedade": [date(2023, 1, 1), date(2024, 3, 1), date(2019, 1, 1)],
        "nome_representante": ["CARLOS REPRESENTANTE", None, None],
        "faixa_etaria": ["4", "8", "4"], "eh_pf": [True, True, True],
        "id_pessoa": [ID_ANA, ID_BETO, ID_ANA],
        "socio_documento": ["***.456.789-**", "***.111.222-**", "***.456.789-**"],
        "nome_normalizado": ["ANA FICTICIA", "BETO EXEMPLO", "ANA FICTICIA"],
    }))  # fmt: skip
    _p(r, "silver/transparencia/bolsa_familia_cruzamentos.parquet", pl.DataFrame({
        "id_pessoa": [ID_ANA], "mes": ["202303"], "valor_total": [600.0],
    }))  # fmt: skip
    evid = [{"descricao": "Sócio ANA FICTICIA (***.456.789-**) recebeu Novo Bolsa Família em "
             "1 mês(es); 2 sócios entraram: ANA FICTICIA, BETO EXEMPLO.",
             "dados": {"socio": "ANA FICTICIA"}, "fontes": [], "confianca": 1.0}]  # fmt: skip
    _p(r, "gold/alertas/alertas.parquet", pl.DataFrame({
        "sinal_id": ["L01"], "entidade": [EMPRESA], "confianca": [1.0], "n_evidencias": [1],
        "evidencias": [json.dumps(evid, ensure_ascii=False)],
    }))  # fmt: skip
    _p(r, "gold/alertas/scores.parquet", pl.DataFrame({
        "ranking": ["fornecedores"], "posicao": [1], "entidade": [EMPRESA], "score": [25.0],
        "por_categoria": ['{"laranja": 25}'], "sinais": [["L01"]], "descartados": [[]],
    }, schema_overrides={"descartados": pl.List(pl.String)}))  # fmt: skip
    _p(r, "gold/grafo/nos/Empresa.parquet", pl.DataFrame({
        "id": ["11111111", "22222222", "33333333"],
        "nome": ["ALFA SERVICOS LTDA", "BETA COMERCIO LTDA", "ANA FICTICIA 12345678909"],
    }))  # fmt: skip
    _p(r, "gold/grafo/nos/Pessoa.parquet", pl.DataFrame({
        "id": [ID_ANA, ID_BETO], "nome": ["ANA FICTICIA", "BETO EXEMPLO"],
    }))  # fmt: skip
    _p(r, "gold/grafo/arestas/SOCIO_DE.parquet",
       _arestas([ID_ANA, ID_BETO, ID_ANA], ["11111111", "11111111", "22222222"],
                "Pessoa", "Empresa"))  # fmt: skip
    return r


@pytest.fixture
def dados(raiz: Path) -> Dados:
    """Ferramentas de dados sobre a base sintética."""
    return Dados(raiz)


Rota = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def rotas() -> dict[str, Rota]:
    """Respostas falsas por caminho de URL (preencha no teste)."""
    return {}


@pytest.fixture
def documentos(raiz: Path, rotas: dict[str, Rota]) -> Documentos:
    """Ferramentas de documentos com HTTP falso."""

    def responder(req: httpx.Request) -> httpx.Response:
        for prefixo, rota in rotas.items():
            if str(req.url).startswith(prefixo):
                return rota(req)
        return httpx.Response(404, request=req)

    return Documentos(
        raiz,
        cliente=lambda: ClienteHttp(transport=httpx.MockTransport(responder)),
        relogio=lambda: datetime(2026, 11, 20, tzinfo=UTC),
    )


@pytest.fixture
def arquivo(tmp_path: Path) -> ArquivoCasos:
    """Arquivo do caso vazio."""
    with ArquivoCasos(tmp_path / "casos.sqlite") as a:
        yield a  # type: ignore[misc]


@pytest.fixture
def validador(dados: Dados, documentos: Documentos) -> Validador:
    """Validador sobre a base sintética."""
    return Validador(dados, documentos)

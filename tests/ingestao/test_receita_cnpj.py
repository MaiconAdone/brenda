from __future__ import annotations

from collections.abc import Callable

import httpx
import polars as pl

from lupa.ingestion import receita_cnpj
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.qualidade import verificar_tabela
from tests.ingestao.conftest import linha, zip_csv

Fabrica = Callable[[Callable[[httpx.Request], httpx.Response]], ClienteHttp]
MES = "2026-02"


def _estab(basico: str, ordem: str, dv: str, uf: str, municipio: str, email: str) -> str:
    campos = [
        basico,
        ordem,
        dv,
        "1",
        "FANTASIA",
        "02",
        "20200101",
        "00",
        "",
        "",
        "20200101",
        "4711302",
        "",
        "RUA",
        "DAS FLORES",
        "10",
        "",
        "CENTRO",
        "09300000",
        uf,
        municipio,
        "11",
        "40000000",
        "",
        "",
        "",
        "",
        email,
        "",
        "",
    ]
    return linha(*campos)  # fmt: skip


def _preparar_bronze(ctx: Contexto) -> None:
    pasta = ctx.bronze("receita_cnpj", MES)
    zip_csv(
        pasta / "Estabelecimentos0.zip",
        {
            "E0.ESTABELE": (
                _estab("11222333", "0001", "81", "SP", "6689", "CONTATO@EXEMPLO.INVALID ")  # Mauá
                + _estab("44555666", "0001", "81", "RJ", "6001", "x@exemplo.invalid")  # fornecedor
                + _estab("77888999", "0001", "00", "SP", "7107", "y@exemplo.invalid")  # fora
            )
        },
    )
    zip_csv(
        pasta / "Empresas0.zip",
        {
            "E0.EMPRECSV": (
                linha("11222333", "EMPRESA AÇÃO LTDA", "2062", "49", "1.000,50", "01", "")
                + linha("44555666", "FORNECEDORA SA", "2054", "10", "500000,00", "05", "")
                + linha("77888999", "OUTRA LTDA", "2062", "49", "10,00", "01", "")
            )
        },
    )
    zip_csv(pasta / "Socios0.zip", {"S0.SOCIOCSV": (
        linha("11222333", "2", "JOSÉ SINTÉTICO", "***456789**", "49", "20200101", "", "",
              "", "00", "4")
        + linha("44555666", "1", "HOLDING X", "99888777000100", "22", "20190101", "", "",
                "", "00", "0")
        + linha("77888999", "2", "JOSÉ SINTÉTICO", "***456789**", "49", "20210101", "", "",
                "", "00", "4")  # outra empresa do mesmo sócio, fora do piloto
        + linha("77888999", "2", "MARIA FORA", "***111222**", "49", "20210101", "", "",
                "", "00", "3")
    )})  # fmt: skip
    zip_csv(
        pasta / "Simples.zip",
        {
            "SIMPLES.CSV": (
                linha("11222333", "S", "20200101", "00000000", "N", "00000000", "00000000")
            )
        },
    )
    zip_csv(pasta / "Municipios.zip", {"MUNIC": linha("6689", "MAUA")})


def test_silver_filtra_e_pseudonimiza(ctx: Contexto) -> None:
    _preparar_bronze(ctx)
    t = receita_cnpj.silver(ctx, MES, {"44555666"})

    estab = t["estabelecimentos"]
    assert sorted(estab["cnpj"].to_list()) == ["11222333000181", "44555666000181"]
    assert estab.filter(pl.col("no_municipio_piloto"))["cnpj_basico"].to_list() == ["11222333"]
    assert "contato@exemplo.invalid" in estab["correio_eletronico"].to_list()

    empresas = t["empresas"]
    assert sorted(empresas["cnpj_basico"].to_list()) == ["11222333", "44555666"]
    assert empresas.filter(pl.col("cnpj_basico") == "11222333")["capital_social"][0] == 1000.5
    assert "EMPRESA AÇÃO LTDA" in empresas["razao_social"].to_list()  # latin-1 → UTF-8

    socios = t["socios"]
    pf = socios.filter(pl.col("eh_pf"))
    assert pf["socio_documento"].to_list() == ["***.456.789-**"]
    assert pf["id_pessoa"].null_count() == 0
    pj = socios.filter(~pl.col("eh_pf"))
    assert pj["socio_documento"].to_list() == ["99888777000100"]
    assert "cnpj_cpf_socio" not in socios.columns

    contagem = t["socios_contagem_nacional"]
    assert contagem.height == 1  # só o sócio das empresas-alvo, não a "MARIA FORA"
    assert contagem["n_empresas"][0] == 2

    assert t["simples"]["data_exclusao_simples"].null_count() == 1
    assert t["municipios"].height == 1

    for nome in ("estabelecimentos", "empresas", "socios", "simples"):
        assert verificar_tabela(f"receita_cnpj/{nome}", t[nome]) == [], nome
    assert not list(ctx.temporario().iterdir())  # CSVs extraídos foram apagados


PROPFIND = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
<d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/</d:href>
<d:propstat><d:prop></d:prop></d:propstat></d:response>
<d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/2026-01/</d:href>
<d:propstat><d:prop></d:prop></d:propstat></d:response>
<d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/2026-02/</d:href>
<d:propstat><d:prop></d:prop></d:propstat></d:response>
<d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/cnpj.tar.gz</d:href>
<d:propstat><d:prop><d:getcontentlength>99</d:getcontentlength></d:prop></d:propstat>
</d:response></d:multistatus>"""


def test_listar_e_mes_mais_recente(cliente_falso: Fabrica) -> None:
    def manipulador(req: httpx.Request) -> httpx.Response:
        assert req.method == "PROPFIND"
        assert req.headers["depth"] == "1"
        return httpx.Response(207, text=PROPFIND)

    cliente = cliente_falso(manipulador)
    assert receita_cnpj.listar(cliente) == {"2026-01": 0, "2026-02": 0, "cnpj.tar.gz": 99}
    assert receita_cnpj.mes_mais_recente(cliente) == "2026-02"

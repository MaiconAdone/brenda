from __future__ import annotations

from collections.abc import Callable

import httpx
import polars as pl

from lupa.ingestion import transparencia
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.privacidade import id_pessoa
from lupa.qualidade import verificar_tabela
from tests.conftest import SAL_TESTE, gerar_cpf
from tests.ingestao.conftest import linha, zip_csv

Fabrica = Callable[[Callable[[httpx.Request], httpx.Response]], ClienteHttp]
CPF_SOCIO = gerar_cpf("123456789")
CPF_OUTRO = gerar_cpf("555444333")

CABECALHO = linha(
    "CADASTRO", "CÓDIGO DA SANÇÃO", "TIPO DE PESSOA", "CPF OU CNPJ DO SANCIONADO",
    "NOME DO SANCIONADO", "CATEGORIA DA SANÇÃO", "DATA INÍCIO SANÇÃO", "DATA FINAL SANÇÃO",
    "ÓRGÃO SANCIONADOR", "VALOR DA MULTA",
)  # fmt: skip


def _bronze(ctx: Contexto) -> None:
    zip_csv(ctx.bronze("transparencia", "ceis") / "20260315_CEIS.zip", {"20260315_CEIS.csv": (
        CABECALHO
        + linha("CEIS", "1", "J", "44.555.666/0001-81", "FORNECEDORA SA", "Inidoneidade",
                "01/01/2025", "01/01/2027", "CGU", "")
        + linha("CEIS", "2", "F", CPF_SOCIO, "José Sintético", "Impedimento",
                "01/02/2025", "", "TJSP", "")
        + linha("CEIS", "3", "F", CPF_OUTRO, "Pessoa Alheia", "Impedimento",
                "01/02/2025", "", "TJSP", "")
        # Tipo de pessoa vazio na fonte: o CPF ainda precisa ser tratado como PF.
        + linha("CEIS", "4", "", CPF_OUTRO, "Pessoa Alheia", "Impedimento",
                "01/02/2025", "", "TJSP", "")
    )})  # fmt: skip
    zip_csv(ctx.bronze("transparencia", "cnep") / "20260315_CNEP.zip", {"20260315_CNEP.csv": (
        CABECALHO + linha("CNEP", "9", "J", "44555666000181", "FORNECEDORA SA", "Multa",
                          "01/07/2022", "", "Petrobras", "36.680,49")
    )})  # fmt: skip


def test_silver_sancoes_minimiza_pf(ctx: Contexto) -> None:
    _bronze(ctx)
    relevante = id_pessoa(CPF_SOCIO, "JOSE SINTETICO", SAL_TESTE)
    df = transparencia.silver_sancoes(ctx, {relevante})

    assert df.height == 3  # 2 PJ + 1 PF relevante; a PF alheia sai
    pf = df.filter(pl.col("eh_pf"))
    assert pf["id_pessoa"].to_list() == [relevante]
    assert pf["documento"].to_list() == ["***.456.789-**"]
    assert CPF_SOCIO not in df.write_csv() and CPF_OUTRO not in df.write_csv()
    assert df.filter(pl.col("cadastro") == "CNEP")["valor_multa"][0] == 36680.49
    assert set(df["cnpj_raiz"].drop_nulls()) == {"44555666"}
    assert verificar_tabela("transparencia/sancoes", df) == []


def test_data_disponivel(cliente_falso: Fabrica) -> None:
    html = 'arquivos.push({"ano" : "2026", "mes" : "03", "dia" : "15", "origem" :  "CEIS"});'
    cliente = cliente_falso(lambda r: httpx.Response(200, text=html))
    assert transparencia.data_disponivel(cliente, "ceis") == "20260315"


def test_bolsa_familia_guarda_so_socios(ctx: Contexto, cliente_falso: Fabrica) -> None:
    ctx.config.ingestao.transparencia.bolsa_familia_meses = ["202601"]
    alvo = id_pessoa(CPF_SOCIO, "José Sintético", SAL_TESTE)

    def manipulador(req: httpx.Request) -> httpx.Response:
        if req.url.params["pagina"] != "1":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[
            {"beneficiarioNovoBolsaFamilia": {"cpfFormatado": "***.456.789-**",
                                              "nome": "JOSE SINTETICO"}, "valorSaque": 600.0},
            {"beneficiarioNovoBolsaFamilia": {"cpfFormatado": "***.444.333-**",
                                              "nome": "PESSOA ALHEIA"}, "valorSaque": 600.0},
        ])  # fmt: skip

    df = transparencia.coletar_bolsa_familia(
        ctx, cliente_falso(manipulador), pl.DataFrame({"id_pessoa": [alvo]})
    )
    assert df.to_dicts() == [{"id_pessoa": alvo, "mes": "202601", "valor_total": 600.0}]
    manifesto = (ctx.bronze("transparencia", "bolsa_familia") / "202601.manifesto.json").read_text(
        encoding="utf-8"
    )
    assert "PESSOA ALHEIA" not in manifesto and '"registros_lidos": 2' in manifesto


def test_bolsa_familia_todos_os_meses(ctx: Contexto) -> None:
    ctx.config.ingestao.transparencia.bolsa_familia_desde = "202511"
    assert transparencia.meses_bolsa_familia(ctx) == [
        "202511",
        "202512",
        "202601",
        "202602",
        "202603",
    ]


def test_bolsa_familia_retoma_meses_fechados(ctx: Contexto, cliente_falso: Fabrica) -> None:
    ctx.config.ingestao.transparencia.bolsa_familia_meses = ["202501", "202603", "202604"]
    alvo = id_pessoa(CPF_SOCIO, "José Sintético", SAL_TESTE)
    chamadas: list[str] = []

    def manipulador(req: httpx.Request) -> httpx.Response:
        mes = req.url.params["mesAno"]
        chamadas.append(mes)
        if req.url.params["pagina"] != "1" or mes == "202604":  # 202604 ainda não publicado
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[
            {"beneficiarioNovoBolsaFamilia": {"cpfFormatado": "***.456.789-**",
                                              "nome": "JOSE SINTETICO", "nis": "123"},
             "valorSaque": 600.0},
        ])  # fmt: skip

    alvos = pl.DataFrame({"id_pessoa": [alvo]})
    df = transparencia.coletar_bolsa_familia(ctx, cliente_falso(manipulador), alvos)
    assert df["mes"].to_list() == ["202501", "202603"]
    pasta = ctx.bronze("transparencia", "bolsa_familia")
    assert not (pasta / "202604.manifesto.json").exists()
    assert "123" not in (pasta / "202501.correspondencias.json").read_text(encoding="utf-8")

    chamadas.clear()
    df2 = transparencia.coletar_bolsa_familia(ctx, cliente_falso(manipulador), alvos)
    assert df2.equals(df)
    assert "202501" not in chamadas  # fechado e com os mesmos alvos: não relê
    assert "202603" in chamadas  # recente: relê

    chamadas.clear()
    outros = pl.DataFrame({"id_pessoa": [alvo, "outro"]})
    transparencia.coletar_bolsa_familia(ctx, cliente_falso(manipulador), outros)
    assert "202501" in chamadas  # alvos mudaram: relê

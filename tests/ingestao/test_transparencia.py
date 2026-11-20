from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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


CABECALHO_BF = linha(
    "MÊS COMPETÊNCIA", "MÊS REFERÊNCIA", "UF", "CÓDIGO MUNICÍPIO SIAFI", "NOME MUNICÍPIO",
    "CPF FAVORECIDO", "NIS FAVORECIDO", "NOME FAVORECIDO", "VALOR PARCELA",
)  # fmt: skip


def _zip_bf(tmp: Path, mes: str) -> bytes:
    """Arquivo mensal sintético: o sócio (com acento), um homônimo e uma pessoa alheia."""
    conteudo = (
        CABECALHO_BF
        + linha(mes, mes, "SP", "6689", "MAUA", "***.456.789-**", "12345678901",
                "JOSÉ SINTÉTICO", "600,00")
        + linha(mes, "202312", "SP", "6689", "MAUA", "***.456.789-**", "12345678901",
                "JOSÉ SINTÉTICO", "1.050,50")
        + linha(mes, mes, "BA", "3849", "SALVADOR", "***.456.789-**", "999",
                "JOSE OUTRO NOME", "600,00")
        + linha(mes, mes, "SP", "6689", "MAUA", "***.444.333-**", "888",
                "PESSOA ALHEIA", "600,00")
    )  # fmt: skip
    return zip_csv(tmp / f"{mes}.zip", {f"{mes}_NovoBolsaFamilia.csv": conteudo}).read_bytes()


def _servidor(tmp: Path, publicados: set[str], chamadas: list[str]):  # type: ignore[no-untyped-def]
    def manipulador(req: httpx.Request) -> httpx.Response:
        mes = req.url.path.rsplit("/", 1)[-1]
        if req.method == "GET":
            chamadas.append(mes)
        if mes not in publicados:
            return httpx.Response(404)
        corpo = _zip_bf(tmp, mes)
        if req.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(corpo))})
        return httpx.Response(200, content=corpo)

    return manipulador


def _alvos() -> pl.DataFrame:
    return pl.DataFrame({
        "id_pessoa": [id_pessoa(CPF_SOCIO, "José Sintético", SAL_TESTE)],
        "miolo": ["456789"], "nome_normalizado": ["JOSE SINTETICO"],
    })  # fmt: skip


def test_bolsa_familia_guarda_so_alvos(
    ctx: Contexto, cliente_falso: Fabrica, tmp_path: Path
) -> None:
    ctx.config.ingestao.transparencia.bolsa_familia_meses = ["202601"]
    alvos = _alvos()
    cliente = cliente_falso(_servidor(tmp_path, {"202601"}, []))
    df = transparencia.coletar_bolsa_familia(ctx, cliente, alvos, SAL_TESTE)
    # Duas parcelas no mês (uma retroativa) somadas; homônimo de outro nome e alheio fora.
    assert df.to_dicts() == [
        {"id_pessoa": alvos["id_pessoa"][0], "mes": "202601", "valor_total": 1650.5}
    ]
    pasta = ctx.bronze("transparencia", "bolsa_familia")
    manifesto = (pasta / "202601.manifesto.json").read_text(encoding="utf-8")
    assert "PESSOA ALHEIA" not in manifesto and '"registros_lidos": 4' in manifesto
    assert "12345678901" not in (pasta / "202601.correspondencias.json").read_text("utf-8")
    assert not list((pasta / "_tmp").glob("*"))  # arquivo nacional apagado


def test_nome_expr_igual_a_normalizar_nome() -> None:
    from lupa.cleaning.texto import normalizar_nome

    nomes = ["  José   da Silva-Júnior ", "MARIA D'ÁVILA", "Ñoño Çá", "ANA  MARIA"]
    df = pl.DataFrame({"n": nomes}).select(transparencia.nome_expr("n"))
    assert df["n"].to_list() == [normalizar_nome(n) for n in nomes]
    cpfs = pl.DataFrame({"c": ["***.456.789-**", "***456789**", "12345678909"]})
    assert cpfs.select(transparencia.miolo_expr("c"))["c"].to_list() == ["456789"] * 2 + ["456789"]


def test_bolsa_familia_todos_os_meses(ctx: Contexto) -> None:
    ctx.config.ingestao.transparencia.bolsa_familia_desde = "202511"
    assert transparencia.meses_bolsa_familia(ctx) == [
        "202511",
        "202512",
        "202601",
        "202602",
        "202603",
    ]


def test_bolsa_familia_retoma_meses_fechados(
    ctx: Contexto, cliente_falso: Fabrica, tmp_path: Path
) -> None:
    ctx.config.ingestao.transparencia.bolsa_familia_meses = ["202501", "202603", "202604"]
    chamadas: list[str] = []
    cliente = cliente_falso(_servidor(tmp_path, {"202501", "202603"}, chamadas))
    alvos = _alvos()
    df = transparencia.coletar_bolsa_familia(ctx, cliente, alvos, SAL_TESTE)
    assert df["mes"].to_list() == ["202501", "202603"]
    pasta = ctx.bronze("transparencia", "bolsa_familia")
    assert not (pasta / "202604.manifesto.json").exists()  # ainda não publicado

    chamadas.clear()
    df2 = transparencia.coletar_bolsa_familia(ctx, cliente, alvos, SAL_TESTE)
    assert df2.equals(df)
    assert "202501" not in chamadas  # fechado e com os mesmos alvos: não relê
    assert "202603" in chamadas  # recente: relê

    chamadas.clear()
    outros = pl.concat([alvos, pl.DataFrame(
        {"id_pessoa": ["outro"], "miolo": ["111111"], "nome_normalizado": ["OUTRO"]}
    )])  # fmt: skip
    transparencia.coletar_bolsa_familia(ctx, cliente, outros, SAL_TESTE)
    assert "202501" in chamadas  # alvos mudaram: relê

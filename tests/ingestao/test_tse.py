from __future__ import annotations

import polars as pl

from lupa.ingestion import tse
from lupa.ingestion.contexto import Contexto
from lupa.qualidade import verificar_tabela
from tests.conftest import gerar_cpf
from tests.ingestao.conftest import linha, zip_csv

CPF_DOADOR = gerar_cpf("111222333")
COL_CAND = ["ANO_ELEICAO", "NR_TURNO", "SG_UF", "SG_UE", "NM_UE", "DS_CARGO", "SQ_CANDIDATO",
            "NR_CANDIDATO", "NM_CANDIDATO", "NM_URNA_CANDIDATO", "NR_CPF_CANDIDATO",
            "SG_PARTIDO", "DS_SITUACAO_CANDIDATURA", "DS_SIT_TOT_TURNO", "DT_NASCIMENTO",
            "DS_EMAIL", "NR_TITULO_ELEITORAL_CANDIDATO", "DS_COR_RACA"]  # fmt: skip


def _cand(sq: str, ue: str, nome: str) -> str:
    return linha(
        "2024",
        "1",
        "SP",
        "66893",
        ue,
        "PREFEITO",
        sq,
        "10",
        nome,
        nome[:5],
        "-4",
        "XX",
        "APTO",
        "ELEITO",
        "23/09/1970",
        "x@exemplo.invalid",
        "123",
        "BRANCA",
    )


def _bronze(ctx: Contexto) -> None:
    pasta = ctx.bronze("tse", "2024")
    zip_csv(
        pasta / "consulta_cand_2024.zip",
        {
            "leiame.pdf": "x",
            "consulta_cand_2024_SP.csv": linha(*COL_CAND)
            + _cand("1", "MAUÁ", "CANDIDATA UM")
            + _cand("2", "SANTO ANDRÉ", "CANDIDATO DOIS"),
        },
    )
    zip_csv(pasta / "prestacao_de_contas_eleitorais_candidatos_2024.zip", {
        "receitas_candidatos_2024_SP.csv": linha(
            "SQ_CANDIDATO", "NR_CPF_CNPJ_DOADOR", "NM_DOADOR", "VR_RECEITA", "DT_RECEITA")
        + linha("1", CPF_DOADOR, "DOADOR PF", "1.500,00", "10/09/2024")
        + linha("1", "44555666000181", "FORNECEDORA SA", "200,00", "11/09/2024")
        + linha("2", "44555666000181", "FORNECEDORA SA", "999,00", "11/09/2024"),
        "despesas_contratadas_candidatos_2024_SP.csv": linha(
            "SQ_CANDIDATO", "NR_CPF_CNPJ_FORNECEDOR", "NM_FORNECEDOR", "VR_DESPESA_CONTRATADA",
            "DT_DESPESA")
        + linha("1", "44555666000181", "GRAFICA SA", "300,00", "01/09/2024"),
    })  # fmt: skip


def test_silver_filtra_municipio_e_minimiza(ctx: Contexto) -> None:
    _bronze(ctx)
    t = tse.silver(ctx)

    cand = t["candidatos"]
    assert cand["nome"].to_list() == ["CANDIDATA UM"]
    assert cand["ano_nascimento"].to_list() == [1970]
    assert cand["cpf_mascarado"].null_count() == 1  # TSE não publica CPF (-4)
    assert not {"DS_EMAIL", "ds_email", "data_nascimento"} & set(cand.columns)

    rec = t["receitas"]
    assert rec.height == 2  # só o candidato de Mauá
    assert rec["valor"].sum() == 1700.0
    assert rec.filter(pl.col("doador_tipo") == "PF")["doador_documento"].to_list() == [
        f"***.{CPF_DOADOR[3:6]}.{CPF_DOADOR[6:9]}-**"
    ]
    assert CPF_DOADOR not in rec.write_csv()
    assert t["despesas"]["fornecedor_tipo"].to_list() == ["PJ"]

    for nome in ("candidatos", "receitas", "despesas"):
        assert verificar_tabela(f"tse/{nome}", t[nome]) == [], nome

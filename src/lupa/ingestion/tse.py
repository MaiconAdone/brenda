"""TSE: candidatos, receitas (doações) e despesas contratadas das eleições municipais.

Arquivos nacionais por ano em ``cdn.tse.jus.br``; extraímos só o CSV da UF do piloto e
filtramos o município (``NM_UE``). Colunas sensíveis e desnecessárias (e-mail, título de
eleitor, gênero, cor/raça, estado civil, instrução) não passam para o silver.

Limitação importante: desde 2024 o TSE publica ``NR_CPF_CANDIDATO = -4`` (não divulgado).
Ligar candidato a sócio exige resolução de entidades probabilística (nome + ano de
nascimento × faixa etária), nunca só o nome. Doadores PJ têm CNPJ e ligam exatamente.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from lupa import escopo
from lupa.cleaning.documentos import somente_digitos
from lupa.ingestion.arquivos import extrair_utf8, gravar_parquet, ler_csv_texto, membros_zip
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.privacidade import pseudonimizar

log = logging.getLogger("lupa.ingestion.tse")

FONTE = "tse"
URL_BASE = "https://cdn.tse.jus.br/estatistica/sead/odsele"


def urls(ano: int) -> dict[str, str]:
    """Arquivos usados por ano de eleição."""
    return {
        "candidatos": f"{URL_BASE}/consulta_cand/consulta_cand_{ano}.zip",
        "contas": f"{URL_BASE}/prestacao_contas/"
        f"prestacao_de_contas_eleitorais_candidatos_{ano}.zip",
    }


def coletar(ctx: Contexto, cliente: ClienteHttp) -> list[Path]:
    """Baixa candidatos e prestação de contas de cada ano configurado."""
    caminhos = []
    for ano in ctx.config.ingestao.tse.anos:
        for url in urls(ano).values():
            destino = ctx.bronze(FONTE, str(ano)) / url.rsplit("/", 1)[-1]
            if cliente.baixar(url, destino):
                ctx.linhagem.registrar(FONTE, url, destino, ctx.agora())
            caminhos.append(destino)
    return caminhos


COLUNAS_CANDIDATO = {
    "ANO_ELEICAO": "ano_eleicao", "NR_TURNO": "turno", "SG_UF": "uf", "SG_UE": "codigo_ue",
    "NM_UE": "nome_ue", "DS_CARGO": "cargo", "SQ_CANDIDATO": "sq_candidato",
    "NR_CANDIDATO": "numero", "NM_CANDIDATO": "nome", "NM_URNA_CANDIDATO": "nome_urna",
    "NR_CPF_CANDIDATO": "cpf_original", "SG_PARTIDO": "partido",
    "DS_SITUACAO_CANDIDATURA": "situacao_candidatura", "DS_SIT_TOT_TURNO": "resultado",
    "DT_NASCIMENTO": "data_nascimento",
}  # fmt: skip
COLUNAS_RECEITA = {
    "SQ_CANDIDATO": "sq_candidato", "NR_CPF_CNPJ_DOADOR": "doador_documento_original",
    "NM_DOADOR": "doador_nome", "NM_DOADOR_RFB": "doador_nome_rfb",
    "DS_ORIGEM_RECEITA": "origem", "DS_FONTE_RECEITA": "fonte",
    "DS_NATUREZA_RECEITA": "natureza", "CD_CNAE_DOADOR": "doador_cnae",
    "VR_RECEITA": "valor", "DT_RECEITA": "data", "NR_RECIBO_DOACAO": "recibo",
}  # fmt: skip
COLUNAS_DESPESA = {
    "SQ_CANDIDATO": "sq_candidato", "NR_CPF_CNPJ_FORNECEDOR": "fornecedor_documento_original",
    "NM_FORNECEDOR": "fornecedor_nome", "NM_FORNECEDOR_RFB": "fornecedor_nome_rfb",
    "CD_CNAE_FORNECEDOR": "fornecedor_cnae", "DS_ORIGEM_DESPESA": "origem",
    "DS_DESPESA": "descricao", "VR_DESPESA_CONTRATADA": "valor", "DT_DESPESA": "data",
}  # fmt: skip


def _ler_membro(ctx: Contexto, zip_path: Path, sufixo: str) -> pl.DataFrame | None:
    membros = [m for m in membros_zip(zip_path) if m.lower().endswith(sufixo.lower())]
    if not membros:
        log.warning("%s não contém *%s", zip_path.name, sufixo)
        return None
    csv = extrair_utf8(zip_path, ctx.temporario() / Path(membros[0]).name, membros[0])
    try:
        return ler_csv_texto(csv, None, cabecalho=True).collect()
    finally:
        csv.unlink(missing_ok=True)


def _selecionar(df: pl.DataFrame, colunas: dict[str, str], arquivo: str) -> pl.DataFrame:
    ausentes = [c for c in colunas if c not in df.columns]
    if ausentes:
        log.warning("%s: colunas ausentes %s", arquivo, ausentes)
    return df.select(
        [pl.col(c).alias(n) for c, n in colunas.items() if c in df.columns]
        + [
            pl.lit(None, dtype=pl.String).alias(n)
            for c, n in colunas.items()
            if c not in df.columns
        ]
    )


def _valor(col: str) -> pl.Expr:
    return (
        pl.col(col).str.replace_all(r"\.", "").str.replace(",", ".").cast(pl.Float64, strict=False)
    )


def _pseudonimizar_doc(df: pl.DataFrame, col_doc: str, col_nome: str, prefixo: str) -> pl.DataFrame:
    """CPF (11 dígitos) vira máscara + id; CNPJ (14) fica como está."""
    df = df.with_columns(
        pl.col(col_doc).map_elements(somente_digitos, return_dtype=pl.String).alias(col_doc)
    ).with_columns(_eh_pf=pl.col(col_doc).str.len_chars() == 11)
    return (
        pseudonimizar(df, col_doc, col_nome, "_eh_pf")
        .rename(
            {
                "documento": f"{prefixo}_documento",
                "id_pessoa": f"{prefixo}_id_pessoa",
                "nome_normalizado": f"{prefixo}_nome_normalizado",
            }
        )
        .with_columns(
            **{
                f"{prefixo}_tipo": pl.when("_eh_pf")
                .then(pl.lit("PF"))
                .when(pl.col(f"{prefixo}_documento").str.len_chars() == 14)
                .then(pl.lit("PJ"))
            }
        )
        .drop("_eh_pf")
    )


def silver(ctx: Contexto) -> dict[str, pl.DataFrame]:
    """Candidatos, receitas e despesas dos municípios do escopo, todos os anos."""
    codigos, uf = ctx.config.piloto.exigir_escopo()
    # O TSE identifica o município pelo nome (NM_UE); compara pela chave sem espaços.
    chaves = set(escopo.municipios(ctx)["chave"]) if codigos else None
    candidatos: list[pl.DataFrame] = []
    receitas: list[pl.DataFrame] = []
    despesas: list[pl.DataFrame] = []
    for ano in ctx.config.ingestao.tse.anos:
        pasta = ctx.bronze(FONTE, str(ano))
        cand_zip = pasta / urls(ano)["candidatos"].rsplit("/", 1)[-1]
        contas_zip = pasta / urls(ano)["contas"].rsplit("/", 1)[-1]
        if not cand_zip.exists():
            log.warning("Candidatos %s não baixados", ano)
            continue
        bruto = _ler_membro(ctx, cand_zip, f"_{uf}.csv")
        if bruto is None:
            continue
        cand = _selecionar(bruto, COLUNAS_CANDIDATO, cand_zip.name).filter(pl.col("uf") == uf)
        if chaves is not None:
            cand = cand.filter(escopo.chave_expr("nome_ue").is_in(list(chaves)))
            faltam = chaves - set(cand.select(escopo.chave_expr("nome_ue"))["nome_ue"])
            if faltam:
                log.warning("TSE %s: sem candidatos para %s", ano, sorted(faltam))
        candidatos.append(cand)
        sqs = cand["sq_candidato"].unique().implode()
        if contas_zip.exists():
            for sufixo, colunas, destino in (
                (f"receitas_candidatos_{ano}_{uf}.csv", COLUNAS_RECEITA, receitas),
                (f"despesas_contratadas_candidatos_{ano}_{uf}.csv", COLUNAS_DESPESA, despesas),
            ):
                df = _ler_membro(ctx, contas_zip, sufixo)
                if df is not None:
                    destino.append(
                        _selecionar(df, colunas, sufixo)
                        .filter(pl.col("sq_candidato").is_in(sqs))
                        .with_columns(ano_eleicao=pl.lit(str(ano)))
                    )
        else:
            log.warning("Prestação de contas %s não baixada", ano)

    saida = ctx.silver(FONTE)
    resultado: dict[str, pl.DataFrame] = {}
    if candidatos:
        cand = (
            pl.concat(candidatos)
            .with_columns(
                ano_nascimento=pl.col("data_nascimento").str.slice(-4).cast(pl.Int32, strict=False),
                cpf_original=pl.when(pl.col("cpf_original").str.len_chars() >= 11).then(
                    pl.col("cpf_original")
                ),
                _eh_pf=pl.lit(True),
            )
            .drop("data_nascimento")
        )
        cand = (
            pseudonimizar(cand, "cpf_original", "nome", "_eh_pf")
            .rename({"documento": "cpf_mascarado"})
            .drop("_eh_pf")
            .unique()
        )
        resultado["candidatos"] = gravar_df(cand, saida / "candidatos.parquet")
    if receitas:
        rec = pl.concat(receitas).with_columns(
            valor=_valor("valor"), data=pl.col("data").str.to_date("%d/%m/%Y", strict=False)
        )
        rec = _pseudonimizar_doc(rec, "doador_documento_original", "doador_nome", "doador")
        resultado["receitas"] = gravar_df(rec, saida / "receitas.parquet")
    if despesas:
        desp = pl.concat(despesas).with_columns(
            valor=_valor("valor"), data=pl.col("data").str.to_date("%d/%m/%Y", strict=False)
        )
        desp = _pseudonimizar_doc(
            desp, "fornecedor_documento_original", "fornecedor_nome", "fornecedor"
        )
        resultado["despesas"] = gravar_df(desp, saida / "despesas.parquet")
    return resultado


def gravar_df(df: pl.DataFrame, caminho: Path) -> pl.DataFrame:
    """Grava e devolve o DataFrame."""
    gravar_parquet(df, caminho)
    return df

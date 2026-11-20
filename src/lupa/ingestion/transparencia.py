"""Portal da Transparência (CGU): sanções (CEIS, CNEP) e Novo Bolsa Família.

- **Sanções**: arquivos diários em ``/download-de-dados/{ceis|cnep}``; não exigem chave.
  A data disponível é lida da própria página (só o arquivo do dia fica acessível).
  O CEIS publica CPF completo de pessoas físicas: no silver ele é mascarado e só são
  mantidas as PF que correspondem a sócios ou fornecedores do piloto.
- **Novo Bolsa Família**: arquivo mensal nacional em ``/download-de-dados/novo-bolsa-familia``
  (não exige chave). Por minimização, o arquivo é apagado depois da leitura: só os
  beneficiários que correspondem a sócios, doadores ou fornecedores PF de campanha
  (miolo do CPF + nome) são mantidos, como ``id_pessoa``, mês e valor. Um manifesto por
  mês registra a coleta na linhagem.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from lupa.cleaning.documentos import somente_digitos
from lupa.cleaning.texto import normalizar_nome
from lupa.ingestion.arquivos import extrair_utf8, gravar_parquet, ler_csv_texto
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.privacidade import id_pessoa, pseudonimizar

log = logging.getLogger("lupa.ingestion.transparencia")

FONTE = "transparencia"
URL_DOWNLOAD = "https://portaldatransparencia.gov.br/download-de-dados"
CADASTROS_SANCAO = ("ceis", "cnep")

# Cabeçalho normalizado (sem acento, caixa alta) → nome da coluna silver
COLUNAS_SANCAO = {
    "CADASTRO": "cadastro",
    "CODIGO DA SANCAO": "codigo_sancao",
    "TIPO DE PESSOA": "tipo_pessoa",
    "CPF OU CNPJ DO SANCIONADO": "documento_original",
    "NOME DO SANCIONADO": "nome",
    "RAZAO SOCIAL CADASTRO RECEITA": "razao_social_receita",
    "NUMERO DO PROCESSO": "numero_processo",
    "CATEGORIA DA SANCAO": "categoria",
    "VALOR DA MULTA": "valor_multa",
    "DATA INICIO SANCAO": "data_inicio",
    "DATA FINAL SANCAO": "data_fim",
    "DATA PUBLICACAO": "data_publicacao",
    "ABRAGENCIA DA SANCAO": "abrangencia",
    "ORGAO SANCIONADOR": "orgao_sancionador",
    "UF ORGAO SANCIONADOR": "uf_orgao_sancionador",
    "ESFERA ORGAO SANCIONADOR": "esfera_orgao_sancionador",
    "FUNDAMENTACAO LEGAL": "fundamentacao_legal",
}


def data_disponivel(cliente: ClienteHttp, cadastro: str) -> str:
    """Data (``AAAAMMDD``) do arquivo disponível, lida da página de download."""
    html = cliente.get(f"{URL_DOWNLOAD}/{cadastro}").text
    achado = re.search(
        r'"ano"\s*:\s*"(\d{4})"\s*,\s*"mes"\s*:\s*"(\d{2})"\s*,\s*"dia"\s*:\s*"(\d{2})"', html
    )
    if not achado:
        raise RuntimeError(f"Data do arquivo {cadastro} não encontrada na página.")
    return "".join(achado.groups())


def coletar_sancoes(ctx: Contexto, cliente: ClienteHttp) -> list[Path]:
    """Baixa o arquivo do dia de cada cadastro de sanções."""
    caminhos = []
    for cadastro in CADASTROS_SANCAO:
        data = data_disponivel(cliente, cadastro)
        url = f"{URL_DOWNLOAD}/{cadastro}/{data}"
        destino = ctx.bronze(FONTE, cadastro) / f"{data}_{cadastro.upper()}.zip"
        if cliente.baixar(url, destino):
            ctx.linhagem.registrar(FONTE, url, destino, ctx.agora())
        caminhos.append(destino)
    return caminhos


def silver_sancoes(ctx: Contexto, ids_pessoas_relevantes: set[str] | None = None) -> pl.DataFrame:
    """Sanções CEIS + CNEP do arquivo mais recente de cada cadastro.

    PJ: todas (dado de pessoa jurídica). PF: CPF mascarado e, se
    ``ids_pessoas_relevantes`` for informado, só as que estão nesse conjunto.
    """
    pedacos = []
    for cadastro in CADASTROS_SANCAO:
        zips = sorted(ctx.bronze(FONTE, cadastro).glob("*.zip"))
        if not zips:
            log.warning("Sem arquivo de %s no bronze", cadastro)
            continue
        csv = extrair_utf8(zips[-1], ctx.temporario() / f"{zips[-1].stem}.csv")
        try:
            df = ler_csv_texto(csv, None, cabecalho=True).collect()
        finally:
            csv.unlink(missing_ok=True)
        renomear = {
            c: COLUNAS_SANCAO[normalizar_nome(c)]
            for c in df.columns
            if normalizar_nome(c) in COLUNAS_SANCAO
        }
        df = df.select(list(renomear)).rename(renomear)
        for coluna in COLUNAS_SANCAO.values():
            if coluna not in df.columns:
                df = df.with_columns(pl.lit(None, dtype=pl.String).alias(coluna))
        pedacos.append(
            df.select(list(COLUNAS_SANCAO.values())).with_columns(
                arquivo_fonte=pl.lit(f"{URL_DOWNLOAD}/{cadastro}/{zips[-1].name[:8]}")
            )
        )
    if not pedacos:
        return pl.DataFrame()
    df = pl.concat(pedacos).with_columns(
        documento_original=pl.col("documento_original").map_elements(
            somente_digitos, return_dtype=pl.String
        ),
        # A CGU às vezes deixa TIPO DE PESSOA vazio; 11 dígitos é CPF.
        eh_pf=(pl.col("tipo_pessoa") == "F")
        | (
            (pl.col("tipo_pessoa").fill_null("") != "J")
            & (pl.col("documento_original").str.replace_all(r"\D", "").str.len_chars() == 11)
        ),
        valor_multa=pl.col("valor_multa")
        .str.replace_all(r"\.", "")
        .str.replace(",", ".")
        .cast(pl.Float64, strict=False),
        **{
            c: pl.col(c).str.to_date("%d/%m/%Y", strict=False)
            for c in ("data_inicio", "data_fim", "data_publicacao")
        },
    )
    df = pseudonimizar(df, "documento_original", "nome", "eh_pf")
    if ids_pessoas_relevantes is not None:
        df = df.filter(~pl.col("eh_pf") | pl.col("id_pessoa").is_in(sorted(ids_pessoas_relevantes)))
    df = df.with_columns(
        cnpj_raiz=pl.when(~pl.col("eh_pf") & (pl.col("documento").str.len_chars() == 14)).then(
            pl.col("documento").str.slice(0, 8)
        )
    )
    gravar_parquet(df, ctx.silver(FONTE) / "sancoes.parquet")
    return df


# --------------------------------------------------------------------- Bolsa Família


def meses_bolsa_familia(ctx: Contexto) -> list[str]:
    """Meses (AAAAMM) a consultar: os configurados ou todos desde ``bolsa_familia_desde``."""
    cfg = ctx.config.ingestao.transparencia
    if cfg.bolsa_familia_meses:
        return list(cfg.bolsa_familia_meses)
    ano, mes = int(cfg.bolsa_familia_desde[:4]), int(cfg.bolsa_familia_desde[4:])
    hoje = ctx.hoje()
    meses = []
    while (ano, mes) <= (hoje.year, hoje.month):
        meses.append(f"{ano}{mes:02d}")
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


def _assinatura(ids: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def _mes_fechado(mes: str, hoje: date, meses_revisao: int = 3) -> bool:
    """Meses mais antigos que ``meses_revisao`` não mudam mais e não são recoletados."""
    ano, m = int(mes[:4]), int(mes[4:])
    return (hoje.year - ano) * 12 + (hoje.month - m) > meses_revisao


URL_BOLSA_FAMILIA = f"{URL_DOWNLOAD}/novo-bolsa-familia/{{mes}}"
COLUNAS_BOLSA_FAMILIA = [
    "mes_competencia", "mes_referencia", "uf", "municipio_siafi", "municipio", "cpf", "nis",
    "nome", "valor",
]  # fmt: skip


def miolo_expr(coluna: str) -> pl.Expr:
    """Miolo do CPF (6 dígitos) a partir de CPF mascarado ou completo, vetorizado."""
    d = pl.col(coluna).str.replace_all(r"\D", "")
    return pl.when(d.str.len_chars() == 11).then(d.str.slice(3, 6)).otherwise(d)


def nome_expr(coluna: str) -> pl.Expr:
    """Mesmo resultado de :func:`lupa.cleaning.texto.normalizar_nome`, vetorizado."""
    return (
        pl.col(coluna)
        .str.normalize("NFKD")
        .str.replace_all(r"[^\x00-\x7F]", "")
        .str.to_uppercase()
        .str.replace_all(r"[^A-Z0-9 ]", " ")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
    )


def coletar_bolsa_familia(
    ctx: Contexto, cliente: ClienteHttp, alvos: pl.DataFrame, sal: str | None = None
) -> pl.DataFrame:
    """Pagamentos do Novo Bolsa Família só das pessoas em ``alvos``.

    ``alvos`` tem ``id_pessoa``, ``miolo`` (6 dígitos do CPF) e ``nome_normalizado``: sócios
    PF, doadores PF e fornecedores PF de campanha. Usa o arquivo mensal **nacional** do
    Portal da Transparência (um ZIP de ~360 MB por mês): cada linha é comparada por miolo +
    nome e descartada se não coincidir, e cada coincidência é conferida recalculando o
    ``id_pessoa``. Nem o NIS nem os demais beneficiários são gravados, e o arquivo baixado é
    apagado em seguida. Por ser nacional, acha também sócios que moram fora do escopo.

    Retomada: cada mês grava um manifesto e as correspondências. Um mês fechado já lido
    com o mesmo conjunto de alvos não é lido de novo. Mês não publicado é pulado.
    """
    ids_alvo = set(alvos["id_pessoa"].drop_nulls().to_list())
    assinatura = _assinatura(ids_alvo)
    chaves = (
        alvos.select("miolo", "nome_normalizado", "id_pessoa")
        .drop_nulls()
        .unique(["miolo", "nome_normalizado"])
    )
    miolos = chaves["miolo"].unique().implode()
    pasta = ctx.bronze(FONTE, "bolsa_familia")
    linhas: list[dict[str, Any]] = []
    for mes in meses_bolsa_familia(ctx):
        manifesto = pasta / f"{mes}.manifesto.json"
        achados_arq = pasta / f"{mes}.correspondencias.json"
        if manifesto.exists() and achados_arq.exists() and _mes_fechado(mes, ctx.hoje()):
            info = json.loads(manifesto.read_text(encoding="utf-8"))
            if info.get("alvos_sha256") == assinatura and info.get("origem") == "arquivo":
                linhas.extend(json.loads(achados_arq.read_text(encoding="utf-8")))
                continue
        url = URL_BOLSA_FAMILIA.format(mes=mes)
        zip_path = pasta / "_tmp" / f"{mes}.zip"
        csv_path = pasta / "_tmp" / f"{mes}.csv"
        try:
            try:
                cliente.baixar(url, zip_path)
            except httpx.HTTPStatusError as erro:
                if erro.response.status_code in (403, 404):
                    log.info("Bolsa Família %s: arquivo ainda não publicado.", mes)
                    continue
                raise
            extrair_utf8(zip_path, csv_path)
            zip_path.unlink()
            lf = ler_csv_texto(csv_path, COLUNAS_BOLSA_FAMILIA, cabecalho=True)
            n_registros = int(lf.select(pl.len()).collect().item())
            candidatos = (
                lf.filter(miolo_expr("cpf").is_in(miolos))
                .with_columns(miolo=miolo_expr("cpf"), nome_normalizado=nome_expr("nome"))
                .join(chaves.lazy(), on=["miolo", "nome_normalizado"])
                .select(
                    "id_pessoa", "cpf", "nome",
                    mes=pl.col("mes_competencia"),
                    valor=pl.col("valor").str.replace_all(r"\.", "").str.replace(",", ".")
                    .cast(pl.Float64, strict=False),
                )
                .collect()
            )  # fmt: skip
        finally:
            zip_path.unlink(missing_ok=True)
            csv_path.unlink(missing_ok=True)
        achados = [
            {"id_pessoa": r["id_pessoa"], "mes": r["mes"], "valor": r["valor"] or 0.0}
            for r in candidatos.iter_rows(named=True)
            if id_pessoa(r["cpf"], r["nome"], sal) == r["id_pessoa"]
        ]
        if len(achados) != candidatos.height:
            log.warning("Bolsa Família %s: %d coincidências descartadas na conferência do id.",
                        mes, candidatos.height - len(achados))  # fmt: skip
        achados_arq.write_text(json.dumps(achados), encoding="utf-8")
        manifesto.write_text(
            json.dumps(
                {
                    "mes": mes,
                    "origem": "arquivo",
                    "url": url,
                    "registros_lidos": n_registros,
                    "correspondencias": len(achados),
                    "alvos": len(ids_alvo),
                    "alvos_sha256": assinatura,
                    "observacao": "Só correspondências com os alvos são guardadas (LGPD).",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ctx.linhagem.registrar(FONTE, url, manifesto, ctx.agora())
        linhas.extend(achados)
        log.info(
            "Bolsa Família %s: %d registros, %d correspondências", mes, n_registros, len(achados)
        )
    df = (
        pl.DataFrame(linhas, schema={"id_pessoa": pl.String, "mes": pl.String, "valor": pl.Float64})
        .group_by("id_pessoa", "mes")
        .agg(valor_total=pl.col("valor").sum())
        .sort("id_pessoa", "mes")
    )
    gravar_parquet(df, ctx.silver(FONTE) / "bolsa_familia_cruzamentos.parquet")
    return df

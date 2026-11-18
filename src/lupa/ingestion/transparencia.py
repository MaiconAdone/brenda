"""Portal da Transparência (CGU): sanções (CEIS, CNEP) e Novo Bolsa Família.

- **Sanções**: arquivos diários em ``/download-de-dados/{ceis|cnep}``; não exigem chave.
  A data disponível é lida da própria página (só o arquivo do dia fica acessível).
  O CEIS publica CPF completo de pessoas físicas: no silver ele é mascarado e só são
  mantidas as PF que correspondem a sócios ou fornecedores do piloto.
- **Novo Bolsa Família**: API ``/api-de-dados/novo-bolsa-familia-sacado-beneficiario-por-municipio``
  (exige ``PORTAL_TRANSPARENCIA_API_KEY``). Por minimização, as respostas **não** são
  gravadas: só os beneficiários que correspondem a sócios (miolo do CPF + nome) são
  mantidos, como ``id_pessoa`` e mês. Um manifesto por mês registra a coleta na linhagem.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

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
URL_API = "https://api.portaldatransparencia.gov.br/api-de-dados"
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


def chave_api() -> str | None:
    """Chave da API (``PORTAL_TRANSPARENCIA_API_KEY``) ou ``None``."""
    return os.environ.get("PORTAL_TRANSPARENCIA_API_KEY") or None


def coletar_bolsa_familia(
    ctx: Contexto, cliente: ClienteHttp, socios: pl.DataFrame, sal: str | None = None
) -> pl.DataFrame:
    """Marca sócios PF que receberam Novo Bolsa Família nos meses configurados.

    ``socios`` precisa ter ``id_pessoa`` (silver do CNPJ). Só correspondências exatas
    de miolo do CPF + nome normalizado são mantidas.
    """
    municipio, _ = ctx.config.piloto.exigir_definido()
    meses = ctx.config.ingestao.transparencia.bolsa_familia_meses
    destino = ctx.silver(FONTE) / "bolsa_familia_socios.parquet"
    if not meses:
        log.info("Nenhum mês de Bolsa Família configurado; etapa ignorada.")
        return pl.DataFrame(
            schema={"id_pessoa": pl.String, "mes": pl.String, "valor_total": pl.Float64}
        )
    ids_alvo = set(socios["id_pessoa"].drop_nulls().to_list())
    linhas: list[dict[str, Any]] = []
    for mes in meses:
        manifesto = ctx.bronze(FONTE, "bolsa_familia") / f"{mes}.manifesto.json"
        n_registros, achados = 0, []
        pagina = 1
        while True:
            lote = cliente.get_json(
                f"{URL_API}/novo-bolsa-familia-sacado-beneficiario-por-municipio",
                params={"mesAno": mes, "codigoIbge": municipio, "pagina": pagina},
            )
            if not lote:
                break
            for registro in lote:
                n_registros += 1
                benef = registro.get("beneficiarioNovoBolsaFamilia") or {}
                try:
                    pid = id_pessoa(benef.get("cpfFormatado") or "", benef.get("nome") or "", sal)
                except ValueError:
                    continue
                if pid in ids_alvo:
                    achados.append(
                        {
                            "id_pessoa": pid,
                            "mes": mes,
                            "valor": float(registro.get("valorSaque") or 0),
                        }
                    )
            pagina += 1
        manifesto.write_text(
            json.dumps(
                {
                    "mes": mes,
                    "municipio_ibge": municipio,
                    "paginas": pagina - 1,
                    "registros_lidos": n_registros,
                    "correspondencias": len(achados),
                    "observacao": "Respostas não armazenadas (minimização LGPD).",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ctx.linhagem.registrar(
            FONTE,
            f"{URL_API}/novo-bolsa-familia-sacado-beneficiario-por-municipio"
            f"?mesAno={mes}&codigoIbge={municipio}",
            manifesto,
            ctx.agora(),
        )
        linhas.extend(achados)
        log.info("Bolsa Família %s: %d registros, %d sócios", mes, n_registros, len(achados))
    df = (
        pl.DataFrame(linhas, schema={"id_pessoa": pl.String, "mes": pl.String, "valor": pl.Float64})
        .group_by("id_pessoa", "mes")
        .agg(valor_total=pl.col("valor").sum())
        .sort("id_pessoa", "mes")
    )
    gravar_parquet(df, destino)
    return df


def cliente_api(ctx: Contexto) -> ClienteHttp | None:
    """Cliente com a chave da API, ou ``None`` se a chave não estiver configurada."""
    chave = chave_api()
    if not chave:
        log.warning("PORTAL_TRANSPARENCIA_API_KEY não definida: Bolsa Família não será coletado.")
        return None
    return ClienteHttp(
        intervalo_min_s=ctx.config.ingestao.transparencia.intervalo_min_s,
        cabecalhos={"chave-api-dados": chave, "Accept": "application/json"},
    )

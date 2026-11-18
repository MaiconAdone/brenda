"""IBGE: municípios da UF do piloto (normalização e validação do código)."""

from __future__ import annotations

import json

import polars as pl

from lupa.cleaning.texto import normalizar_nome
from lupa.ingestion.arquivos import gravar_parquet
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp

FONTE = "ibge"
URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"


def coletar(ctx: Contexto, cliente: ClienteHttp) -> pl.DataFrame:
    """Baixa os municípios da UF, grava bronze e silver e valida o piloto."""
    municipio, uf = ctx.config.piloto.exigir_definido()
    url = URL.format(uf=uf)
    dados = cliente.get_json(url) or []
    bruto = ctx.bronze(FONTE) / f"municipios_{uf}.json"
    bruto.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    ctx.linhagem.registrar(FONTE, url, bruto, ctx.agora())
    df = pl.DataFrame(
        {
            "municipio_ibge": [str(m["id"]) for m in dados],
            "nome": [m["nome"] for m in dados],
            "nome_normalizado": [normalizar_nome(m["nome"]) for m in dados],
            "uf": [uf] * len(dados),
        },
        schema={
            "municipio_ibge": pl.String,
            "nome": pl.String,
            "nome_normalizado": pl.String,
            "uf": pl.String,
        },
    )
    if municipio not in set(df["municipio_ibge"]):
        raise ValueError(f"Município {municipio} não encontrado no IBGE para {uf}.")
    nome_cfg = ctx.config.piloto.nome_municipio
    nome_ibge = df.filter(pl.col("municipio_ibge") == municipio)["nome_normalizado"][0]
    if nome_cfg and normalizar_nome(nome_cfg) != nome_ibge:
        raise ValueError(f"nome_municipio '{nome_cfg}' difere do IBGE ('{nome_ibge}').")
    gravar_parquet(df, ctx.silver(FONTE) / "municipios.parquet")
    return df

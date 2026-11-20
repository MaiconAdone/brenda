"""Municípios do escopo (um, vários ou a UF inteira) e a correspondência entre bases.

Cada fonte identifica o município de um jeito: IBGE (7 dígitos), Receita (4 dígitos,
tabela própria), TSE e Portal da Transparência (nome). A correspondência é feita pelo
nome normalizado sem espaços nem pontuação, e qualquer município sem par é um erro, para
nada ficar de fora em silêncio.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import polars as pl

from lupa.cleaning.texto import normalizar_nome
from lupa.ingestion.contexto import Contexto

# Nomes que mudaram e ainda aparecem na grafia antiga em alguma base (chave → chave IBGE).
NOMES_ALTERNATIVOS = {
    "EMBU": "EMBUDASARTES",
    "MOJIMIRIM": "MOGIMIRIM",
    "MOJIGUACU": "MOGIGUACU",
    "MOJIDASCRUZES": "MOGIDASCRUZES",
    "SAOLUISDOPARAITINGA": "SAOLUIZDOPARAITINGA",
    "BIRITIBAMIRIM": "BIRITIBAMIRIM",
    "FLORINIA": "FLORINEA",
}


def chave(nome: str) -> str:
    """Chave de comparação de nome de município (sem acento, espaço ou pontuação)."""
    k = normalizar_nome(nome).replace(" ", "")
    return NOMES_ALTERNATIVOS.get(k, k)


def chave_expr(coluna: str) -> pl.Expr:
    """Mesma chave como expressão Polars."""
    return pl.col(coluna).map_elements(chave, return_dtype=pl.String)


def municipios(ctx: Contexto) -> pl.DataFrame:
    """Municípios do escopo: ``municipio_ibge``, ``nome``, ``nome_normalizado``, ``uf``,
    ``chave``. Exige a silver do IBGE (``python -m lupa.ingestion ibge``)."""
    codigos, uf = ctx.config.piloto.exigir_escopo()
    arquivo = ctx.silver("ibge") / "municipios.parquet"
    if not arquivo.exists():
        raise RuntimeError("Rode a ingestão do IBGE antes: ela define os municípios do escopo.")
    df = pl.read_parquet(arquivo).filter(pl.col("uf") == uf)
    if codigos:
        faltam = sorted(set(codigos) - set(df["municipio_ibge"]))
        if faltam:
            raise ValueError(f"Municípios não encontrados no IBGE para {uf}: {faltam}")
        df = df.filter(pl.col("municipio_ibge").is_in(codigos))
    return df.with_columns(chave=chave_expr("nome")).sort("municipio_ibge")


def correspondencia(
    escopo: pl.DataFrame, outra: Mapping[str, str] | Iterable[tuple[str, str]], base: str
) -> dict[str, str]:
    """Liga cada município do escopo ao código de outra base pelo nome.

    ``outra`` é uma sequência ``(codigo, nome)``. Devolve ``{municipio_ibge: codigo}`` e
    falha se algum município do escopo ficar sem par ou com mais de um.
    """
    pares = outra.items() if isinstance(outra, Mapping) else outra
    por_chave: dict[str, list[str]] = {}
    for codigo, nome in pares:
        por_chave.setdefault(chave(nome), []).append(codigo)
    saida: dict[str, str] = {}
    problemas = []
    for ibge, k, nome in escopo.select("municipio_ibge", "chave", "nome").iter_rows():
        achados = por_chave.get(k, [])
        if len(achados) != 1:
            problemas.append(f"{nome} ({ibge}): {len(achados)} correspondências")
        else:
            saida[ibge] = achados[0]
    if problemas:
        raise ValueError(f"Municípios sem correspondência única em {base}: {problemas}")
    return saida


def codigos_homonimos(
    escopo: pl.DataFrame, outra: Iterable[tuple[str, str]], base: str
) -> list[str]:
    """Todos os códigos de ``outra`` com o nome de algum município do escopo.

    Para bases sem UF na tabela de municípios (ex.: Receita, em que "Santo André" existe em
    SP e na PB). Só serve quando a UF é filtrada à parte; falha se algum município ficar
    sem nenhum par.
    """
    por_chave: dict[str, list[str]] = {}
    for codigo, nome in outra:
        por_chave.setdefault(chave(nome), []).append(codigo)
    faltam = [
        f"{nome} ({ibge})"
        for ibge, k, nome in escopo.select("municipio_ibge", "chave", "nome").iter_rows()
        if k not in por_chave
    ]
    if faltam:
        raise ValueError(f"Municípios sem correspondência em {base}: {faltam}")
    return sorted({c for k in escopo["chave"] for c in por_chave[k]})

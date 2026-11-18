"""Identificação pseudonimizada de pessoas físicas (LGPD: minimização).

Pessoas são identificadas por um hash com sal secreto de (miolo do CPF + nome
normalizado). O miolo sozinho não identifica ninguém: a Receita publica só 6 dos 11
dígitos, e o mesmo miolo aparece em vários CPFs. Por isso o nome entra no hash, e
duas pessoas só são consideradas a mesma quando miolo e nome coincidem. Ligações mais
fracas passam pela resolução de entidades, sempre com score de confiança.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import polars as pl

from lupa.cleaning.documentos import mascarar_cpf, miolo_cpf
from lupa.cleaning.texto import normalizar_nome


class SalAusenteError(RuntimeError):
    """``LUPA_SALT`` não foi definido no ambiente."""


def _sal(sal: str | None) -> bytes:
    valor = sal if sal is not None else os.environ.get("LUPA_SALT", "")
    if len(valor) < 16:
        raise SalAusenteError("Defina LUPA_SALT (mínimo 16 caracteres) no .env.")
    return valor.encode("utf-8")


def id_pessoa(cpf: str, nome: str, sal: str | None = None) -> str:
    """Identificador estável e não reversível de uma pessoa física."""
    chave = f"{miolo_cpf(cpf)}|{normalizar_nome(nome)}".encode()
    return hmac.new(_sal(sal), chave, hashlib.sha256).hexdigest()[:32]


def pseudonimizar(
    df: pl.DataFrame,
    col_documento: str,
    col_nome: str,
    col_eh_pf: str | None = None,
    sal: str | None = None,
) -> pl.DataFrame:
    """Substitui o CPF de pessoas físicas por CPF mascarado + ``id_pessoa``.

    Linhas de pessoa jurídica (``col_eh_pf`` falso) mantêm o CNPJ em ``documento``.
    A coluna original ``col_documento`` é removida. Colunas criadas:
    ``documento`` (CNPJ ou CPF mascarado), ``id_pessoa`` (só PF) e ``nome_normalizado``.
    """
    chave = _sal(sal).decode("utf-8")
    eh_pf = pl.col(col_eh_pf) if col_eh_pf else pl.lit(True)
    pares = df.filter(eh_pf).select(col_documento, col_nome).unique().drop_nulls()
    mapa: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for doc, nome in pares.iter_rows():
        try:
            mapa[(doc, nome)] = (mascarar_cpf(doc), id_pessoa(doc, nome, chave))
        except ValueError:
            mapa[(doc, nome)] = (None, None)
    tabela = pl.DataFrame(
        {
            col_documento: [k[0] for k in mapa],
            col_nome: [k[1] for k in mapa],
            "_mascara": [v[0] for v in mapa.values()],
            "id_pessoa": [v[1] for v in mapa.values()],
        },
        schema={
            col_documento: pl.String,
            col_nome: pl.String,
            "_mascara": pl.String,
            "id_pessoa": pl.String,
        },
    )
    return (
        df.join(tabela, on=[col_documento, col_nome], how="left", nulls_equal=False)
        .with_columns(
            documento=pl.when(eh_pf).then(pl.col("_mascara")).otherwise(pl.col(col_documento)),
            id_pessoa=pl.when(eh_pf).then(pl.col("id_pessoa")).otherwise(None),
            nome_normalizado=pl.col(col_nome).map_elements(normalizar_nome, return_dtype=pl.String),
        )
        .drop(col_documento, "_mascara")
    )

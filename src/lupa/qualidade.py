"""Testes de qualidade de dados das tabelas silver.

Cada tabela tem uma especificação declarativa (``ESPECIFICACOES``). As verificações
devolvem falhas em vez de levantar exceção, para que o orquestrador possa registrá-las
como *asset checks* e o relatório mostre tudo de uma vez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from lupa.cleaning.documentos import cnpj_valido

_CPF_ABERTO = r"^\d{11}$|^\d{3}\.\d{3}\.\d{3}-\d{2}$"


@dataclass(frozen=True)
class Falha:
    """Violação de uma regra de qualidade."""

    tabela: str
    regra: str
    detalhe: str
    linhas: int


@dataclass(frozen=True)
class Especificacao:
    """Regras de qualidade de uma tabela."""

    chave: tuple[str, ...] = ()
    nao_nulos: tuple[str, ...] = ()
    cnpj: tuple[str, ...] = ()
    nao_negativos: tuple[str, ...] = ()
    datas: tuple[str, ...] = ()
    sem_cpf_aberto: tuple[str, ...] = ()
    minimo_linhas: int = 1
    # CNPJ só é validado quando a coluna de condição for verdadeira (ex.: PJ)
    cnpj_condicao: dict[str, str] = field(default_factory=dict)


DATA_MINIMA = date(1900, 1, 1)


def verificar(df: pl.DataFrame, tabela: str, esp: Especificacao) -> list[Falha]:
    """Aplica a especificação e devolve as falhas encontradas."""
    falhas: list[Falha] = []

    def falha(regra: str, detalhe: str, linhas: int) -> None:
        if linhas:
            falhas.append(Falha(tabela, regra, detalhe, linhas))

    falha(
        "minimo_linhas",
        f"esperado >= {esp.minimo_linhas}, obtido {df.height}",
        int(df.height < esp.minimo_linhas),
    )
    colunas = set(df.columns)
    exigidas = {
        *esp.chave,
        *esp.nao_nulos,
        *esp.cnpj,
        *esp.nao_negativos,
        *esp.datas,
        *esp.sem_cpf_aberto,
    }
    for ausente in sorted(exigidas - colunas):
        falha("coluna_ausente", ausente, 1)
    if df.is_empty():
        return falhas

    if esp.chave and set(esp.chave) <= colunas:
        duplicadas = df.height - df.select(esp.chave).unique().height
        falha("chave_unica", ",".join(esp.chave), duplicadas)
    for c in esp.nao_nulos:
        if c in colunas:
            falha("nao_nulo", c, df[c].null_count())
    for c in esp.cnpj:
        if c in colunas:
            sub = df
            if c in esp.cnpj_condicao:
                sub = df.filter(pl.col(esp.cnpj_condicao[c]))
            invalidos = sum(1 for v in sub[c].drop_nulls() if not cnpj_valido(v))
            falha("cnpj_valido", c, invalidos)
    for c in esp.nao_negativos:
        if c in colunas:
            falha("nao_negativo", c, df.filter(pl.col(c) < 0).height)
    hoje = date.today()
    for c in esp.datas:
        if c in colunas:
            serie = df[c].drop_nulls()
            if serie.dtype == pl.Datetime:
                serie = serie.dt.date()
            if serie.dtype == pl.Date:
                fora = serie.filter((serie < DATA_MINIMA) | (serie > date(hoje.year + 30, 12, 31)))
                falha("data_plausivel", c, fora.len())
    for c in esp.sem_cpf_aberto:
        if c in colunas:
            falha(
                "sem_cpf_aberto",
                c,
                df.filter(pl.col(c).cast(pl.String).str.contains(_CPF_ABERTO)).height,
            )
    return falhas


ESPECIFICACOES: dict[str, Especificacao] = {
    "ibge/municipios": Especificacao(chave=("municipio_ibge",), nao_nulos=("nome",)),
    "pncp/contratacoes": Especificacao(
        chave=("numero_controle_pncp",),
        nao_nulos=("orgao_cnpj", "modalidade_id", "data_publicacao", "url_fonte"),
        cnpj=("orgao_cnpj",),
        nao_negativos=("valor_estimado", "valor_homologado"),
        datas=("data_publicacao",),
    ),
    "pncp/itens": Especificacao(
        chave=("numero_controle_pncp", "numero_item"),
        nao_negativos=("quantidade", "valor_total"),
    ),
    "pncp/resultados": Especificacao(
        chave=("numero_controle_pncp", "numero_item", "sequencial_resultado"),
        nao_nulos=("fornecedor_documento",),
        nao_negativos=("valor_total_homologado",),
        sem_cpf_aberto=("fornecedor_documento",),
        minimo_linhas=0,
    ),
    "pncp/contratos": Especificacao(
        chave=("numero_controle_pncp",),
        nao_nulos=("fornecedor_documento", "orgao_cnpj"),
        cnpj=("orgao_cnpj",),
        nao_negativos=("valor_global",),
        datas=("data_assinatura", "data_vigencia_inicio"),
        sem_cpf_aberto=("fornecedor_documento",),
        minimo_linhas=0,
    ),
    "receita_cnpj/estabelecimentos": Especificacao(
        chave=("cnpj",), nao_nulos=("cnpj_basico", "uf"), cnpj=("cnpj",),
        datas=("data_inicio_atividade",),
    ),
    "receita_cnpj/empresas": Especificacao(
        chave=("cnpj_basico",), nao_nulos=("razao_social",), nao_negativos=("capital_social",),
    ),
    "receita_cnpj/socios": Especificacao(
        nao_nulos=("cnpj_basico", "nome_socio"),
        sem_cpf_aberto=("socio_documento",),
        datas=("data_entrada_sociedade",),
    ),
    "receita_cnpj/simples": Especificacao(chave=("cnpj_basico",), minimo_linhas=0),
    "transparencia/sancoes": Especificacao(
        nao_nulos=("cadastro", "categoria"),
        sem_cpf_aberto=("documento",),
        datas=("data_inicio",),
    ),
    "tse/candidatos": Especificacao(nao_nulos=("sq_candidato", "nome", "cargo"),
                                    sem_cpf_aberto=("cpf_mascarado",)),
    "tse/receitas": Especificacao(nao_nulos=("sq_candidato",), nao_negativos=("valor",),
                                  sem_cpf_aberto=("doador_documento",), minimo_linhas=0),
    "tse/despesas": Especificacao(nao_nulos=("sq_candidato",), nao_negativos=("valor",),
                                  sem_cpf_aberto=("fornecedor_documento",), minimo_linhas=0),
}  # fmt: skip


def verificar_tabela(nome: str, df: pl.DataFrame) -> list[Falha]:
    """Verifica uma tabela silver pelo nome ``fonte/tabela``."""
    return verificar(df, nome, ESPECIFICACOES[nome])

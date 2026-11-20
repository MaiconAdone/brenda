"""Etapas de ingestão encadeadas, usadas pelo Dagster e pela linha de comando.

Ordem de dependência::

    ibge ─┐
    pncp ─┴─► receita_cnpj ─► sancoes
    tse ──────────────┴──────────► bolsa_familia
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from lupa.ingestion import ibge, pncp, receita_cnpj, transparencia, tse
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.qualidade import Falha, verificar_tabela

log = logging.getLogger("lupa.ingestion")


@dataclass
class Resultado:
    """Tabelas produzidas por uma etapa e falhas de qualidade encontradas."""

    tabelas: dict[str, pl.DataFrame]
    falhas: list[Falha]

    def resumo(self) -> dict[str, int]:
        """Número de linhas por tabela."""
        return {nome: df.height for nome, df in self.tabelas.items()}


def _verificar(tabelas: dict[str, pl.DataFrame]) -> Resultado:
    falhas = [f for nome, df in tabelas.items() for f in verificar_tabela(nome, df)]
    for f in falhas:
        log.warning("Qualidade: %s %s %s (%d linhas)", f.tabela, f.regra, f.detalhe, f.linhas)
    return Resultado(tabelas, falhas)


def etapa_ibge(ctx: Contexto) -> Resultado:
    """Municípios do IBGE e validação do código do piloto."""
    with ClienteHttp() as cliente:
        return _verificar({"ibge/municipios": ibge.coletar(ctx, cliente)})


def etapa_pncp(ctx: Contexto) -> Resultado:
    """Contratações, itens, resultados e contratos."""
    cfg = ctx.config.ingestao.pncp
    # A API de consulta limita requisições por IP; a de itens/resultados, não. Clientes
    # separados para que o ritmo reduzido de uma não freie a outra.
    with ClienteHttp(intervalo_min_s=cfg.intervalo_min_s) as consulta:
        pncp.coletar_contratacoes(ctx, consulta)
        with ClienteHttp(intervalo_min_s=cfg.intervalo_itens_s) as api:
            pncp.coletar_itens(ctx, api)
        pncp.coletar_contratos(ctx, consulta)
    itens, resultados = pncp.silver_itens_resultados(ctx)
    return _verificar(
        {
            "pncp/contratacoes": pncp.silver_contratacoes(ctx),
            "pncp/itens": itens,
            "pncp/resultados": resultados,
            "pncp/contratos": pncp.silver_contratos(ctx),
        }
    )


def etapa_receita_cnpj(ctx: Contexto) -> Resultado:
    """CNPJ do mês mais recente, filtrado pelo piloto e pelos fornecedores do PNCP."""
    with ClienteHttp() as cliente:
        mes = receita_cnpj.coletar(ctx, cliente)
    tabelas = {
        f"receita_cnpj/{n}": df
        for n, df in receita_cnpj.silver(ctx, mes, pncp.cnpjs_fornecedores(ctx)).items()
    }
    verificadas = ("estabelecimentos", "empresas", "socios", "simples")
    resultado = _verificar({f"receita_cnpj/{n}": tabelas[f"receita_cnpj/{n}"] for n in verificadas})
    resultado.tabelas.update(tabelas)
    return resultado


def _ids_relevantes(ctx: Contexto) -> set[str]:
    ids: set[str] = set()
    for caminho, coluna in (
        (ctx.silver("receita_cnpj") / "socios.parquet", "id_pessoa"),
        (ctx.silver("pncp") / "resultados.parquet", "fornecedor_id_pessoa"),
        (ctx.silver("pncp") / "contratos.parquet", "fornecedor_id_pessoa"),
    ):
        if caminho.exists():
            ids |= set(pl.read_parquet(caminho, columns=[coluna])[coluna].drop_nulls().to_list())
    return ids


def etapa_sancoes(ctx: Contexto) -> Resultado:
    """CEIS e CNEP; PF só se forem sócios ou fornecedores do piloto."""
    with ClienteHttp() as cliente:
        transparencia.coletar_sancoes(ctx, cliente)
    return _verificar(
        {"transparencia/sancoes": transparencia.silver_sancoes(ctx, _ids_relevantes(ctx))}
    )


def alvos_bolsa_familia(ctx: Contexto) -> pl.DataFrame:
    """Pessoas a procurar no Bolsa Família: sócios PF, doadores PF e fornecedores PF de campanha.

    Colunas ``id_pessoa``, ``miolo`` e ``nome_normalizado`` (o CPF já vem mascarado da silver).
    """
    partes = []
    for caminho, doc, nome, pid in (
        (ctx.silver("receita_cnpj") / "socios.parquet", "socio_documento", "nome_normalizado",
         "id_pessoa"),
        (ctx.silver("tse") / "receitas.parquet", "doador_documento", "doador_nome_normalizado",
         "doador_id_pessoa"),
        (ctx.silver("tse") / "despesas.parquet", "fornecedor_documento",
         "fornecedor_nome_normalizado", "fornecedor_id_pessoa"),
    ):  # fmt: skip
        if caminho.exists():
            df = pl.read_parquet(caminho, columns=[doc, nome, pid])
            partes.append(
                df.select(
                    id_pessoa=pl.col(pid),
                    miolo=transparencia.miolo_expr(doc),
                    nome_normalizado=pl.col(nome),
                )
            )
    vazio = pl.DataFrame(
        schema={"id_pessoa": pl.String, "miolo": pl.String, "nome_normalizado": pl.String}
    )
    if not partes:
        return vazio
    return (
        pl.concat(partes)
        .drop_nulls()
        .filter(pl.col("miolo").str.len_chars() == 6)
        .unique("id_pessoa")
        .sort("id_pessoa")
    )


def etapa_bolsa_familia(ctx: Contexto) -> Resultado:
    """Sócios, doadores e fornecedores PF de campanha que receberam Novo Bolsa Família."""
    alvos = alvos_bolsa_familia(ctx)
    if alvos.is_empty():
        return Resultado({}, [])
    with ClienteHttp() as cliente:
        df = transparencia.coletar_bolsa_familia(ctx, cliente, alvos)
    return _verificar({"transparencia/bolsa_familia_cruzamentos": df})


def etapa_tse(ctx: Contexto) -> Resultado:
    """Candidatos, receitas e despesas de campanha do município."""
    with ClienteHttp() as cliente:
        tse.coletar(ctx, cliente)
    return _verificar({f"tse/{n}": df for n, df in tse.silver(ctx).items()})


ETAPAS: dict[str, Callable[[Contexto], Resultado]] = {
    "ibge": etapa_ibge,
    "pncp": etapa_pncp,
    "tse": etapa_tse,
    "receita_cnpj": etapa_receita_cnpj,
    "sancoes": etapa_sancoes,
    "bolsa_familia": etapa_bolsa_familia,
}

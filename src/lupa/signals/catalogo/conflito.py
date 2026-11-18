"""Sinais de conflito de interesse (C01 parcial, C03)."""

from __future__ import annotations

from typing import ClassVar

import polars as pl

from lupa.signals.base import Alerta, Categoria, Sinal, registrar
from lupa.signals.catalogo._util import montar, reais
from lupa.signals.dados import ELEITOS, URL_RECEITA, URL_TSE, Tabelas, expandir_pessoas, fonte


def _eleitas(t: Tabelas) -> pl.DataFrame | None:
    candidaturas = t.get("grafo/nos/Candidatura")
    if candidaturas is None:
        return None
    return candidaturas.filter(pl.col("resultado").is_in(ELEITOS)).select(
        candidatura="id", candidato="nome", cargo="cargo", ano="ano_eleicao", partido="partido"
    )


def _fornecedoras(t: Tabelas) -> pl.DataFrame | None:
    partes = [
        df.select("entidade", "url_fonte", "valor")
        for nome in ("derivado/vitorias", "derivado/contratos")
        if (df := t.get(nome)) is not None
        and not (df := df.rename({"valor_global": "valor"}, strict=False)).is_empty()
    ]
    if not partes:
        return None
    return (
        pl.concat(partes)
        .filter(pl.col("entidade").str.starts_with("Empresa:"))
        .group_by("entidade")
        .agg(
            pl.col("valor").sum().alias("total_fornecido"),
            pl.col("url_fonte").sort_by("valor", descending=True).first(),
        )
    )


@registrar
class C01SocioAgentePolitico(Sinal):
    """C01 (parcial): sócio da fornecedora foi eleito no município."""

    id: ClassVar[str] = "C01"
    nome: ClassVar[str] = "Sócio é agente político eleito no município"
    descricao: ClassVar[str] = (
        "Um sócio da fornecedora foi eleito prefeito, vice ou vereador no município do piloto."
    )
    categoria: ClassVar[Categoria] = Categoria.CONFLITO_INTERESSE
    peso_padrao: ClassVar[float] = 20
    limitacoes: ClassVar[str] = (
        "Versão parcial do C01: cobre agentes eleitos (TSE), não servidores, que não têm "
        "fonte aberta no município. Candidatos de 2024 não têm CPF publicado; a ligação "
        "usa a resolução de entidades e herda a probabilidade."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        eleitas = _eleitas(t)
        candidato_em = t.get("grafo/arestas/CANDIDATO_EM")
        socios = t.get("derivado/socios")
        fornecedoras = _fornecedoras(t)
        if eleitas is None or candidato_em is None or socios is None or fornecedoras is None:
            return []
        eleitos = candidato_em.select(
            socio=pl.lit("Pessoa:") + pl.col("origem"), candidatura="destino"
        ).join(eleitas, on="candidatura")
        eleitos = expandir_pessoas(t, eleitos, "socio")
        df = (
            socios.filter(pl.col("eh_pf"))
            .join(eleitos, on="socio")
            .join(fornecedoras, left_on="empresa", right_on="entidade")
            .sort("confianca", descending=True)
            .unique(["empresa", "socio", "candidatura"], keep="first")
        )
        return montar(
            self.id, df,
            lambda r: (
                f"Sócio {r['nome_socio']} ({r['documento']}) foi eleito {r['cargo'].lower()} "
                f"em {r['ano']} ({r['partido']}); a empresa forneceu {reais(r['total_fornecido'])}."
                + (f" Ligação provável: {r['via']}." if r["via"] else "")
            ),
            lambda r: (
                fonte(t, "receita_cnpj", URL_RECEITA),
                fonte(t, "tse", URL_TSE),
                fonte(t, "pncp", r["url_fonte"]),
            ),
            ["socio", "candidatura", "cargo", "ano", "confianca"],
            coluna_entidade="empresa",
            coluna_confianca="confianca",
        )  # fmt: skip


@registrar
class C03CampanhaDeEleito(Sinal):
    """C03: fornecedora ou sócio financiou ou prestou serviço à campanha de um eleito."""

    id: ClassVar[str] = "C03"
    nome: ClassVar[str] = "Doação ou serviço à campanha de agente eleito"
    descricao: ClassVar[str] = (
        "Um sócio da fornecedora doou para a campanha de quem foi eleito no município, ou a "
        "empresa ou um sócio prestou serviço a essa campanha."
    )
    categoria: ClassVar[Categoria] = Categoria.CONFLITO_INTERESSE
    peso_padrao: ClassVar[float] = 12
    limitacoes: ClassVar[str] = (
        "Doação é legal; o sinal aponta proximidade, não irregularidade. Doadores PF têm "
        "CPF publicado (ligação exata); quando passa pela resolução de entidades, herda a "
        "probabilidade."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        eleitas = _eleitas(t)
        socios = t.get("derivado/socios")
        fornecedoras = _fornecedoras(t)
        if eleitas is None or socios is None or fornecedoras is None:
            return []
        partes = []
        for tipo, verbo in (("DOOU_PARA", "doou"), ("FORNECEU_CAMPANHA", "prestou serviço por")):
            arestas = t.get(f"grafo/arestas/{tipo}")
            if arestas is None:
                continue
            ligadas = arestas.join(eleitas, left_on="destino", right_on="candidatura").with_columns(
                agente=pl.col("rotulo_origem") + pl.lit(":") + pl.col("origem"),
                verbo=pl.lit(verbo),
            )
            # Pessoa: via quadro societário (com equivalências prováveis).
            pessoas = expandir_pessoas(
                t, ligadas.filter(pl.col("rotulo_origem") == "Pessoa"), "agente"
            ).join(socios.filter(pl.col("eh_pf")), left_on="agente", right_on="socio")
            partes.append(
                pessoas.select(
                    "empresa", "agente", "verbo", "valor", "candidato", "cargo", "ano",
                    "confianca", "via", quem=pl.lit("Sócio ") + pl.col("nome_socio"),
                )
            )  # fmt: skip
            # Empresa: a própria fornecedora.
            empresas = ligadas.filter(pl.col("rotulo_origem") == "Empresa")
            partes.append(
                empresas.select(
                    empresa="agente", agente="agente", verbo="verbo", valor="valor",
                    candidato="candidato", cargo="cargo", ano="ano",
                    confianca=pl.lit(1.0), via=pl.lit(None, dtype=pl.String),
                    quem=pl.lit("A empresa"),
                )
            )  # fmt: skip
        if not partes:
            return []
        df = (
            pl.concat(partes, how="diagonal_relaxed")
            .join(fornecedoras, left_on="empresa", right_on="entidade")
            .sort("confianca", descending=True)
            .unique(["empresa", "agente", "verbo", "candidato", "ano"], keep="first")
            .sort("empresa", "ano")
        )
        return montar(
            self.id, df,
            lambda r: (
                f"{r['quem']} {r['verbo']} {reais(r['valor'])} na campanha de {r['candidato']} "
                f"({r['cargo'].lower()} eleito em {r['ano']}); a empresa forneceu "
                f"{reais(r['total_fornecido'])}."
                + (f" Ligação provável: {r['via']}." if r["via"] else "")
            ),
            lambda r: (
                fonte(t, "tse", URL_TSE),
                fonte(t, "receita_cnpj", URL_RECEITA),
                fonte(t, "pncp", r["url_fonte"]),
            ),
            ["agente", "verbo", "valor", "candidato", "ano", "confianca"],
            coluna_entidade="empresa",
            coluna_confianca="confianca",
        )  # fmt: skip

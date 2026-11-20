"""Cruzamentos com o Novo Bolsa Família (L01, L05, L06, L07, L08).

O benefício em si é legal e não é suspeito. O que se aponta é a combinação: alguém de
baixa renda à frente de uma empresa que vende ao poder público, ou financiando e
prestando serviço a campanhas eleitorais, que é o perfil clássico de "laranja".

Só são usadas as pessoas que coincidem com sócios, doadores ou fornecedores de campanha
(a coleta descarta os demais beneficiários). Nos sinais de campanha (L07 e L08), a
entidade do alerta é a **candidatura**, não o beneficiário.
"""

from __future__ import annotations

from typing import Any, ClassVar

import polars as pl

from lupa.signals.base import Alerta, Categoria, Fonte, Sinal, registrar
from lupa.signals.catalogo._util import data_br, montar, reais
from lupa.signals.dados import (
    URL_BOLSA_FAMILIA,
    URL_RECEITA,
    URL_TSE,
    Tabelas,
    elegiveis,
    expandir_pessoas,
    fonte,
)


def _mes_br(mes: str) -> str:
    return f"{mes[4:]}/{mes[:4]}"


def _beneficio(r: dict[str, Any]) -> str:
    periodo = (
        _mes_br(r["primeiro_mes"])
        if r["primeiro_mes"] == r["ultimo_mes"]
        else f"{_mes_br(r['primeiro_mes'])} a {_mes_br(r['ultimo_mes'])}"
    )
    return (
        f"recebeu Novo Bolsa Família em {r['n_meses']} mês(es) ({periodo}), "
        f"total de {reais(r['total_recebido'])}"
    )


def _ligacao(r: dict[str, Any]) -> str:
    return f" Ligação provável: {r['via']}." if r.get("via") else ""


def _beneficiarios(t: Tabelas) -> pl.DataFrame | None:
    """Benefícios por pessoa, incluindo as equivalências prováveis do Splink."""
    beneficios = t.get("derivado/beneficios")
    if beneficios is None or beneficios.is_empty():
        return None
    return expandir_pessoas(t, beneficios, "pessoa")


def _socios_beneficiarios(t: Tabelas) -> pl.DataFrame | None:
    beneficiarios = _beneficiarios(t)
    socios = t.get("derivado/socios")
    v = t.get("derivado/vitorias")
    if beneficiarios is None or socios is None or v is None:
        return None
    vitorias = elegiveis(v)
    totais = vitorias.group_by("entidade").agg(
        pl.col("valor").sum().alias("total_vencido"),
        pl.col("data").dt.strftime("%Y%m").unique().alias("meses_vitoria"),
        pl.col("url_fonte").sort_by("valor", descending=True).first(),
    )
    return (
        socios.filter(pl.col("eh_pf"))
        .join(beneficiarios, left_on="socio", right_on="pessoa")
        .join(totais, left_on="empresa", right_on="entidade")
        .with_columns(
            meses_sobrepostos=pl.col("meses").list.set_intersection("meses_vitoria").list.len()
        )
        .sort("confianca", descending=True)
        .unique(["empresa", "socio"], keep="first")
        .sort("empresa", "socio")
    )


def _fontes_socio(t: Tabelas, r: dict[str, Any]) -> tuple[Fonte, ...]:
    return (
        fonte(t, "transparencia", URL_BOLSA_FAMILIA),
        fonte(t, "receita_cnpj", URL_RECEITA),
        fonte(t, "pncp", r["url_fonte"]),
    )


def _descricao_socio(r: dict[str, Any]) -> str:
    sobreposicao = (
        f" Recebia o benefício em {r['meses_sobrepostos']} mês(es) em que a empresa venceu "
        "licitação."
        if r["meses_sobrepostos"]
        else ""
    )
    return (
        f"Sócio {r['nome_socio']} ({r['documento']}) {_beneficio(r)}; a empresa venceu "
        f"{reais(r['total_vencido'])}.{sobreposicao}{_ligacao(r)}"
    )


_DADOS_SOCIO = ["socio", "n_meses", "primeiro_mes", "ultimo_mes", "total_recebido",
                "total_vencido", "meses_sobrepostos", "confianca"]  # fmt: skip


@registrar
class L01SocioBeneficiario(Sinal):
    """L01: sócio da fornecedora recebeu Bolsa Família."""

    id: ClassVar[str] = "L01"
    nome: ClassVar[str] = "Sócio beneficiário do Bolsa Família"
    descricao: ClassVar[str] = (
        "Um sócio pessoa física da fornecedora recebeu Novo Bolsa Família no período."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 12
    limitacoes: ClassVar[str] = (
        "Ligação por miolo do CPF + nome (o Portal mascara o CPF). Microempreendedores de "
        "baixa renda podem receber o benefício legalmente; o sinal só pede atenção."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        df = _socios_beneficiarios(t)
        if df is None:
            return []
        return montar(
            self.id, df, _descricao_socio, lambda r: _fontes_socio(t, r), _DADOS_SOCIO,
            coluna_entidade="empresa", coluna_confianca="confianca",
        )  # fmt: skip


@registrar
class L05SocioBeneficiarioAltoValor(Sinal):
    """L05: sócio beneficiário em empresa que venceu valores altos."""

    id: ClassVar[str] = "L05"
    nome: ClassVar[str] = "Sócio beneficiário em empresa de alto valor"
    descricao: ClassVar[str] = (
        "Um sócio da fornecedora recebeu Bolsa Família e a empresa venceu valores elevados."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 8
    limitacoes: ClassVar[str] = "Complementa o L01; mesmas limitações de ligação."

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        valor_min = float(self.parametros.get("valor_min_contratos", 500_000))
        df = _socios_beneficiarios(t)
        if df is None:
            return []
        df = df.filter(pl.col("total_vencido") >= valor_min)
        return montar(
            self.id, df, _descricao_socio, lambda r: _fontes_socio(t, r), _DADOS_SOCIO,
            coluna_entidade="empresa", coluna_confianca="confianca",
        )  # fmt: skip


@registrar
class L06BeneficiarioEntrouAntesDaLicitacao(Sinal):
    """L06: beneficiário entrou como sócio pouco antes de uma vitória."""

    id: ClassVar[str] = "L06"
    nome: ClassVar[str] = "Beneficiário entrou como sócio antes de licitação"
    descricao: ClassVar[str] = (
        "Uma pessoa que recebeu Bolsa Família entrou no quadro societário pouco antes de a "
        "empresa vencer uma licitação."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 10
    limitacoes: ClassVar[str] = (
        "Só há a foto atual do QSA; a data de entrada é a declarada à Receita."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        janela = int(self.parametros.get("janela_dias", 90))
        beneficiarios = _beneficiarios(t)
        socios = t.get("derivado/socios")
        v = t.get("derivado/vitorias")
        if beneficiarios is None or socios is None or v is None:
            return []
        df = (
            socios.filter(pl.col("eh_pf"))
            .join(beneficiarios, left_on="socio", right_on="pessoa")
            .join(elegiveis(v), left_on="empresa", right_on="entidade")
            .with_columns(dias=(pl.col("data") - pl.col("data_entrada")).dt.total_days())
            .filter(pl.col("dias").is_between(0, janela))
            .sort("confianca", descending=True)
            .unique(["empresa", "socio", "numero_controle_pncp"], keep="first")
            .sort("empresa", "data", "socio")
        )
        return montar(
            self.id, df,
            lambda r: (
                f"Sócio {r['nome_socio']} ({r['documento']}) entrou em "
                f"{data_br(r['data_entrada'])}, {r['dias']} dias antes da vitória de "
                f"{data_br(r['data'])} ({r['orgao_nome']}, {reais(r['valor'])}), e "
                f"{_beneficio(r)}.{_ligacao(r)}"
            ),
            lambda r: _fontes_socio(t, r),
            ["socio", "data_entrada", "data", "dias", "numero_controle_pncp", "confianca"],
            coluna_entidade="empresa", coluna_confianca="confianca",
        )  # fmt: skip


def _campanha(t: Tabelas, tipo: str) -> pl.DataFrame | None:
    beneficiarios = _beneficiarios(t)
    arestas = t.get(f"grafo/arestas/{tipo}")
    pessoas = t.get("grafo/nos/Pessoa")
    if beneficiarios is None or arestas is None or pessoas is None:
        return None
    return (
        arestas.filter(pl.col("rotulo_origem") == "Pessoa")
        .with_columns(pessoa=pl.lit("Pessoa:") + pl.col("origem"))
        .join(beneficiarios, on="pessoa")
        .join(pessoas.select(origem="id", nome_pessoa="nome", documento="documento"), on="origem",
              how="left")
        .with_columns(candidatura=pl.lit("Candidatura:") + pl.col("destino"))
        .sort("confianca", descending=True)
        .unique(["candidatura", "pessoa"], keep="first")
        .sort("candidatura", "pessoa")
    )  # fmt: skip


_DADOS_CAMPANHA = ["pessoa", "valor", "n_meses", "primeiro_mes", "ultimo_mes",
                   "total_recebido", "confianca"]  # fmt: skip


@registrar
class L07DoadorBeneficiario(Sinal):
    """L07: campanha recebeu doação de beneficiário do Bolsa Família."""

    id: ClassVar[str] = "L07"
    nome: ClassVar[str] = "Campanha com doador beneficiário do Bolsa Família"
    descricao: ClassVar[str] = (
        "A candidatura recebeu doação de pessoa que recebeu Bolsa Família, possível doador laranja."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 10
    limitacoes: ClassVar[str] = (
        "Pequenas doações de eleitores de baixa renda são legítimas; o valor doado frente "
        "ao benefício é mostrado para avaliar. O alerta recai sobre a candidatura."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        df = _campanha(t, "DOOU_PARA")
        if df is None:
            return []
        df = df.with_columns(
            media_mensal=pl.col("total_recebido") / pl.col("n_meses"),
        )
        return montar(
            self.id, df,
            lambda r: (
                f"Doador {r['nome_pessoa']} ({r['documento']}) doou {reais(r['valor'])} e "
                f"{_beneficio(r)} (média de {reais(r['media_mensal'])}/mês).{_ligacao(r)}"
            ),
            lambda r: (fonte(t, "tse", URL_TSE), fonte(t, "transparencia", URL_BOLSA_FAMILIA)),
            [*_DADOS_CAMPANHA, "media_mensal"],
            coluna_entidade="candidatura", coluna_confianca="confianca",
        )  # fmt: skip


@registrar
class L08FornecedorCampanhaBeneficiario(Sinal):
    """L08: campanha pagou pessoa física beneficiária do Bolsa Família."""

    id: ClassVar[str] = "L08"
    nome: ClassVar[str] = "Campanha pagou fornecedor beneficiário do Bolsa Família"
    descricao: ClassVar[str] = (
        "A candidatura declarou despesa com pessoa física que recebeu Bolsa Família, "
        "possível serviço fictício."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 8
    limitacoes: ClassVar[str] = (
        "Cabos eleitorais e militantes de baixa renda são contratados legitimamente. O "
        "alerta recai sobre a candidatura."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        df = _campanha(t, "FORNECEU_CAMPANHA")
        if df is None:
            return []
        return montar(
            self.id, df,
            lambda r: (
                f"A campanha pagou {reais(r['valor'])} a {r['nome_pessoa']} "
                f"({r['documento']}), que {_beneficio(r)}.{_ligacao(r)}"
            ),
            lambda r: (fonte(t, "tse", URL_TSE), fonte(t, "transparencia", URL_BOLSA_FAMILIA)),
            _DADOS_CAMPANHA,
            coluna_entidade="candidatura", coluna_confianca="confianca",
        )  # fmt: skip

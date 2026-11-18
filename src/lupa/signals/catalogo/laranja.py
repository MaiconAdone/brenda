"""Sinais de laranja (L02, L03, L04). L01 depende do Bolsa Família (chave da API)."""

from __future__ import annotations

from typing import ClassVar

import polars as pl

from lupa.signals.base import Alerta, Categoria, Sinal, registrar
from lupa.signals.catalogo._util import data_br, montar, reais
from lupa.signals.dados import URL_RECEITA, Tabelas, elegiveis, fonte

FAIXAS = {"1": "0 a 12 anos", "2": "13 a 20 anos", "9": "mais de 80 anos"}


def _totais(t: Tabelas) -> pl.DataFrame | None:
    v = t.get("derivado/vitorias")
    if v is None:
        return None
    return (
        elegiveis(v)
        .group_by("entidade")
        .agg(
            pl.col("valor").sum().alias("total_vencido"),
            pl.col("data").min().alias("primeira_vitoria"),
            pl.col("url_fonte").sort_by("valor", descending=True).first(),
        )
    )


@registrar
class L02SocioDeMuitasEmpresas(Sinal):
    """L02: sócio pessoa física presente num número anômalo de empresas."""

    id: ClassVar[str] = "L02"
    nome: ClassVar[str] = "Sócio de número anômalo de empresas"
    descricao: ClassVar[str] = (
        "Um sócio pessoa física da fornecedora aparece em muito mais empresas do que o normal."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 8
    limitacoes: ClassVar[str] = (
        "Conta empresas no país pelo CPF parcial + nome. Empresários com holdings e "
        "grupos familiares têm muitas empresas legitimamente."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        percentil = float(self.parametros.get("percentil_anomalo", 0.99))
        minimo = int(self.parametros.get("min_empresas", 10))
        totais = _totais(t)
        socios = t.get("derivado/socios")
        contagem = t.get("receita_cnpj/socios_contagem_nacional")
        if totais is None or socios is None or contagem is None or contagem.is_empty():
            return []
        corte = max(minimo, float(contagem["n_empresas"].quantile(percentil) or 0))
        df = (
            socios.filter(pl.col("eh_pf"))
            .join(totais, left_on="empresa", right_on="entidade")
            .join(
                contagem.select(
                    socio=pl.lit("Pessoa:") + pl.col("id_pessoa"), n_empresas="n_empresas"
                ),
                on="socio",
            )
            .filter(pl.col("n_empresas") >= corte)
            .with_columns(corte=pl.lit(corte))
        )  # fmt: skip
        return montar(
            self.id, df,
            lambda r: (
                f"Sócio {r['nome_socio']} ({r['documento']}) aparece em {r['n_empresas']} "
                f"empresas no país (corte: {r['corte']:.0f})."
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA), fonte(t, "pncp", r["url_fonte"])),
            ["socio", "documento", "n_empresas", "corte"],
            coluna_entidade="empresa",
        )  # fmt: skip


@registrar
class L03FaixaEtariaExtrema(Sinal):
    """L03: sócio muito jovem ou muito idoso em fornecedora de valores altos."""

    id: ClassVar[str] = "L03"
    nome: ClassVar[str] = "Sócio em faixa etária extrema com contratos altos"
    descricao: ClassVar[str] = (
        "A fornecedora tem sócio de até 20 ou mais de 80 anos e venceu valores elevados."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 6
    limitacoes: ClassVar[str] = (
        "O QSA publica só a faixa etária, na data do arquivo. Sucessão familiar e herança "
        "explicam muitos casos."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        valor_min = float(self.parametros.get("valor_min_contratos", 500_000))
        totais = _totais(t)
        socios = t.get("derivado/socios")
        if totais is None or socios is None:
            return []
        df = (
            socios.filter(pl.col("eh_pf") & pl.col("faixa_etaria").is_in(list(FAIXAS)))
            .join(
                totais.filter(pl.col("total_vencido") >= valor_min),
                left_on="empresa", right_on="entidade",
            )
            .with_columns(faixa=pl.col("faixa_etaria").replace_strict(FAIXAS))
        )  # fmt: skip
        return montar(
            self.id, df,
            lambda r: (
                f"Sócio {r['nome_socio']} ({r['documento']}) está na faixa de {r['faixa']}; "
                f"a empresa venceu {reais(r['total_vencido'])}."
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA), fonte(t, "pncp", r["url_fonte"])),
            ["socio", "faixa", "total_vencido"],
            coluna_entidade="empresa",
        )  # fmt: skip


@registrar
class L04EntradaDeSociosPertoDeLicitacao(Sinal):
    """L04: vários sócios entraram pouco antes de uma vitória."""

    id: ClassVar[str] = "L04"
    nome: ClassVar[str] = "Troca de quadro societário perto de licitações"
    descricao: ClassVar[str] = (
        "Vários sócios entraram na empresa pouco antes de ela vencer uma licitação."
    )
    categoria: ClassVar[Categoria] = Categoria.LARANJA
    peso_padrao: ClassVar[float] = 8
    limitacoes: ClassVar[str] = (
        "Só há a foto atual do QSA: saídas de sócios não aparecem. Detectar trocas "
        "completas exige snapshots mensais acumulados."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        janela = int(self.parametros.get("janela_dias", 90))
        minimo = int(self.parametros.get("min_trocas", 2))
        v = t.get("derivado/vitorias")
        socios = t.get("derivado/socios")
        if v is None or socios is None:
            return []
        entradas = (
            elegiveis(v)
            .join(socios, left_on="entidade", right_on="empresa")
            .with_columns(dias=(pl.col("data") - pl.col("data_entrada")).dt.total_days())
            .filter(pl.col("dias").is_between(0, janela))
        )
        grupos = (
            entradas.group_by("entidade", "numero_controle_pncp")
            .agg(
                pl.col("socio").n_unique().alias("n_entradas"),
                pl.col("nome_socio").unique().sort().str.join(", ").alias("socios"),
                pl.col("data").first(),
                pl.col("data_entrada").min().alias("primeira_entrada"),
                pl.col("orgao_nome").first(),
                pl.col("url_fonte").first(),
            )
            .filter(pl.col("n_entradas") >= minimo)
            .sort("entidade", "data")
        )
        return montar(
            self.id, grupos,
            lambda r: (
                f"{r['n_entradas']} sócios entraram a partir de {data_br(r['primeira_entrada'])}, "
                f"até {janela} dias antes da vitória de {data_br(r['data'])} "
                f"({r['orgao_nome']}): {r['socios']}."
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA), fonte(t, "pncp", r["url_fonte"])),
            ["numero_controle_pncp", "n_entradas", "primeira_entrada", "data"],
        )  # fmt: skip

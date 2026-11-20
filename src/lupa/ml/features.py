"""Features por fornecedora para a detecção de anomalias.

Uma linha por empresa privada que venceu ao menos uma licitação (mesmo filtro de
natureza jurídica dos sinais de fachada e conluio). Cada feature tem nome legível e
formato, usados para explicar o alerta em português.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl

from lupa.signals.dados import elegiveis

DISPENSA_INEXIGIBILIDADE = (8, 9)
# Razão homologado/estimado fora desta faixa indica unidades diferentes (preço do lote
# contra preço unitário), não sobrepreço: o item é ignorado.
RAZAO_PRECO_VALIDA = (0.05, 10.0)
# Contatos usados por mais empresas que isto são de contadores ou centrais (como no F06).
MAX_EMPRESAS_POR_CONTATO = 10


@dataclass(frozen=True)
class Feature:
    """Metadados de uma feature.

    ``risco`` diz qual lado da distribuição merece atenção ("alto" ou "baixo"). O lado
    seguro é cortado na mediana antes do modelo: o Isolation Forest não tem direção e,
    sem isso, apontaria como atípica uma empresa antiga ou com preço muito abaixo do
    estimado, o que não é indício de nada.
    """

    nome: str
    formato: str  # reais, inteiro, pct, dias, razao
    risco: str  # "alto" ou "baixo"
    log: bool = False  # o modelo usa log1p do valor (variáveis de cauda longa)


FEATURES: dict[str, Feature] = {
    "n_vitorias": Feature("licitações vencidas", "inteiro", "alto", log=True),
    "valor_total": Feature("valor total vencido", "reais", "alto", log=True),
    "frac_dispensa": Feature("vitórias por dispensa ou inexigibilidade", "pct", "alto"),
    "razao_preco": Feature("preço homologado / estimado (mediana dos itens)", "razao", "alto"),
    "dias_ate_1a_vitoria": Feature("dias entre abertura e 1ª vitória", "dias", "baixo"),
    "valor_por_capital": Feature("valor vencido / capital social", "razao", "alto", log=True),
    "aditivo_max": Feature("maior acréscimo em contrato", "pct", "alto"),
    "empresas_mesmo_contato": Feature(
        "empresas com o mesmo telefone/e-mail", "inteiro", "alto", log=True
    ),
}


def formatar(feature: str, valor: float | None) -> str:
    """Valor da feature em texto legível."""
    if valor is None:
        return "sem dado"
    formato = FEATURES[feature].formato
    if formato == "reais":
        texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {texto}"
    if formato == "pct":
        return f"{valor * 100:.0f}%"
    if formato == "dias":
        return f"{valor:.0f} dias"
    if formato == "razao":
        return f"{valor:.2f}".replace(".", ",")
    return f"{valor:.0f}"


def features_empresas(t: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    """Tabela ``entidade`` + uma coluna por feature (nulos onde não há dado)."""
    v = t.get("derivado/vitorias")
    if v is None or v.is_empty():
        return pl.DataFrame(schema={"entidade": pl.String, **dict.fromkeys(FEATURES, pl.Float64)})
    v = elegiveis(v)
    base = (
        v.group_by("entidade")
        .agg(
            n_vitorias=pl.col("numero_controle_pncp").n_unique(),
            valor_total=pl.col("valor").sum(),
            frac_dispensa=pl.col("modalidade_id").cast(pl.Int64).is_in(DISPENSA_INEXIGIBILIDADE).mean(),
            primeira=pl.col("data").min(),
        )
    )  # fmt: skip
    base = base.join(_precos(t), on="entidade", how="left")
    base = base.join(_cadastro(t), on="entidade", how="left")
    base = base.join(_aditivos(t), on="entidade", how="left")
    base = base.join(_contatos(t), on="entidade", how="left")
    return (
        base.with_columns(
            dias_ate_1a_vitoria=(pl.col("primeira") - pl.col("abertura")).dt.total_days(),
            valor_por_capital=pl.when(pl.col("capital") > 0)
            .then(pl.col("valor_total") / pl.col("capital"))
            .otherwise(None),
            aditivo_max=pl.col("aditivo_max").fill_null(0.0),
            empresas_mesmo_contato=pl.col("empresas_mesmo_contato").fill_null(1),
        )
        .select("entidade", *[pl.col(f).cast(pl.Float64) for f in FEATURES])
        .sort("entidade")
    )


def _precos(t: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    vazio = pl.DataFrame(schema={"entidade": pl.String, "razao_preco": pl.Float64})
    res, itens = t.get("pncp/resultados"), t.get("pncp/itens")
    chave = ["numero_controle_pncp", "numero_item"]
    if (
        res is None
        or itens is None
        or not {*chave, "valor_unitario_homologado"} <= set(res.columns)
        or not {*chave, "valor_unitario_estimado"} <= set(itens.columns)
    ):
        return vazio
    res = res.filter(pl.col("fornecedor_tipo_pessoa") == "PJ").with_columns(
        entidade=pl.lit("Empresa:")
        + pl.col("fornecedor_documento").str.replace_all(r"\D", "").str.slice(0, 8)
    )
    res = res.join(itens.select(*chave, "valor_unitario_estimado"), on=chave, how="left")
    # O PNCP traz ``percentual_desconto`` zerado na maioria dos resultados (campo não
    # preenchido), por isso o desconto é medido pela razão de preços.
    razao = pl.col("valor_unitario_homologado") / pl.col("valor_unitario_estimado")
    return (
        res.filter(pl.col("valor_unitario_estimado") > 0)
        .with_columns(razao=razao)
        .filter(pl.col("razao").is_between(*RAZAO_PRECO_VALIDA))
        .group_by("entidade")
        .agg(razao_preco=pl.col("razao").median())
    )


def _cadastro(t: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    partes = []
    if (emp := t.get("receita_cnpj/empresas")) is not None:
        partes.append(
            emp.select(
                entidade=pl.lit("Empresa:") + pl.col("cnpj_basico"),
                capital=pl.col("capital_social").cast(pl.Float64),
            )
        )
    if (est := t.get("receita_cnpj/estabelecimentos")) is not None:
        partes.append(
            est.group_by("cnpj_basico").agg(abertura=pl.col("data_inicio_atividade").min())
            .select(entidade=pl.lit("Empresa:") + pl.col("cnpj_basico"), abertura="abertura")
        )  # fmt: skip
    if (soc := t.get("derivado/socios")) is not None:
        partes.append(soc.group_by(entidade="empresa").agg(n_socios=pl.len()))
    saida = pl.DataFrame(schema={"entidade": pl.String})
    for parte in partes:
        saida = saida.join(parte.unique("entidade"), on="entidade", how="full", coalesce=True)
    tipos = {"capital": pl.Float64, "abertura": pl.Date, "n_socios": pl.UInt32}
    return saida.with_columns(
        pl.lit(None, tipo).alias(nome) for nome, tipo in tipos.items() if nome not in saida.columns
    )


def _aditivos(t: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    c = t.get("derivado/contratos")
    if c is None:
        return pl.DataFrame(schema={"entidade": pl.String, "aditivo_max": pl.Float64})
    return (
        c.filter(pl.col("valor_inicial") > 0)
        .group_by("entidade")
        .agg(aditivo_max=(pl.col("valor_global") / pl.col("valor_inicial") - 1).max().clip(0))
    )


def _contatos(t: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    partes = [
        t[f"grafo/arestas/{rel}"].select("origem", "destino")
        for rel in ("TEM_TELEFONE", "TEM_EMAIL")
        if f"grafo/arestas/{rel}" in t
    ]
    if not partes:
        return pl.DataFrame(schema={"entidade": pl.String, "empresas_mesmo_contato": pl.Int64})
    arestas = pl.concat(partes).with_columns(empresa=pl.col("origem").str.slice(0, 8))
    por_contato = (
        arestas.group_by("destino")
        .agg(n=pl.col("empresa").n_unique())
        .filter(pl.col("n") <= MAX_EMPRESAS_POR_CONTATO)
    )
    return (
        arestas.join(por_contato, on="destino")
        .group_by("empresa")
        .agg(empresas_mesmo_contato=pl.col("n").max().cast(pl.Int64))
        .select(
            entidade=pl.lit("Empresa:") + pl.col("empresa"),
            empresas_mesmo_contato="empresas_mesmo_contato",
        )
    )  # fmt: skip

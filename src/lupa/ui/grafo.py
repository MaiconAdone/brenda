"""Desenho do subgrafo com Altair (vem com o Streamlit): zoom, arrasto e dica no mouse.

As posições saem do ``spring_layout`` do NetworkX com semente fixa, então o mesmo
subgrafo é sempre desenhado igual. O texto tem tamanho fixo em pixels e não encolhe
com o número de nós.
"""

from __future__ import annotations

import altair as alt
import networkx as nx
import polars as pl

from lupa.dossier.base import Subgrafo

CORES = {
    "Empresa": "#4c78a8",
    "Estabelecimento": "#9ecae9",
    "Pessoa": "#f58518",
    "Candidatura": "#e45756",
    "Licitacao": "#54a24b",
    "Contrato": "#88d27a",
    "OrgaoPublico": "#b279a2",
    "Sancao": "#222222",
    "Endereco": "#bab0ac",
    "Telefone": "#d8b5a5",
    "Email": "#d8b5a5",
}
TIPOS = {
    "SOCIO_DE": "sócio(a)",
    "VENCEU": "venceu",
    "CELEBRADO_POR": "contrato",
    "PROMOVIDA_POR": "promovida por",
    "LOCALIZADA_EM": "endereço",
    "TEM_TELEFONE": "telefone",
    "TEM_EMAIL": "e-mail",
    "DOOU_PARA": "doou",
    "FORNECEU_CAMPANHA": "serviço de campanha",
    "SANCIONADA": "sanção",
    "MESMA_ENTIDADE_PROVAVEL": "mesma pessoa?",
    "UNIDADE_DE": "unidade",
    "CANDIDATO_EM": "candidatura",
    "PARTE_DE": "parte de",
    "ORIGINADO_DE": "originado de",
}


def _curto(texto: str, n: int = 28) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def posicoes(sub: Subgrafo) -> dict[str, tuple[float, float]]:
    """Posição de cada nó; o centro fica fixo na origem."""
    g: nx.Graph[str] = nx.Graph()
    g.add_nodes_from(sub.nos["no"])
    g.add_edges_from(zip(sub.arestas["o"], sub.arestas["d"], strict=True))
    if g.number_of_nodes() == 1:
        return {sub.centro: (0.0, 0.0)}
    pos = nx.spring_layout(
        g, seed=42, pos={sub.centro: (0.0, 0.0)}, fixed=[sub.centro], k=1.2, iterations=200
    )
    return {n: (float(x), float(y)) for n, (x, y) in pos.items()}


def tabelas(sub: Subgrafo) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Nós e arestas com coordenadas, prontos para o gráfico."""
    pos = posicoes(sub)
    xs = {k: v[0] for k, v in pos.items()}
    ys = {k: v[1] for k, v in pos.items()}
    nos = sub.nos.with_columns(
        x=pl.col("no").replace_strict(xs, return_dtype=pl.Float64),
        y=pl.col("no").replace_strict(ys, return_dtype=pl.Float64),
        rotulo_curto=pl.col("nome").fill_null(pl.col("no")).map_elements(_curto, pl.String),
        papel=pl.when(pl.col("no") == sub.centro)
        .then(pl.lit("entidade da ficha"))
        .when(pl.col("com_alerta"))
        .then(pl.lit("tem alerta"))
        .otherwise(pl.lit("sem alerta")),
    )
    arestas = sub.arestas.with_columns(
        x=pl.col("o").replace_strict(xs, return_dtype=pl.Float64),
        y=pl.col("o").replace_strict(ys, return_dtype=pl.Float64),
        x2=pl.col("d").replace_strict(xs, return_dtype=pl.Float64),
        y2=pl.col("d").replace_strict(ys, return_dtype=pl.Float64),
        ligacao=pl.col("tipo").replace_strict(TIPOS, default=pl.col("tipo"))
        + pl.when(pl.col("probabilidade").is_not_null())
        .then(pl.lit(" (") + pl.col("probabilidade").round(2).cast(pl.String) + pl.lit(")"))
        .otherwise(pl.lit("")),
        provavel=pl.col("probabilidade").is_not_null(),
    ).with_columns(xm=(pl.col("x") + pl.col("x2")) / 2, ym=(pl.col("y") + pl.col("y2")) / 2)
    return nos, arestas


def grafico(sub: Subgrafo, altura: int = 560) -> alt.LayerChart | alt.FacetChart:
    """Gráfico interativo do subgrafo."""
    nos, arestas = tabelas(sub)
    eixo = alt.Axis(labels=False, ticks=False, grid=False, domain=False, title=None)
    x = alt.X("x:Q", axis=eixo)
    y = alt.Y("y:Q", axis=eixo)
    linhas = (
        alt.Chart(arestas)
        .mark_rule(opacity=0.6, color="#8a8a8a")
        .encode(
            x=x, y=y, x2="x2:Q", y2="y2:Q",
            strokeDash=alt.condition("datum.provavel", alt.value([5, 4]), alt.value([1, 0])),
            tooltip=[alt.Tooltip("ligacao:N", title="Ligação")],
        )
    )  # fmt: skip
    rotulos_arestas = (
        alt.Chart(arestas)
        .mark_text(fontSize=9, opacity=0.75, color="#8a8a8a")
        .encode(x=alt.X("xm:Q", axis=eixo), y=alt.Y("ym:Q", axis=eixo), text="ligacao:N")
    )
    cores = alt.Scale(domain=list(CORES), range=list(CORES.values()))
    pontos = (
        alt.Chart(nos)
        .mark_circle(opacity=1, strokeWidth=3)
        .encode(
            x=x, y=y,
            color=alt.Color(
                "rotulo:N", scale=cores, legend=alt.Legend(title="Tipo", symbolStrokeWidth=0)
            ),
            size=alt.condition(
                alt.datum.papel == "entidade da ficha", alt.value(900), alt.value(260)
            ),
            stroke=alt.condition(
                alt.datum.papel == "sem alerta", alt.value("transparent"), alt.value("#d62728")
            ),
            tooltip=[
                alt.Tooltip("nome:N", title="Nome"),
                alt.Tooltip("rotulo:N", title="Tipo"),
                alt.Tooltip("papel:N", title="Alerta"),
                alt.Tooltip("salto:Q", title="Saltos"),
                alt.Tooltip("no:N", title="Identificador"),
            ],
        )
    )  # fmt: skip
    textos = (
        alt.Chart(nos)
        .mark_text(fontSize=12, dy=-16, color="#9aa0a6")
        .encode(x=x, y=y, text="rotulo_curto:N")
    )
    return (
        alt.layer(linhas, rotulos_arestas, pontos, textos).properties(height=altura).interactive()
    )

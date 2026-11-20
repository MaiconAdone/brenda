"""Comunidades de fornecedoras (Louvain) para achar possíveis cartéis.

Grafo não direcionado entre empresas privadas que venceram licitações, com dois tipos
de ligação:

- **co-vitória:** venceram itens da mesma licitação. Peso ``1/(k-1)`` para uma licitação
  com ``k`` vencedoras, para que um pregão com 20 lotes não ligue todo mundo com força.
  O PNCP só publica vencedoras, então isto não é "disputaram juntas".
- **laço:** sócio em comum (peso 2), mesmo telefone ou e-mail (peso 2, ignorando
  contatos de mais de 10 empresas) e mesmo endereço (peso 1, ignorando endereços de mais
  de 5 empresas).

O Louvain (NetworkX, semente fixa) agrupa as empresas. Um grupo interessa quando tem ao
menos 3 empresas, ao menos um laço e vende para o mesmo órgão. O sinal M02 alerta só as
empresas do grupo que têm laço com outra do grupo.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import networkx as nx
import polars as pl

from lupa.signals.dados import elegiveis


@dataclass(frozen=True)
class ConfigComunidades:
    """Parâmetros do agrupamento."""

    semente: int = 42
    resolucao: float = 1.0
    peso_socio: float = 2.0
    peso_contato: float = 2.0
    peso_endereco: float = 1.0
    max_empresas_por_contato: int = 10
    max_empresas_por_endereco: int = 5


Ligacoes = dict[tuple[str, str], dict[str, float]]


def _par(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _somar(ligacoes: Ligacoes, grupos: pl.DataFrame, tipo: str, peso: float, maximo: int) -> None:
    """Liga todas as empresas que compartilham o mesmo valor de ``chave``."""
    for membros in grupos.group_by("chave").agg(pl.col("empresa").unique())["empresa"]:
        if 2 <= len(membros) <= maximo:
            for a, b in itertools.combinations(sorted(membros), 2):
                ligacoes[_par(a, b)][tipo] += peso


def ligacoes_empresas(
    t: Mapping[str, pl.DataFrame], cfg: ConfigComunidades | None = None
) -> tuple[Ligacoes, set[str]]:
    """Pares de fornecedoras com o peso de cada tipo de ligação, e o conjunto de empresas."""
    cfg = cfg or ConfigComunidades()
    ligacoes: Ligacoes = defaultdict(lambda: defaultdict(float))
    v = t.get("derivado/vitorias")
    if v is None or v.is_empty():
        return ligacoes, set()
    v = elegiveis(v)
    empresas = set(v["entidade"])
    for membros in v.group_by("numero_controle_pncp").agg(pl.col("entidade").unique())["entidade"]:
        k = len(membros)
        for a, b in itertools.combinations(sorted(membros), 2):
            ligacoes[_par(a, b)]["co_vitoria"] += 1 / (k - 1)

    if (socios := t.get("derivado/socios")) is not None:
        s = socios.filter(pl.col("empresa").is_in(list(empresas))).select(
            chave="socio", empresa="empresa"
        )
        _somar(ligacoes, s, "socio", cfg.peso_socio, len(empresas))
    contatos = [
        t[f"grafo/arestas/{rel}"] for rel in ("TEM_TELEFONE", "TEM_EMAIL")
        if f"grafo/arestas/{rel}" in t
    ]  # fmt: skip
    if contatos:
        c = pl.concat([x.select("origem", "destino") for x in contatos]).select(
            chave="destino", empresa=pl.lit("Empresa:") + pl.col("origem").str.slice(0, 8)
        )
        c = c.unique()
        tamanho = c.group_by("chave").len()
        validos = tamanho.filter(pl.col("len") <= cfg.max_empresas_por_contato)["chave"]
        c = c.filter(pl.col("chave").is_in(validos) & pl.col("empresa").is_in(list(empresas)))
        _somar(ligacoes, c, "contato", cfg.peso_contato, cfg.max_empresas_por_contato)
    if (end := t.get("grafo/arestas/LOCALIZADA_EM")) is not None:
        e = end.select(
            chave="destino", empresa=pl.lit("Empresa:") + pl.col("origem").str.slice(0, 8)
        ).unique()
        tamanho = e.group_by("chave").len()
        validos = tamanho.filter(pl.col("len") <= cfg.max_empresas_por_endereco)["chave"]
        e = e.filter(pl.col("chave").is_in(validos) & pl.col("empresa").is_in(list(empresas)))
        _somar(ligacoes, e, "endereco", cfg.peso_endereco, cfg.max_empresas_por_endereco)
    return ligacoes, empresas


LACOS = ("socio", "contato", "endereco")
LACOS_FORTES = ("socio", "contato")


def comunidades(
    ligacoes: Ligacoes, empresas: set[str], cfg: ConfigComunidades | None = None
) -> list[list[str]]:
    """Comunidades Louvain (maiores primeiro, membros em ordem)."""
    cfg = cfg or ConfigComunidades()
    g: nx.Graph[str] = nx.Graph()
    g.add_nodes_from(sorted(empresas))
    for (a, b), tipos in sorted(ligacoes.items()):
        g.add_edge(a, b, weight=sum(tipos.values()))
    grupos = nx.community.louvain_communities(
        g, weight="weight", resolution=cfg.resolucao, seed=cfg.semente
    )
    return sorted((sorted(x) for x in grupos), key=lambda x: (-len(x), x))


def detectar(t: Mapping[str, pl.DataFrame], cfg: ConfigComunidades | None = None) -> pl.DataFrame:
    """Uma linha por empresa de núcleo de interesse.

    Núcleo: dentro de uma comunidade Louvain, componente conexo de 3 ou mais empresas
    ligadas por laços (sócio, contato, endereço), com ao menos um laço forte (sócio ou
    contato) e duas ou mais empresas vendendo ao mesmo órgão.

    Colunas: ``entidade``, ``comunidade``, ``tamanho_comunidade``, ``nucleo``,
    ``membros`` (do núcleo), ``lacos`` (``tipo|empresa`` da empresa dentro do núcleo),
    ``orgao_comum``, ``n_no_orgao``, ``valor_nucleo``, ``co_vitorias`` (licitações em
    que duas ou mais empresas do núcleo venceram).
    """
    cfg = cfg or ConfigComunidades()
    schema = pl.Schema({
        "entidade": pl.String(), "comunidade": pl.Int64(), "tamanho_comunidade": pl.Int64(),
        "nucleo": pl.Int64(), "membros": pl.List(pl.String), "lacos": pl.List(pl.String),
        "orgao_comum": pl.String(), "n_no_orgao": pl.Int64(), "valor_nucleo": pl.Float64(),
        "co_vitorias": pl.Int64(),
    })  # fmt: skip
    ligacoes, empresas = ligacoes_empresas(t, cfg)
    if not ligacoes:
        return pl.DataFrame(schema=schema)
    v = elegiveis(t["derivado/vitorias"])
    linhas = []
    n_nucleo = 0
    for i, grupo in enumerate(comunidades(ligacoes, empresas, cfg)):
        rede: nx.Graph[str] = nx.Graph()
        for a, b in itertools.combinations(grupo, 2):
            tipos = ligacoes.get(_par(a, b), {})
            presentes = [x for x in LACOS if tipos.get(x)]
            if presentes:
                rede.add_edge(a, b, tipos=presentes)
        for componente in sorted((sorted(c) for c in nx.connected_components(rede)), key=str):
            sub = rede.subgraph(componente)
            fortes = any(set(d["tipos"]) & set(LACOS_FORTES) for *_, d in sub.edges(data=True))
            if len(componente) < 3 or not fortes:
                continue
            vn = v.filter(pl.col("entidade").is_in(componente))
            por_orgao = (
                vn.group_by("orgao_nome")
                .agg(n=pl.col("entidade").n_unique(), valor=pl.col("valor").sum())
                .sort("n", "valor", descending=True)
            )
            if por_orgao.is_empty() or por_orgao["n"][0] < 2:
                continue
            co = (
                vn.group_by("numero_controle_pncp")
                .agg(pl.col("entidade").n_unique())
                .filter(pl.col("entidade") >= 2)
                .height
            )
            for empresa in componente:
                lacos = sorted(
                    f"{tipo}|{outra}" for outra in sub.neighbors(empresa)
                    for tipo in sub.edges[empresa, outra]["tipos"]
                )  # fmt: skip
                linhas.append({
                    "entidade": empresa, "comunidade": i, "tamanho_comunidade": len(grupo),
                    "nucleo": n_nucleo, "membros": componente, "lacos": lacos,
                    "orgao_comum": por_orgao["orgao_nome"][0], "n_no_orgao": por_orgao["n"][0],
                    "valor_nucleo": float(vn["valor"].sum()), "co_vitorias": co,
                })  # fmt: skip
            n_nucleo += 1
    return pl.DataFrame(linhas, schema=schema)

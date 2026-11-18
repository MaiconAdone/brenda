"""Consultas básicas sobre o grafo em memória (NetworkX).

Os nós do NetworkX são ``"<Rotulo>:<id>"`` (ex.: ``"Empresa:11222333"``), com os
atributos ``rotulo`` e ``nome``. Cada aresta tem ``tipo`` e, quando houver,
``probabilidade``. As mesmas consultas em Cypher estão em ``consultas.cypher``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

import networkx as nx
import polars as pl

from lupa.graph.modelo import Grafo

type Rede = nx.MultiDiGraph[str]


def no(rotulo: str, id_: str) -> str:
    """Chave do nó no NetworkX."""
    return f"{rotulo}:{id_}"


def carregar_networkx(
    grafo: Grafo,
    rotulos: Iterable[str] | None = None,
    tipos: Iterable[str] | None = None,
) -> Rede:
    """Monta o grafo NetworkX, opcionalmente só com alguns rótulos e tipos de aresta."""
    rotulos_ok = set(rotulos) if rotulos is not None else set(grafo.nos)
    tipos_ok = set(tipos) if tipos is not None else set(grafo.arestas)
    g: Rede = nx.MultiDiGraph()
    for rotulo, df in grafo.nos.items():
        if rotulo not in rotulos_ok:
            continue
        nomes = df["nome"] if "nome" in df.columns else pl.Series([None] * df.height)
        g.add_nodes_from(
            (no(rotulo, i), {"rotulo": rotulo, "id": i, "nome": n})
            for i, n in zip(df["id"], nomes, strict=True)
        )
    for tipo, df in grafo.arestas.items():
        if tipo not in tipos_ok:
            continue
        df = df.filter(
            pl.col("rotulo_origem").is_in(list(rotulos_ok))
            & pl.col("rotulo_destino").is_in(list(rotulos_ok))
        )
        prob = (
            df["probabilidade"] if "probabilidade" in df.columns else pl.Series([None] * df.height)
        )
        g.add_edges_from(
            (no(ro, o), no(rd, d), {"tipo": tipo, "probabilidade": p})
            for o, d, ro, rd, p in zip(
                df["origem"], df["destino"], df["rotulo_origem"], df["rotulo_destino"], prob,
                strict=True,
            )
        )  # fmt: skip
    return g


def _vizinhos(g: Rede, n: str) -> set[str]:
    return set(g.predecessors(n)) | set(g.successors(n))


def vizinhanca(g: Rede, centro: str, saltos: int = 2, grau_max: int = 200) -> Rede:
    """Subgrafo de até ``saltos`` passos em volta de ``centro`` (ignorando direção).

    Nós com grau acima de ``grau_max`` (ex.: endereço de um centro empresarial) entram
    no subgrafo, mas não são expandidos, para o resultado continuar legível.
    """
    visitados = {centro}
    fila = deque([(centro, 0)])
    while fila:
        atual, d = fila.popleft()
        if d == saltos or (atual != centro and g.degree(atual) > grau_max):
            continue
        for v in _vizinhos(g, atual):
            if v not in visitados:
                visitados.add(v)
                fila.append((v, d + 1))
    return g.subgraph(visitados).copy()


def _arestas(g: Rede, n: str, tipo: str, saida: bool) -> list[str]:
    arestas: list[tuple[str, str, str]] = list(
        g.out_edges(n, data="tipo") if saida else g.in_edges(n, data="tipo")
    )
    return sorted({(d if saida else o) for o, d, t in arestas if t == tipo})


def socios_de(g: Rede, empresa: str) -> list[str]:
    """Sócios (pessoas ou empresas) de uma empresa."""
    return _arestas(g, empresa, "SOCIO_DE", saida=False)


def empresas_de(g: Rede, socio: str) -> list[str]:
    """Empresas em que uma pessoa ou empresa é sócia."""
    return _arestas(g, socio, "SOCIO_DE", saida=True)


def socios_em_comum(g: Rede, empresa_a: str, empresa_b: str) -> list[str]:
    """Sócios presentes nas duas empresas."""
    return sorted(set(socios_de(g, empresa_a)) & set(socios_de(g, empresa_b)))


def mesma_pessoa_provavel(g: Rede, pessoa: str) -> list[tuple[str, float]]:
    """Outras pessoas ligadas por resolução de entidades, com a probabilidade."""
    arestas: list[tuple[str, str, dict[str, Any]]] = [
        *g.out_edges(pessoa, data=True),
        *g.in_edges(pessoa, data=True),
    ]
    pares = {
        (d if o == pessoa else o): float(a["probabilidade"])
        for o, d, a in arestas
        if a["tipo"] == "MESMA_ENTIDADE_PROVAVEL"
    }
    return sorted(pares.items(), key=lambda x: (-x[1], x[0]))


def fornecedores_do_orgao(g: Rede, orgao: str) -> pl.DataFrame:
    """Vencedores das licitações de um órgão, com o número de licitações vencidas."""
    licitacoes = _arestas(g, orgao, "PROMOVIDA_POR", saida=False)
    contagem: dict[str, int] = {}
    for lic in licitacoes:
        for fornecedor in _arestas(g, lic, "VENCEU", saida=False):
            contagem[fornecedor] = contagem.get(fornecedor, 0) + 1
    linhas: list[dict[str, Any]] = [
        {"fornecedor": f, "nome": g.nodes[f].get("nome"), "licitacoes_vencidas": n}
        for f, n in contagem.items()
    ]
    esquema = {"fornecedor": pl.String, "nome": pl.String, "licitacoes_vencidas": pl.Int64}
    return pl.DataFrame(linhas, schema=esquema).sort(
        ["licitacoes_vencidas", "fornecedor"], descending=[True, False]
    )


def menor_caminho(g: Rede, a: str, b: str, max_saltos: int = 6) -> list[str] | None:
    """Menor caminho entre dois nós (ignorando direção), ou ``None`` se não houver."""
    try:
        caminho: list[str] = nx.shortest_path(g.to_undirected(as_view=True), a, b)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return caminho if len(caminho) - 1 <= max_saltos else None

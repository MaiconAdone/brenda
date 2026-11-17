"""Score por regras: soma ponderada dos sinais com teto por categoria.

Fórmula (transparente e configurável em ``config/regras.yaml``)::

    contribuicao(alerta) = peso(sinal) × confianca(alerta)
    score_categoria      = min(teto_categoria, Σ contribuicoes da categoria)
    score_total          = Σ score_categoria

O teto por categoria evita dupla contagem de sinais correlacionados (ex.: F01 e F02
costumam disparar juntos). Sinais ``somente_combinado`` (ex.: C04, sobrenome em
comum) são descartados quando são os únicos a disparar para a entidade.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from lupa.config import ConfigRegras
from lupa.signals.base import Alerta, Categoria, Sinal


@dataclass(frozen=True)
class ScoreEntidade:
    """Score de uma entidade, com a decomposição necessária para explicá-lo."""

    entidade_id: str
    total: float
    por_categoria: Mapping[str, float]
    alertas: tuple[Alerta, ...]
    descartados: tuple[str, ...] = field(default_factory=tuple)


def calcular_scores(
    alertas: Iterable[Alerta],
    catalogo: Mapping[str, type[Sinal]],
    regras: ConfigRegras,
) -> list[ScoreEntidade]:
    """Calcula o score de cada entidade, em ordem decrescente de score."""
    por_entidade: dict[str, list[Alerta]] = defaultdict(list)
    for alerta in alertas:
        cfg = regras.sinais.get(alerta.sinal_id)
        if alerta.sinal_id not in catalogo:
            raise KeyError(f"Sinal {alerta.sinal_id} não está no catálogo.")
        if cfg is not None and not cfg.ativo:
            continue
        por_entidade[alerta.entidade_id].append(alerta)

    resultado = []
    for entidade, lista in por_entidade.items():
        fortes = [a for a in lista if not _somente_combinado(a.sinal_id, catalogo, regras)]
        validos = lista if fortes else []
        descartados = tuple(sorted({a.sinal_id for a in lista})) if not fortes else ()

        brutos: dict[str, float] = defaultdict(float)
        for alerta in validos:
            sinal = catalogo[alerta.sinal_id]
            cfg = regras.sinais.get(alerta.sinal_id)
            peso = cfg.peso if cfg is not None else sinal.peso_padrao
            brutos[sinal.categoria] += peso * alerta.confianca

        por_categoria = {cat: min(_teto(cat, regras), valor) for cat, valor in brutos.items()}
        resultado.append(
            ScoreEntidade(
                entidade_id=entidade,
                total=round(sum(por_categoria.values()), 4),
                por_categoria=por_categoria,
                alertas=tuple(validos),
                descartados=descartados,
            )
        )
    return sorted(resultado, key=lambda s: (-s.total, s.entidade_id))


def _somente_combinado(
    sinal_id: str, catalogo: Mapping[str, type[Sinal]], regras: ConfigRegras
) -> bool:
    cfg = regras.sinais.get(sinal_id)
    return catalogo[sinal_id].somente_combinado or (cfg is not None and cfg.somente_combinado)


def _teto(categoria: Categoria | str, regras: ConfigRegras) -> float:
    cfg = regras.categorias.get(str(categoria))
    return cfg.teto if cfg is not None else float("inf")

"""Montagem de alertas a partir de linhas de um DataFrame."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import polars as pl

from lupa.signals.base import Alerta, Evidencia, Fonte

Linha = dict[str, Any]


def _serializavel(v: Any) -> Any:
    if isinstance(v, date | datetime):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 2)
    return v


def montar(
    sinal_id: str,
    df: pl.DataFrame,
    descrever: Callable[[Linha], str],
    fontes: Callable[[Linha], tuple[Fonte, ...]],
    dados: list[str],
    coluna_entidade: str = "entidade",
    coluna_confianca: str | None = None,
) -> list[Alerta]:
    """Um alerta por entidade; cada linha de ``df`` vira uma evidência."""
    por_entidade: dict[str, list[Evidencia]] = defaultdict(list)
    for r in df.iter_rows(named=True):
        confianca = float(r[coluna_confianca]) if coluna_confianca else 1.0
        por_entidade[r[coluna_entidade]].append(
            Evidencia(
                descricao=descrever(r),
                dados={k: _serializavel(r[k]) for k in dados},
                fontes=fontes(r),
                confianca=min(1.0, max(0.0, confianca)),
            )
        )
    return [Alerta(sinal_id, e, tuple(ev)) for e, ev in sorted(por_entidade.items())]


def reais(valor: float | None) -> str:
    """Formata valor em reais no padrão brasileiro."""
    if valor is None:
        return "R$ —"
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def data_br(d: date | None) -> str:
    """Data no formato dd/mm/aaaa."""
    return d.strftime("%d/%m/%Y") if d else "—"

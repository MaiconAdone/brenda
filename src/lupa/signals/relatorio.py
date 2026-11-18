"""Relatório em Markdown dos principais alertas, para revisão humana."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date

import polars as pl

from lupa import AVISO_LEGAL
from lupa.scoring import ScoreEntidade
from lupa.signals.base import Sinal


def _nome(entidade: str, nomes: dict[str, str]) -> str:
    tipo, _, ident = entidade.partition(":")
    nome = nomes.get(entidade) or "(sem nome)"
    if tipo == "Empresa":
        return f"{nome} — CNPJ raiz {ident}"
    return f"{nome} — pessoa física (id {ident[:8]}…)"


def gerar_relatorio(
    scores: list[ScoreEntidade],
    catalogo: Mapping[str, type[Sinal]],
    t: Mapping[str, pl.DataFrame],
    top: int = 50,
    gerado_em: date | None = None,
) -> str:
    """Ranking das ``top`` entidades com todas as evidências e fontes."""
    nomes_df = t.get("derivado/nomes")
    nomes: dict[str, str] = (
        dict(nomes_df.select("entidade", "nome").iter_rows()) if nomes_df is not None else {}
    )
    selecionados = scores[:top]
    contagem = Counter(a.sinal_id for s in scores for a in s.alertas)
    linhas = [
        f"# Lupa Cidadã — {top} principais alertas",
        "",
        f"> **{AVISO_LEGAL}**",
        ">",
        "> **Uso interno.** Contém dados pessoais publicados pelas fontes oficiais. Nenhum item "
        "pode ser divulgado sem revisão humana registrada (ver `docs/etica.md`).",
        "",
        f"- Gerado em: {gerado_em.isoformat() if gerado_em else '—'}",
        f"- Entidades com alerta: {len(scores)}; listadas: {len(selecionados)}",
        "- Score = Σ por categoria de min(teto, Σ peso × confiança). "
        "Pesos em `config/regras.yaml`.",
        "- Situação da revisão: **não revisado**",
        "",
        "## Alertas por sinal (todas as entidades)",
        "",
        "| Sinal | Nome | Entidades |",
        "|---|---|---|",
        *[f"| {sid} | {catalogo[sid].nome} | {n} |" for sid, n in sorted(contagem.items())],
        "",
        "## Ranking",
        "",
        "| # | Entidade | Score | Categorias | Sinais |",
        "|---|---|---|---|---|",
    ]
    for i, s in enumerate(selecionados, 1):
        categorias = ", ".join(f"{c} {v:g}" for c, v in sorted(s.por_categoria.items()))
        sinais_ = ", ".join(sorted({a.sinal_id for a in s.alertas}))
        linhas.append(
            f"| {i} | {_nome(s.entidade_id, nomes)} | {s.total:g} | {categorias} | {sinais_} |"
        )

    linhas += ["", "## Evidências", ""]
    for i, s in enumerate(selecionados, 1):
        linhas += [f"### {i}. {_nome(s.entidade_id, nomes)} — score {s.total:g}", ""]
        for alerta in sorted(s.alertas, key=lambda a: a.sinal_id):
            sinal = catalogo[alerta.sinal_id]
            linhas.append(f"**{sinal.id} – {sinal.nome}** (confiança {alerta.confianca:.2f})")
            linhas.append("")
            for ev in alerta.evidencias:
                fontes = "; ".join(
                    f"[{f.nome}]({f.url})" + (f" (coleta {f.coletado_em})" if f.coletado_em else "")
                    for f in dict.fromkeys(ev.fontes)
                )
                linhas.append(f"- {ev.descricao} Fontes: {fontes}")
            linhas.append("")
        if s.descartados:
            linhas += [f"_Sinais só combinados, descartados: {', '.join(s.descartados)}_", ""]

    usados = sorted({a.sinal_id for s in selecionados for a in s.alertas})
    linhas += ["## Limitações dos sinais usados", ""]
    linhas += [f"- **{sid}**: {catalogo[sid].limitacoes or '—'}" for sid in usados]
    linhas.append("")
    return "\n".join(linhas)

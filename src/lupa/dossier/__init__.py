"""Dossiê por alerta: ficha da entidade, revisão humana e PDF."""

from __future__ import annotations

from lupa import acesso, revisao
from lupa.dossier.base import Base, Ficha, Subgrafo


def gerar_dossie(base: Base, entidade: str, usuario: str) -> bytes:
    """PDF da entidade, exigindo revisão aprovada e registrando a exportação no log."""
    from lupa.dossier.pdf import gerar_pdf

    ficha = base.ficha(entidade)
    atual = revisao.atuais(base.raiz).get(entidade)
    pdf = gerar_pdf(ficha, atual, base.vizinhanca(entidade))
    acesso.registrar("gerar_dossie", usuario, entidade=entidade, sinais=list(ficha.sinais))
    return pdf


__all__ = ["Base", "Ficha", "Subgrafo", "gerar_dossie"]

"""Sinais de risco: um arquivo por sinal, todos derivados de :class:`lupa.signals.base.Sinal`."""

from lupa.signals.base import Alerta, Categoria, Evidencia, Fonte, Sinal, registrar, sinais

__all__ = ["Alerta", "Categoria", "Evidencia", "Fonte", "Sinal", "registrar", "sinais"]

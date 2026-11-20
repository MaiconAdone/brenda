"""Linha de comando: ``python -m lupa.signals [--top N]``."""

from __future__ import annotations

import argparse
import logging
import sys

from lupa.config import carregar_env
from lupa.ingestion.contexto import contexto_padrao
from lupa.signals.executar import etapa_sinais


def main(argv: list[str] | None = None) -> int:
    """Roda os sinais e grava alertas, scores e relatório."""
    parser = argparse.ArgumentParser(description="Sinais e score da Lupa Cidadã")
    parser.add_argument("--top", type=int, default=50, help="Tamanho do relatório")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    carregar_env()
    ctx = contexto_padrao()
    resultado = etapa_sinais(ctx, top=args.top)
    for tabela, linhas in resultado.resumo().items():
        print(f"[sinais] {tabela}: {linhas}")
    for nome in (f"top{args.top}.md", f"campanhas_top{args.top}.md"):
        print(f"[sinais] relatório: {ctx.gold('relatorios') / nome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

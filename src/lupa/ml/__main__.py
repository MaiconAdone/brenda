"""Linha de comando: ``python -m lupa.ml`` (modelos) e ``python -m lupa.ml avaliacao``."""

from __future__ import annotations

import argparse
import logging
import sys

from lupa.config import carregar_env
from lupa.ingestion.contexto import contexto_padrao


def main(argv: list[str] | None = None) -> int:
    """Roda os modelos ou mostra a avaliação por precisão@k."""
    parser = argparse.ArgumentParser(description="Fase 5 da Lupa Cidadã")
    parser.add_argument("acao", nargs="?", default="modelos", choices=["modelos", "avaliacao"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    carregar_env()
    ctx = contexto_padrao()
    if args.acao == "avaliacao":
        from lupa.ml.avaliacao import etapa_avaliacao

        resultado = etapa_avaliacao(ctx)
        for nome, df in resultado.tabelas.items():
            print(f"[avaliacao] {nome}")
            print(df)
        return 0
    from lupa.ml.etapas import etapa_ml

    for tabela, linhas in etapa_ml(ctx).resumo().items():
        print(f"[ml] {tabela}: {linhas}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

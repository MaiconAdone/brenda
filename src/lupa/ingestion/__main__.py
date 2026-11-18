"""Linha de comando: ``python -m lupa.ingestion [etapa ...]``.

Sem argumentos, roda todas as etapas na ordem de dependência.
"""

from __future__ import annotations

import argparse
import logging
import sys

from lupa.config import carregar_env
from lupa.ingestion.contexto import contexto_padrao
from lupa.ingestion.etapas import ETAPAS


def main(argv: list[str] | None = None) -> int:
    """Executa as etapas pedidas e devolve 1 se houver falha de qualidade."""
    parser = argparse.ArgumentParser(description="Ingestão da Lupa Cidadã")
    parser.add_argument("etapas", nargs="*", help=f"Etapas: {', '.join(ETAPAS)}")
    args = parser.parse_args(argv)
    pedidas = args.etapas or list(ETAPAS)
    desconhecidas = set(pedidas) - set(ETAPAS)
    if desconhecidas:
        parser.error(f"etapas desconhecidas: {sorted(desconhecidas)}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    carregar_env()
    ctx = contexto_padrao()
    falhas = 0
    for nome in [e for e in ETAPAS if e in pedidas]:
        resultado = ETAPAS[nome](ctx)
        print(f"[{nome}] linhas: {resultado.resumo()}")
        for f in resultado.falhas:
            print(f"[{nome}] QUALIDADE {f.tabela} {f.regra} {f.detalhe}: {f.linhas}")
        falhas += len(resultado.falhas)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

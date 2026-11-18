"""Linha de comando: ``python -m lupa.graph [entidades] [grafo] [neo4j]``.

Sem argumentos roda ``entidades`` e ``grafo``. A carga no Neo4j só roda se pedida.
"""

from __future__ import annotations

import argparse
import logging
import sys

from lupa.config import carregar_env
from lupa.graph.etapas import ETAPAS
from lupa.ingestion.contexto import contexto_padrao


def main(argv: list[str] | None = None) -> int:
    """Executa as etapas pedidas e devolve 1 se houver falha de qualidade."""
    parser = argparse.ArgumentParser(description="Entidades e grafo da Lupa Cidadã")
    parser.add_argument("etapas", nargs="*", help=f"Etapas: {', '.join(ETAPAS)}")
    args = parser.parse_args(argv)
    pedidas = args.etapas or ["entidades", "grafo"]
    desconhecidas = set(pedidas) - set(ETAPAS)
    if desconhecidas:
        parser.error(f"etapas desconhecidas: {sorted(desconhecidas)}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    carregar_env()
    ctx = contexto_padrao()
    falhas = 0
    for nome in [e for e in ETAPAS if e in pedidas]:
        resultado = ETAPAS[nome](ctx)
        for tabela, linhas in resultado.resumo().items():
            print(f"[{nome}] {tabela}: {linhas}")
        for f in resultado.falhas:
            print(f"[{nome}] QUALIDADE {f.tabela} {f.regra} {f.detalhe}: {f.linhas}")
        falhas += len(resultado.falhas)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

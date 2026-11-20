"""Linha de comando: ``python -m lupa.dossier Empresa:<raiz> -o dossie.pdf --usuario nome``.

Só gera o PDF se houver revisão aprovada e vigente para a entidade.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lupa.config import carregar_env
from lupa.dossier import Base, gerar_dossie
from lupa.ingestion.contexto import contexto_padrao
from lupa.revisao import RevisaoPendenteError


def main(argv: list[str] | None = None) -> int:
    """Gera o dossiê de uma entidade."""
    parser = argparse.ArgumentParser(description="Dossiê de alerta da Lupa Cidadã")
    parser.add_argument("entidade", help="Ex.: Empresa:12345678 ou Candidatura:2024-123")
    parser.add_argument("-o", "--saida", type=Path, required=True)
    parser.add_argument("--usuario", required=True, help="Quem está exportando (vai ao log)")
    args = parser.parse_args(argv)
    carregar_env()
    ctx = contexto_padrao()
    base = Base(ctx.raiz, ctx.config.piloto.uf, ctx.config.piloto.nome_municipio)
    try:
        pdf = gerar_dossie(base, args.entidade, args.usuario)
    except RevisaoPendenteError as erro:
        print(f"[dossie] não gerado: {erro}", file=sys.stderr)
        return 2
    args.saida.write_bytes(pdf)
    print(f"[dossie] {args.saida} ({len(pdf) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

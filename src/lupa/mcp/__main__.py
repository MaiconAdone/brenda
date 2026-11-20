"""``python -m lupa.mcp <dados|documentos|caso> --agente <papel>`` (transporte stdio)."""

from __future__ import annotations

import argparse
import sys

from lupa.config import carregar_env
from lupa.mcp.servidores import montar


def main(argv: list[str] | None = None) -> int:
    """Sobe um servidor MCP via stdio."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("servidor", choices=["dados", "documentos", "caso"])
    p.add_argument("--agente", required=True)
    args = p.parse_args(argv)
    carregar_env()
    montar(args.servidor, args.agente).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())

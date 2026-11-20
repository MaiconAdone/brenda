"""Aplicação FastAPI. Fase 0: só saúde, aviso legal e log de acesso (LGPD)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response

from lupa import AVISO_LEGAL, __version__
from lupa.acesso import arquivo_log

app = FastAPI(
    title="Lupa Cidadã",
    version=__version__,
    description=AVISO_LEGAL,
    license_info={"name": "AGPL-3.0-or-later", "identifier": "AGPL-3.0-or-later"},
)


@app.middleware("http")
async def registrar_acesso(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Grava quem acessou o quê e quando, e anexa o aviso legal a toda resposta.

    A query string não é gravada, para não registrar termos de busca com dados pessoais.
    """
    resposta = await call_next(request)
    resposta.headers["X-Aviso-Legal"] = AVISO_LEGAL.encode("ascii", "ignore").decode()
    registro = {
        "em": datetime.now(UTC).isoformat(),
        "metodo": request.method,
        "caminho": request.url.path,
        "status": resposta.status_code,
        "usuario": request.headers.get("X-Usuario", "anonimo"),
    }
    arquivo = arquivo_log()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    return resposta


@app.get("/saude")
def saude() -> dict[str, str]:
    """Verificação de disponibilidade."""
    return {"status": "ok", "versao": __version__, "aviso": AVISO_LEGAL}

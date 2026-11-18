"""Cliente HTTP com retry, respeito a rate limit e download idempotente."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("lupa.ingestion.http")

USER_AGENT = "lupa-cidada/0.1 (+https://github.com/MaiconAdone/brenda)"
_STATUS_REPETIVEIS = {408, 425, 429, 500, 502, 503, 504}


def _deve_repetir(erro: BaseException) -> bool:
    if isinstance(erro, httpx.HTTPStatusError):
        return erro.response.status_code in _STATUS_REPETIVEIS
    return isinstance(erro, httpx.TransportError)


_repetir = retry(
    retry=retry_if_exception(_deve_repetir),
    wait=wait_exponential(multiplier=2, min=2, max=120),
    stop=stop_after_attempt(7),
    reraise=True,
)


class ClienteHttp:
    """Envolve ``httpx.Client`` com intervalo mínimo entre requisições e retry.

    ``transport`` permite injetar um ``httpx.MockTransport`` nos testes.
    """

    def __init__(
        self,
        intervalo_min_s: float = 0.0,
        cabecalhos: dict[str, str] | None = None,
        timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.intervalo_min_s = intervalo_min_s
        self._ultimo = 0.0
        self._trava = threading.Lock()
        self._cliente = httpx.Client(
            headers={"User-Agent": USER_AGENT, **(cabecalhos or {})},
            timeout=httpx.Timeout(timeout_s, connect=30.0),
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> ClienteHttp:
        return self

    def __exit__(self, *_: object) -> None:
        self._cliente.close()

    def _aguardar_vez(self) -> None:
        with self._trava:
            espera = self._ultimo + self.intervalo_min_s - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self._ultimo = time.monotonic()

    @_repetir
    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET com retry. Levanta erro para status >= 400 (exceto 404, devolvido)."""
        self._aguardar_vez()
        resposta = self._cliente.get(url, **kwargs)
        if resposta.status_code >= 400 and resposta.status_code != 404:
            resposta.raise_for_status()
        return resposta

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """GET que devolve JSON; ``None`` para 204/404 (sem dados)."""
        resposta = self.get(url, params=params, **kwargs)
        if resposta.status_code in (204, 404) or not resposta.content:
            return None
        return resposta.json()

    @_repetir
    def requisitar(self, metodo: str, url: str, **kwargs: Any) -> httpx.Response:
        """Requisição genérica (ex.: PROPFIND do WebDAV) com retry."""
        self._aguardar_vez()
        resposta = self._cliente.request(metodo, url, **kwargs)
        resposta.raise_for_status()
        return resposta

    def tamanho_remoto(self, url: str, **kwargs: Any) -> int | None:
        """Content-Length informado pelo servidor, se houver."""
        try:
            resposta = self.requisitar("HEAD", url, **kwargs)
        except httpx.HTTPStatusError:
            return None
        valor = resposta.headers.get("content-length")
        return int(valor) if valor and valor.isdigit() else None

    def baixar(
        self, url: str, destino: Path, tamanho_esperado: int | None = None, **kwargs: Any
    ) -> bool:
        """Baixa ``url`` para ``destino`` de forma idempotente e retomável.

        Devolve ``False`` se o arquivo já existia completo. Grava em ``.part`` e só
        renomeia ao final; se o ``.part`` existir, retoma com ``Range``.
        """
        if tamanho_esperado is None:
            tamanho_esperado = self.tamanho_remoto(url, **kwargs)
        if destino.exists() and (
            tamanho_esperado is None or destino.stat().st_size == tamanho_esperado
        ):
            log.info("já baixado: %s", destino.name)
            return False
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_name(destino.name + ".part")
        self._baixar_parcial(url, parcial, **kwargs)
        if tamanho_esperado is not None and parcial.stat().st_size != tamanho_esperado:
            raise OSError(
                f"Download incompleto de {url}: "
                f"{parcial.stat().st_size} de {tamanho_esperado} bytes"
            )
        parcial.replace(destino)
        return True

    @_repetir
    def _baixar_parcial(self, url: str, parcial: Path, **kwargs: Any) -> None:
        inicio = parcial.stat().st_size if parcial.exists() else 0
        cabecalhos = dict(kwargs.pop("headers", {}) or {})
        if inicio:
            cabecalhos["Range"] = f"bytes={inicio}-"
        self._aguardar_vez()
        with self._cliente.stream("GET", url, headers=cabecalhos, **kwargs) as resposta:
            resposta.raise_for_status()
            modo = "ab" if inicio and resposta.status_code == 206 else "wb"
            log.info("baixando %s (%s)", url, "retomando" if modo == "ab" else "do início")
            with parcial.open(modo) as arquivo:
                for pedaco in resposta.iter_bytes(1 << 20):
                    arquivo.write(pedaco)

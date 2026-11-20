"""Pipeline sintético completo (entidades → grafo → sinais) para a interface e o dossiê."""

from __future__ import annotations

import pytest

from lupa.graph import etapas
from lupa.ingestion.contexto import Contexto
from lupa.signals.executar import etapa_sinais
from tests.grafo.conftest import _sal, ctx, silver  # noqa: F401  (fixtures reaproveitadas)


@pytest.fixture
def processado(silver: Contexto, monkeypatch: pytest.MonkeyPatch) -> Contexto:  # noqa: F811
    """Silver sintética processada até os alertas; log de acesso na pasta temporária."""
    etapas.etapa_entidades(silver)
    etapas.etapa_grafo(silver)
    etapa_sinais(silver, top=10)
    monkeypatch.setenv("LUPA_LOG_DIR", str(silver.raiz / "logs"))
    return silver

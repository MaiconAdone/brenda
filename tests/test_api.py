from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from lupa.api.main import app


def test_saude_e_log_de_acesso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUPA_LOG_DIR", str(tmp_path))
    resposta = TestClient(app).get("/saude?busca=dado-pessoal")

    assert resposta.status_code == 200
    assert "não constituem acusação" in resposta.json()["aviso"]
    assert "X-Aviso-Legal" in resposta.headers

    [linha] = (tmp_path / "acesso.jsonl").read_text(encoding="utf-8").splitlines()
    registro = json.loads(linha)
    assert registro["caminho"] == "/saude"
    assert "dado-pessoal" not in linha

"""Interface Streamlit sobre o pipeline sintético (sem navegador)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from lupa import AVISO_LEGAL, acesso
from lupa.dossier import Base
from lupa.ingestion.contexto import Contexto
from lupa.ui import md
from lupa.ui.grafo import grafico, tabelas

APP = str(Path(__file__).parents[2] / "src" / "lupa" / "ui" / "app.py")
EMPRESA = "Empresa:11222333"


@pytest.fixture
def app(processado: Contexto, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    import streamlit as st

    st.cache_resource.clear()
    monkeypatch.setenv("LUPA_DATA_DIR", str(processado.raiz))
    return AppTest.from_file(APP, default_timeout=60)


def test_ranking_com_aviso(app: AppTest) -> None:
    app.run()
    assert not app.exception
    assert app.warning[0].value == AVISO_LEGAL
    assert app.dataframe  # ranking


def test_ficha_exige_identificacao_e_registra_acesso(app: AppTest) -> None:
    app.query_params["entidade"] = EMPRESA
    app.run()
    assert not app.exception
    assert any("Informe seu nome" in i.value for i in app.info)
    assert not acesso.arquivo_log().exists()

    app.text_input(key="usuario").input("Revisora Sintética").run()
    assert not app.exception
    assert app.warning[0].value == AVISO_LEGAL
    linhas = acesso.arquivo_log().read_text("utf-8").splitlines()
    [registro] = [json.loads(x) for x in linhas]
    assert registro["acao"] == "ver_entidade"
    assert registro["entidade"] == EMPRESA and registro["usuario"] == "Revisora Sintética"
    assert any("exige revisão humana" in w.value for w in app.warning)


def test_md_escapa_cpf_e_reais() -> None:
    assert md("***.123.456-** recebeu R$ 10,00 e R$ 5,00") == (
        r"\*\*\*.123.456-\*\* recebeu R\$ 10,00 e R\$ 5,00"
    )


def test_grafico_do_subgrafo(processado: Contexto) -> None:
    sub = Base(processado.raiz).vizinhanca(EMPRESA)
    nos, arestas = tabelas(sub)
    assert nos.filter(nos["no"] == EMPRESA)["papel"][0] == "entidade da ficha"
    assert arestas["x"].null_count() == 0 and arestas["x2"].null_count() == 0
    assert grafico(sub).to_dict()["layer"]

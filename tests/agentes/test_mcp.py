from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from lupa.config import ConfigAgentes, carregar_agentes
from lupa.mcp.servidores import Recursos, servidor_caso, servidor_dados, servidor_documentos
from tests.agentes.conftest import EMPRESA

CONFIG = Path(__file__).parents[2] / "config" / "agentes.yaml"


def _nomes(servidor: Any) -> set[str]:
    return {t.name for t in asyncio.run(servidor.list_tools())}


def _chamar(servidor: Any, nome: str, **args: Any) -> Any:
    resultado = asyncio.run(servidor.call_tool(nome, args))
    assert not resultado.is_error, resultado
    return json.loads(resultado.content[0].text)


@pytest.fixture
def recursos(raiz: Path, tmp_path: Path) -> Recursos:
    return Recursos(raiz, carregar_agentes(CONFIG))


def test_config_do_repositorio_e_segura() -> None:
    config = carregar_agentes(CONFIG)
    assert "caso" not in config.agentes["analista_documental"].servidores
    bruto = config.model_dump()
    bruto["agentes"]["analista_documental"]["servidores"] = ["documentos", "caso"]
    with pytest.raises(ValidationError, match="Analista"):
        ConfigAgentes.model_validate(bruto)


def test_ferramentas_expostas(recursos: Recursos) -> None:
    assert _nomes(servidor_dados(recursos)) == {
        "descrever_tabelas", "consultar_tabelas", "perfil_entidade", "sinais_da_entidade",
        "vizinhanca_grafo", "caminho_grafo", "consultar_grafo",
    }  # fmt: skip
    assert _nomes(servidor_documentos(recursos)) == {
        "buscar_diario_oficial", "listar_documentos_pncp", "ler_documento",
    }  # fmt: skip
    caso = _nomes(servidor_caso(recursos, "investigador"))
    assert "registrar_evidencia" in caso and "agente" not in str(caso)


def test_analista_nao_recebe_servidor_de_escrita(recursos: Recursos) -> None:
    with pytest.raises(PermissionError):
        servidor_caso(recursos, "analista_documental")
    with pytest.raises(PermissionError):
        servidor_caso(recursos, "desconhecido")


def test_chamadas_pelo_protocolo(recursos: Recursos) -> None:
    dados = servidor_dados(recursos)
    r = _chamar(dados, "consultar_tabelas", sql="SELECT count(*) AS n FROM receita_socios")
    assert r["linhas"] == [[3]]
    with pytest.raises(ToolError, match="Só consultas SELECT"):
        asyncio.run(dados.call_tool("consultar_tabelas", {"sql": "DELETE FROM scores"}))

    recursos.arquivo.abrir_caso("coordenador", [EMPRESA], orcamento_tokens=10)
    caso_id = recursos.arquivo.listar_casos()[0]["id"]
    coord = servidor_caso(recursos, "coordenador")
    _chamar(coord, "atualizar_status_caso", caso_id=caso_id, status="em_investigacao",
            motivo="início")  # fmt: skip
    inv = servidor_caso(recursos, "investigador")
    hip = _chamar(inv, "registrar_hipotese", caso_id=caso_id, enunciado="Indício de laranja",
                  categoria_sinal="L", confianca=0.4)["hipotese_id"]  # fmt: skip
    with pytest.raises(ToolError, match="Só o Cético"):  # não sustenta a própria hipótese
        asyncio.run(inv.call_tool(
            "atualizar_hipotese", {"hipotese_id": hip, "status": "sustentada", "justificativa": "x"}
        ))  # fmt: skip
    assert recursos.arquivo.caso(caso_id).eventos[-1]["agente"] == "investigador"

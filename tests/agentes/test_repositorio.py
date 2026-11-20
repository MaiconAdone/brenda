from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lupa.casos import schema
from lupa.casos.linguagem import LinguagemProibidaError, termos_encontrados
from lupa.casos.repositorio import (
    ArquivoCasos,
    PermissaoNegadaError,
    RegraVioladaError,
)

REF = {"tipo": "registro_banco", "sql": "SELECT 1", "sha_resultado": "x"}


def _caso(a: ArquivoCasos) -> str:
    caso = a.abrir_caso("coordenador", ["Empresa:11111111"], orcamento_tokens=1000)
    a.atualizar_status_caso("coordenador", caso, "em_investigacao", "início")
    return caso


def _hipotese(a: ArquivoCasos, caso: str) -> str:
    return a.registrar_hipotese("investigador", caso, "Indício de sócio laranja", "L", 0.6)


def test_migracoes_sao_idempotentes(tmp_path: Path) -> None:
    caminho = tmp_path / "c.sqlite"
    con = schema.conectar(caminho)
    assert schema.versao_atual(con) == len(schema.MIGRACOES)
    con.close()
    con = schema.conectar(caminho)
    assert schema.migrar(con) == len(schema.MIGRACOES)


def test_regra_de_ouro(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    hip = _hipotese(arquivo, caso)
    with pytest.raises(RegraVioladaError, match="evidência"):
        arquivo.atualizar_hipotese("cetico", hip, "sustentada", "ok")
    arquivo.registrar_evidencia("investigador", hip, "registro_banco", REF, "sócio recebe", "apoia")
    obj = arquivo.registrar_objecao("cetico", hip, "Pode ser homônimo")
    with pytest.raises(RegraVioladaError, match="objeção"):
        arquivo.atualizar_hipotese("cetico", hip, "sustentada", "ok")
    arquivo.verificar_objecao("investigador", obj, "id_pessoa usa miolo do CPF e nome", False)
    arquivo.atualizar_hipotese("cetico", hip, "sustentada", "objeções verificadas")
    assert arquivo.caso(caso).hipoteses[0]["status"] == "sustentada"


def test_objecao_procedente_impede_sustentar(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    hip = _hipotese(arquivo, caso)
    arquivo.registrar_evidencia("investigador", hip, "registro_banco", REF, "t", "apoia")
    obj = arquivo.registrar_objecao("cetico", hip, "Escritório de contabilidade no endereço")
    arquivo.verificar_objecao("cetico", obj, "o endereço é de contabilidade", True)
    with pytest.raises(RegraVioladaError, match="legítima"):
        arquivo.atualizar_hipotese("cetico", hip, "sustentada", "ok")


def test_nova_objecao_reabre_hipotese_sustentada(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    hip = _hipotese(arquivo, caso)
    arquivo.registrar_evidencia("investigador", hip, "registro_banco", REF, "t", "apoia")
    arquivo.atualizar_hipotese("cetico", hip, "sustentada", "ok")
    arquivo.registrar_objecao("cetico", hip, "CNAE secundário compatível")
    assert arquivo.caso(caso).hipoteses[0]["status"] == "proposta"
    assert [h["versao"] for h in arquivo.historico("hipotese", hip)] == [1, 2]


def test_permissoes_por_papel(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    hip = _hipotese(arquivo, caso)
    with pytest.raises(PermissaoNegadaError):
        arquivo.atualizar_status_caso("analista_documental", caso, "arquivado", "x")
    with pytest.raises(PermissaoNegadaError):
        arquivo.registrar_hipotese("analista_documental", caso, "Indício", "L", 0.5)
    with pytest.raises(PermissaoNegadaError, match="Cético"):
        arquivo.atualizar_hipotese("investigador", hip, "sustentada", "eu mesmo aprovo")
    with pytest.raises(PermissaoNegadaError):
        arquivo.registrar_parecer("coordenador", caso, "x", "aprovar", "ok")
    with pytest.raises(PermissaoNegadaError):
        arquivo.abrir_caso("intruso", ["Empresa:1"], orcamento_tokens=10)


def test_transicoes_e_revisao_humana(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    with pytest.raises(RegraVioladaError, match="Transição"):
        arquivo.atualizar_status_caso("coordenador", caso, "aberto", "voltar")
    arquivo.atualizar_status_caso("coordenador", caso, "em_revisao_cetica", "pronto p/ cético")
    with pytest.raises(RegraVioladaError, match="hipótese sustentada"):
        arquivo.atualizar_status_caso("coordenador", caso, "pronto_para_humano", "x")
    with pytest.raises(PermissaoNegadaError, match="humano"):
        arquivo.atualizar_status_caso("coordenador", caso, "aprovado", "x")
    arquivo.atualizar_status_caso("coordenador", caso, "em_investigacao", "objeções")
    assert arquivo.caso(caso).caso["ciclos_ceticos"] == 1
    hip = _hipotese(arquivo, caso)
    arquivo.registrar_evidencia("investigador", hip, "registro_banco", REF, "t", "apoia")
    arquivo.atualizar_hipotese("cetico", hip, "sustentada", "ok")
    arquivo.atualizar_status_caso("coordenador", caso, "em_revisao_cetica", "revisão")
    arquivo.atualizar_status_caso("coordenador", caso, "pronto_para_humano", "pronto")
    with pytest.raises(RegraVioladaError, match="reabra"):
        _hipotese(arquivo, caso)
    arquivo.registrar_parecer("humano", caso, "Revisora", "mais_investigacao", "ver aditivos")
    c = arquivo.caso(caso)
    assert c.caso["status"] == "em_investigacao"
    assert c.pareceres[0]["decisao"] == "mais_investigacao"


def test_linguagem(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    assert termos_encontrados("A empresa é CORRUPTA e fraudou") == ["fraud", "corrupt"]
    assert termos_encontrados("indício de direcionamento") == []
    with pytest.raises(LinguagemProibidaError):
        arquivo.registrar_hipotese("investigador", caso, "O dono é culpado", "L", 0.5)


def test_log_encadeado_detecta_adulteracao(arquivo: ArquivoCasos, tmp_path: Path) -> None:
    caso = _caso(arquivo)
    _hipotese(arquivo, caso)
    assert arquivo.verificar_log() == []
    con = sqlite3.connect(arquivo.caminho)
    with pytest.raises(sqlite3.DatabaseError, match="acréscimo"):
        con.execute("UPDATE evento SET saida = 'x' WHERE seq = 2")
    con.execute("DROP TRIGGER evento_sem_update")
    con.execute("UPDATE evento SET agente = 'monitor' WHERE seq = 2")
    con.commit()
    con.close()
    assert arquivo.verificar_log() == [2]


def test_consumo_acumula(arquivo: ArquivoCasos) -> None:
    caso = _caso(arquivo)
    arquivo.registrar_consumo(caso, "investigador", "chamada_modelo", tokens=300)
    totais = arquivo.registrar_consumo(caso, "cetico", "chamada_modelo", tokens=200, custo=0.1)
    assert totais == {"tokens_usados": 500, "passos_usados": 2, "ciclos_ceticos": 0}
    assert [e["acao"] for e in arquivo.caso(caso).eventos][-2:] == ["chamada_modelo"] * 2

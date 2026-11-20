"""Ficha da entidade, subgrafo e PDF do dossiê sobre o pipeline sintético."""

from __future__ import annotations

import json
import re

import polars as pl
import pytest

from lupa import AVISO_LEGAL, acesso, revisao
from lupa.dossier import Base, gerar_dossie
from lupa.dossier.pdf import Bloco, Tabela, Titulo, gerar_pdf, montar_conteudo
from lupa.ingestion.contexto import Contexto
from lupa.revisao import Revisao, RevisaoPendenteError

EMPRESA = "Empresa:11222333"
CPF_ABERTO = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}|\b\d{11}\b")


@pytest.fixture
def base(processado: Contexto) -> Base:
    return Base(processado.raiz, "SP", "MAUA")


def _aprovar(base: Base, entidade: str) -> Revisao:
    ficha = base.ficha(entidade)
    r = Revisao(
        entidade, "aprovado", "Revisora Sintética",
        "Fontes conferidas no cenário sintético de teste.", ficha.sinais, ficha.score,
    )  # fmt: skip
    revisao.registrar(base.raiz, r)
    return r


def _textos(blocos: list[Bloco]) -> str:
    partes = []
    for b in blocos:
        if isinstance(b, Tabela):
            partes += [" ".join(b.cabecalho), *(" ".join(linha) for linha in b.linhas)]
        else:
            partes.append(b.texto)
    return "\n".join(partes)


def test_indice_tem_campos_dos_filtros(base: Base) -> None:
    indice = base.indice()
    assert {"ranking", "posicao", "nome", "categorias", "orgaos", "data_min", "data_max",
            "valor_total"} <= set(indice.columns)  # fmt: skip
    linha = indice.filter(pl.col("entidade") == EMPRESA).row(0, named=True)
    assert linha["ranking"] == "fornecedores"
    assert "conluio" in linha["categorias"]
    assert linha["orgaos"] and linha["data_min"] is not None


def test_ficha_da_empresa(base: Base) -> None:
    ficha = base.ficha(EMPRESA)
    campos = dict(ficha.cadastro)
    assert campos["CNPJ raiz"] == "11222333"
    assert "K04" in ficha.sinais and ficha.score > 0
    assert any(a.sinal_id == "K04" and a.evidencias for a in ficha.alertas)
    assert all(url.startswith("https://") for url in ficha.fontes.values())
    assert ficha.cronologia == sorted(ficha.cronologia, key=lambda e: (e.data, e.descricao))
    assert ficha.socios.height and "documento" in ficha.socios.columns


def test_vizinhanca_dois_saltos(base: Base) -> None:
    sub = base.vizinhanca(EMPRESA)
    nos = dict(zip(sub.nos["no"], sub.nos["salto"], strict=True))
    assert nos[EMPRESA] == 0
    assert max(nos.values()) <= 2
    assert "Empresa:44555666" in nos  # sócio em comum, a 2 saltos
    assert set(sub.arestas["o"]) | set(sub.arestas["d"]) <= set(nos)
    pequeno = base.vizinhanca(EMPRESA, max_nos=3)
    assert pequeno.cortado and pequeno.nos.height == 3


def test_conteudo_do_dossie(base: Base) -> None:
    ficha = base.ficha(EMPRESA)
    r = _aprovar(base, EMPRESA)
    texto = _textos(montar_conteudo(ficha, r, base.vizinhanca(EMPRESA)))
    assert AVISO_LEGAL in texto
    assert "Revisora Sintética" in texto and "Aprovado para encaminhamento" in texto
    assert "K04" in texto and "Cronologia" in texto and "Conexões" in texto
    assert not CPF_ABERTO.search(texto)
    titulos = [b.texto for b in montar_conteudo(ficha, r) if isinstance(b, Titulo) and b.nivel == 1]
    numeros = [int(t.split(".")[0]) for t in titulos[1:]]
    assert numeros == list(range(1, len(numeros) + 1))


def test_pdf_exige_revisao_aprovada(base: Base) -> None:
    ficha = base.ficha(EMPRESA)
    with pytest.raises(RevisaoPendenteError):
        gerar_pdf(ficha, None)
    with pytest.raises(RevisaoPendenteError):
        gerar_dossie(base, EMPRESA, "alguem")
    assert not acesso.arquivo_log().exists()


def test_gerar_dossie_registra_acesso(base: Base) -> None:
    _aprovar(base, EMPRESA)
    pdf = gerar_dossie(base, EMPRESA, "Revisora Sintética")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
    linhas = acesso.arquivo_log().read_text("utf-8").splitlines()
    [registro] = [json.loads(x) for x in linhas]
    assert registro["acao"] == "gerar_dossie" and registro["entidade"] == EMPRESA

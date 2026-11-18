"""Cada sinal contra o cenário sintético de ``conftest.py``."""

from __future__ import annotations

import polars as pl
import pytest

import lupa.signals.catalogo  # noqa: F401  (registra os sinais)
from lupa.signals.base import Alerta, Sinal, sinais

A, B, C = "Empresa:11111111", "Empresa:33333333", "Empresa:22222222"
Tabelas = dict[str, pl.DataFrame]


def _rodar(sinal_id: str, t: Tabelas, **parametros: object) -> dict[str, Alerta]:
    classe: type[Sinal] = sinais()[sinal_id]
    return {a.entidade_id: a for a in classe(parametros).evidencias(t)}


def test_catalogo_tem_pelo_menos_dez_sinais() -> None:
    implementados = set(sinais())
    assert len(implementados) >= 10
    for classe in sinais().values():
        assert classe.limitacoes, f"{classe.id} sem limitações documentadas"


def test_toda_evidencia_tem_fonte(tabelas: Tabelas) -> None:
    for sinal_id in sinais():
        for alerta in _rodar(sinal_id, tabelas).values():
            for ev in alerta.evidencias:
                assert ev.fontes and all(f.url for f in ev.fontes)


def test_f01_abertura_recente(tabelas: Tabelas) -> None:
    alertas = _rodar("F01", tabelas)
    assert set(alertas) == {A}  # C também é nova, mas é S.A. aberta
    assert alertas[A].evidencias[0].dados["dias"] == 45
    assert set(_rodar("F01", tabelas, dias_max_abertura=30)) == set()


def test_f02_capital_baixo(tabelas: Tabelas) -> None:
    alertas = _rodar("F02", tabelas)
    assert set(alertas) == {A}
    assert alertas[A].evidencias[0].dados["razao"] == 100


def test_f04_endereco_compartilhado(tabelas: Tabelas) -> None:
    assert set(_rodar("F04", tabelas)) == {A}
    assert set(_rodar("F04", tabelas, min_empresas_mesmo_endereco=6)) == set()


def test_f06_contato_compartilhado_ignora_contador(tabelas: Tabelas) -> None:
    alertas = _rodar("F06", tabelas)
    assert set(alertas) == {A, B}
    assert len(alertas[A].evidencias) == 1  # só o telefone exclusivo, não o do contador
    assert set(_rodar("F06", tabelas, max_empresas_por_contato=100)) == {A, B}


def test_l02_socio_de_muitas_empresas(tabelas: Tabelas) -> None:
    alertas = _rodar("L02", tabelas)
    assert set(alertas) == {A, B}
    assert alertas[A].evidencias[0].dados["n_empresas"] == 50


def test_l03_faixa_etaria_extrema(tabelas: Tabelas) -> None:
    assert set(_rodar("L03", tabelas)) == {A}  # B tem o mesmo sócio, mas valor baixo


def test_l04_entradas_perto_da_vitoria(tabelas: Tabelas) -> None:
    alertas = _rodar("L04", tabelas)
    assert set(alertas) == {A}
    assert alertas[A].evidencias[0].dados["n_entradas"] == 2
    assert set(_rodar("L04", tabelas, min_trocas=3)) == set()


def test_c01_eleito_via_resolucao_de_entidades(tabelas: Tabelas) -> None:
    alertas = _rodar("C01", tabelas)
    assert set(alertas) == {A}
    assert alertas[A].confianca == pytest.approx(0.9)
    assert "Ligação provável" in alertas[A].evidencias[0].descricao


def test_c03_doacao_e_servico_de_campanha(tabelas: Tabelas) -> None:
    alertas = _rodar("C03", tabelas)
    assert set(alertas) == {A, B}
    assert "doou" in alertas[A].evidencias[0].descricao
    assert "prestou serviço" in alertas[B].evidencias[0].descricao


def test_k04_socio_em_comum(tabelas: Tabelas) -> None:
    alertas = _rodar("K04", tabelas)
    assert set(alertas) == {A, B}  # C (S.A. aberta) fica de fora
    assert alertas[A].evidencias[0].dados["empresa_outra"] == B


def test_e01_acrescimo_acima_do_limite(tabelas: Tabelas) -> None:
    assert set(_rodar("E01", tabelas)) == {A}
    assert set(_rodar("E01", tabelas, limite_aditivo=0.5)) == set()


def test_e02_dispensas_repetidas(tabelas: Tabelas) -> None:
    alertas = _rodar("E02", tabelas)
    assert set(alertas) == {B}
    assert alertas[B].evidencias[0].dados["n_dispensas"] == 3


def test_e03_fracionamento(tabelas: Tabelas) -> None:
    alertas = _rodar("E03", tabelas)
    assert set(alertas) == {B}
    dados = alertas[B].evidencias[0].dados
    assert dados["valor_total"] == 90_000 and dados["limite"] == 62_725.59
    assert set(_rodar("E03", tabelas, janela_dias=10)) == set()


def test_e04_sancao_que_alcanca_o_contratante(tabelas: Tabelas) -> None:
    alertas = _rodar("E04", tabelas)
    # A: inidoneidade em todas as esferas. B: sanção federal não alcança o município.
    assert set(alertas) == {A}
    assert len(alertas[A].evidencias) == 2  # vitória e contrato
    assert alertas[A].confianca == 1.0


def test_sem_cpf_aberto_nas_evidencias(tabelas: Tabelas) -> None:
    import re

    cpf = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
    for sinal_id in sinais():
        for alerta in _rodar(sinal_id, tabelas).values():
            for ev in alerta.evidencias:
                assert not cpf.search(ev.descricao)

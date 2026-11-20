"""Núcleos de fornecedoras (Louvain + laços) em cenário sintético.

- A, B, C: ligadas por sócio (A-B) e telefone (B-C); vencem do mesmo órgão; A e B
  venceram juntas a licitação L1.
- D, E, F: só compartilham endereço (laço fraco): não formam núcleo.
- G, H: par com sócio em comum (K04 cobre pares): não formam núcleo.
"""

from __future__ import annotations

import polars as pl

from lupa.ml.comunidades import detectar, ligacoes_empresas

ORGAO = "MUNICIPIO SINTETICO"


def _vitoria(empresa: str, controle: str, valor: float = 100.0) -> dict[str, object]:
    return {
        "entidade": f"Empresa:{empresa * 8}", "numero_controle_pncp": controle,
        "orgao_cnpj": "1", "orgao_nome": ORGAO, "valor": valor, "natureza_juridica": "2062",
    }  # fmt: skip


def _tabelas() -> dict[str, pl.DataFrame]:
    vitorias = pl.DataFrame([
        _vitoria("1", "L1"), _vitoria("2", "L1"), _vitoria("3", "L2"),
        _vitoria("4", "L3"), _vitoria("5", "L4"), _vitoria("6", "L5"),
        _vitoria("7", "L6"), _vitoria("8", "L7"),
    ])  # fmt: skip
    socios = pl.DataFrame({
        "empresa": ["Empresa:11111111", "Empresa:22222222", "Empresa:77777777", "Empresa:88888888"],
        "socio": ["Pessoa:p1", "Pessoa:p1", "Pessoa:p2", "Pessoa:p2"],
    })  # fmt: skip
    telefones = pl.DataFrame({"origem": ["22222222000100", "33333333000100"],
                              "destino": ["tel-bc", "tel-bc"]})  # fmt: skip
    enderecos = pl.DataFrame({"origem": ["44444444000100", "55555555000100", "66666666000100"],
                              "destino": ["end-def"] * 3})  # fmt: skip
    return {
        "derivado/vitorias": vitorias,
        "derivado/socios": socios,
        "grafo/arestas/TEM_TELEFONE": telefones,
        "grafo/arestas/LOCALIZADA_EM": enderecos,
    }


def test_ligacoes_e_pesos() -> None:
    ligacoes, empresas = ligacoes_empresas(_tabelas())
    assert len(empresas) == 8
    ab = ligacoes[("Empresa:11111111", "Empresa:22222222")]
    assert ab["socio"] == 2.0 and ab["co_vitoria"] == 1.0
    assert ligacoes[("Empresa:22222222", "Empresa:33333333")]["contato"] == 2.0


def test_nucleo_exige_tres_empresas_e_laco_forte() -> None:
    n = detectar(_tabelas())
    assert set(n["entidade"]) == {"Empresa:11111111", "Empresa:22222222", "Empresa:33333333"}
    linha = n.filter(pl.col("entidade") == "Empresa:22222222").row(0, named=True)
    assert sorted(linha["lacos"]) == ["contato|Empresa:33333333", "socio|Empresa:11111111"]
    assert linha["co_vitorias"] == 1 and linha["orgao_comum"] == ORGAO
    assert linha["n_no_orgao"] == 3


def test_sem_vitorias() -> None:
    assert detectar({}).is_empty()

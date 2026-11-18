"""Cenário sintético para os sinais. Nenhum dado real.

Empresas (CNPJ raiz):

- ``11111111`` (A): aberta 45 dias antes de vencer R$ 1 mi com capital de R$ 10 mil;
  dois sócios entraram pouco antes; sócio P1 tem 13–20 anos e está em 50 empresas;
  mesmo telefone de B; endereço com 5 empresas ativas; contrato com acréscimo de 40%;
  sancionada em todas as esferas; sócio P2 é provavelmente um vereador eleito sem CPF
  publicado; P2 doou para a campanha.
- ``33333333`` (B): três dispensas (art. 75, II) de R$ 30 mil em 60 dias; sócio P1 em
  comum com A; prestou serviço à campanha do eleito; sanção federal (não alcança Mauá).
- ``22222222`` (C): S.A. aberta com o mesmo sócio P1 — fora dos sinais de fachada,
  laranja e conluio.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from lupa.signals.dados import derivar

ORGAO = "46522959000198"
URL = "https://pncp.gov.br/app/editais/sintetico"
P1, P2, P3 = "p1" * 8, "p2" * 8, "p3" * 8


def _contratacao(ctrl: str, dia: date, modalidade: int, amparo: str) -> dict[str, object]:
    return {
        "numero_controle_pncp": ctrl, "orgao_cnpj": ORGAO, "orgao_nome": "MUNICIPIO DE MAUA",
        "orgao_esfera": "M", "modalidade_id": modalidade, "amparo_legal": amparo,
        "objeto": f"Objeto {ctrl}", "url_fonte": f"{URL}/{ctrl}",
        "data_publicacao": datetime(dia.year, dia.month, dia.day),
    }  # fmt: skip


def _resultado(ctrl: str, raiz: str, valor: float, dia: date) -> dict[str, object]:
    return {
        "numero_controle_pncp": ctrl, "fornecedor_tipo_pessoa": "PJ",
        "fornecedor_documento": f"{raiz}000100", "fornecedor_id_pessoa": None,
        "fornecedor_nome_normalizado": f"EMPRESA {raiz[:2]}", "valor_total_homologado": valor,
        "data_resultado": datetime(dia.year, dia.month, dia.day),
    }  # fmt: skip


def _socio(raiz: str, pessoa: str, faixa: str, entrada: date) -> dict[str, object]:
    return {
        "cnpj_basico": raiz, "identificador_socio": "2", "nome_socio": f"SOCIO {pessoa[:2]}",
        "qualificacao_socio": "49", "data_entrada_sociedade": entrada, "faixa_etaria": faixa,
        "eh_pf": True, "id_pessoa": pessoa, "socio_documento": "***.123.456-**",
    }  # fmt: skip


def _aresta(origem: str, destino: str, ro: str, rd: str, **extra: object) -> dict[str, object]:
    return {"origem": origem, "destino": destino, "rotulo_origem": ro, "rotulo_destino": rd,
            "fonte": "sintetico", **extra}  # fmt: skip


DISPENSA_II = "Lei 14.133/2021, Art. 75, II"


@pytest.fixture
def tabelas() -> dict[str, pl.DataFrame]:
    """Silver + grafo sintéticos e as tabelas derivadas."""
    t: dict[str, pl.DataFrame] = {
        "meta/piloto": pl.DataFrame({"uf": ["SP"], "nome_municipio": ["MAUA"]}),
        "meta/coletas": pl.DataFrame({"fonte": ["pncp"], "coletado_em": ["2026-03-15"]}),
        "pncp/contratacoes": pl.DataFrame([
            _contratacao("L1", date(2025, 3, 1), 6, "Lei 14.133/2021, Art. 28, I"),
            _contratacao("L2", date(2025, 6, 1), 6, "Lei 14.133/2021, Art. 28, I"),
            *[_contratacao(f"D{i}", date(2025, 4, 1) + timedelta(days=20 * i), 8, DISPENSA_II) for i in range(3)],
        ]),
        "pncp/resultados": pl.DataFrame([
            _resultado("L1", "11111111", 1_000_000, date(2025, 3, 1)),
            _resultado("L2", "22222222", 500_000, date(2025, 6, 1)),
            *[_resultado(f"D{i}", "33333333", 30_000, date(2025, 4, 1) + timedelta(days=20 * i)) for i in range(3)],
        ]),
        "pncp/contratos": pl.DataFrame([{
            "numero_controle_pncp": "CT1", "orgao_cnpj": ORGAO, "orgao_nome": "MUNICIPIO DE MAUA",
            "objeto": "Contrato", "data_assinatura": datetime(2025, 4, 1), "valor_inicial": 100.0,
            "valor_global": 140.0, "url_fonte": f"{URL}/CT1", "fornecedor_tipo_pessoa": "PJ",
            "fornecedor_documento": "11111111000100", "fornecedor_id_pessoa": None,
        }]),
        "receita_cnpj/empresas": pl.DataFrame({
            "cnpj_basico": ["11111111", "33333333", "22222222"],
            "capital_social": [10_000.0, 1_000_000.0, 5_000_000.0],
            "natureza_juridica": ["2062", "2062", "2046"],
        }),
        "receita_cnpj/estabelecimentos": pl.DataFrame({
            "cnpj_basico": ["11111111", "33333333", "22222222"],
            "data_inicio_atividade": [date(2025, 1, 15), date(2010, 1, 1), date(2025, 5, 1)],
        }),
        "receita_cnpj/socios": pl.DataFrame([
            _socio("11111111", P1, "2", date(2025, 2, 1)),
            _socio("11111111", P2, "5", date(2025, 2, 15)),
            _socio("33333333", P1, "2", date(2015, 1, 1)),
            _socio("22222222", P1, "2", date(2025, 5, 1)),
        ]),
        "receita_cnpj/socios_contagem_nacional": pl.DataFrame({
            "id_pessoa": [P1, P2, *[f"x{i}" for i in range(200)]],
            "n_empresas": [50, 1, *[1] * 200],
        }),
        "entidades/ligacoes": pl.DataFrame({
            "id_a": ["p2" * 8], "id_b": ["tse-2024-9"], "probabilidade": [0.9],
            "evidencia": ["nome idêntico; mesmo ano de nascimento"],
        }),
        "grafo/nos/Empresa": pl.DataFrame({
            "id": ["11111111", "33333333", "22222222"], "nome": ["EMPRESA A", "EMPRESA B", "C SA"],
        }),
        "grafo/nos/Pessoa": pl.DataFrame({"id": [P1, P2], "nome": ["SOCIO P1", "SOCIO P2"]}),
        "grafo/nos/Estabelecimento": pl.DataFrame({
            "id": ["11111111000100", "33333333000100", *[f"{i}" * 8 + "000100" for i in range(4, 8)]],
            "situacao_cadastral": ["02"] * 6,
        }),
        "grafo/nos/Endereco": pl.DataFrame({"id": ["end1"], "texto": ["RUA SINTETICA 1"]}),
        "grafo/arestas/LOCALIZADA_EM": pl.DataFrame([
            _aresta(c, "end1", "Estabelecimento", "Endereco")
            for c in ["11111111000100", *[f"{i}" * 8 + "000100" for i in range(4, 8)]]
        ]),
        "grafo/arestas/TEM_TELEFONE": pl.DataFrame([
            _aresta("11111111000100", "tel-ab", "Estabelecimento", "Telefone"),
            _aresta("33333333000100", "tel-ab", "Estabelecimento", "Telefone"),
            # e-mail de contador: 20 empresas, ignorado
            *[_aresta(f"{i:08d}000100", "tel-contador", "Estabelecimento", "Telefone")
              for i in range(20)],
            _aresta("11111111000100", "tel-contador", "Estabelecimento", "Telefone"),
            _aresta("33333333000100", "tel-contador", "Estabelecimento", "Telefone"),
        ]),
        "grafo/nos/Candidatura": pl.DataFrame({
            "id": ["2024-9"], "nome": ["VEREADOR X"], "cargo": ["VEREADOR"],
            "ano_eleicao": ["2024"], "partido": ["PSINT"], "resultado": ["ELEITO POR QP"],
        }),
        "grafo/arestas/CANDIDATO_EM": pl.DataFrame([
            _aresta("tse-2024-9", "2024-9", "Pessoa", "Candidatura"),
        ]),
        "grafo/arestas/DOOU_PARA": pl.DataFrame([
            _aresta(P2, "2024-9", "Pessoa", "Candidatura", valor=1000.0),
        ]),
        "grafo/arestas/FORNECEU_CAMPANHA": pl.DataFrame([
            _aresta("33333333", "2024-9", "Empresa", "Candidatura", valor=5000.0),
        ]),
        "transparencia/sancoes": pl.DataFrame({
            "eh_pf": [False, False], "id_pessoa": [None, None],
            "cnpj_raiz": ["11111111", "33333333"], "cadastro": ["CEIS", "CEIS"],
            "categoria": ["Inidoneidade", "Impedimento"],
            "data_inicio": [date(2025, 1, 1), date(2025, 1, 1)], "data_fim": [None, None],
            "orgao_sancionador": ["CGU", "MINISTERIO X"],
            "esfera_orgao_sancionador": ["FEDERAL", "FEDERAL"], "uf_orgao_sancionador": ["DF", "DF"],
            "abrangencia": ["Todas as Esferas em todos os Poderes",
                            "Em todos os Poderes da Esfera do órgão sancionador"],
            "arquivo_fonte": ["https://portaldatransparencia.gov.br/ceis"] * 2,
        }, schema_overrides={"id_pessoa": pl.String, "data_fim": pl.Date}),
    }  # fmt: skip
    t.update(derivar(t))
    return t

"""Silver sintética mínima para testar entidades e grafo. Nenhum dado real."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from lupa.cleaning.documentos import mascarar_cpf
from lupa.config import ConfigPiloto
from lupa.ingestion.contexto import Contexto
from lupa.privacidade import id_pessoa
from tests.conftest import SAL_TESTE, gerar_cpf, gravar_ibge_sintetico

ORGAO = "46522959000198"
URL = "https://pncp.gov.br/app/editais/sintetico"

PRENOMES = [
    "ANA",
    "BRUNO",
    "CAIO",
    "DORA",
    "EDU",
    "FABIA",
    "GIL",
    "HELO",
    "IVO",
    "JANE",
    "KAUE",
    "LIA",
    "MURILO",
    "NINA",
    "OTAVIO",
    "PAULA",
    "RUI",
    "SARA",
    "TALES",
    "VERA",
]
SOBRENOMES = ["ALFA", "BETA", "GAMA", "DELTA", "EPSILON", "ZETA", "ETA", "TETA", "IOTA",
              "CAPA", "LAMBDA", "OMICRON", "SIGMA", "TAU", "UPSILON", "FI"]  # fmt: skip


@pytest.fixture(autouse=True)
def _sal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUPA_SALT", SAL_TESTE)


@pytest.fixture
def ctx(tmp_path: Path) -> Contexto:
    """Piloto sintético com eleições de 2020 e 2024, referência 2026."""
    config = ConfigPiloto.model_validate(
        {
            "piloto": {
                "municipio_ibge": "3529401",
                "uf": "SP",
                "nome_municipio": "MAUA",
                "municipio_receita": "6689",
                "data_referencia": date(2026, 3, 15),
            },
            "ingestao": {"tse": {"anos": [2020, 2024]}},
            "finalidade": "Teste sintético de interesse público.",
        }
    )
    gravar_ibge_sintetico(tmp_path / "data")
    return Contexto(
        raiz=tmp_path / "data", config=config, agora=lambda: datetime(2026, 3, 15, tzinfo=UTC)
    )


@dataclass(frozen=True)
class Pessoa:
    """Pessoa sintética com CPF válido gerado."""

    nome: str
    cpf: str

    @property
    def id(self) -> str:
        return id_pessoa(self.cpf, self.nome, SAL_TESTE)

    @property
    def mascarado(self) -> str:
        return mascarar_cpf(self.cpf)


JOSE = Pessoa("JOSE SINTETICO DA SILVA", gerar_cpf("123456789"))
JOSE_VARIANTE = Pessoa("JOSE SINTETICO SILVA", JOSE.cpf)  # mesmo CPF, nome abreviado
MARIA_2020 = Pessoa("MARIA FICTICIA SOUZA", gerar_cpf("222333444"))
MARIA_HOMONIMA = Pessoa("MARIA FICTICIA SOUZA", gerar_cpf("555666777"))  # outra pessoa
CARLOS = Pessoa("CARLOS IMAGINARIO", gerar_cpf("888999000"))


def _gravar(ctx: Contexto, fonte: str, tabela: str, linhas: list[dict[str, object]]) -> None:
    pl.DataFrame(linhas, infer_schema_length=None).write_parquet(
        ctx.silver(fonte) / f"{tabela}.parquet"
    )


def _socio(cnpj: str, p: Pessoa, faixa: str) -> dict[str, object]:
    return {
        "cnpj_basico": cnpj, "identificador_socio": "2", "nome_socio": p.nome,
        "qualificacao_socio": "49", "data_entrada_sociedade": date(2020, 1, 1),
        "faixa_etaria": faixa, "eh_pf": True, "id_pessoa": p.id,
        "socio_documento": p.mascarado, "nome_normalizado": p.nome,
    }  # fmt: skip


def _estab(cnpj: str, telefone: str, email: str) -> dict[str, object]:
    return {
        "cnpj": cnpj, "cnpj_basico": cnpj[:8], "nome_fantasia": f"FANTASIA {cnpj[:4]}",
        "identificador_matriz_filial": "1", "situacao_cadastral": "02",
        "data_situacao_cadastral": date(2019, 1, 1), "data_inicio_atividade": date(2019, 1, 1),
        "cnae_fiscal_principal": "4120400", "municipio": "6689", "uf": "SP",
        "no_municipio_piloto": True, "tipo_logradouro": "RUA", "logradouro": "DAS FLORES",
        "numero": "100", "complemento": f"SALA {cnpj[:2]}", "bairro": "CENTRO", "cep": "09300000",
        "telefone_1_completo": telefone, "telefone_2_completo": None, "correio_eletronico": email,
    }  # fmt: skip


def _resultado(ctrl: str, cnpj: str, valor: float) -> dict[str, object]:
    return {
        "numero_controle_pncp": ctrl, "fornecedor_tipo_pessoa": "PJ", "fornecedor_documento": cnpj,
        "fornecedor_id_pessoa": None, "fornecedor_nome_normalizado": f"EMPRESA {cnpj[:4]}",
        "valor_total_homologado": valor, "data_resultado": datetime(2025, 5, 10),
    }  # fmt: skip


def _candidato(
    ano: str, sq: str, p: Pessoa, nascimento: int, com_cpf: bool, turno: str = "1"
) -> dict[str, object]:
    return {
        "ano_eleicao": ano, "turno": turno, "sq_candidato": sq, "nome_urna": p.nome.split()[0],
        "cargo": "VEREADOR", "partido": "PSINT", "numero": "12345", "nome_ue": "MAUA",
        "situacao_candidatura": "APTO", "resultado": "ELEITO" if turno == "2" else "2O TURNO",
        "ano_nascimento": str(nascimento), "nome_normalizado": p.nome,
        "id_pessoa": p.id if com_cpf else None, "cpf_mascarado": p.mascarado if com_cpf else None,
    }  # fmt: skip


def populacao_de_fundo(n: int = 300) -> list[Pessoa]:
    """Pessoas sintéticas distintas, para o Splink estimar as probabilidades u."""
    nomes = [f"{a} {b} {c}" for a in PRENOMES for b in SOBRENOMES for c in SOBRENOMES if b != c]
    return [Pessoa(nomes[i * 7], gerar_cpf(f"{100000000 + i * 7919:09d}")) for i in range(n)]


@pytest.fixture
def silver(ctx: Contexto) -> Contexto:
    """Grava a silver sintética e devolve o contexto."""
    (ctx.bronze("receita_cnpj") / "2026-03").mkdir(parents=True)
    fundo = populacao_de_fundo()
    faixas = "3456789"
    _gravar(ctx, "receita_cnpj", "socios", [
        _socio("11222333", JOSE, "5"),
        _socio("44555666", JOSE, "5"),
        _socio("66666666", MARIA_HOMONIMA, "3"),
        *[_socio(f"{50000000 + i}", p, faixas[i % 7]) for i, p in enumerate(fundo)],
        {**_socio("11222333", JOSE, "5"), "identificador_socio": "1", "eh_pf": False,
         "id_pessoa": None, "socio_documento": "99888777000100", "nome_socio": "HOLDING SA",
         "nome_normalizado": "HOLDING SA", "faixa_etaria": "0"},
    ])  # fmt: skip
    _gravar(ctx, "receita_cnpj", "empresas", [
        {"cnpj_basico": c, "razao_social": f"EMPRESA {c[:4]}", "natureza_juridica": "2062",
         "capital_social": 1000.0, "porte_empresa": "01"}
        for c in ("11222333", "44555666")
    ])  # fmt: skip
    _gravar(ctx, "receita_cnpj", "simples", [
        {"cnpj_basico": "11222333", "opcao_simples": "S", "opcao_mei": "N"},
    ])  # fmt: skip
    _gravar(ctx, "receita_cnpj", "estabelecimentos", [
        _estab("11222333000181", "1140001111", "contato@sintetica.test"),
        _estab("44555666000181", "1140001111", "outro@sintetica.test"),
    ])  # fmt: skip

    _gravar(ctx, "pncp", "contratacoes", [
        {"numero_controle_pncp": ctrl, "orgao_cnpj": ORGAO, "orgao_nome": "MUNICIPIO SINTETICO",
         "orgao_esfera": "M", "orgao_poder": "E", "objeto": f"Objeto {ctrl}",
         "modalidade_nome": "Pregão", "modalidade_id": 6,
         "amparo_legal": "Lei 14.133/2021, Art. 28, I", "situacao": "Divulgada", "valor_estimado": 100.0,
         "valor_homologado": 90.0, "data_publicacao": date(2025, 5, 1), "unidade_nome": "SEC",
         "url_fonte": f"{URL}/{ctrl}"}
        for ctrl in ("CTRL-1", "CTRL-2")
    ])  # fmt: skip
    _gravar(ctx, "pncp", "resultados", [
        _resultado("CTRL-1", "11222333000181", 40.0),
        _resultado("CTRL-1", "11222333000181", 50.0),
        _resultado("CTRL-2", "44555666000181", 70.0),
    ])  # fmt: skip
    _gravar(ctx, "pncp", "contratos", [
        {"numero_controle_pncp": "CT-1", "numero_controle_compra": "CTRL-1", "orgao_cnpj": ORGAO,
         "orgao_nome": "MUNICIPIO SINTETICO", "objeto": "Contrato", "tipo_contrato": "Contrato",
         "valor_inicial": 90.0, "valor_global": 90.0, "data_assinatura": date(2025, 6, 1),
         "data_vigencia_inicio": date(2025, 6, 1), "data_vigencia_fim": date(2026, 6, 1),
         "url_fonte": f"{URL}/CT-1", "fornecedor_tipo_pessoa": "PJ",
         "fornecedor_documento": "11222333000181", "fornecedor_id_pessoa": None,
         "fornecedor_nome_normalizado": "EMPRESA 1122"},
    ])  # fmt: skip

    _gravar(ctx, "tse", "candidatos", [
        _candidato("2020", "100", MARIA_2020, 1980, com_cpf=True),
        _candidato("2024", "200", MARIA_2020, 1980, com_cpf=False),
        _candidato("2024", "200", MARIA_2020, 1980, com_cpf=False, turno="2"),
        _candidato("2024", "300", CARLOS, 1970, com_cpf=False),
    ])  # fmt: skip
    _gravar(ctx, "tse", "receitas", [
        {"ano_eleicao": "2020", "sq_candidato": "100", "doador_tipo": "PF", "valor": v,
         "doador_documento": JOSE.mascarado, "doador_id_pessoa": JOSE.id,
         "doador_nome_normalizado": JOSE.nome}
        for v in (100.0, 200.0)
    ] + [
        {"ano_eleicao": "2024", "sq_candidato": "300", "doador_tipo": "PF", "valor": 50.0,
         "doador_documento": CARLOS.mascarado, "doador_id_pessoa": CARLOS.id,
         "doador_nome_normalizado": CARLOS.nome},
    ])  # fmt: skip
    _gravar(ctx, "tse", "despesas", [
        {"ano_eleicao": "2020", "sq_candidato": "100", "fornecedor_tipo": "PF", "valor": 10.0,
         "fornecedor_documento": JOSE_VARIANTE.mascarado,
         "fornecedor_id_pessoa": JOSE_VARIANTE.id,
         "fornecedor_nome_normalizado": JOSE_VARIANTE.nome},
        {"ano_eleicao": "2020", "sq_candidato": "100", "fornecedor_tipo": "PJ", "valor": 30.0,
         "fornecedor_documento": "44555666000181", "fornecedor_id_pessoa": None,
         "fornecedor_nome_normalizado": "EMPRESA 4455"},
    ])  # fmt: skip

    _gravar(ctx, "transparencia", "sancoes", [
        {"cadastro": "CEIS", "codigo_sancao": cod, "eh_pf": False, "id_pessoa": None,
         "cnpj_raiz": raiz, "documento": f"{raiz}000100", "nome_normalizado": "X",
         "categoria": "Impedimento", "data_inicio": date(2025, 1, 1), "data_fim": None,
         "orgao_sancionador": "CGU", "esfera_orgao_sancionador": "FEDERAL",
         "uf_orgao_sancionador": "DF",
         "abrangencia": "Em todos os Poderes da Esfera do órgão sancionador",
         "fundamentacao_legal": "Lei", "numero_processo": "1",
         "arquivo_fonte": "https://portaldatransparencia.gov.br/download-de-dados/ceis/20260315"}
        for cod, raiz in (("1", "44555666"), ("2", "77777777"))
    ])  # fmt: skip
    return ctx

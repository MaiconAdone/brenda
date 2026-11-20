"""Visões das camadas silver e gold que os agentes podem consultar.

Os agentes nunca leem os Parquet diretamente: só estas visões, que aplicam a política de
privacidade do projeto (``docs/lgpd.md``) antes de qualquer dado chegar a um modelo:

- nome de pessoa física vira nulo (a pessoa é identificada por ``id_pessoa``, um HMAC, e
  pelo CPF mascarado ``***.123.456-**``);
- a razão social de MEI perde o CPF que a Receita põe no fim do nome;
- logradouro e número de MEI (em geral a casa do titular) viram nulos; o endereço entra
  como hash, para comparar endereços sem expô-los;
- telefone e e-mail não entram (o compartilhamento de contato aparece no grafo, como HMAC).

Candidatos, órgãos e empresas (não MEI) aparecem com nome, por serem informação pública de
interesse coletivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Visao:
    """Uma visão sobre um Parquet, com colunas trocadas por expressões."""

    nome: str
    arquivo: str  # relativo à raiz de dados
    descricao: str
    trocas: dict[str, str] = field(default_factory=dict)  # coluna -> expressão SQL
    remover: tuple[str, ...] = ()
    juncao: str = ""  # trecho FROM extra (ex.: LEFT JOIN), com a tabela base como "t"


def _pf(cond: str, *colunas: str) -> dict[str, str]:
    return {c: f"CASE WHEN {cond} THEN NULL ELSE t.{c} END" for c in colunas}


_ENDERECO = "sha256(concat_ws('|', t.logradouro, t.numero, t.complemento, t.cep))"
_MEI = "coalesce(s.opcao_mei, '') = 'S'"

VISOES: tuple[Visao, ...] = (
    # ---------------------------------------------------------------- PNCP
    Visao(
        "pncp_contratacoes",
        "silver/pncp/contratacoes.parquet",
        "Contratações (licitações, dispensas) publicadas no PNCP.",
    ),
    Visao("pncp_itens", "silver/pncp/itens.parquet", "Itens de cada contratação."),
    Visao(
        "pncp_resultados",
        "silver/pncp/resultados.parquet",
        "Vencedores homologados por item.",
        _pf("t.fornecedor_tipo_pessoa = 'PF'", "fornecedor_nome", "fornecedor_nome_normalizado"),
    ),
    Visao(
        "pncp_contratos",
        "silver/pncp/contratos.parquet",
        "Contratos e aditivos.",
        _pf("t.fornecedor_tipo_pessoa = 'PF'", "fornecedor_nome", "fornecedor_nome_normalizado"),
    ),
    # ---------------------------------------------------------------- Receita
    Visao(
        "receita_empresas",
        "silver/receita_cnpj/empresas.parquet",
        "Empresas (CNPJ raiz): razão social, natureza jurídica, capital, porte.",
        {"razao_social": r"regexp_replace(t.razao_social, '\s*\d{11}$', ' [CPF omitido]')"},
    ),
    Visao(
        "receita_estabelecimentos",
        "silver/receita_cnpj/estabelecimentos.parquet",
        "Estabelecimentos (CNPJ completo): situação, CNAE, endereço (sem logradouro de MEI), "
        "datas. endereco_hash permite comparar endereços.",
        {
            "logradouro": f"CASE WHEN {_MEI} THEN NULL ELSE t.logradouro END",
            "numero": f"CASE WHEN {_MEI} THEN NULL ELSE t.numero END",
            "complemento": f"CASE WHEN {_MEI} THEN NULL ELSE t.complemento END",
            "endereco_hash": _ENDERECO,
        },
        remover=(
            "ddd_1",
            "telefone_1",
            "ddd_2",
            "telefone_2",
            "ddd_fax",
            "fax",
            "correio_eletronico",
            "telefone_1_completo",
            "telefone_2_completo",
        ),
        juncao="LEFT JOIN read_parquet('{raiz}/silver/receita_cnpj/simples.parquet') s "
        "ON s.cnpj_basico = t.cnpj_basico",
    ),
    Visao(
        "receita_socios",
        "silver/receita_cnpj/socios.parquet",
        "Quadro societário; pessoa física identificada por id_pessoa e CPF mascarado.",
        {**_pf("t.eh_pf", "nome_socio", "nome_normalizado"), "nome_representante": "NULL"},
    ),
    Visao("receita_simples", "silver/receita_cnpj/simples.parquet", "Opção pelo Simples e MEI."),
    Visao(
        "receita_socios_contagem_nacional",
        "silver/receita_cnpj/socios_contagem_nacional.parquet",
        "Em quantas empresas do país cada pessoa é sócia.",
    ),
    Visao("receita_cnaes", "silver/receita_cnpj/dominio_cnaes.parquet", "Tabela de CNAEs."),
    Visao(
        "receita_naturezas", "silver/receita_cnpj/dominio_naturezas.parquet", "Naturezas jurídicas."
    ),
    Visao(
        "receita_qualificacoes",
        "silver/receita_cnpj/dominio_qualificacoes.parquet",
        "Qualificações de sócio.",
    ),
    Visao(
        "receita_municipios",
        "silver/receita_cnpj/dominio_municipios.parquet",
        "Códigos de município da Receita.",
    ),
    # ---------------------------------------------------------------- CGU
    Visao(
        "transparencia_sancoes",
        "silver/transparencia/sancoes.parquet",
        "Sanções CEIS/CNEP vigentes.",
        _pf("t.eh_pf", "nome", "nome_normalizado"),
    ),
    Visao(
        "transparencia_bolsa_familia",
        "silver/transparencia/bolsa_familia_cruzamentos.parquet",
        "Meses em que sócios, doadores ou fornecedores de campanha receberam o benefício.",
    ),
    # ---------------------------------------------------------------- TSE
    Visao("tse_candidatos", "silver/tse/candidatos.parquet", "Candidaturas."),
    Visao(
        "tse_receitas",
        "silver/tse/receitas.parquet",
        "Doações a campanhas.",
        _pf("t.doador_tipo = 'PF'", "doador_nome", "doador_nome_rfb", "doador_nome_normalizado"),
    ),
    Visao(
        "tse_despesas",
        "silver/tse/despesas.parquet",
        "Gastos de campanha.",
        _pf(
            "t.fornecedor_tipo = 'PF'",
            "fornecedor_nome",
            "fornecedor_nome_rfb",
            "fornecedor_nome_normalizado",
        ),
    ),
    Visao("ibge_municipios", "silver/ibge/municipios.parquet", "Municípios do IBGE."),
    # ---------------------------------------------------------------- gold
    # As evidências dos alertas citam sócios pelo nome: elas saem só por
    # sinais_da_entidade, que as anonimiza.
    Visao(
        "alertas",
        "gold/alertas/alertas.parquet",
        "Sinais disparados por entidade (as evidências estão em sinais_da_entidade).",
        remover=("evidencias",),
    ),
    Visao("scores", "gold/alertas/scores.parquet", "Score de risco e posição nos rankings."),
    Visao("ml_anomalias", "gold/ml/anomalias.parquet", "Isolation Forest + SHAP (M01)."),
    Visao("ml_features", "gold/ml/features.parquet", "Atributos usados no M01."),
    Visao("ml_nucleos", "gold/ml/nucleos.parquet", "Núcleos de fornecedoras (M02)."),
    Visao(
        "pessoas",
        "gold/entidades/pessoas.parquet",
        "Pessoas resolvidas (no_id = id do nó Pessoa).",
        {"nome_normalizado": "NULL"},
    ),
    Visao(
        "ligacoes_pessoas",
        "gold/entidades/ligacoes.parquet",
        "Pares de registros que provavelmente são a mesma pessoa (Splink).",
    ),
)

# Nós e arestas do grafo: uma visão por rótulo/tipo e uma união de todas as arestas.
ROTULOS_NOS = (
    "Candidatura", "Contrato", "Email", "Empresa", "Endereco", "Estabelecimento", "Licitacao",
    "OrgaoPublico", "Pessoa", "Sancao", "Telefone",
)  # fmt: skip
TROCAS_NOS: dict[str, dict[str, str]] = {
    "Pessoa": {"nome": "NULL"},
    "Empresa": {"nome": r"regexp_replace(t.nome, '\s*\d{11}$', ' [CPF omitido]')"},
}


def visoes_grafo(raiz: Path) -> list[Visao]:
    """Visões dos nós e arestas que existirem em ``gold/grafo``."""
    saida = []
    for rotulo in ROTULOS_NOS:
        if (raiz / "gold" / "grafo" / "nos" / f"{rotulo}.parquet").exists():
            saida.append(Visao(f"grafo_no_{rotulo.lower()}", f"gold/grafo/nos/{rotulo}.parquet",
                               f"Nós {rotulo} do grafo.", TROCAS_NOS.get(rotulo, {})))  # fmt: skip
    pasta = raiz / "gold" / "grafo" / "arestas"
    for arquivo in sorted(pasta.glob("*.parquet")) if pasta.exists() else []:
        saida.append(Visao(f"grafo_aresta_{arquivo.stem.lower()}",
                           f"gold/grafo/arestas/{arquivo.name}",
                           f"Arestas {arquivo.stem} (origem -> destino)."))  # fmt: skip
    return saida

"""Tabelas de entrada dos sinais.

:func:`carregar` lê a silver e o grafo gold e acrescenta tabelas derivadas
(prefixo ``derivado/``) usadas por vários sinais, para que cada sinal fique curto e
todos vejam os mesmos dados:

- ``derivado/vitorias``: uma linha por fornecedor × licitação vencida;
- ``derivado/contratos``: contratos com o fornecedor identificado;
- ``derivado/socios``: quadro societário com o sócio como nó do grafo;
- ``derivado/mesma_pessoa``: ligações do Splink nos dois sentidos;
- ``derivado/nomes``: nome de exibição de cada entidade;
- ``meta/coletas``: data da coleta de cada fonte (para citar nas evidências).

Entidades são identificadas como ``Empresa:<cnpj raiz>`` ou ``Pessoa:<id>``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import polars as pl

from lupa.signals.base import Fonte

Tabelas = Mapping[str, pl.DataFrame]

ELEITOS = ["ELEITO", "ELEITO POR QP", "ELEITO POR MÉDIA"]

# Naturezas jurídicas fora dos sinais de fachada, laranja e conluio: órgãos públicos
# (1xxx), empresa pública (2011), economia mista (2038), S.A. aberta (2046) e consórcio
# de sociedades (2151). Diretores, agências e centrais de atendimento delas disparam
# esses sinais sem
# significado; consórcios são criados para a licitação e têm as consorciadas como sócias.
NATUREZAS_EXCLUIDAS = ("2011", "2038", "2046", "2151")
URL_RECEITA = "https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK"
URL_TSE = "https://dadosabertos.tse.jus.br/"
URL_BOLSA_FAMILIA = "https://portaldatransparencia.gov.br/beneficios/novo-bolsa-familia"


def raiz(coluna: str) -> pl.Expr:
    """CNPJ raiz (8 caracteres) de uma coluna de CNPJ."""
    return pl.col(coluna).str.replace_all(r"[^0-9A-Za-z]", "").str.to_uppercase().str.slice(0, 8)


def entidade(col_tipo: str, col_documento: str, col_id_pessoa: str) -> pl.Expr:
    """``Pessoa:<id>`` para PF e ``Empresa:<raiz>`` para PJ."""
    return (
        pl.when(pl.col(col_tipo) == "PF")
        .then(pl.lit("Pessoa:") + pl.col(col_id_pessoa))
        .otherwise(pl.lit("Empresa:") + raiz(col_documento))
    )


def fonte(t: Tabelas, nome: str, url: str) -> Fonte:
    """Referência à fonte com a data em que foi coletada."""
    coletas = t.get("meta/coletas")
    data = ""
    if coletas is not None:
        linha = coletas.filter(pl.col("fonte") == nome)
        data = linha["coletado_em"][0] if linha.height else ""
    return Fonte(nome, url, data)


def _coletas(arquivo: Path) -> pl.DataFrame:
    ultima: dict[str, str] = {}
    if arquivo.exists():
        with arquivo.open(encoding="utf-8") as f:
            for linha in f:
                r = json.loads(linha)
                ultima[r["fonte"]] = max(ultima.get(r["fonte"], ""), r.get("coletado_em", ""))
    return pl.DataFrame(
        {"fonte": list(ultima), "coletado_em": [v[:10] for v in ultima.values()]},
        schema={"fonte": pl.String, "coletado_em": pl.String},
    )


def _ler_pasta(base: Path, prefixo: str, t: dict[str, pl.DataFrame]) -> None:
    if not base.exists():
        return
    for arquivo in sorted(base.rglob("*.parquet")):
        if "dominio_" in arquivo.name:
            continue
        nome = arquivo.relative_to(base).with_suffix("").as_posix()
        t[f"{prefixo}{nome}"] = pl.read_parquet(arquivo)


def carregar(
    raiz_dados: Path, uf: str | None = None, municipio: str | None = None
) -> dict[str, pl.DataFrame]:
    """Lê silver + gold e calcula as tabelas derivadas."""
    t: dict[str, pl.DataFrame] = {}
    t["meta/piloto"] = pl.DataFrame(
        {"uf": [uf], "nome_municipio": [municipio]},
        schema={"uf": pl.String, "nome_municipio": pl.String},
    )
    _ler_pasta(raiz_dados / "silver", "", t)
    _ler_pasta(raiz_dados / "gold", "", t)
    t["meta/coletas"] = _coletas(raiz_dados / "linhagem.jsonl")
    t.update(derivar(t))
    return t


def derivar(t: Tabelas) -> dict[str, pl.DataFrame]:
    """Tabelas derivadas a partir da silver e do grafo."""
    d: dict[str, pl.DataFrame] = {}
    contratacoes = t.get("pncp/contratacoes")
    resultados = t.get("pncp/resultados")
    if contratacoes is not None and resultados is not None:
        d["derivado/vitorias"] = (
            resultados.join(
                contratacoes.select(
                    "numero_controle_pncp", "orgao_cnpj", "orgao_nome", "orgao_esfera",
                    "modalidade_id",
                    "amparo_legal", "objeto", "url_fonte", "data_publicacao",
                ),
                on="numero_controle_pncp",
            )
            .with_columns(
                entidade=entidade(
                    "fornecedor_tipo_pessoa", "fornecedor_documento", "fornecedor_id_pessoa"
                ),
                data=pl.coalesce("data_resultado", "data_publicacao").cast(pl.Date),
            )
            .group_by("entidade", "numero_controle_pncp")
            .agg(
                pl.col("fornecedor_documento").first().alias("documento"),
                pl.col("fornecedor_nome_normalizado").first().alias("nome"),
                pl.col("orgao_cnpj").first(),
                pl.col("orgao_nome").first(),
                pl.col("orgao_esfera").first(),
                pl.col("modalidade_id").first(),
                pl.col("amparo_legal").first(),
                pl.col("objeto").first(),
                pl.col("url_fonte").first(),
                pl.col("data").min(),
                pl.col("valor_total_homologado").sum().cast(pl.Float64).alias("valor"),
            )
            .filter(pl.col("entidade").is_not_null())
            .sort("entidade", "data")
        )  # fmt: skip
        if (empresas := t.get("receita_cnpj/empresas")) is not None:
            d["derivado/vitorias"] = d["derivado/vitorias"].join(
                empresas.select(
                    entidade=pl.lit("Empresa:") + pl.col("cnpj_basico"),
                    natureza_juridica=pl.col("natureza_juridica"),
                ),
                on="entidade",
                how="left",
            )

    contratos = t.get("pncp/contratos")
    if contratos is not None:
        d["derivado/contratos"] = contratos.select(
            entidade=entidade(
                "fornecedor_tipo_pessoa", "fornecedor_documento", "fornecedor_id_pessoa"
            ),
            controle=pl.col("numero_controle_pncp"),
            orgao_cnpj=pl.col("orgao_cnpj"),
            orgao_nome=pl.col("orgao_nome"),
            objeto=pl.col("objeto"),
            data=pl.col("data_assinatura").cast(pl.Date),
            valor_inicial=pl.col("valor_inicial").cast(pl.Float64),
            valor_global=pl.col("valor_global").cast(pl.Float64),
            url_fonte=pl.col("url_fonte"),
        ).filter(pl.col("entidade").is_not_null())
        if contratacoes is not None:
            esferas = contratacoes.select("orgao_cnpj", "orgao_esfera").unique("orgao_cnpj")
            d["derivado/contratos"] = d["derivado/contratos"].join(
                esferas, on="orgao_cnpj", how="left"
            )

    socios = t.get("receita_cnpj/socios")
    if socios is not None:
        d["derivado/socios"] = socios.select(
            empresa=pl.lit("Empresa:") + pl.col("cnpj_basico"),
            socio=pl.when(pl.col("eh_pf"))
            .then(pl.lit("Pessoa:") + pl.col("id_pessoa"))
            .when(pl.col("identificador_socio") == "1")
            .then(pl.lit("Empresa:") + raiz("socio_documento")),
            eh_pf=pl.col("eh_pf"),
            nome_socio=pl.col("nome_socio"),
            documento=pl.col("socio_documento"),
            faixa_etaria=pl.col("faixa_etaria").cast(pl.String),
            data_entrada=pl.col("data_entrada_sociedade").cast(pl.Date),
            qualificacao=pl.col("qualificacao_socio"),
        ).filter(pl.col("socio").is_not_null())

    ligacoes = t.get("entidades/ligacoes")
    if ligacoes is not None:
        a = ligacoes.select(
            a=pl.lit("Pessoa:") + pl.col("id_a"),
            b=pl.lit("Pessoa:") + pl.col("id_b"),
            probabilidade=pl.col("probabilidade"),
            evidencia=pl.col("evidencia"),
        )
        d["derivado/mesma_pessoa"] = pl.concat(
            [a, a.select(a="b", b="a", probabilidade="probabilidade", evidencia="evidencia")]
        )

    nomes = []
    if (empresas := t.get("grafo/nos/Empresa")) is not None:
        nomes.append(empresas.select(entidade=pl.lit("Empresa:") + pl.col("id"), nome="nome"))
    if (pessoas := t.get("grafo/nos/Pessoa")) is not None:
        nomes.append(pessoas.select(entidade=pl.lit("Pessoa:") + pl.col("id"), nome="nome"))
    if (candidaturas := t.get("grafo/nos/Candidatura")) is not None:
        nomes.append(
            candidaturas.select(
                entidade=pl.lit("Candidatura:") + pl.col("id"),
                nome=pl.concat_str(
                    pl.col("nome"), pl.lit(" ("), pl.col("cargo").str.to_lowercase(),
                    pl.lit(" "), pl.col("ano_eleicao").cast(pl.String), pl.lit(")"),
                ),
            )
        )  # fmt: skip
    if nomes:
        d["derivado/nomes"] = pl.concat(nomes).unique("entidade")

    if (bolsa := t.get("transparencia/bolsa_familia_cruzamentos")) is not None:
        d["derivado/beneficios"] = (
            bolsa.sort("mes")
            .group_by("id_pessoa")
            .agg(
                pl.col("mes").alias("meses"),
                pl.len().alias("n_meses"),
                pl.col("mes").first().alias("primeiro_mes"),
                pl.col("mes").last().alias("ultimo_mes"),
                pl.col("valor_total").sum().alias("total_recebido"),
            )
            .with_columns(pessoa=pl.lit("Pessoa:") + pl.col("id_pessoa"))
            .select("pessoa", "meses", "n_meses", "primeiro_mes", "ultimo_mes", "total_recebido")
        )
    return d


def elegiveis(v: pl.DataFrame) -> pl.DataFrame:
    """Vitórias de empresas privadas (sem as naturezas de ``NATUREZAS_EXCLUIDAS``)."""
    v = v.filter(pl.col("entidade").str.starts_with("Empresa:"))
    if "natureza_juridica" not in v.columns:
        return v
    natureza = pl.col("natureza_juridica").fill_null("")
    return v.filter(~natureza.str.starts_with("1") & ~natureza.is_in(NATUREZAS_EXCLUIDAS))


def expandir_pessoas(t: Tabelas, df: pl.DataFrame, coluna: str) -> pl.DataFrame:
    """Acrescenta a cada pessoa de ``coluna`` as pessoas prováveis equivalentes.

    Devolve ``df`` com ``coluna`` possivelmente trocada pela equivalente e duas colunas
    novas: ``confianca`` (1 para a própria pessoa, a probabilidade do Splink para a
    equivalente) e ``via`` (evidência da ligação, nula quando é a própria pessoa).
    """
    proprio = df.with_columns(confianca=pl.lit(1.0), via=pl.lit(None, dtype=pl.String))
    mesma = t.get("derivado/mesma_pessoa")
    if mesma is None or mesma.is_empty():
        return proprio
    equivalentes = (
        df.join(mesma, left_on=coluna, right_on="a")
        .drop(coluna)
        .rename({"b": coluna, "probabilidade": "confianca", "evidencia": "via"})
        .select(proprio.columns)
    )
    return pl.concat([proprio, equivalentes])

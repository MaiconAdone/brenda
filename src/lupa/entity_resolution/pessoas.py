"""Resolução de entidades de pessoas físicas.

Duas camadas:

1. **Determinística.** Cada menção de pessoa nas tabelas silver já tem ``id_pessoa``
   (hash de miolo do CPF + nome normalizado). Menções com o mesmo id são a mesma pessoa.
   Candidatos sem CPF publicado (TSE 2024) recebem um id próprio por candidatura.
2. **Probabilística (Splink).** Compara pessoas com ids diferentes usando nome, miolo do
   CPF e idade (ano de nascimento do TSE ou faixa etária do QSA). O resultado são
   ligações ``MESMA_ENTIDADE_PROVAVEL`` com probabilidade e o peso de cada comparação,
   nunca uma fusão silenciosa.

Regra do projeto: **ninguém é ligado só pelo nome.** Pares sem miolo do CPF nos dois
lados e sem idade nos dois lados são descartados, por mais parecidos que sejam os nomes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl
import splink.comparison_level_library as cll
import splink.comparison_library as cl
from splink import Linker, SettingsCreator, block_on
from splink.backends.duckdb import DuckDBAPI

from lupa.config import ConfigResolucao
from lupa.ingestion.contexto import Contexto

log = logging.getLogger("lupa.entidades")

# Faixas etárias do QSA da Receita (código → idade mínima e máxima).
FAIXAS_ETARIAS = {
    "1": (0, 12),
    "2": (13, 20),
    "3": (21, 30),
    "4": (31, 40),
    "5": (41, 50),
    "6": (51, 60),
    "7": (61, 70),
    "8": (71, 80),
    "9": (81, 120),
}

ESQUEMA_MENCAO = {
    "no_id": pl.String,
    "origem": pl.String,
    "nome_normalizado": pl.String,
    "documento": pl.String,
    "idade_min": pl.Int32,
    "idade_max": pl.Int32,
}

ESQUEMA_LIGACAO = {
    "id_a": pl.String,
    "id_b": pl.String,
    "probabilidade": pl.Float64,
    "peso": pl.Float64,
    "nivel_nome": pl.Int32,
    "nivel_miolo": pl.Int32,
    "nivel_idade": pl.Int32,
    "mw_nome": pl.Float64,
    "mw_miolo": pl.Float64,
    "mw_idade": pl.Float64,
    "evidencia": pl.String,
}

# Probabilidade m (comparação | mesma pessoa), fixada por julgamento e documentada em
# docs/metodologia.md. As probabilidades u são estimadas nos próprios dados.
M_NOME = [0.80, 0.12, 0.05, 0.03]  # idêntico, JW ≥ 0,95, JW ≥ 0,88, diferente
M_MIOLO = [0.97, 0.03]  # igual, diferente
M_IDADE = [0.85, 0.12, 0.03]  # mesmo ano, faixa compatível, incompatível

_ROTULOS = {
    "nome": {3: "nome idêntico", 2: "nome quase idêntico", 1: "nome parecido", 0: "nome diferente"},
    "miolo": {1: "miolo do CPF igual", 0: "miolo do CPF diferente"},
    "idade": {2: "mesmo ano de nascimento", 1: "idade compatível", 0: "idade incompatível"},
}


def _ler(caminho: Path) -> pl.DataFrame | None:
    return pl.read_parquet(caminho) if caminho.exists() else None


def _mencao(df: pl.DataFrame, origem: str, **colunas: pl.Expr) -> pl.DataFrame:
    base = {
        "origem": pl.lit(origem),
        "idade_min": pl.lit(None, dtype=pl.Int32),
        "idade_max": pl.lit(None, dtype=pl.Int32),
        **colunas,
    }
    return df.select(**base).filter(pl.col("no_id").is_not_null()).cast(ESQUEMA_MENCAO)  # type: ignore[arg-type]


def mencoes(ctx: Contexto) -> pl.DataFrame:
    """Todas as menções de pessoa física nas tabelas silver, com origem."""
    ano_ref = ctx.hoje().year
    pedacos: list[pl.DataFrame] = []

    socios = _ler(ctx.silver("receita_cnpj") / "socios.parquet")
    if socios is not None:
        faixa = pl.col("faixa_etaria").cast(pl.String)
        pedacos.append(
            _mencao(
                socios.filter(pl.col("eh_pf")),
                "socio",
                no_id=pl.col("id_pessoa"),
                nome_normalizado=pl.col("nome_normalizado"),
                documento=pl.col("socio_documento"),
                idade_min=faixa.replace_strict(
                    {k: v[0] for k, v in FAIXAS_ETARIAS.items()}, default=None
                ),
                idade_max=faixa.replace_strict(
                    {k: v[1] for k, v in FAIXAS_ETARIAS.items()}, default=None
                ),
            )
        )

    for caminho, prefixo, origem in (
        (ctx.silver("pncp") / "resultados.parquet", "fornecedor", "fornecedor_pncp"),
        (ctx.silver("pncp") / "contratos.parquet", "fornecedor", "fornecedor_pncp"),
        (ctx.silver("tse") / "receitas.parquet", "doador", "doador_campanha"),
        (ctx.silver("tse") / "despesas.parquet", "fornecedor", "fornecedor_campanha"),
    ):
        df = _ler(caminho)
        if df is not None:
            pedacos.append(
                _mencao(
                    df,
                    origem,
                    no_id=pl.col(f"{prefixo}_id_pessoa"),
                    nome_normalizado=pl.col(f"{prefixo}_nome_normalizado"),
                    documento=pl.col(f"{prefixo}_documento"),
                )
            )

    candidatos = _ler(ctx.silver("tse") / "candidatos.parquet")
    if candidatos is not None:
        nascimento = pl.col("ano_nascimento").cast(pl.Int32, strict=False)
        nascimento = pl.when(nascimento.is_between(1900, ano_ref)).then(nascimento)
        pedacos.append(
            _mencao(
                candidatos,
                "candidato",
                no_id=pl.coalesce(
                    pl.col("id_pessoa"),
                    pl.concat_str(
                        pl.lit("tse-"), pl.col("ano_eleicao"), pl.lit("-"), pl.col("sq_candidato")
                    ),
                ),
                nome_normalizado=pl.col("nome_normalizado"),
                documento=pl.col("cpf_mascarado"),
                idade_min=pl.lit(ano_ref) - nascimento - 1,
                idade_max=pl.lit(ano_ref) - nascimento,
            )
        )

    sancoes = _ler(ctx.silver("transparencia") / "sancoes.parquet")
    if sancoes is not None:
        pedacos.append(
            _mencao(
                sancoes.filter(pl.col("eh_pf")),
                "sancionado",
                no_id=pl.col("id_pessoa"),
                nome_normalizado=pl.col("nome_normalizado"),
                documento=pl.col("documento"),
            )
        )

    if not pedacos:
        return pl.DataFrame(schema=ESQUEMA_MENCAO)
    return pl.concat(pedacos).unique()


def consolidar(mencoes_df: pl.DataFrame) -> pl.DataFrame:
    """Uma linha por pessoa (``no_id``), juntando as informações das menções.

    A idade é a interseção dos intervalos de todas as menções. Se forem incompatíveis
    entre si, fica nula (dado inconsistente não vira evidência).
    """
    pessoas = mencoes_df.group_by("no_id").agg(
        pl.col("nome_normalizado").drop_nulls().mode().sort().first(),
        pl.col("documento").drop_nulls().sort().first(),
        pl.col("idade_min").max(),
        pl.col("idade_max").min(),
        pl.col("origem").unique().sort().str.join(";").alias("origens"),
        pl.len().alias("n_mencoes"),
    )
    inconsistente = pl.col("idade_min") > pl.col("idade_max")
    return pessoas.with_columns(
        idade_min=pl.when(inconsistente).then(None).otherwise(pl.col("idade_min")),
        idade_max=pl.when(inconsistente).then(None).otherwise(pl.col("idade_max")),
    ).sort("no_id")


def _entrada_splink(pessoas: pl.DataFrame) -> pl.DataFrame:
    digitos = pl.col("documento").str.replace_all(r"\D", "")
    partes = pl.col("nome_normalizado").str.split(" ")
    return pessoas.select(
        unique_id=pl.col("no_id"),
        nome_normalizado=pl.col("nome_normalizado"),
        primeiro_nome=partes.list.first(),
        ultimo_nome=partes.list.last(),
        miolo=pl.when(digitos.str.len_chars() == 6).then(digitos),
        idade_min=pl.col("idade_min"),
        idade_max=pl.col("idade_max"),
    ).filter(pl.col("nome_normalizado").is_not_null())


def _configuracao(prior: float) -> SettingsCreator:
    idade = cl.CustomComparison(
        output_column_name="idade",
        comparison_levels=[
            cll.CustomLevel("idade_min_l IS NULL OR idade_min_r IS NULL").configure(
                is_null_level=True
            ),
            cll.CustomLevel(
                "idade_max_l - idade_min_l <= 1 AND idade_max_r - idade_min_r <= 1 "
                "AND idade_min_l = idade_min_r",
                "mesmo ano de nascimento",
            ).configure(m_probability=M_IDADE[0]),
            cll.CustomLevel(
                "idade_min_l <= idade_max_r AND idade_min_r <= idade_max_l", "idade compatível"
            ).configure(m_probability=M_IDADE[1]),
            cll.ElseLevel().configure(m_probability=M_IDADE[2]),
        ],
    )
    return SettingsCreator(
        link_type="dedupe_only",
        comparisons=[
            cl.JaroWinklerAtThresholds("nome_normalizado", [0.95, 0.88]).configure(
                m_probabilities=M_NOME, term_frequency_adjustments=True
            ),
            cl.ExactMatch("miolo").configure(m_probabilities=M_MIOLO),
            idade,
        ],
        blocking_rules_to_generate_predictions=[
            block_on("miolo"),
            block_on("nome_normalizado"),
            block_on("primeiro_nome", "ultimo_nome"),
        ],
        probability_two_random_records_match=prior,
        # Mantém o peso de cada comparação (mw_*) para explicar cada ligação.
        retain_intermediate_calculation_columns=True,
    )


def _evidencia(nome: int, miolo: int, idade: int) -> str:
    partes = [
        _ROTULOS["nome"].get(nome),
        _ROTULOS["miolo"].get(miolo),
        _ROTULOS["idade"].get(idade),
    ]
    return "; ".join(p for p in partes if p)


def resolver(pessoas: pl.DataFrame, cfg: ConfigResolucao) -> pl.DataFrame:
    """Ligações prováveis entre pessoas diferentes, acima do limiar configurado."""
    entrada = _entrada_splink(pessoas)
    n = entrada.height
    if n < 2:
        return pl.DataFrame(schema=ESQUEMA_LIGACAO)
    # Espera-se que uma fração das pessoas tenha uma duplicata:
    # pares verdadeiros ≈ fração × n, de n(n−1)/2 pares possíveis.
    prior = min(0.5, 2 * cfg.fracao_esperada_duplicatas / (n - 1))
    db = DuckDBAPI()
    linker = Linker(
        db.register(entrada.to_arrow(), dataset_display_name="pessoas"),
        _configuracao(prior),
        log_level=logging.WARNING,
    )
    linker.training.estimate_u_using_random_sampling(max_pairs=2e6, seed=cfg.semente)
    previsao = linker.inference.predict(threshold_match_probability=cfg.limiar_probabilidade)
    pares: pl.DataFrame = previsao.as_duckdbpyrelation().pl()
    if pares.is_empty():
        return pl.DataFrame(schema=ESQUEMA_LIGACAO)

    # Nunca só pelo nome: exige miolo nos dois lados, ou idade nos dois lados com nome
    # idêntico/quase idêntico. Sem miolo, "nome parecido" + mesmo ano liga homônimos
    # parciais (ex.: mesmo prenome e sobrenome final), o que foi visto nos dados reais.
    pares = pares.filter(
        (pl.col("gamma_miolo") >= 0)
        | ((pl.col("gamma_idade") >= 0) & (pl.col("gamma_nome_normalizado") >= 2))
    )
    ligacoes = pares.select(
        id_a=pl.min_horizontal("unique_id_l", "unique_id_r"),
        id_b=pl.max_horizontal("unique_id_l", "unique_id_r"),
        probabilidade=pl.col("match_probability"),
        peso=pl.col("match_weight"),
        nivel_nome=pl.col("gamma_nome_normalizado"),
        nivel_miolo=pl.col("gamma_miolo"),
        nivel_idade=pl.col("gamma_idade"),
        mw_nome=pl.col("mw_nome_normalizado"),
        mw_miolo=pl.col("mw_miolo"),
        mw_idade=pl.col("mw_idade"),
    )
    ligacoes = ligacoes.with_columns(
        evidencia=pl.struct("nivel_nome", "nivel_miolo", "nivel_idade").map_elements(
            lambda s: _evidencia(s["nivel_nome"], s["nivel_miolo"], s["nivel_idade"]),
            return_dtype=pl.String,
        )
    )
    log.info(
        "Splink: %d pessoas, %d ligações acima de %.2f",
        n,
        ligacoes.height,
        cfg.limiar_probabilidade,
    )
    resultado: pl.DataFrame = ligacoes.cast(ESQUEMA_LIGACAO).sort("id_a", "id_b")  # type: ignore[arg-type]
    return resultado

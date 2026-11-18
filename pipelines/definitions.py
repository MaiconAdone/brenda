"""Ponto de entrada do Dagster (``dagster dev -m pipelines.definitions``).

Cada etapa de ingestão é um asset (bronze + silver da fonte). As falhas de qualidade
viram metadados e um *asset check* por asset.
"""

from collections.abc import Callable

from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    AssetExecutionContext,
    AssetsDefinition,
    Definitions,
    MaterializeResult,
    MetadataValue,
    asset,
    define_asset_job,
)

from lupa.config import carregar_env, carregar_regras
from lupa.ingestion.contexto import Contexto, contexto_padrao
from lupa.ingestion.etapas import ETAPAS, Resultado

carregar_env()


@asset(group_name="configuracao", description="Valida config/piloto.yaml e config/regras.yaml.")
def configuracao_validada(context: AssetExecutionContext) -> MaterializeResult:
    """Falha cedo se a configuração estiver inválida."""
    ctx = contexto_padrao()
    regras = carregar_regras()
    municipio, uf = ctx.config.piloto.exigir_definido()
    inicio, fim = ctx.config.piloto.periodo()
    context.log.info(f"Piloto {municipio}/{uf}, {inicio} a {fim}")
    return MaterializeResult(
        metadata={
            "municipio_ibge": municipio,
            "uf": uf,
            "periodo": f"{inicio} a {fim}",
            "sinais_ativos": sum(s.ativo for s in regras.sinais.values()),
        }
    )


DEPENDENCIAS = {
    "ibge": ["configuracao_validada"],
    "pncp": ["ibge"],
    "tse": ["ibge"],
    "receita_cnpj": ["pncp"],
    "sancoes": ["receita_cnpj"],
    "bolsa_familia": ["receita_cnpj"],
}


def _materializar(resultado: Resultado) -> MaterializeResult:
    return MaterializeResult(
        metadata={
            **{f"linhas/{n}": MetadataValue.int(v) for n, v in resultado.resumo().items()},
            "falhas_qualidade": MetadataValue.int(len(resultado.falhas)),
        },
        check_results=[
            AssetCheckResult(
                check_name="qualidade",
                passed=not resultado.falhas,
                severity=AssetCheckSeverity.WARN,
                metadata={
                    "falhas": MetadataValue.md(
                        "\n".join(
                            f"- {f.tabela} `{f.regra}` {f.detalhe}: {f.linhas}"
                            for f in resultado.falhas
                        )
                        or "nenhuma"
                    )
                },
            )
        ],
    )


def _criar_asset(nome: str, funcao: Callable[[Contexto], Resultado]) -> AssetsDefinition:
    @asset(
        name=nome,
        group_name="ingestao",
        deps=DEPENDENCIAS[nome],
        description=(funcao.__doc__ or "").strip(),
        check_specs=[AssetCheckSpec("qualidade", asset=nome)],
    )
    def _asset(context: AssetExecutionContext) -> MaterializeResult:
        return _materializar(funcao(contexto_padrao()))

    return _asset


ASSETS_INGESTAO = [_criar_asset(nome, funcao) for nome, funcao in ETAPAS.items()]

ingestao_completa = define_asset_job("ingestao_completa", selection="*")

defs = Definitions(
    assets=[configuracao_validada, *ASSETS_INGESTAO],
    jobs=[ingestao_completa],
)

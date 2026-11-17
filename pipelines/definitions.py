"""Ponto de entrada do Dagster (``dagster dev -m pipelines.definitions``).

Fase 0: apenas um asset que valida a configuração. Os assets de ingestão entram na Fase 1.
"""

from dagster import AssetExecutionContext, Definitions, MaterializeResult, asset

from lupa.config import carregar_piloto, carregar_regras


@asset(group_name="configuracao", description="Valida config/piloto.yaml e config/regras.yaml.")
def configuracao_validada(context: AssetExecutionContext) -> MaterializeResult:
    """Falha cedo se a configuração estiver inválida."""
    piloto = carregar_piloto()
    regras = carregar_regras()
    municipio, uf = piloto.piloto.exigir_definido()
    inicio, fim = piloto.piloto.periodo()
    context.log.info(f"Piloto {municipio}/{uf}, {inicio} a {fim}")
    return MaterializeResult(
        metadata={
            "municipio_ibge": municipio,
            "uf": uf,
            "periodo": f"{inicio} a {fim}",
            "sinais_ativos": sum(s.ativo for s in regras.sinais.values()),
        }
    )


defs = Definitions(assets=[configuracao_validada])

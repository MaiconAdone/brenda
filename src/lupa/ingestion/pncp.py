"""PNCP: contratações, itens, resultados (vencedores) e contratos do município do piloto.

Fluxo:

1. ``coletar_contratacoes``: ``/consulta/v1/contratacoes/publicacao`` por mês e modalidade,
   filtrado por ``codigoMunicipioIbge``. Um arquivo JSON Lines por mês.
2. ``coletar_itens``: para cada contratação, itens e resultados (``/pncp/v1/orgaos/...``).
3. ``coletar_contratos``: ``/consulta/v1/contratos`` por mês e por órgão encontrado no passo 1.

Meses encerrados há mais de ``DIAS_REVISAO`` dias não são recoletados (incremental).

Limitação: os resultados trazem apenas fornecedores homologados (vencedores), não todas as
propostas. Isso restringe o sinal K03 (propostas próximas).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from lupa.ingestion.arquivos import gravar_jsonl, gravar_parquet, ler_jsonl, ler_jsonls
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.privacidade import pseudonimizar

log = logging.getLogger("lupa.ingestion.pncp")

FONTE = "pncp"
URL_CONSULTA = "https://pncp.gov.br/api/consulta/v1"
URL_PNCP = "https://pncp.gov.br/api/pncp/v1"
URL_APP = "https://pncp.gov.br/app"
TAMANHO_PAGINA = 50
TAMANHO_PAGINA_ITENS = 500
DIAS_REVISAO = 45


def meses(inicio: date, fim: date) -> list[tuple[date, date]]:
    """Divide ``[inicio, fim]`` em janelas mensais."""
    janelas = []
    atual = inicio
    while atual <= fim:
        proximo = (atual.replace(day=1) + timedelta(days=32)).replace(day=1)
        janelas.append((atual, min(proximo - timedelta(days=1), fim)))
        atual = proximo
    return janelas


def _mes_fechado(fim_janela: date, hoje: date) -> bool:
    return (hoje - fim_janela).days > DIAS_REVISAO


def paginar(cliente: ClienteHttp, url: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Percorre as páginas de um endpoint de consulta do PNCP."""
    pagina = 1
    while True:
        corpo = cliente.get_json(
            url, params={**params, "pagina": pagina, "tamanhoPagina": TAMANHO_PAGINA}
        )
        if not corpo or corpo.get("empty") or not corpo.get("data"):
            return
        yield from corpo["data"]
        if pagina >= int(corpo.get("totalPaginas", 1)):
            return
        pagina += 1


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def coletar_contratacoes(ctx: Contexto, cliente: ClienteHttp) -> list[Path]:
    """Contratações publicadas no município, um arquivo por mês."""
    municipio, _ = ctx.config.piloto.exigir_definido()
    inicio, fim = ctx.config.piloto.periodo(ctx.hoje())
    destino = ctx.bronze(FONTE, "contratacoes")
    gravados = []
    for ini, fim_mes in meses(inicio, fim):
        arquivo = destino / f"{ini:%Y-%m}.jsonl"
        if arquivo.exists() and _mes_fechado(fim_mes, ctx.hoje()):
            continue
        registros: list[dict[str, Any]] = []
        for modalidade in ctx.config.ingestao.pncp.modalidades:
            registros.extend(
                paginar(
                    cliente,
                    f"{URL_CONSULTA}/contratacoes/publicacao",
                    {
                        "dataInicial": _fmt(ini),
                        "dataFinal": _fmt(fim_mes),
                        "codigoModalidadeContratacao": modalidade,
                        "codigoMunicipioIbge": municipio,
                    },
                )
            )
        gravar_jsonl(arquivo, registros)
        ctx.linhagem.registrar(
            FONTE,
            f"{URL_CONSULTA}/contratacoes/publicacao?codigoMunicipioIbge={municipio}"
            f"&dataInicial={_fmt(ini)}&dataFinal={_fmt(fim_mes)}",
            arquivo,
            ctx.agora(),
        )
        log.info("PNCP contratações %s: %d", f"{ini:%Y-%m}", len(registros))
        gravados.append(arquivo)
    return gravados


def _contratacoes_bronze(ctx: Contexto) -> list[dict[str, Any]]:
    return ler_jsonls(ctx.bronze(FONTE, "contratacoes").glob("*.jsonl"))


def _chave_compra(c: dict[str, Any]) -> tuple[str, int, int]:
    return c["orgaoEntidade"]["cnpj"], int(c["anoCompra"]), int(c["sequencialCompra"])


def coletar_itens(ctx: Contexto, cliente: ClienteHttp) -> int:
    """Itens e resultados de cada contratação. Devolve quantas foram (re)coletadas."""
    destino = ctx.bronze(FONTE, "itens")
    n = 0
    vistos: set[str] = set()
    for c in _contratacoes_bronze(ctx):
        controle = c["numeroControlePNCP"]
        if controle in vistos:
            continue
        vistos.add(controle)
        arquivo = destino / f"{controle.replace('/', '_')}.jsonl"
        atualizado = c.get("dataAtualizacaoGlobal") or c.get("dataAtualizacao") or ""
        if arquivo.exists() and _ja_atualizado(arquivo, atualizado):
            continue
        cnpj, ano, seq = _chave_compra(c)
        base = f"{URL_PNCP}/orgaos/{cnpj}/compras/{ano}/{seq}"
        itens = _itens(cliente, base)
        registros: list[dict[str, Any]] = [
            {"tipo": "controle", "numeroControlePNCP": controle, "dataAtualizacao": atualizado}
        ]
        for item in itens:
            registros.append({"tipo": "item", "numeroControlePNCP": controle, **item})
            if item.get("temResultado"):
                resultados = cliente.get_json(f"{base}/itens/{item['numeroItem']}/resultados")
                for r in resultados or []:
                    registros.append({"tipo": "resultado", **r, "numeroControlePNCP": controle})
        gravar_jsonl(arquivo, registros)
        ctx.linhagem.registrar(FONTE, f"{base}/itens", arquivo, ctx.agora())
        n += 1
    log.info("PNCP itens/resultados coletados para %d contratações", n)
    return n


def _itens(cliente: ClienteHttp, base: str) -> list[dict[str, Any]]:
    itens: list[dict[str, Any]] = []
    pagina = 1
    while True:
        lote = cliente.get_json(
            f"{base}/itens", params={"pagina": pagina, "tamanhoPagina": TAMANHO_PAGINA_ITENS}
        )
        if not lote:
            return itens
        itens.extend(lote)
        if len(lote) < TAMANHO_PAGINA_ITENS:
            return itens
        pagina += 1


def _ja_atualizado(arquivo: Path, atualizado: str) -> bool:
    primeiro = next(ler_jsonl(arquivo), {})
    return bool(primeiro.get("dataAtualizacao", "") >= atualizado)


def coletar_contratos(ctx: Contexto, cliente: ClienteHttp) -> list[Path]:
    """Contratos dos órgãos que publicaram contratações no município, por mês."""
    orgaos = sorted({c["orgaoEntidade"]["cnpj"] for c in _contratacoes_bronze(ctx)})
    inicio, fim = ctx.config.piloto.periodo(ctx.hoje())
    destino = ctx.bronze(FONTE, "contratos")
    gravados = []
    for ini, fim_mes in meses(inicio, fim):
        arquivo = destino / f"{ini:%Y-%m}.jsonl"
        if arquivo.exists() and _mes_fechado(fim_mes, ctx.hoje()):
            continue
        registros: list[dict[str, Any]] = []
        for cnpj in orgaos:
            registros.extend(
                paginar(
                    cliente,
                    f"{URL_CONSULTA}/contratos",
                    {"dataInicial": _fmt(ini), "dataFinal": _fmt(fim_mes), "cnpjOrgao": cnpj},
                )
            )
        gravar_jsonl(arquivo, registros)
        ctx.linhagem.registrar(
            FONTE,
            f"{URL_CONSULTA}/contratos?dataInicial={_fmt(ini)}&dataFinal={_fmt(fim_mes)}",
            arquivo,
            ctx.agora(),
        )
        gravados.append(arquivo)
    return gravados


# --------------------------------------------------------------------------- silver


def _get(d: dict[str, Any] | None, *chaves: str) -> Any:
    for chave in chaves:
        if not isinstance(d, dict):
            return None
        d = d.get(chave)
    return d


def _url_compra(cnpj: str, ano: Any, seq: Any) -> str:
    return f"{URL_APP}/editais/{cnpj}/{ano}/{seq}"


def silver_contratacoes(ctx: Contexto) -> pl.DataFrame:
    """Contratações do município (deduplicadas pela versão mais recente)."""
    municipio, _ = ctx.config.piloto.exigir_definido()
    linhas = [
        {
            "numero_controle_pncp": c["numeroControlePNCP"],
            "orgao_cnpj": _get(c, "orgaoEntidade", "cnpj"),
            "orgao_nome": _get(c, "orgaoEntidade", "razaoSocial"),
            "orgao_esfera": _get(c, "orgaoEntidade", "esferaId"),
            "orgao_poder": _get(c, "orgaoEntidade", "poderId"),
            "unidade_codigo": _get(c, "unidadeOrgao", "codigoUnidade"),
            "unidade_nome": _get(c, "unidadeOrgao", "nomeUnidade"),
            "municipio_ibge": str(_get(c, "unidadeOrgao", "codigoIbge") or ""),
            "uf": _get(c, "unidadeOrgao", "ufSigla"),
            "ano_compra": c.get("anoCompra"),
            "sequencial_compra": c.get("sequencialCompra"),
            "numero_compra": c.get("numeroCompra"),
            "processo": c.get("processo"),
            "modalidade_id": c.get("modalidadeId"),
            "modalidade_nome": c.get("modalidadeNome"),
            "modo_disputa": c.get("modoDisputaNome"),
            "situacao": c.get("situacaoCompraNome"),
            "amparo_legal": _get(c, "amparoLegal", "nome"),
            "srp": c.get("srp"),
            "objeto": c.get("objetoCompra"),
            "valor_estimado": c.get("valorTotalEstimado"),
            "valor_homologado": c.get("valorTotalHomologado"),
            "data_publicacao": c.get("dataPublicacaoPncp"),
            "data_abertura_proposta": c.get("dataAberturaProposta"),
            "data_encerramento_proposta": c.get("dataEncerramentoProposta"),
            "data_atualizacao": c.get("dataAtualizacaoGlobal") or c.get("dataAtualizacao"),
            "link_sistema_origem": c.get("linkSistemaOrigem"),
            "url_fonte": _url_compra(
                _get(c, "orgaoEntidade", "cnpj"), c.get("anoCompra"), c.get("sequencialCompra")
            ),
        }
        for c in _contratacoes_bronze(ctx)
    ]
    df = _df(linhas, SCHEMA_CONTRATACOES)
    df = _tipar_datas(
        df,
        [
            "data_publicacao",
            "data_abertura_proposta",
            "data_encerramento_proposta",
            "data_atualizacao",
        ],
    )
    df = (
        df.filter(pl.col("municipio_ibge") == municipio)
        .sort("data_atualizacao", nulls_last=False)
        .unique("numero_controle_pncp", keep="last")
        .sort("numero_controle_pncp")
    )
    gravar_parquet(df, ctx.silver(FONTE) / "contratacoes.parquet")
    return df


def silver_itens_resultados(ctx: Contexto) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Itens e resultados (fornecedores homologados), com CPF de PF pseudonimizado."""
    itens: list[dict[str, Any]] = []
    resultados: list[dict[str, Any]] = []
    for arquivo in sorted(ctx.bronze(FONTE, "itens").glob("*.jsonl")):
        for r in ler_jsonl(arquivo):
            if r["tipo"] == "item":
                itens.append(
                    {
                        "numero_controle_pncp": r["numeroControlePNCP"],
                        "numero_item": r.get("numeroItem"),
                        "descricao": r.get("descricao"),
                        "material_ou_servico": r.get("materialOuServicoNome"),
                        "quantidade": r.get("quantidade"),
                        "valor_unitario_estimado": r.get("valorUnitarioEstimado"),
                        "valor_total": r.get("valorTotal"),
                        "criterio_julgamento": r.get("criterioJulgamentoNome"),
                        "situacao": r.get("situacaoCompraItemNome"),
                        "ncm_nbs": r.get("ncmNbsCodigo"),
                        "tem_resultado": r.get("temResultado"),
                    }
                )
            elif r["tipo"] == "resultado":
                resultados.append(
                    {
                        "numero_controle_pncp": r["numeroControlePNCP"],
                        "numero_item": r.get("numeroItem"),
                        "sequencial_resultado": r.get("sequencialResultado"),
                        "fornecedor_ni": r.get("niFornecedor"),
                        "fornecedor_tipo_pessoa": r.get("tipoPessoa"),
                        "fornecedor_nome": r.get("nomeRazaoSocialFornecedor"),
                        "fornecedor_porte": r.get("porteFornecedorNome"),
                        "quantidade_homologada": r.get("quantidadeHomologada"),
                        "valor_unitario_homologado": r.get("valorUnitarioHomologado"),
                        "valor_total_homologado": r.get("valorTotalHomologado"),
                        "percentual_desconto": r.get("percentualDesconto"),
                        "ordem_classificacao": r.get("ordemClassificacaoSrp"),
                        "situacao": r.get("situacaoCompraItemResultadoNome"),
                        "data_resultado": r.get("dataResultado"),
                    }
                )
    df_itens = _df(itens, SCHEMA_ITENS)
    df_res = _tipar_datas(_df(resultados, SCHEMA_RESULTADOS), ["data_resultado"])
    df_res = _pseudonimizar_fornecedor(df_res)
    gravar_parquet(df_itens, ctx.silver(FONTE) / "itens.parquet")
    gravar_parquet(df_res, ctx.silver(FONTE) / "resultados.parquet")
    return df_itens, df_res


def silver_contratos(ctx: Contexto) -> pl.DataFrame:
    """Contratos de unidades localizadas no município, com CPF de PF pseudonimizado."""
    municipio, _ = ctx.config.piloto.exigir_definido()
    linhas = [
        {
            "numero_controle_pncp": c.get("numeroControlePNCP"),
            "numero_controle_compra": c.get("numeroControlePncpCompra"),
            "orgao_cnpj": _get(c, "orgaoEntidade", "cnpj"),
            "orgao_nome": _get(c, "orgaoEntidade", "razaoSocial"),
            "unidade_nome": _get(c, "unidadeOrgao", "nomeUnidade"),
            "municipio_ibge": str(_get(c, "unidadeOrgao", "codigoIbge") or ""),
            "fornecedor_ni": c.get("niFornecedor"),
            "fornecedor_tipo_pessoa": c.get("tipoPessoa"),
            "fornecedor_nome": c.get("nomeRazaoSocialFornecedor"),
            "tipo_contrato": _get(c, "tipoContrato", "nome"),
            "categoria_processo": _get(c, "categoriaProcesso", "nome"),
            "objeto": c.get("objetoContrato"),
            "valor_inicial": c.get("valorInicial"),
            "valor_global": c.get("valorGlobal"),
            "valor_acumulado": c.get("valorAcumulado"),
            "data_assinatura": c.get("dataAssinatura"),
            "data_vigencia_inicio": c.get("dataVigenciaInicio"),
            "data_vigencia_fim": c.get("dataVigenciaFim"),
            "data_atualizacao": c.get("dataAtualizacaoGlobal") or c.get("dataAtualizacao"),
            "url_fonte": f"{URL_APP}/contratos/{_get(c, 'orgaoEntidade', 'cnpj')}/"
            f"{c.get('anoContrato')}/{c.get('sequencialContrato')}",
        }
        for c in ler_jsonls(ctx.bronze(FONTE, "contratos").glob("*.jsonl"))
    ]
    df = _tipar_datas(
        _df(linhas, SCHEMA_CONTRATOS),
        ["data_assinatura", "data_vigencia_inicio", "data_vigencia_fim", "data_atualizacao"],
    )
    df = (
        df.filter(pl.col("municipio_ibge") == municipio)
        .sort("data_atualizacao", nulls_last=False)
        .unique("numero_controle_pncp", keep="last")
        .sort("numero_controle_pncp")
    )
    df = _pseudonimizar_fornecedor(df)
    gravar_parquet(df, ctx.silver(FONTE) / "contratos.parquet")
    return df


def cnpjs_fornecedores(ctx: Contexto) -> set[str]:
    """CNPJs raiz (8 caracteres) dos fornecedores PJ em resultados e contratos (silver)."""
    raizes: set[str] = set()
    for nome in ("resultados.parquet", "contratos.parquet"):
        caminho = ctx.silver(FONTE) / nome
        if caminho.exists():
            df = pl.read_parquet(
                caminho, columns=["fornecedor_documento", "fornecedor_tipo_pessoa"]
            )
            raizes |= {
                d[:8]
                for d in df.filter(pl.col("fornecedor_tipo_pessoa") == "PJ")[
                    "fornecedor_documento"
                ].drop_nulls()
                if len(d) == 14
            }
    return raizes


def _pseudonimizar_fornecedor(df: pl.DataFrame) -> pl.DataFrame:
    df = df.with_columns(_eh_pf=pl.col("fornecedor_tipo_pessoa") == "PF")
    return (
        pseudonimizar(df, "fornecedor_ni", "fornecedor_nome", "_eh_pf")
        .rename(
            {
                "documento": "fornecedor_documento",
                "id_pessoa": "fornecedor_id_pessoa",
                "nome_normalizado": "fornecedor_nome_normalizado",
            }
        )
        .drop("_eh_pf")
    )


def _df(linhas: list[dict[str, Any]], schema: dict[str, Any]) -> pl.DataFrame:
    return pl.DataFrame(linhas, schema=schema, strict=False)


def _tipar_datas(df: pl.DataFrame, colunas: list[str]) -> pl.DataFrame:
    """Converte textos ``AAAA-MM-DDTHH:MM:SS`` ou ``AAAA-MM-DD`` em datetime."""
    return df.with_columns(
        pl.coalesce(
            pl.col(c).str.slice(0, 19).str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False),
            pl.col(c).str.slice(0, 10).str.to_datetime("%Y-%m-%d", strict=False),
        ).alias(c)
        for c in colunas
    )


S, F, INT, B = pl.String, pl.Float64, pl.Int64, pl.Boolean
SCHEMA_CONTRATACOES = {
    "numero_controle_pncp": S, "orgao_cnpj": S, "orgao_nome": S, "orgao_esfera": S,
    "orgao_poder": S, "unidade_codigo": S, "unidade_nome": S, "municipio_ibge": S, "uf": S,
    "ano_compra": INT, "sequencial_compra": INT, "numero_compra": S, "processo": S,
    "modalidade_id": INT, "modalidade_nome": S, "modo_disputa": S, "situacao": S,
    "amparo_legal": S, "srp": B, "objeto": S, "valor_estimado": F, "valor_homologado": F,
    "data_publicacao": S, "data_abertura_proposta": S, "data_encerramento_proposta": S,
    "data_atualizacao": S, "link_sistema_origem": S, "url_fonte": S,
}  # fmt: skip
SCHEMA_ITENS = {
    "numero_controle_pncp": S, "numero_item": INT, "descricao": S, "material_ou_servico": S,
    "quantidade": F, "valor_unitario_estimado": F, "valor_total": F, "criterio_julgamento": S,
    "situacao": S, "ncm_nbs": S, "tem_resultado": B,
}  # fmt: skip
SCHEMA_RESULTADOS = {
    "numero_controle_pncp": S, "numero_item": INT, "sequencial_resultado": INT,
    "fornecedor_ni": S, "fornecedor_tipo_pessoa": S, "fornecedor_nome": S,
    "fornecedor_porte": S, "quantidade_homologada": F, "valor_unitario_homologado": F,
    "valor_total_homologado": F, "percentual_desconto": F, "ordem_classificacao": INT,
    "situacao": S, "data_resultado": S,
}  # fmt: skip
SCHEMA_CONTRATOS = {
    "numero_controle_pncp": S, "numero_controle_compra": S, "orgao_cnpj": S, "orgao_nome": S,
    "unidade_nome": S, "municipio_ibge": S, "fornecedor_ni": S, "fornecedor_tipo_pessoa": S,
    "fornecedor_nome": S, "tipo_contrato": S, "categoria_processo": S, "objeto": S,
    "valor_inicial": F, "valor_global": F, "valor_acumulado": F, "data_assinatura": S,
    "data_vigencia_inicio": S, "data_vigencia_fim": S, "data_atualizacao": S, "url_fonte": S,
}  # fmt: skip

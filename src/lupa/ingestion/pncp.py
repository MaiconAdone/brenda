"""PNCP: contratações, itens, resultados (vencedores) e contratos dos municípios do escopo.

Fluxo:

1. ``coletar_contratacoes``: ``/consulta/v1/contratacoes/publicacao`` por mês e modalidade,
   filtrado por ``codigoMunicipioIbge`` (ou por ``uf`` com o estado inteiro). Um arquivo
   JSON Lines por município e mês (``contratacoes/<ibge ou UF-xx>/<AAAA-MM>.jsonl``), para
   que incluir um município no escopo não invalide o que já foi coletado.
2. ``coletar_itens``: para cada contratação, itens e resultados (``/pncp/v1/orgaos/...``).
   Com ``itens: seletivo`` só as de valor alto ou ligadas a fornecedores com alerta: no
   estado inteiro, buscar tudo custaria cerca de 13 milhões de requisições.
3. ``coletar_contratos``: ``/consulta/v1/contratos`` por mês e por órgão encontrado no passo 1
   (``contratos/<cnpj>/<AAAA-MM>.jsonl``); com muitos órgãos, varre o país inteiro por mês
   (``contratos/_nacional/``, 500 por página) e filtra na silver.

Meses encerrados há mais de ``DIAS_REVISAO`` dias não são recoletados (incremental).

Limitação: os resultados trazem apenas fornecedores homologados (vencedores), não todas as
propostas. Isso restringe o sinal K03 (propostas próximas).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
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
TAMANHO_PAGINA_CONTRATOS = 500
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


def anos(inicio: date, fim: date) -> list[tuple[date, date]]:
    """Divide ``[inicio, fim]`` em janelas por ano civil.

    A API de consulta do PNCP limita o número de requisições por IP (responde 429 com
    poucas dezenas por minuto). Janelas anuais em vez de mensais cortam ~12x as consultas.
    """
    return [
        (max(inicio, date(ano, 1, 1)), min(fim, date(ano, 12, 31)))
        for ano in range(inicio.year, fim.year + 1)
    ]


def _substituir_mensais(arquivo: Path) -> None:
    """Apaga os arquivos mensais antigos (``AAAA-MM.jsonl``) cobertos pelo arquivo anual."""
    for antigo in arquivo.parent.glob(f"{arquivo.stem}-*.jsonl"):
        antigo.unlink()


def _mes_fechado(fim_janela: date, hoje: date) -> bool:
    return (hoje - fim_janela).days > DIAS_REVISAO


def paginar(
    cliente: ClienteHttp, url: str, params: dict[str, Any], tamanho: int = TAMANHO_PAGINA
) -> Iterator[dict[str, Any]]:
    """Percorre as páginas de um endpoint de consulta do PNCP."""
    pagina = 1
    while True:
        corpo = cliente.get_json(url, params={**params, "pagina": pagina, "tamanhoPagina": tamanho})
        if not corpo or corpo.get("empty") or not corpo.get("data"):
            return
        yield from corpo["data"]
        if pagina >= int(corpo.get("totalPaginas", 1)):
            return
        pagina += 1


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def alvos_consulta(ctx: Contexto) -> list[tuple[str, dict[str, str]]]:
    """``(pasta, filtro)`` de cada consulta: um por município ou um pela UF inteira."""
    codigos, uf = ctx.config.piloto.exigir_escopo()
    if not codigos:
        return [(f"UF-{uf}", {"uf": uf})]
    return [(c, {"codigoMunicipioIbge": c}) for c in codigos]


Tarefa = Callable[[], tuple[Path, str, str]]


def _executar(ctx: Contexto, tarefas: list[Tarefa], trabalhadores: int) -> list[Path]:
    """Roda as tarefas em paralelo, com ``trabalhadores`` simultâneos.

    Cada tarefa baixa e grava um arquivo e devolve ``(arquivo, url, mensagem)``. A
    linhagem e o log são gravados aqui, na thread principal. O intervalo mínimo entre
    requisições continua valendo: ele é controlado pelo ``ClienteHttp``, compartilhado.
    O PNCP responde em ~2 s por requisição, então em série a coleta seria limitada
    pela latência, não pelo intervalo.
    """
    gravados: list[Path] = []
    falhas: list[str] = []
    if not tarefas:
        return gravados
    with ThreadPoolExecutor(max_workers=trabalhadores) as pool:
        for futuro in as_completed(pool.submit(t) for t in tarefas):
            try:
                arquivo, url, mensagem = futuro.result()
            except (httpx.HTTPError, OSError) as erro:
                # Uma falha não descarta o resto: o que deu certo fica gravado e a próxima
                # execução só refaz o que falta. Mas a etapa termina com erro.
                log.error("PNCP: tarefa falhou: %s", erro)
                falhas.append(str(erro))
                continue
            ctx.linhagem.registrar(FONTE, url, arquivo, ctx.agora())
            if mensagem:
                log.info(mensagem)
            gravados.append(arquivo)
            if len(gravados) % 500 == 0:
                log.info("PNCP: %d de %d tarefas concluídas", len(gravados), len(tarefas))
    if falhas:
        raise RuntimeError(
            f"PNCP: {len(falhas)} de {len(tarefas)} tarefas falharam (as demais foram "
            f"gravadas; rode de novo para refazer só as que faltam). Primeira: {falhas[0]}"
        )
    return gravados


def _contratos_janela(
    cliente: ClienteHttp, url: str, params: dict[str, str]
) -> list[dict[str, Any]]:
    """Contratos de uma janela; se o PNCP der erro 5xx, refaz mês a mês.

    Órgãos grandes têm dezenas de milhares de contratos por ano, e as páginas finais de
    uma janela anual chegam a estourar o tempo do servidor (504).
    """
    try:
        return list(paginar(cliente, url, params, TAMANHO_PAGINA_CONTRATOS))
    except httpx.HTTPStatusError as erro:
        ini = datetime.strptime(params["dataInicial"], "%Y%m%d").date()
        fim = datetime.strptime(params["dataFinal"], "%Y%m%d").date()
        if erro.response.status_code < 500 or (ini.year, ini.month) == (fim.year, fim.month):
            raise
        log.warning("PNCP contratos %s: erro %d na janela %s-%s; refazendo mês a mês",
                    params.get("cnpjOrgao", "-"), erro.response.status_code,
                    params["dataInicial"], params["dataFinal"])  # fmt: skip
    registros: list[dict[str, Any]] = []
    for i, f in meses(ini, fim):
        mensal = {**params, "dataInicial": _fmt(i), "dataFinal": _fmt(f)}
        registros.extend(paginar(cliente, url, mensal, TAMANHO_PAGINA_CONTRATOS))
    return registros


def coletar_contratacoes(ctx: Contexto, cliente: ClienteHttp) -> list[Path]:
    """Contratações publicadas no escopo, um arquivo por município (ou UF) e ano."""
    inicio, fim = ctx.config.piloto.periodo(ctx.hoje())
    modalidades = ctx.config.ingestao.pncp.modalidades
    tarefas: list[Tarefa] = []
    for pasta, filtro in alvos_consulta(ctx):
        destino = ctx.bronze(FONTE, "contratacoes", pasta)
        for ini, fim_mes in anos(inicio, fim):
            arquivo = destino / f"{ini:%Y}.jsonl"
            if arquivo.exists() and _mes_fechado(fim_mes, ctx.hoje()):
                continue

            def tarefa(
                arquivo: Path = arquivo,
                pasta: str = pasta,
                filtro: dict[str, str] = filtro,
                ini: date = ini,
                fim_mes: date = fim_mes,
            ) -> tuple[Path, str, str]:
                registros: list[dict[str, Any]] = []
                for modalidade in modalidades:
                    registros.extend(
                        paginar(
                            cliente,
                            f"{URL_CONSULTA}/contratacoes/publicacao",
                            {
                                "dataInicial": _fmt(ini),
                                "dataFinal": _fmt(fim_mes),
                                "codigoModalidadeContratacao": modalidade,
                                **filtro,
                            },
                        )
                    )
                gravar_jsonl(arquivo, registros)
                _substituir_mensais(arquivo)
                consulta = "&".join(f"{k}={v}" for k, v in filtro.items())
                url = (
                    f"{URL_CONSULTA}/contratacoes/publicacao?{consulta}"
                    f"&dataInicial={_fmt(ini)}&dataFinal={_fmt(fim_mes)}"
                )
                return arquivo, url, f"PNCP contratações {pasta} {ini:%Y}: {len(registros)}"

            tarefas.append(tarefa)
    return _executar(ctx, tarefas, ctx.config.ingestao.pncp.trabalhadores_consulta)


def _contratacoes_bronze(ctx: Contexto) -> list[dict[str, Any]]:
    return ler_jsonls(sorted(ctx.bronze(FONTE, "contratacoes").rglob("*.jsonl")))


def _valor(c: dict[str, Any]) -> float:
    return float(c.get("valorTotalHomologado") or c.get("valorTotalEstimado") or 0)


def _compras_prioritarias(ctx: Contexto) -> set[str]:
    """Compras ligadas por contrato a fornecedores que já têm alerta (coleta seletiva)."""
    contratos = ctx.silver(FONTE) / "contratos.parquet"
    scores = ctx.gold("alertas") / "scores.parquet"
    if not contratos.exists() or not scores.exists():
        return set()
    com_alerta = (
        pl.read_parquet(scores, columns=["entidade"])
        .filter(pl.col("entidade").str.starts_with("Empresa:"))
        .select(pl.col("entidade").str.slice(8))["entidade"]
    )
    c = pl.read_parquet(contratos, columns=["numero_controle_compra", "fornecedor_documento"])
    alvo = c.filter(pl.col("fornecedor_documento").str.slice(0, 8).is_in(com_alerta.implode()))
    return set(alvo["numero_controle_compra"].drop_nulls())


def _chave_compra(c: dict[str, Any]) -> tuple[str, int, int]:
    return c["orgaoEntidade"]["cnpj"], int(c["anoCompra"]), int(c["sequencialCompra"])


def coletar_itens(ctx: Contexto, cliente: ClienteHttp) -> int:
    """Itens e resultados das contratações. Devolve quantas foram (re)coletadas.

    Com ``itens: seletivo`` só entram contratações de valor a partir de
    ``itens_valor_min`` ou ligadas por contrato a fornecedoras que já têm alerta.
    """
    cfg = ctx.config.ingestao.pncp
    prioritarias = _compras_prioritarias(ctx) if cfg.itens == "seletivo" else set()
    destino = ctx.bronze(FONTE, "itens")
    puladas = 0
    tarefas: list[Tarefa] = []
    vistos: set[str] = set()
    for c in _contratacoes_bronze(ctx):
        controle = c["numeroControlePNCP"]
        if controle in vistos:
            continue
        vistos.add(controle)
        if (
            cfg.itens == "seletivo"
            and _valor(c) < cfg.itens_valor_min
            and controle not in prioritarias
        ):
            puladas += 1
            continue
        arquivo = destino / f"{controle.replace('/', '_')}.jsonl"
        atualizado = c.get("dataAtualizacaoGlobal") or c.get("dataAtualizacao") or ""
        if arquivo.exists() and _ja_atualizado(arquivo, atualizado):
            continue
        cnpj, ano, seq = _chave_compra(c)
        base = f"{URL_PNCP}/orgaos/{cnpj}/compras/{ano}/{seq}"

        def tarefa(
            arquivo: Path = arquivo,
            base: str = base,
            controle: str = controle,
            atualizado: str = atualizado,
        ) -> tuple[Path, str, str]:
            registros: list[dict[str, Any]] = [
                {"tipo": "controle", "numeroControlePNCP": controle, "dataAtualizacao": atualizado}
            ]
            for item in _itens(cliente, base):
                registros.append({"tipo": "item", "numeroControlePNCP": controle, **item})
                if item.get("temResultado"):
                    resultados = cliente.get_json(f"{base}/itens/{item['numeroItem']}/resultados")
                    for r in resultados or []:
                        registros.append({"tipo": "resultado", **r, "numeroControlePNCP": controle})
            gravar_jsonl(arquivo, registros)
            return arquivo, f"{base}/itens", ""

        tarefas.append(tarefa)  # fmt: skip
    log.info("PNCP itens: %d contratações a coletar (%d puladas)", len(tarefas), puladas)
    return len(_executar(ctx, tarefas, cfg.trabalhadores))


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
    """Contratos dos órgãos que publicaram contratações no escopo, por órgão e ano.

    Com mais de ``max_orgaos_contratos`` órgãos (estado inteiro), varre os contratos do
    país mês a mês, que sai mais barato que consultar órgão por órgão.
    """
    orgaos = sorted({c["orgaoEntidade"]["cnpj"] for c in _contratacoes_bronze(ctx)})
    inicio, fim = ctx.config.piloto.periodo(ctx.hoje())
    nacional = len(orgaos) > ctx.config.ingestao.pncp.max_orgaos_contratos
    alvos: list[tuple[str, dict[str, str]]] = (
        [("_nacional", {})] if nacional else [(cnpj, {"cnpjOrgao": cnpj}) for cnpj in orgaos]
    )
    url = f"{URL_CONSULTA}/contratos"
    tarefas: list[Tarefa] = []
    for pasta, filtro in alvos:
        destino = ctx.bronze(FONTE, "contratos", pasta)
        # Por órgão, janelas anuais (menos consultas); na varredura nacional, mensais (um
        # ano do país inteiro teria milhões de contratos num arquivo só).
        for ini, fim_mes in (meses if nacional else anos)(inicio, fim):
            nome = f"{ini:%Y-%m}" if nacional else f"{ini:%Y}"
            arquivo = destino / f"{nome}.jsonl"
            if arquivo.exists() and _mes_fechado(fim_mes, ctx.hoje()):
                continue
            params = {"dataInicial": _fmt(ini), "dataFinal": _fmt(fim_mes), **filtro}

            def tarefa(
                arquivo: Path = arquivo, params: dict[str, str] = params
            ) -> tuple[Path, str, str]:
                registros = _contratos_janela(cliente, url, params)
                gravar_jsonl(arquivo, registros)
                if not nacional:
                    _substituir_mensais(arquivo)
                consulta = "&".join(f"{k}={v}" for k, v in params.items())
                return arquivo, f"{url}?{consulta}", ""

            tarefas.append(tarefa)
    gravados = _executar(ctx, tarefas, ctx.config.ingestao.pncp.trabalhadores_consulta)
    log.info("PNCP contratos: %d arquivos novos (%d órgãos)", len(gravados), len(orgaos))
    return gravados


# --------------------------------------------------------------------------- silver


def _filtro_escopo(ctx: Contexto) -> Callable[[dict[str, Any]], bool]:
    """Registro do PNCP pertence ao escopo (pela unidade do órgão)?"""
    codigos, uf = ctx.config.piloto.exigir_escopo()
    conjunto = set(codigos)
    if conjunto:
        return lambda c: str(_get(c, "unidadeOrgao", "codigoIbge") or "") in conjunto
    return lambda c: bool(_get(c, "unidadeOrgao", "ufSigla") == uf)


def _get(d: dict[str, Any] | None, *chaves: str) -> Any:
    for chave in chaves:
        if not isinstance(d, dict):
            return None
        d = d.get(chave)
    return d


def _url_compra(cnpj: str, ano: Any, seq: Any) -> str:
    return f"{URL_APP}/editais/{cnpj}/{ano}/{seq}"


def silver_contratacoes(ctx: Contexto) -> pl.DataFrame:
    """Contratações do escopo (deduplicadas pela versão mais recente)."""
    no_escopo = _filtro_escopo(ctx)
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
        if no_escopo(c)
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
        df.sort("data_atualizacao", nulls_last=False)
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
    """Contratos de unidades localizadas no escopo, com CPF de PF pseudonimizado."""
    no_escopo = _filtro_escopo(ctx)
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
        for c in ler_jsonls(sorted(ctx.bronze(FONTE, "contratos").rglob("*.jsonl")))
        if no_escopo(c)
    ]
    df = _tipar_datas(
        _df(linhas, SCHEMA_CONTRATOS),
        ["data_assinatura", "data_vigencia_inicio", "data_vigencia_fim", "data_atualizacao"],
    )
    df = (
        df.sort("data_atualizacao", nulls_last=False)
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

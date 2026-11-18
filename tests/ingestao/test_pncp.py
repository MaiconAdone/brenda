from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import httpx

from lupa.ingestion import pncp
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.qualidade import verificar_tabela
from tests.conftest import gerar_cpf

Fabrica = Callable[[Callable[[httpx.Request], httpx.Response]], ClienteHttp]
ORGAO = "11222333000181"
CPF_FORNECEDOR = gerar_cpf("987654321")


def _contratacao(seq: int, municipio: str = "3529401") -> dict[str, Any]:
    return {
        "numeroControlePNCP": f"{ORGAO}-1-{seq:06d}/2026",
        "orgaoEntidade": {"cnpj": ORGAO, "razaoSocial": "MUNICIPIO SINTETICO",
                          "esferaId": "M", "poderId": "E"},
        "unidadeOrgao": {"codigoIbge": municipio, "ufSigla": "SP", "codigoUnidade": "1",
                         "nomeUnidade": "SECRETARIA"},
        "anoCompra": 2026, "sequencialCompra": seq, "numeroCompra": str(seq),
        "modalidadeId": 6, "modalidadeNome": "Pregão - Eletrônico",
        "objetoCompra": "Aquisição sintética", "valorTotalEstimado": 1000.0,
        "valorTotalHomologado": 900.0, "dataPublicacaoPncp": "2026-02-10T10:00:00",
        "dataAtualizacaoGlobal": "2026-02-11T10:00:00", "srp": False,
    }  # fmt: skip


def _api(req: httpx.Request) -> httpx.Response:
    caminho, p = req.url.path, req.url.params
    if caminho.endswith("/contratacoes/publicacao"):
        if p["codigoModalidadeContratacao"] != "6" or not p["dataInicial"].startswith("202602"):
            return httpx.Response(204)
        dados = [_contratacao(1), _contratacao(2, municipio="9999999")]
        if p["pagina"] == "1":
            return httpx.Response(200, json={"data": dados[:1], "totalPaginas": 2})
        return httpx.Response(200, json={"data": dados[1:], "totalPaginas": 2})
    if caminho.endswith("/itens"):
        return httpx.Response(200, json=[
            {"numeroItem": 1, "descricao": "Item A", "quantidade": 10, "valorTotal": 500.0,
             "temResultado": True},
            {"numeroItem": 2, "descricao": "Item B", "quantidade": 5, "valorTotal": 500.0,
             "temResultado": True},
        ])  # fmt: skip
    if caminho.endswith("/itens/1/resultados"):
        return httpx.Response(200, json=[{
            "numeroItem": 1, "sequencialResultado": 1, "niFornecedor": "44555666000181",
            "tipoPessoa": "PJ", "nomeRazaoSocialFornecedor": "EMPRESA SINTETICA LTDA",
            "valorTotalHomologado": 450.0, "dataResultado": "2026-02-20T00:00:00"}])  # fmt: skip
    if caminho.endswith("/itens/2/resultados"):
        return httpx.Response(200, json=[{
            "numeroItem": 2, "sequencialResultado": 1, "niFornecedor": CPF_FORNECEDOR,
            "tipoPessoa": "PF", "nomeRazaoSocialFornecedor": "Fulano Sintético",
            "valorTotalHomologado": 450.0}])  # fmt: skip
    if caminho.endswith("/contratos"):
        if not p["dataInicial"].startswith("202602"):
            return httpx.Response(204)
        return httpx.Response(200, json={"data": [{
            "numeroControlePNCP": f"{ORGAO}-2-000001/2026",
            "numeroControlePncpCompra": f"{ORGAO}-1-000001/2026",
            "orgaoEntidade": {"cnpj": ORGAO, "razaoSocial": "MUNICIPIO SINTETICO"},
            "unidadeOrgao": {"codigoIbge": "3529401", "nomeUnidade": "SECRETARIA"},
            "niFornecedor": "44555666000181", "tipoPessoa": "PJ",
            "nomeRazaoSocialFornecedor": "EMPRESA SINTETICA LTDA", "valorGlobal": 900.0,
            "dataAssinatura": "2026-02-25", "anoContrato": 2026, "sequencialContrato": 1,
        }], "totalPaginas": 1})  # fmt: skip
    return httpx.Response(404)


def test_meses() -> None:
    assert pncp.meses(date(2026, 1, 15), date(2026, 3, 2)) == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 2)),
    ]


def test_fluxo_completo(ctx: Contexto, cliente_falso: Fabrica) -> None:
    cliente = cliente_falso(_api)
    arquivos = pncp.coletar_contratacoes(ctx, cliente)
    assert len(arquivos) == 13  # 15/03/2025 a 15/03/2026
    assert pncp.coletar_itens(ctx, cliente) == 2
    assert pncp.coletar_itens(ctx, cliente) == 0  # incremental: nada mudou
    pncp.coletar_contratos(ctx, cliente)

    contratacoes = pncp.silver_contratacoes(ctx)
    assert contratacoes["numero_controle_pncp"].to_list() == [f"{ORGAO}-1-000001/2026"]
    assert contratacoes["url_fonte"][0] == f"https://pncp.gov.br/app/editais/{ORGAO}/2026/1"

    itens, resultados = pncp.silver_itens_resultados(ctx)
    assert itens.height == 4  # 2 itens × 2 contratações coletadas
    pf = resultados.filter(resultados["fornecedor_tipo_pessoa"] == "PF")
    assert (
        pf["fornecedor_documento"].to_list()
        == [f"***.{CPF_FORNECEDOR[3:6]}.{CPF_FORNECEDOR[6:9]}-**"] * 2
    )
    assert pf["fornecedor_id_pessoa"].null_count() == 0
    assert CPF_FORNECEDOR not in resultados.write_csv()

    contratos = pncp.silver_contratos(ctx)
    assert contratos.height == 1
    assert pncp.cnpjs_fornecedores(ctx) == {"44555666"}

    for nome, df in [
        ("pncp/contratacoes", contratacoes),
        ("pncp/resultados", resultados),
        ("pncp/contratos", contratos),
    ]:
        assert verificar_tabela(nome, df) == [], nome
    assert len(ctx.linhagem.registros()) > 0


def test_meses_fechados_nao_sao_recoletados(ctx: Contexto, cliente_falso: Fabrica) -> None:
    chamadas: list[str] = []

    def contar(req: httpx.Request) -> httpx.Response:
        chamadas.append(str(req.url))
        return _api(req)

    cliente = cliente_falso(contar)
    pncp.coletar_contratacoes(ctx, cliente)
    chamadas.clear()
    pncp.coletar_contratacoes(ctx, cliente)
    meses_recoletados = {c.split("dataInicial=")[1][:6] for c in chamadas}
    # só meses encerrados há até 45 dias (31/01 está a 43 dias de 15/03)
    assert meses_recoletados == {"202601", "202602", "202603"}

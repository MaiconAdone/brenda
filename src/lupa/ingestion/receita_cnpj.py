"""Receita Federal: CNPJ dados abertos (empresas, estabelecimentos, sócios, Simples).

Os arquivos são nacionais (~8 GB por mês, compactados); não há recorte por município.
Baixamos o mês inteiro e filtramos localmente:

- **estabelecimentos**: os localizados no município do piloto (código de município da
  Receita, que difere do IBGE) **ou** de empresas fornecedoras no PNCP;
- **empresas, sócios, Simples**: das empresas (CNPJ raiz) selecionadas acima;
- **contagem nacional de empresas por sócio PF**: calculada sobre a base inteira, mas
  gravada só para os sócios das empresas selecionadas (minimização).

O compartilhamento oficial fica em https://arquivos.receitafederal.gov.br/ (Nextcloud),
acessado por WebDAV público. O token abaixo é o do link público para onde a página
inicial redireciona; se mudar, atualize ``TOKEN_COMPARTILHAMENTO``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import polars as pl

from lupa import escopo
from lupa.ingestion.arquivos import extrair_utf8, gravar_parquet, ler_csv_texto
from lupa.ingestion.contexto import Contexto
from lupa.ingestion.http import ClienteHttp
from lupa.privacidade import pseudonimizar

log = logging.getLogger("lupa.ingestion.receita_cnpj")

FONTE = "receita_cnpj"
TOKEN_COMPARTILHAMENTO = "gn672Ad4CF8N6TK"
URL_WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav/Dados/Cadastros/CNPJ"
URL_PAGINA = f"https://arquivos.receitafederal.gov.br/index.php/s/{TOKEN_COMPARTILHAMENTO}"

COLUNAS = {
    "Empresas": [
        "cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
        "capital_social", "porte_empresa", "ente_federativo_responsavel",
    ],
    "Estabelecimentos": [
        "cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz_filial", "nome_fantasia",
        "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
        "nome_cidade_exterior", "pais", "data_inicio_atividade", "cnae_fiscal_principal",
        "cnae_fiscal_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
        "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
        "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial",
    ],
    "Socios": [
        "cnpj_basico", "identificador_socio", "nome_socio", "cnpj_cpf_socio",
        "qualificacao_socio", "data_entrada_sociedade", "pais", "representante_legal",
        "nome_representante", "qualificacao_representante", "faixa_etaria",
    ],
    "Simples": [
        "cnpj_basico", "opcao_simples", "data_opcao_simples", "data_exclusao_simples",
        "opcao_mei", "data_opcao_mei", "data_exclusao_mei",
    ],
}  # fmt: skip
TABELAS_DOMINIO = ["Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes"]
_AUTH = (TOKEN_COMPARTILHAMENTO, "")


def listar(cliente: ClienteHttp, caminho: str = "") -> dict[str, int]:
    """Lista ``{nome: tamanho}`` de uma pasta do WebDAV (pastas com tamanho 0)."""
    resposta = cliente.requisitar(
        "PROPFIND", f"{URL_WEBDAV}/{caminho}", auth=_AUTH, headers={"Depth": "1"}
    )
    itens: dict[str, int] = {}
    for bloco in re.findall(r"<d:response>(.*?)</d:response>", resposta.text, re.S):
        href = re.search(r"<d:href>([^<]+)</d:href>", bloco)
        if not href:
            continue
        nome = href.group(1).rstrip("/").rsplit("/", 1)[-1]
        tamanho = re.search(r"<d:getcontentlength>(\d+)</d:getcontentlength>", bloco)
        itens[nome] = int(tamanho.group(1)) if tamanho else 0
    itens.pop(caminho.rstrip("/").rsplit("/", 1)[-1] or "CNPJ", None)
    return itens


def mes_mais_recente(cliente: ClienteHttp) -> str:
    """Último mês (``AAAA-MM``) publicado."""
    meses = sorted(n for n in listar(cliente) if re.fullmatch(r"\d{4}-\d{2}", n))
    if not meses:
        raise RuntimeError(f"Nenhum mês encontrado em {URL_PAGINA}")
    return meses[-1]


def coletar(ctx: Contexto, cliente: ClienteHttp) -> str:
    """Baixa todos os ZIPs do mês configurado (ou do mais recente). Devolve o mês."""
    mes = ctx.config.ingestao.receita_cnpj.mes or mes_mais_recente(cliente)
    arquivos = {n: t for n, t in listar(cliente, f"{mes}/").items() if n.endswith(".zip")}
    destino = ctx.bronze(FONTE, mes)
    for nome, tamanho in sorted(arquivos.items()):
        url = f"{URL_WEBDAV}/{mes}/{nome}"
        if cliente.baixar(url, destino / nome, tamanho_esperado=tamanho, auth=_AUTH):
            ctx.linhagem.registrar(
                FONTE,
                f"{URL_PAGINA}?dir=/Dados/Cadastros/CNPJ/{mes}/{nome}",
                destino / nome,
                ctx.agora(),
            )
    return mes


# --------------------------------------------------------------------------- silver


def _partes(pasta: Path, prefixo: str) -> list[Path]:
    return sorted(pasta.glob(f"{prefixo}*.zip"), key=lambda p: p.name)


def _processar_zips(
    ctx: Contexto,
    zips: list[Path],
    colunas: list[str],
    filtro: pl.Expr,
    destino: Path,
) -> pl.DataFrame:
    """Extrai cada ZIP, filtra e concatena. Os arquivos extraídos são apagados."""
    pedacos = []
    for zip_path in zips:
        csv = extrair_utf8(zip_path, ctx.temporario() / f"{zip_path.stem}.csv")
        try:
            pedacos.append(ler_csv_texto(csv, colunas, cabecalho=False).filter(filtro).collect())
        finally:
            csv.unlink(missing_ok=True)
        log.info("%s: %d linhas selecionadas", zip_path.name, pedacos[-1].height)
    df = pl.concat(pedacos) if pedacos else pl.DataFrame(schema=dict.fromkeys(colunas, pl.String))
    gravar_parquet(df, destino)
    return df


def municipios_receita(ctx: Contexto, dominio: pl.DataFrame) -> list[str]:
    """Códigos da Receita dos municípios do escopo (vazio = UF inteira)."""
    piloto = ctx.config.piloto
    codigos, _ = piloto.exigir_escopo()
    if not codigos:
        return []
    if len(codigos) == 1 and piloto.municipio_receita:
        return [piloto.municipio_receita]
    # A tabela de municípios da Receita não tem UF, e há homônimos em outros estados. O
    # filtro de estabelecimentos também exige a UF, então os homônimos não entram.
    return escopo.codigos_homonimos(
        escopo.municipios(ctx), dominio.select("codigo", "descricao").iter_rows(), "Receita"
    )


def silver(ctx: Contexto, mes: str, raizes_fornecedores: set[str]) -> dict[str, pl.DataFrame]:
    """Gera as tabelas silver do CNPJ para o escopo."""
    _, uf = ctx.config.piloto.exigir_escopo()
    pasta = ctx.bronze(FONTE, mes)
    saida = ctx.silver(FONTE)
    fornecedores = pl.Series(sorted(raizes_fornecedores), dtype=pl.String)

    dominios = {}
    for tabela in TABELAS_DOMINIO:
        zips = _partes(pasta, tabela)
        if not zips:
            log.warning("Tabela de domínio %s ausente no mês %s", tabela, mes)
            continue
        dominios[tabela.lower()] = _processar_zips(
            ctx,
            zips,
            ["codigo", "descricao"],
            pl.lit(True),
            saida / f"dominio_{tabela.lower()}.parquet",
        )
    if "municipios" not in dominios:
        raise RuntimeError(f"Tabela de municípios da Receita ausente no mês {mes}.")
    codigos = municipios_receita(ctx, dominios["municipios"])
    no_escopo = (pl.col("uf") == uf) & (
        pl.col("municipio").is_in(codigos) if codigos else pl.lit(True)
    )
    log.info("Receita: %s municípios do escopo", len(codigos) or "todos os")
    selecao = no_escopo
    if ctx.config.ingestao.receita_cnpj.somente_ativas:
        selecao = no_escopo & (pl.col("situacao_cadastral") == "02")

    estab = _processar_zips(
        ctx,
        _partes(pasta, "Estabelecimentos"),
        COLUNAS["Estabelecimentos"],
        selecao | pl.col("cnpj_basico").is_in(fornecedores.implode()),
        saida / "_estabelecimentos_bruto.parquet",
    )
    alvo = estab["cnpj_basico"].unique()
    estab = _tratar_estabelecimentos(estab, no_escopo)
    gravar_parquet(estab, saida / "estabelecimentos.parquet")
    (saida / "_estabelecimentos_bruto.parquet").unlink()

    filtro_alvo = pl.col("cnpj_basico").is_in(alvo.implode())
    empresas = _processar_zips(
        ctx,
        _partes(pasta, "Empresas"),
        COLUNAS["Empresas"],
        filtro_alvo,
        saida / "empresas.parquet",
    )
    empresas = empresas.with_columns(
        capital_social=pl.col("capital_social")
        .str.replace_all(r"\.", "")
        .str.replace(",", ".")
        .cast(pl.Float64, strict=False)
    )
    gravar_parquet(empresas, saida / "empresas.parquet")

    simples = _processar_zips(
        ctx,
        _partes(pasta, "Simples"),
        COLUNAS["Simples"],
        filtro_alvo,
        saida / "simples.parquet",
    )
    simples = _datas(simples, [c for c in simples.columns if c.startswith("data_")])
    gravar_parquet(simples, saida / "simples.parquet")

    socios, contagem = _socios(ctx, _partes(pasta, "Socios"), alvo)
    gravar_parquet(socios, saida / "socios.parquet")
    gravar_parquet(contagem, saida / "socios_contagem_nacional.parquet")

    (saida / "_mes.txt").write_text(mes, encoding="utf-8")
    return {
        "estabelecimentos": estab,
        "empresas": empresas,
        "simples": simples,
        "socios": socios,
        "socios_contagem_nacional": contagem,
        **dominios,
    }


def _datas(df: pl.DataFrame, colunas: list[str]) -> pl.DataFrame:
    """Datas ``AAAAMMDD`` da Receita; ``0`` e ``00000000`` viram nulo."""
    return df.with_columns(pl.col(c).str.to_date("%Y%m%d", strict=False).alias(c) for c in colunas)


def _tratar_estabelecimentos(df: pl.DataFrame, no_escopo: pl.Expr) -> pl.DataFrame:
    df = _datas(df, ["data_situacao_cadastral", "data_inicio_atividade", "data_situacao_especial"])
    return df.with_columns(
        cnpj=pl.concat_str("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
        # Nome mantido por compatibilidade: verdadeiro para qualquer município do escopo.
        no_municipio_piloto=no_escopo,
        telefone_1_completo=pl.concat_str("ddd_1", "telefone_1"),
        telefone_2_completo=pl.concat_str("ddd_2", "telefone_2"),
        correio_eletronico=pl.col("correio_eletronico").str.to_lowercase().str.strip_chars(),
    )


def _socios(ctx: Contexto, zips: list[Path], alvo: pl.Series) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Sócios das empresas-alvo e contagem nacional de empresas por sócio PF.

    A Receita publica o CPF de sócios PF mascarado (``***123456**``). A chave de
    contagem nacional é (miolo do CPF, nome), a mesma usada em ``id_pessoa``.
    """
    alvo_l = alvo.implode()
    selecionados = []
    contagens = []
    for zip_path in zips:
        csv = extrair_utf8(zip_path, ctx.temporario() / f"{zip_path.stem}.csv")
        try:
            lf = ler_csv_texto(csv, COLUNAS["Socios"], cabecalho=False)
            selecionados.append(lf.filter(pl.col("cnpj_basico").is_in(alvo_l)).collect())
            contagens.append(
                lf.filter(pl.col("identificador_socio") == "2")
                .select("cnpj_basico", "cnpj_cpf_socio", "nome_socio")
                .unique()
                .collect()
            )
        finally:
            csv.unlink(missing_ok=True)
    socios = (
        pl.concat(selecionados)
        if selecionados
        else pl.DataFrame(schema=dict.fromkeys(COLUNAS["Socios"], pl.String))
    )
    nacional = (
        pl.concat(contagens)
        .unique()
        .group_by("cnpj_cpf_socio", "nome_socio")
        .agg(n_empresas=pl.len())
        if contagens
        else pl.DataFrame(
            schema={"cnpj_cpf_socio": pl.String, "nome_socio": pl.String, "n_empresas": pl.UInt32}
        )
    )
    socios = _datas(socios, ["data_entrada_sociedade"]).with_columns(
        eh_pf=pl.col("identificador_socio") == "2"
    )
    contagem = nacional.join(
        socios.filter("eh_pf").select("cnpj_cpf_socio", "nome_socio").unique(),
        on=["cnpj_cpf_socio", "nome_socio"],
    )
    socios = pseudonimizar(socios, "cnpj_cpf_socio", "nome_socio", "eh_pf").rename(
        {"documento": "socio_documento"}
    )
    contagem = pseudonimizar(contagem, "cnpj_cpf_socio", "nome_socio").select(
        "id_pessoa", "n_empresas"
    )
    return socios, contagem

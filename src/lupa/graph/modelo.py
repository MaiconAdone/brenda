"""Modelo do grafo: nós e arestas construídos a partir das tabelas silver.

O grafo é gravado na camada gold como uma tabela Parquet por rótulo de nó
(``gold/grafo/nos/<Rotulo>.parquet``) e por tipo de aresta
(``gold/grafo/arestas/<TIPO>.parquet``). Toda aresta tem ``fonte``: o link ou arquivo
oficial de onde veio. Daí o grafo vai para o NetworkX (consultas locais) ou para o
Neo4j.

Identificadores:

=================  ===============================================
Rótulo             ``id``
=================  ===============================================
Pessoa             ``id_pessoa`` (hash) ou ``tse-<ano>-<sq>``
Empresa            CNPJ raiz (8 caracteres)
Estabelecimento    CNPJ completo (14 caracteres)
Endereco           hash do endereço normalizado (sem complemento)
Telefone / Email   hash com sal (o contato em si não é guardado)
OrgaoPublico       CNPJ do órgão
Licitacao          número de controle PNCP da contratação
Contrato           número de controle PNCP do contrato
Candidatura        ``<ano>-<sq_candidato>``
Sancao             ``<cadastro>-<codigo_sancao>``
=================  ===============================================
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from lupa.cleaning.texto import normalizar_nome
from lupa.ingestion import receita_cnpj, tse
from lupa.ingestion.arquivos import gravar_parquet
from lupa.ingestion.contexto import Contexto
from lupa.privacidade import hash_contato

COLUNAS_ARESTA = ["origem", "destino", "rotulo_origem", "rotulo_destino", "fonte"]


@dataclass
class Grafo:
    """Nós por rótulo e arestas por tipo, como tabelas Polars."""

    nos: dict[str, pl.DataFrame] = field(default_factory=dict)
    arestas: dict[str, pl.DataFrame] = field(default_factory=dict)

    def adicionar_nos(self, rotulo: str, df: pl.DataFrame) -> None:
        """Junta nós a um rótulo; em ids repetidos vale o primeiro (fonte mais rica)."""
        df = df.filter(pl.col("id").is_not_null() & (pl.col("id") != ""))
        if rotulo in self.nos:
            df = pl.concat([self.nos[rotulo], df], how="diagonal_relaxed")
        self.nos[rotulo] = df.unique(subset="id", keep="first", maintain_order=True)

    def adicionar_arestas(self, tipo: str, df: pl.DataFrame) -> None:
        """Junta arestas de um tipo, descartando as que têm ponta nula."""
        df = df.filter(pl.col("origem").is_not_null() & pl.col("destino").is_not_null())
        if tipo in self.arestas:
            df = pl.concat([self.arestas[tipo], df], how="diagonal_relaxed")
        self.arestas[tipo] = df.unique(maintain_order=True)

    def ids(self, rotulo: str) -> pl.Series:
        """Ids dos nós de um rótulo."""
        return self.nos[rotulo]["id"] if rotulo in self.nos else pl.Series("id", [], pl.String)

    def resumo(self) -> dict[str, int]:
        """Contagem de nós e arestas."""
        return {
            **{f"nos/{r}": df.height for r, df in sorted(self.nos.items())},
            **{f"arestas/{t}": df.height for t, df in sorted(self.arestas.items())},
        }

    def gravar(self, diretorio: Path) -> None:
        """Grava em Parquet, apagando tabelas antigas que não existem mais."""
        for sub, tabelas in (("nos", self.nos), ("arestas", self.arestas)):
            pasta = diretorio / sub
            pasta.mkdir(parents=True, exist_ok=True)
            for antigo in pasta.glob("*.parquet"):
                if antigo.stem not in tabelas:
                    antigo.unlink()
            for nome, df in tabelas.items():
                gravar_parquet(df, pasta / f"{nome}.parquet")

    @classmethod
    def ler(cls, diretorio: Path) -> Grafo:
        """Lê o grafo gravado por :meth:`gravar`."""
        return cls(
            nos={p.stem: pl.read_parquet(p) for p in sorted((diretorio / "nos").glob("*.parquet"))},
            arestas={
                p.stem: pl.read_parquet(p)
                for p in sorted((diretorio / "arestas").glob("*.parquet"))
            },
        )


# ---------------------------------------------------------------- utilidades


def _ler(caminho: Path) -> pl.DataFrame | None:
    return pl.read_parquet(caminho) if caminho.exists() else None


def _raiz(coluna: str) -> pl.Expr:
    return pl.col(coluna).str.replace_all(r"[^0-9A-Za-z]", "").str.to_uppercase().str.slice(0, 8)


def _aresta(
    df: pl.DataFrame,
    rotulo_origem: str,
    rotulo_destino: str,
    origem: pl.Expr,
    destino: pl.Expr,
    fonte: pl.Expr,
    **props: pl.Expr,
) -> pl.DataFrame:
    return df.select(
        origem=origem.cast(pl.String),
        destino=destino.cast(pl.String),
        rotulo_origem=pl.lit(rotulo_origem),
        rotulo_destino=pl.lit(rotulo_destino),
        fonte=fonte.cast(pl.String),
        **props,
    )


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:20]


def _mes_cnpj(ctx: Contexto) -> str:
    meses = sorted(p.name for p in ctx.bronze(receita_cnpj.FONTE).iterdir() if p.is_dir())
    return meses[-1] if meses else "desconhecido"


def _fornecedor(
    g: Grafo,
    tipo: str,
    df: pl.DataFrame,
    prefixo: str,
    destino: pl.Expr,
    rotulo_destino: str,
    fonte: pl.Expr,
    col_tipo: str,
    **props: pl.Expr,
) -> None:
    """Arestas de um fornecedor/doador (PJ → Empresa, PF → Pessoa) para um destino."""
    pj = df.filter(pl.col(col_tipo) == "PJ")
    g.adicionar_nos(
        "Empresa",
        pj.select(id=_raiz(f"{prefixo}_documento"), nome=pl.col(f"{prefixo}_nome_normalizado")),
    )
    g.adicionar_arestas(
        tipo,
        _aresta(
            pj, "Empresa", rotulo_destino, _raiz(f"{prefixo}_documento"), destino, fonte,
            cnpj=pl.col(f"{prefixo}_documento"), **props,
        ),
    )  # fmt: skip
    pf = df.filter(pl.col(f"{prefixo}_id_pessoa").is_not_null())
    g.adicionar_arestas(
        tipo,
        _aresta(
            pf, "Pessoa", rotulo_destino, pl.col(f"{prefixo}_id_pessoa"), destino, fonte,
            cnpj=pl.lit(None, dtype=pl.String), **props,
        ),
    )  # fmt: skip


# ---------------------------------------------------------------- construção


def _pessoas(g: Grafo, pessoas: pl.DataFrame, ligacoes: pl.DataFrame) -> None:
    g.adicionar_nos(
        "Pessoa",
        pessoas.select(
            id=pl.col("no_id"),
            nome=pl.col("nome_normalizado"),
            documento=pl.col("documento"),
            origens=pl.col("origens"),
            idade_min=pl.col("idade_min"),
            idade_max=pl.col("idade_max"),
        ),
    )
    g.adicionar_arestas(
        "MESMA_ENTIDADE_PROVAVEL",
        _aresta(
            ligacoes, "Pessoa", "Pessoa", pl.col("id_a"), pl.col("id_b"),
            pl.lit("resolucao_entidades:splink"),
            probabilidade=pl.col("probabilidade"), evidencia=pl.col("evidencia"),
            mw_nome=pl.col("mw_nome"), mw_miolo=pl.col("mw_miolo"), mw_idade=pl.col("mw_idade"),
        ),
    )  # fmt: skip


def _receita(g: Grafo, ctx: Contexto) -> None:
    fonte = pl.lit(f"{receita_cnpj.URL_PAGINA}?path=/Dados/Cadastros/CNPJ/{_mes_cnpj(ctx)}")
    base = ctx.silver(receita_cnpj.FONTE)
    empresas = _ler(base / "empresas.parquet")
    simples = _ler(base / "simples.parquet")
    if empresas is not None:
        if simples is not None:
            empresas = empresas.join(
                simples.select("cnpj_basico", "opcao_simples", "opcao_mei"),
                on="cnpj_basico",
                how="left",
            )
        g.adicionar_nos(
            "Empresa",
            empresas.select(
                *[pl.col(c) for c in ("opcao_simples", "opcao_mei") if c in empresas.columns],
                id=pl.col("cnpj_basico"),
                nome=pl.col("razao_social"),
                natureza_juridica=pl.col("natureza_juridica"),
                capital_social=pl.col("capital_social"),
                porte=pl.col("porte_empresa"),
            ),
        )

    estab = _ler(base / "estabelecimentos.parquet")
    if estab is not None:
        g.adicionar_nos(
            "Estabelecimento",
            estab.select(
                id=pl.col("cnpj"),
                nome=pl.col("nome_fantasia"),
                matriz=pl.col("identificador_matriz_filial") == "1",
                situacao_cadastral=pl.col("situacao_cadastral"),
                data_situacao_cadastral=pl.col("data_situacao_cadastral"),
                data_inicio_atividade=pl.col("data_inicio_atividade"),
                cnae_principal=pl.col("cnae_fiscal_principal"),
                municipio=pl.col("municipio"),
                uf=pl.col("uf"),
                no_municipio_piloto=pl.col("no_municipio_piloto"),
            ),
        )
        g.adicionar_nos("Empresa", estab.select(id=pl.col("cnpj_basico")))
        g.adicionar_arestas(
            "UNIDADE_DE",
            _aresta(
                estab, "Estabelecimento", "Empresa", pl.col("cnpj"), pl.col("cnpj_basico"), fonte
            ),
        )
        _enderecos(g, estab, fonte)
        _contatos(g, estab, fonte)

    socios = _ler(base / "socios.parquet")
    if socios is not None:
        props = {
            "qualificacao": pl.col("qualificacao_socio"),
            "data_entrada": pl.col("data_entrada_sociedade"),
            "faixa_etaria": pl.col("faixa_etaria").cast(pl.String),
        }
        pf = socios.filter(pl.col("eh_pf") & pl.col("id_pessoa").is_not_null())
        g.adicionar_arestas(
            "SOCIO_DE",
            _aresta(
                pf, "Pessoa", "Empresa", pl.col("id_pessoa"), pl.col("cnpj_basico"), fonte, **props
            ),
        )
        pj = socios.filter(pl.col("identificador_socio") == "1")
        g.adicionar_nos(
            "Empresa", pj.select(id=_raiz("socio_documento"), nome=pl.col("nome_socio"))
        )
        g.adicionar_arestas(
            "SOCIO_DE",
            _aresta(
                pj,
                "Empresa",
                "Empresa",
                _raiz("socio_documento"),
                pl.col("cnpj_basico"),
                fonte,
                **props,
            ),
        )


def _texto_endereco(r: dict[str, str | None]) -> str:
    partes = [r.get(c) or "" for c in ("tipo_logradouro", "logradouro", "numero", "bairro")]
    numero = normalizar_nome(partes[2])
    partes[2] = "SN" if numero in {"", "SN", "S N", "SEM NUMERO"} else numero
    texto = " ".join(normalizar_nome(p) for p in partes if p)
    return f"{texto} CEP {r.get('cep') or ''} MUN {r.get('municipio') or ''}".strip()


def _enderecos(g: Grafo, estab: pl.DataFrame, fonte: pl.Expr) -> None:
    """Endereço no nível do prédio (sem complemento), para achar endereços compartilhados."""
    colunas = ["tipo_logradouro", "logradouro", "numero", "bairro", "cep", "municipio"]
    e = estab.select("cnpj", "complemento", "uf", *colunas).with_columns(
        texto=pl.struct(colunas).map_elements(_texto_endereco, return_dtype=pl.String)
    )
    e = e.filter(pl.col("logradouro").is_not_null()).with_columns(
        id=pl.col("texto").map_elements(_sha, return_dtype=pl.String)
    )
    g.adicionar_nos(
        "Endereco",
        e.select("id", "texto", "cep", "municipio", "uf"),
    )
    g.adicionar_arestas(
        "LOCALIZADA_EM",
        _aresta(e, "Estabelecimento", "Endereco", pl.col("cnpj"), pl.col("id"), fonte,
                complemento=pl.col("complemento")),
    )  # fmt: skip


def _contatos(g: Grafo, estab: pl.DataFrame, fonte: pl.Expr) -> None:
    """Telefones e e-mails viram hash com sal: dá para comparar sem expor o contato."""
    telefones = pl.concat(
        [
            estab.select("cnpj", valor=pl.col(c))
            for c in ("telefone_1_completo", "telefone_2_completo")
            if c in estab.columns
        ]
    ).filter(pl.col("valor").str.len_chars() >= 10)
    emails = estab.select("cnpj", valor=pl.col("correio_eletronico").str.to_lowercase()).filter(
        pl.col("valor").str.contains("@")
    )
    for rotulo, tipo, df in (
        ("Telefone", "TEM_TELEFONE", telefones),
        ("Email", "TEM_EMAIL", emails),
    ):
        df = df.unique().with_columns(
            id=pl.col("valor").map_elements(hash_contato, return_dtype=pl.String)
        )
        g.adicionar_nos(rotulo, df.select("id"))
        g.adicionar_arestas(
            tipo, _aresta(df, "Estabelecimento", rotulo, pl.col("cnpj"), pl.col("id"), fonte)
        )


def _pncp(g: Grafo, ctx: Contexto) -> None:
    base = ctx.silver("pncp")
    contratacoes = _ler(base / "contratacoes.parquet")
    contratos = _ler(base / "contratos.parquet")
    resultados = _ler(base / "resultados.parquet")

    orgaos = [
        df.select(
            *[pl.col(c) for c in ("orgao_esfera", "orgao_poder") if c in df.columns],
            id=pl.col("orgao_cnpj"),
            nome=pl.col("orgao_nome"),
        )
        for df in (contratacoes, contratos)
        if df is not None
    ]
    for df in orgaos:
        g.adicionar_nos("OrgaoPublico", df)

    if contratacoes is not None:
        g.adicionar_nos(
            "Licitacao",
            contratacoes.select(
                id=pl.col("numero_controle_pncp"),
                nome=pl.col("objeto").str.slice(0, 200),
                objeto=pl.col("objeto"),
                modalidade=pl.col("modalidade_nome"),
                situacao=pl.col("situacao"),
                valor_estimado=pl.col("valor_estimado"),
                valor_homologado=pl.col("valor_homologado"),
                data_publicacao=pl.col("data_publicacao"),
                unidade=pl.col("unidade_nome"),
                url_fonte=pl.col("url_fonte"),
            ),
        )
        g.adicionar_arestas(
            "PROMOVIDA_POR",
            _aresta(contratacoes, "Licitacao", "OrgaoPublico", pl.col("numero_controle_pncp"),
                    pl.col("orgao_cnpj"), pl.col("url_fonte")),
        )  # fmt: skip

    if resultados is not None and contratacoes is not None:
        vencedores = (
            resultados.join(
                contratacoes.select("numero_controle_pncp", "url_fonte"),
                on="numero_controle_pncp",
                how="left",
            )
            .group_by(
                "numero_controle_pncp", "fornecedor_tipo_pessoa", "fornecedor_documento",
                "fornecedor_id_pessoa", "fornecedor_nome_normalizado",
            )
            .agg(
                pl.len().alias("n_itens"),
                pl.col("valor_total_homologado").sum().alias("valor_homologado"),
                pl.col("url_fonte").first(),
            )
        )  # fmt: skip
        _fornecedor(
            g, "VENCEU", vencedores, "fornecedor", pl.col("numero_controle_pncp"), "Licitacao",
            pl.col("url_fonte"), "fornecedor_tipo_pessoa",
            n_itens=pl.col("n_itens"), valor_homologado=pl.col("valor_homologado"),
        )  # fmt: skip

    if contratos is not None:
        g.adicionar_nos(
            "Contrato",
            contratos.select(
                id=pl.col("numero_controle_pncp"),
                nome=pl.col("objeto").str.slice(0, 200),
                objeto=pl.col("objeto"),
                tipo=pl.col("tipo_contrato"),
                valor_inicial=pl.col("valor_inicial"),
                valor_global=pl.col("valor_global"),
                data_assinatura=pl.col("data_assinatura"),
                vigencia_inicio=pl.col("data_vigencia_inicio"),
                vigencia_fim=pl.col("data_vigencia_fim"),
                url_fonte=pl.col("url_fonte"),
            ),
        )
        url = pl.col("url_fonte")
        g.adicionar_arestas(
            "CELEBRADO_POR",
            _aresta(contratos, "Contrato", "OrgaoPublico", pl.col("numero_controle_pncp"),
                    pl.col("orgao_cnpj"), url),
        )  # fmt: skip
        originados = contratos.filter(pl.col("numero_controle_compra").is_in(g.ids("Licitacao")))
        g.adicionar_arestas(
            "ORIGINADO_DE",
            _aresta(originados, "Contrato", "Licitacao", pl.col("numero_controle_pncp"),
                    pl.col("numero_controle_compra"), url),
        )  # fmt: skip
        _fornecedor(
            g, "PARTE_DE", contratos, "fornecedor", pl.col("numero_controle_pncp"), "Contrato",
            url, "fornecedor_tipo_pessoa", valor_global=pl.col("valor_global"),
        )  # fmt: skip


def _tse(g: Grafo, ctx: Contexto) -> None:
    base = ctx.silver(tse.FONTE)
    candidatos = _ler(base / "candidatos.parquet")
    id_candidatura = pl.concat_str(pl.col("ano_eleicao"), pl.lit("-"), pl.col("sq_candidato"))

    def fonte(chave: str) -> pl.Expr:
        mapa = {str(a): tse.urls(a)[chave] for a in ctx.config.ingestao.tse.anos}
        return pl.col("ano_eleicao").cast(pl.String).replace_strict(mapa, default=None)

    if candidatos is not None:
        # No 2º turno o candidato aparece de novo; vale a linha mais recente.
        candidatos = candidatos.sort("turno", descending=True)
        g.adicionar_nos(
            "Candidatura",
            candidatos.select(
                id=id_candidatura,
                nome=pl.col("nome_urna"),
                ano_eleicao=pl.col("ano_eleicao"),
                cargo=pl.col("cargo"),
                partido=pl.col("partido"),
                numero=pl.col("numero"),
                municipio=pl.col("nome_ue"),
                situacao_candidatura=pl.col("situacao_candidatura"),
                resultado=pl.col("resultado"),
            ),
        )
        no_pessoa = pl.coalesce(
            pl.col("id_pessoa"),
            pl.concat_str(
                pl.lit("tse-"), pl.col("ano_eleicao"), pl.lit("-"), pl.col("sq_candidato")
            ),
        )
        g.adicionar_arestas(
            "CANDIDATO_EM",
            _aresta(candidatos, "Pessoa", "Candidatura", no_pessoa, id_candidatura,
                    fonte("candidatos")),
        )  # fmt: skip

    for arquivo, tipo, prefixo in (
        ("receitas", "DOOU_PARA", "doador"),
        ("despesas", "FORNECEU_CAMPANHA", "fornecedor"),
    ):
        df = _ler(base / f"{arquivo}.parquet")
        if df is None:
            continue
        agregado = (
            df.with_columns(candidatura=id_candidatura)
            .group_by("candidatura", "ano_eleicao", f"{prefixo}_tipo", f"{prefixo}_documento",
                      f"{prefixo}_id_pessoa", f"{prefixo}_nome_normalizado")
            .agg(pl.col("valor").sum(), pl.len().alias("n_lancamentos"))
        )  # fmt: skip
        _fornecedor(
            g, tipo, agregado, prefixo, pl.col("candidatura"), "Candidatura", fonte("contas"),
            f"{prefixo}_tipo", valor=pl.col("valor"), n_lancamentos=pl.col("n_lancamentos"),
        )  # fmt: skip


def _sancoes(g: Grafo, ctx: Contexto) -> None:
    """Só sanções de empresas e pessoas que já estão no grafo (minimização)."""
    sancoes = _ler(ctx.silver("transparencia") / "sancoes.parquet")
    if sancoes is None:
        return
    sancoes = sancoes.with_columns(
        id_sancao=pl.concat_str(pl.col("cadastro"), pl.lit("-"), pl.col("codigo_sancao")),
        titular=pl.when(pl.col("eh_pf")).then(pl.col("id_pessoa")).otherwise(pl.col("cnpj_raiz")),
        rotulo=pl.when(pl.col("eh_pf")).then(pl.lit("Pessoa")).otherwise(pl.lit("Empresa")),
    )
    relevantes = sancoes.filter(
        (pl.col("eh_pf") & pl.col("titular").is_in(g.ids("Pessoa")))
        | (~pl.col("eh_pf") & pl.col("titular").is_in(g.ids("Empresa")))
    )
    g.adicionar_nos(
        "Sancao",
        relevantes.select(
            id=pl.col("id_sancao"),
            nome=pl.col("categoria"),
            cadastro=pl.col("cadastro"),
            categoria=pl.col("categoria"),
            data_inicio=pl.col("data_inicio"),
            data_fim=pl.col("data_fim"),
            orgao_sancionador=pl.col("orgao_sancionador"),
            esfera=pl.col("esfera_orgao_sancionador"),
            fundamentacao_legal=pl.col("fundamentacao_legal"),
            numero_processo=pl.col("numero_processo"),
        ),
    )
    for rotulo in ("Pessoa", "Empresa"):
        df = relevantes.filter(pl.col("rotulo") == rotulo)
        g.adicionar_arestas(
            "SANCIONADA",
            _aresta(df, rotulo, "Sancao", pl.col("titular"), pl.col("id_sancao"),
                    pl.col("arquivo_fonte")),
        )  # fmt: skip


def construir(ctx: Contexto, pessoas: pl.DataFrame, ligacoes: pl.DataFrame) -> Grafo:
    """Monta o grafo completo a partir das tabelas silver e das entidades resolvidas."""
    g = Grafo()
    _pessoas(g, pessoas, ligacoes)
    _receita(g, ctx)
    _pncp(g, ctx)
    _tse(g, ctx)
    _sancoes(g, ctx)
    # Pessoas citadas em arestas sem menção consolidada (não deveria ocorrer) ganham nó mínimo.
    _completar_pontas(g)
    return g


def _completar_pontas(g: Grafo) -> None:
    for rotulo in ("Pessoa", "Empresa"):
        pontas: Iterable[pl.Series] = [
            df.filter(pl.col(lado_rotulo) == rotulo)[lado]
            for df in g.arestas.values()
            for lado, lado_rotulo in (("origem", "rotulo_origem"), ("destino", "rotulo_destino"))
        ]
        todas = pl.concat([s.rename("id") for s in pontas]).unique()
        faltam = todas.filter(~todas.is_in(g.ids(rotulo)))
        if faltam.len():
            g.adicionar_nos(rotulo, pl.DataFrame({"id": faltam}))

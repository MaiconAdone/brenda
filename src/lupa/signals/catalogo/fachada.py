"""Sinais de empresa de fachada (F01, F02, F04, F06)."""

from __future__ import annotations

from typing import ClassVar

import polars as pl

from lupa.signals.base import Alerta, Categoria, Sinal, registrar
from lupa.signals.catalogo._util import data_br, montar, reais
from lupa.signals.dados import URL_RECEITA, Tabelas, elegiveis, fonte


def _empresas_fornecedoras(t: Tabelas) -> pl.DataFrame | None:
    v = t.get("derivado/vitorias")
    return None if v is None else elegiveis(v)


@registrar
class F01AberturaRecente(Sinal):
    """F01: empresa aberta pouco antes de vencer a primeira licitação."""

    id: ClassVar[str] = "F01"
    nome: ClassVar[str] = "Empresa aberta pouco antes da 1ª vitória"
    descricao: ClassVar[str] = (
        "A empresa começou a atividade poucos dias antes de vencer a primeira licitação no piloto."
    )
    categoria: ClassVar[Categoria] = Categoria.FACHADA
    peso_padrao: ClassVar[float] = 12
    limitacoes: ClassVar[str] = (
        "Usa a data de início de atividade mais antiga entre os estabelecimentos. "
        "Empresas novas vencem licitações legitimamente; o sinal só indica atenção."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        dias_max = int(self.parametros.get("dias_max_abertura", 180))
        v = _empresas_fornecedoras(t)
        estab = t.get("receita_cnpj/estabelecimentos")
        if v is None or estab is None:
            return []
        primeira = v.sort("data").group_by("entidade", maintain_order=True).first()
        abertura = estab.group_by("cnpj_basico").agg(
            pl.col("data_inicio_atividade").min().alias("abertura")
        )
        df = (
            primeira.join(
                abertura.select(
                    entidade=pl.lit("Empresa:") + pl.col("cnpj_basico"), abertura="abertura"
                ),
                on="entidade",
            )
            .with_columns(dias=(pl.col("data") - pl.col("abertura")).dt.total_days())
            .filter(pl.col("dias").is_between(0, dias_max))
        )  # fmt: skip
        return montar(
            self.id, df,
            lambda r: (
                f"Início de atividade em {data_br(r['abertura'])} e primeira vitória em "
                f"{data_br(r['data'])} ({r['dias']} dias depois), em licitação de "
                f"{r['orgao_nome']} no valor de {reais(r['valor'])}."
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA), fonte(t, "pncp", r["url_fonte"])),
            ["abertura", "data", "dias", "valor", "numero_controle_pncp"],
        )  # fmt: skip


@registrar
class F02CapitalBaixo(Sinal):
    """F02: valor vencido muito maior que o capital social."""

    id: ClassVar[str] = "F02"
    nome: ClassVar[str] = "Capital social baixo frente ao valor contratado"
    descricao: ClassVar[str] = (
        "O valor total vencido supera muitas vezes o capital social declarado."
    )
    categoria: ClassVar[Categoria] = Categoria.FACHADA
    peso_padrao: ClassVar[float] = 10
    limitacoes: ClassVar[str] = (
        "Capital social é declaratório e raramente atualizado. Empresas com capital zero "
        "ou sem informação ficam de fora."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        razao_min = float(self.parametros.get("razao_min_contrato_capital", 20))
        v = _empresas_fornecedoras(t)
        empresas = t.get("receita_cnpj/empresas")
        if v is None or empresas is None:
            return []
        total = v.group_by("entidade").agg(
            pl.col("valor").sum().alias("total_vencido"),
            pl.len().alias("licitacoes"),
            pl.col("url_fonte").sort_by("valor", descending=True).head(3).alias("urls"),
        )
        df = (
            total.join(
                empresas.select(
                    entidade=pl.lit("Empresa:") + pl.col("cnpj_basico"),
                    capital_social=pl.col("capital_social"),
                ),
                on="entidade",
            )
            .filter(pl.col("capital_social") > 0)
            .with_columns(razao=pl.col("total_vencido") / pl.col("capital_social"))
            .filter(pl.col("razao") >= razao_min)
        )
        return montar(
            self.id, df,
            lambda r: (
                f"Venceu {reais(r['total_vencido'])} em {r['licitacoes']} licitação(ões), "
                f"{r['razao']:.0f} vezes o capital social de {reais(r['capital_social'])}."
            ),
            lambda r: (
                fonte(t, "receita_cnpj", URL_RECEITA),
                *(fonte(t, "pncp", u) for u in r["urls"]),
            ),
            ["total_vencido", "capital_social", "razao", "licitacoes"],
        )  # fmt: skip


@registrar
class F04EnderecoCompartilhado(Sinal):
    """F04: muitas empresas ativas registradas no mesmo endereço."""

    id: ClassVar[str] = "F04"
    nome: ClassVar[str] = "Endereço compartilhado com muitas empresas"
    descricao: ClassVar[str] = (
        "O endereço (no nível do prédio) da fornecedora concentra várias empresas ativas."
    )
    categoria: ClassVar[Categoria] = Categoria.FACHADA
    peso_padrao: ClassVar[float] = 8
    limitacoes: ClassVar[str] = (
        "Centros empresariais, escritórios de contabilidade e endereços fiscais "
        "compartilhados geram falso positivo. Não se sabe se o endereço é residencial."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        minimo = int(self.parametros.get("min_empresas_mesmo_endereco", 5))
        v = _empresas_fornecedoras(t)
        local = t.get("grafo/arestas/LOCALIZADA_EM")
        estab = t.get("grafo/nos/Estabelecimento")
        enderecos = t.get("grafo/nos/Endereco")
        if v is None or local is None or estab is None or enderecos is None:
            return []
        ativos = local.join(
            estab.filter(pl.col("situacao_cadastral") == "02").select(origem="id"), on="origem"
        ).with_columns(empresa=pl.lit("Empresa:") + pl.col("origem").str.slice(0, 8))
        concentracao = ativos.group_by("destino").agg(
            pl.col("empresa").n_unique().alias("n_empresas")
        )
        fornecedoras = v.select("entidade").unique()
        df = (
            ativos.join(fornecedoras, left_on="empresa", right_on="entidade")
            .join(concentracao.filter(pl.col("n_empresas") >= minimo), on="destino")
            .join(enderecos.select(destino="id", endereco="texto"), on="destino")
            .select(entidade="empresa", cnpj="origem", endereco="endereco", n_empresas="n_empresas")
            .unique()
        )
        return montar(
            self.id, df,
            lambda r: (
                f"Estabelecimento {r['cnpj']} fica em endereço com {r['n_empresas']} empresas "
                f"ativas: {r['endereco']}."
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA),),
            ["cnpj", "endereco", "n_empresas"],
        )  # fmt: skip


@registrar
class F06ContatoCompartilhado(Sinal):
    """F06: fornecedoras do mesmo órgão com o mesmo telefone ou e-mail."""

    id: ClassVar[str] = "F06"
    nome: ClassVar[str] = "Telefone/e-mail compartilhado entre fornecedoras"
    descricao: ClassVar[str] = (
        "Empresas diferentes que fornecem ao mesmo órgão declaram o mesmo telefone ou e-mail."
    )
    categoria: ClassVar[Categoria] = Categoria.FACHADA
    peso_padrao: ClassVar[float] = 12
    limitacoes: ClassVar[str] = (
        "Contadores e despachantes costumam registrar o próprio contato em várias empresas. "
        "O PNCP só mostra vencedores, então a comparação é entre fornecedoras, não entre "
        "todos os concorrentes. Contatos usados por muitas empresas (centrais, contadores) "
        "são ignorados."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        max_empresas = int(self.parametros.get("max_empresas_por_contato", 10))
        v = _empresas_fornecedoras(t)
        if v is None:
            return []
        contatos = []
        for tipo, rotulo in (("TEM_TELEFONE", "telefone"), ("TEM_EMAIL", "e-mail")):
            arestas = t.get(f"grafo/arestas/{tipo}")
            if arestas is not None:
                contatos.append(
                    arestas.select(
                        empresa=pl.lit("Empresa:") + pl.col("origem").str.slice(0, 8),
                        contato="destino",
                        tipo_contato=pl.lit(rotulo),
                    )
                )
        if not contatos:
            return []
        por_orgao = v.select("entidade", "orgao_cnpj", "orgao_nome").unique()
        todos = pl.concat(contatos).unique()
        grau = todos.group_by("contato").agg(pl.col("empresa").n_unique().alias("n"))
        base = (
            todos.join(grau.filter(pl.col("n") <= max_empresas), on="contato")
            .drop("n")
            .join(por_orgao, left_on="empresa", right_on="entidade")
        )
        nomes = t.get("derivado/nomes")
        pares = (
            base.join(base, on=["contato", "tipo_contato", "orgao_cnpj"], suffix="_outra")
            .filter(pl.col("empresa") != pl.col("empresa_outra"))
            .select(
                entidade="empresa", outra="empresa_outra", tipo_contato="tipo_contato",
                orgao_nome="orgao_nome",
            )
            .unique()
        )  # fmt: skip
        if nomes is not None:
            pares = pares.join(
                nomes.select(outra="entidade", nome_outra="nome"), on="outra", how="left"
            )
        else:
            pares = pares.with_columns(nome_outra=pl.lit(None, dtype=pl.String))
        return montar(
            self.id, pares.sort("entidade", "outra"),
            lambda r: (
                f"Mesmo {r['tipo_contato']} que {r['nome_outra'] or r['outra']} "
                f"({r['outra'].removeprefix('Empresa:')}), também fornecedora de {r['orgao_nome']}."
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA),),
            ["outra", "tipo_contato", "orgao_nome"],
        )  # fmt: skip

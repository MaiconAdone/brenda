"""Sinais de conluio (K04). K01–K03 dependem de todas as propostas, que o PNCP não traz."""

from __future__ import annotations

from typing import ClassVar

import polars as pl

from lupa.signals.base import Alerta, Categoria, Sinal, registrar
from lupa.signals.catalogo._util import montar
from lupa.signals.dados import URL_RECEITA, Tabelas, elegiveis, expandir_pessoas, fonte


@registrar
class K04SocioEmComum(Sinal):
    """K04: fornecedoras do mesmo órgão com sócio em comum."""

    id: ClassVar[str] = "K04"
    nome: ClassVar[str] = "Fornecedoras do mesmo órgão com sócio em comum"
    descricao: ClassVar[str] = (
        "Duas empresas que vencem licitações do mesmo órgão têm um sócio em comum."
    )
    categoria: ClassVar[Categoria] = Categoria.CONLUIO
    peso_padrao: ClassVar[float] = 15
    limitacoes: ClassVar[str] = (
        "Só vencedores são conhecidos: não se sabe se as empresas disputaram a mesma "
        "licitação. Grupos econômicos legítimos também têm sócios em comum; quando o sócio "
        "em comum é uma empresa (holding), a confiança é 0,5."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        v = t.get("derivado/vitorias")
        socios = t.get("derivado/socios")
        if v is None or socios is None:
            return []
        por_orgao = elegiveis(v).select("entidade", "orgao_cnpj", "orgao_nome").unique()
        base = socios.join(por_orgao, left_on="empresa", right_on="entidade")
        expandida = expandir_pessoas(t, base, "socio")
        nomes = t.get("derivado/nomes")
        pares = (
            base.with_columns(confianca=pl.lit(1.0))
            .join(expandida, on=["socio", "orgao_cnpj"], suffix="_outra")
            .filter(pl.col("empresa") != pl.col("empresa_outra"))
            # Sócio PJ em comum costuma ser grupo econômico declarado: confiança reduzida.
            .with_columns(
                confianca=pl.min_horizontal("confianca", "confianca_outra")
                * pl.when(pl.col("socio").str.starts_with("Empresa:")).then(0.5).otherwise(1.0)
            )
            .sort("confianca", descending=True)
            .unique(["empresa", "empresa_outra", "orgao_cnpj"], keep="first")
            .sort("empresa", "empresa_outra")
        )
        if nomes is not None:
            pares = pares.join(
                nomes.select(empresa_outra="entidade", nome_outra="nome"),
                on="empresa_outra", how="left",
            )  # fmt: skip
        else:
            pares = pares.with_columns(nome_outra=pl.lit(None, dtype=pl.String))
        return montar(
            self.id, pares,
            lambda r: (
                f"Sócio {r['nome_socio']} ({r['documento'] or 'PJ'}) também é sócio de "
                f"{r['nome_outra'] or r['empresa_outra']} "
                f"({r['empresa_outra'].removeprefix('Empresa:')}); as duas fornecem a "
                f"{r['orgao_nome']}."
                + (f" Ligação provável: {r['via']}." if r["via"] else "")
            ),
            lambda r: (fonte(t, "receita_cnpj", URL_RECEITA),),
            ["socio", "empresa_outra", "orgao_cnpj", "confianca"],
            coluna_entidade="empresa",
            coluna_confianca="confianca",
        )  # fmt: skip

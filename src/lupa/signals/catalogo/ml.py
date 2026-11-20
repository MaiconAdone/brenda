"""Sinais de apoio vindos da Fase 5 (ML): perfil atípico (M01) e núcleo de fornecedoras (M02).

Leem as tabelas gravadas por ``python -m lupa.ml`` (``gold/ml/``). Sem elas, não
disparam. O M01 é ``somente_combinado``: um modelo nunca é a única justificativa de um
alerta.
"""

from __future__ import annotations

import json
from typing import ClassVar

import polars as pl

from lupa.ml.features import FEATURES, formatar
from lupa.signals.base import Alerta, Categoria, Sinal, registrar
from lupa.signals.catalogo._util import montar, reais
from lupa.signals.dados import URL_RECEITA, Tabelas, fonte

TIPOS_LACO = {"socio": "sócio em comum", "contato": "mesmo telefone/e-mail",
              "endereco": "mesmo endereço"}  # fmt: skip


@registrar
class M01PerfilAtipico(Sinal):
    """M01: fornecedora com perfil atípico (Isolation Forest + SHAP)."""

    id: ClassVar[str] = "M01"
    nome: ClassVar[str] = "Perfil de fornecedora atípico (modelo)"
    descricao: ClassVar[str] = (
        "A combinação de valor vencido, preços, dispensas, aditivos, idade da empresa e "
        "contatos foge do padrão das demais fornecedoras do piloto."
    )
    categoria: ClassVar[Categoria] = Categoria.ANOMALIA
    peso_padrao: ClassVar[float] = 6
    somente_combinado: ClassVar[bool] = True
    limitacoes: ClassVar[str] = (
        "Modelo não supervisionado: aponta o que é raro, não o que é irregular, e só conta "
        "junto com outro sinal. Cooperativas da agricultura familiar vendem por chamada "
        "pública (dispensa) e costumam aparecer. As explicações (SHAP) dizem o que pesou, "
        "não a causa."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        a = t.get("ml/anomalias")
        if a is None or a.is_empty():
            return []
        limiar = float(self.parametros.get("percentil", 0.98))
        df = a.filter(pl.col("percentil") >= limiar).with_columns(
            # 0,5 no limiar, 1,0 na empresa mais atípica.
            confianca=(0.5 + 0.5 * (pl.col("percentil") - limiar) / max(1e-9, 1 - limiar))
        )
        fontes = (fonte(t, "pncp", "https://pncp.gov.br/"),
                  fonte(t, "receita_cnpj", URL_RECEITA))  # fmt: skip
        return montar(
            self.id, df.sort("entidade"),
            lambda r: _descrever_anomalia(r),
            lambda r: fontes,
            ["percentil", "escore", "explicacao"],
            coluna_confianca="confianca",
        )  # fmt: skip


def _descrever_anomalia(r: dict[str, object]) -> str:
    itens = json.loads(str(r["explicacao"]))
    partes = [
        f"{FEATURES[e['feature']].nome}: {formatar(e['feature'], e['valor'])} "
        f"(mediana {formatar(e['feature'], e['mediana'])})"
        for e in itens
    ]
    pct = float(r["percentil"]) * 100  # type: ignore[arg-type]
    return (
        f"Perfil mais atípico que {pct:.0f}% das fornecedoras do piloto (Isolation Forest). "
        f"O que mais pesou (SHAP): {'; '.join(partes) or 'combinação de fatores'}."
    )


@registrar
class M02NucleoDeFornecedoras(Sinal):
    """M02: núcleo de 3+ fornecedoras ligadas entre si que vendem ao mesmo órgão."""

    id: ClassVar[str] = "M02"
    nome: ClassVar[str] = "Núcleo de fornecedoras ligadas (comunidades)"
    descricao: ClassVar[str] = (
        "Três ou mais fornecedoras da mesma comunidade (Louvain) estão ligadas entre si por "
        "sócio, contato ou endereço e vendem ao mesmo órgão."
    )
    categoria: ClassVar[Categoria] = Categoria.CONLUIO
    peso_padrao: ClassVar[float] = 8
    limitacoes: ClassVar[str] = (
        "O PNCP só publica vencedoras: co-vitória não quer dizer que disputaram entre si. "
        "Grupos familiares e econômicos legítimos formam núcleos. Sobrepõe-se ao K04 e ao "
        "F06 (o teto da categoria evita contar duas vezes)."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        n = t.get("ml/nucleos")
        if n is None or n.is_empty():
            return []
        nomes_df = t.get("derivado/nomes")
        nomes = (
            dict(nomes_df.select("entidade", "nome").iter_rows()) if nomes_df is not None else {}
        )
        # Mais confiança quando o núcleo venceu junto a mesma licitação.
        df = n.with_columns(
            confianca=pl.when(pl.col("co_vitorias") > 0).then(1.0).otherwise(0.7)
        ).sort("entidade")
        fontes = (fonte(t, "receita_cnpj", URL_RECEITA),
                  fonte(t, "pncp", "https://pncp.gov.br/"))  # fmt: skip

        def descrever(r: dict[str, object]) -> str:
            membros = [str(m) for m in r["membros"]]  # type: ignore[attr-defined]
            outras = [nomes.get(m, m) for m in membros if m != r["entidade"]]
            lacos = "; ".join(
                f"{TIPOS_LACO[tipo]} com {nomes.get(outra, outra)}"
                for tipo, _, outra in (str(x).partition("|") for x in r["lacos"])  # type: ignore[attr-defined]
            )
            co = int(r["co_vitorias"])  # type: ignore[call-overload]
            return (
                f"Faz parte de um núcleo de {len(membros)} fornecedoras ligadas entre si "
                f"({', '.join(outras)}). Ligações desta empresa: {lacos}. "
                f"{r['n_no_orgao']} delas vendem a {r['orgao_comum']}; o núcleo venceu "
                f"{reais(float(r['valor_nucleo']))} no total"  # type: ignore[arg-type]
                + (f" e venceu junto {co} licitação(ões)." if co else ".")
            )

        return montar(
            self.id, df, descrever, lambda r: fontes,
            ["nucleo", "membros", "lacos", "orgao_comum", "co_vitorias", "valor_nucleo"],
            coluna_confianca="confianca",
        )  # fmt: skip

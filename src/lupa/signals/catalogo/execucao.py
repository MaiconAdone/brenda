"""Sinais de execução contratual (E01, E02, E03, E04)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar

import polars as pl

from lupa.signals.base import Alerta, Categoria, Sinal, registrar
from lupa.signals.catalogo._util import data_br, montar, reais
from lupa.signals.dados import Tabelas, fonte

DISPENSA = 8

# Limites de dispensa por valor (Lei 14.133, art. 75, I e II), atualizados por decreto.
# 2025: Decreto 12.343/2024; 2026: Decreto 12.807/2025. 2023 e 2024: conferir no Planalto.
LIMITES_PADRAO: dict[int, dict[str, float]] = {
    2023: {"I": 114_416.65, "II": 57_208.33},
    2024: {"I": 119_812.02, "II": 59_906.02},
    2025: {"I": 125_451.15, "II": 62_725.59},
    2026: {"I": 130_984.20, "II": 65_492.11},
}


@registrar
class E01AditivoAcimaDoLimite(Sinal):
    """E01: valor global do contrato muito acima do valor inicial."""

    id: ClassVar[str] = "E01"
    nome: ClassVar[str] = "Acréscimo acima do limite legal"
    descricao: ClassVar[str] = (
        "O valor global do contrato supera o inicial além do limite de acréscimos da Lei "
        "14.133 (25%, art. 125)."
    )
    categoria: ClassVar[Categoria] = Categoria.EXECUCAO_CONTRATUAL
    peso_padrao: ClassVar[float] = 10
    limitacoes: ClassVar[str] = (
        "Aproximação: compara valor global e inicial publicados no PNCP, sem ler os termos "
        "aditivos. Prorrogações de serviços contínuos aumentam o valor global sem violar o "
        "limite; reformas têm limite de 50%."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        limite = float(self.parametros.get("limite_aditivo", 0.25))
        contratos = t.get("derivado/contratos")
        if contratos is None:
            return []
        df = contratos.filter(
            (pl.col("valor_inicial") > 0)
            & (pl.col("valor_global") > pl.col("valor_inicial") * (1 + limite))
        ).with_columns(acrescimo=pl.col("valor_global") / pl.col("valor_inicial") - 1)
        return montar(
            self.id, df,
            lambda r: (
                f"Contrato com {r['orgao_nome']} passou de {reais(r['valor_inicial'])} para "
                f"{reais(r['valor_global'])} (+{r['acrescimo']:.0%})."
            ),
            lambda r: (fonte(t, "pncp", r["url_fonte"]),),
            ["controle", "valor_inicial", "valor_global", "acrescimo"],
        )  # fmt: skip


def _janelas(linhas: list[dict[str, Any]], dias: int) -> list[list[dict[str, Any]]]:
    """Para cada linha (ordenada por data), as linhas dentro de ``dias`` a partir dela."""
    janelas = []
    for i, inicio in enumerate(linhas):
        fim: date = inicio["data"] + timedelta(days=dias)
        janelas.append([r for r in linhas[i:] if r["data"] <= fim])
    return janelas


def _dispensas(t: Tabelas) -> pl.DataFrame | None:
    v = t.get("derivado/vitorias")
    if v is None:
        return None
    return v.filter(pl.col("modalidade_id") == DISPENSA).with_columns(
        inciso=pl.col("amparo_legal").str.extract(r"Art\. 75, (I{1,2})$")
    )


@registrar
class E02DispensasRepetidas(Sinal):
    """E02: várias dispensas do mesmo órgão para o mesmo fornecedor em pouco tempo."""

    id: ClassVar[str] = "E02"
    nome: ClassVar[str] = "Dispensas repetidas ao mesmo fornecedor"
    descricao: ClassVar[str] = (
        "O mesmo órgão contratou o mesmo fornecedor por dispensa várias vezes em um ano."
    )
    categoria: ClassVar[Categoria] = Categoria.EXECUCAO_CONTRATUAL
    peso_padrao: ClassVar[float] = 10
    limitacoes: ClassVar[str] = (
        "Dispensas repetidas podem ser legítimas (emergências, fornecedor exclusivo, "
        "pequenas compras de natureza diferente)."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        minimo = int(self.parametros.get("min_dispensas", 3))
        janela = int(self.parametros.get("janela_dias", 365))
        dispensas = _dispensas(t)
        if dispensas is None:
            return []
        linhas = []
        for (entidade, orgao), grupo in dispensas.sort("data").group_by(
            "entidade", "orgao_cnpj", maintain_order=True
        ):
            maior = max(_janelas(grupo.to_dicts(), janela), key=len, default=[])
            if len(maior) >= minimo:
                linhas.append({
                    "entidade": entidade, "orgao_cnpj": orgao,
                    "orgao_nome": maior[0]["orgao_nome"], "n_dispensas": len(maior),
                    "inicio": maior[0]["data"], "fim": maior[-1]["data"],
                    "valor_total": sum(r["valor"] or 0 for r in maior),
                    "urls": [r["url_fonte"] for r in maior],
                })  # fmt: skip
        if not linhas:
            return []
        return montar(
            self.id, pl.DataFrame(linhas),
            lambda r: (
                f"{r['n_dispensas']} dispensas de {r['orgao_nome']} entre {data_br(r['inicio'])} "
                f"e {data_br(r['fim'])}, somando {reais(r['valor_total'])}."
            ),
            lambda r: tuple(fonte(t, "pncp", u) for u in r["urls"]),
            ["orgao_cnpj", "n_dispensas", "inicio", "fim", "valor_total"],
        )  # fmt: skip


@registrar
class E03Fracionamento(Sinal):
    """E03: dispensas por valor que, somadas em pouco tempo, passam do limite."""

    id: ClassVar[str] = "E03"
    nome: ClassVar[str] = "Possível fracionamento de despesa"
    descricao: ClassVar[str] = (
        "Dispensas por valor (art. 75, I ou II) do mesmo órgão ao mesmo fornecedor, cada "
        "uma abaixo do limite, somam mais que o limite num intervalo curto."
    )
    categoria: ClassVar[Categoria] = Categoria.EXECUCAO_CONTRATUAL
    peso_padrao: ClassVar[float] = 12
    limitacoes: ClassVar[str] = (
        "A lei avalia o fracionamento pelo objeto de mesma natureza no exercício; aqui a "
        "aproximação é mesmo órgão + mesmo fornecedor + janela de dias. Os limites de 2023 e "
        "2024 devem ser conferidos no Planalto."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        janela = int(self.parametros.get("janela_dias", 90))
        limites = {int(ano): v for ano, v in self.parametros.get("limites", LIMITES_PADRAO).items()}
        dispensas = _dispensas(t)
        if dispensas is None:
            return []
        linhas = []
        grupos = dispensas.filter(pl.col("inciso").is_not_null()).sort("data")
        for (entidade, orgao, inciso), grupo in grupos.group_by(
            "entidade", "orgao_cnpj", "inciso", maintain_order=True
        ):
            for janela_ in _janelas(grupo.to_dicts(), janela):
                limite = limites.get(janela_[0]["data"].year, {}).get(str(inciso))
                total = sum(r["valor"] or 0 for r in janela_)
                if limite and len(janela_) >= 2 and total > limite:
                    linhas.append({
                        "entidade": entidade, "orgao_cnpj": orgao,
                        "orgao_nome": janela_[0]["orgao_nome"], "inciso": inciso,
                        "n_dispensas": len(janela_), "inicio": janela_[0]["data"],
                        "fim": janela_[-1]["data"], "valor_total": total, "limite": limite,
                        "urls": [r["url_fonte"] for r in janela_],
                    })  # fmt: skip
                    break  # uma evidência por grupo: a primeira janela que estoura
        if not linhas:
            return []
        return montar(
            self.id, pl.DataFrame(linhas),
            lambda r: (
                f"{r['n_dispensas']} dispensas (art. 75, {r['inciso']}) de {r['orgao_nome']} "
                f"entre {data_br(r['inicio'])} e {data_br(r['fim'])} somam "
                f"{reais(r['valor_total'])}, acima do limite de {reais(r['limite'])}."
            ),
            lambda r: tuple(fonte(t, "pncp", u) for u in r["urls"]),
            ["orgao_cnpj", "inciso", "n_dispensas", "inicio", "fim", "valor_total", "limite"],
        )  # fmt: skip


@registrar
class E04SancaoAtiva(Sinal):
    """E04: contratada estava sancionada na data da vitória ou do contrato."""

    id: ClassVar[str] = "E04"
    nome: ClassVar[str] = "Contratada com sanção ativa"
    descricao: ClassVar[str] = (
        "A empresa venceu licitação ou assinou contrato enquanto constava sanção vigente "
        "no CEIS ou no CNEP."
    )
    categoria: ClassVar[Categoria] = Categoria.EXECUCAO_CONTRATUAL
    peso_padrao: ClassVar[float] = 20
    limitacoes: ClassVar[str] = (
        "Só há o arquivo do dia: sanções já encerradas e retiradas do cadastro não "
        "aparecem. A abrangência da sanção (só o órgão sancionador ou todas as esferas) "
        "define se a contratação era vedada; ela é mostrada na evidência."
    )

    def evidencias(self, t: Tabelas) -> list[Alerta]:
        sancoes = t.get("transparencia/sancoes")
        if sancoes is None:
            return []
        eventos = []
        for nome, evento, col_valor in (
            ("derivado/vitorias", "venceu licitação de", "valor"),
            ("derivado/contratos", "assinou contrato com", "valor_global"),
        ):
            df = t.get(nome)
            if df is None:
                continue
            esfera = (
                pl.col("orgao_esfera")
                if "orgao_esfera" in df.columns
                else pl.lit(None, dtype=pl.String)
            )
            eventos.append(
                df.select(
                    "entidade", "orgao_nome", "url_fonte", "data", valor=pl.col(col_valor),
                    esfera_contratante=esfera, evento=pl.lit(evento),
                )
            )  # fmt: skip
        if not eventos:
            return []
        titulares = sancoes.select(
            entidade=pl.when(pl.col("eh_pf"))
            .then(pl.lit("Pessoa:") + pl.col("id_pessoa"))
            .otherwise(pl.lit("Empresa:") + pl.col("cnpj_raiz")),
            cadastro="cadastro", categoria="categoria", data_inicio="data_inicio",
            data_fim="data_fim", orgao_sancionador="orgao_sancionador",
            esfera_sancionador="esfera_orgao_sancionador", uf_sancionador="uf_orgao_sancionador",
            abrangencia="abrangencia", arquivo_fonte="arquivo_fonte",
        )  # fmt: skip
        df = (
            pl.concat(eventos)
            .join(titulares, on="entidade")
            .filter(
                (pl.col("data") >= pl.col("data_inicio"))
                & (pl.col("data_fim").is_null() | (pl.col("data") <= pl.col("data_fim")))
            )
            .with_columns(confianca=_alcance(t))
            .filter(pl.col("confianca") > 0)
            .sort("entidade", "data")
        )
        return montar(
            self.id, df,
            lambda r: (
                f"Em {data_br(r['data'])} {r['evento']} {r['orgao_nome']} ({reais(r['valor'])}) "
                f"com sanção vigente: {r['cadastro']} – {r['categoria']}, aplicada por "
                f"{r['orgao_sancionador']} de {data_br(r['data_inicio'])} a "
                f"{data_br(r['data_fim']) if r['data_fim'] else 'sem prazo'}; "
                f"abrangência: {r['abrangencia']}."
            ),
            lambda r: (
                fonte(t, "transparencia", r["arquivo_fonte"]),
                fonte(t, "pncp", r["url_fonte"]),
            ),
            ["data", "evento", "cadastro", "categoria", "data_inicio", "data_fim",
             "abrangencia", "confianca"],
            coluna_confianca="confianca",
        )  # fmt: skip


def _alcance(t: Tabelas) -> pl.Expr:
    """Confiança de que a sanção alcançava o órgão contratante (0 = não alcançava).

    - Todas as esferas: 1.
    - Restrita à esfera do sancionador: 1 se o contratante é do mesmo ente (o estado do
      piloto, ou o próprio município); 0,8 quando também restrita ao poder, que não é
      conferido.
    - Só no órgão sancionador: 0,8 se o sancionador é do município do piloto.
    - Sem informação: 0,5.
    - Outros casos (ex.: sanção federal e contratante municipal): não dispara.
    """
    piloto = t.get("meta/piloto")
    uf = piloto["uf"][0] if piloto is not None and piloto.height else None
    municipio = piloto["nome_municipio"][0] if piloto is not None and piloto.height else None
    abr = pl.col("abrangencia").fill_null("")
    contratante = pl.col("esfera_contratante").fill_null("")
    sancionador = pl.col("orgao_sancionador").fill_null("").str.to_uppercase()
    do_municipio = (
        sancionador.str.contains(municipio.upper(), literal=True) if municipio else pl.lit(False)
    )
    mesmo_ente = (
        (pl.col("esfera_sancionador") == "ESTADUAL")
        & (pl.col("uf_sancionador") == uf)
        & (contratante == "E")
    ) | ((pl.col("esfera_sancionador") == "MUNICIPAL") & do_municipio & (contratante == "M"))
    return (
        pl.when(abr == "Todas as Esferas em todos os Poderes").then(1.0)
        .when((abr == "Em todos os Poderes da Esfera do órgão sancionador") & mesmo_ente).then(1.0)
        .when((abr == "Na Esfera e no Poder do órgão sancionador") & mesmo_ente).then(0.8)
        .when((abr == "No órgão sancionador") & do_municipio & (contratante == "M")).then(0.8)
        .when(abr == "Sem Informação").then(0.5)
        .otherwise(0.0)
    )  # fmt: skip

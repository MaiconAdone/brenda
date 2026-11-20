"""Dados de consulta da interface e do dossiê, sem dependência de Streamlit.

:class:`Base` lê a silver, o grafo e os alertas da gold uma vez e responde:

- :meth:`Base.indice`: uma linha por entidade com alerta, com os campos dos filtros do
  ranking (órgão, período, categoria, valor);
- :meth:`Base.ficha`: cadastro, sinais com evidências, cronologia, vitórias e contratos;
- :meth:`Base.vizinhanca`: subgrafo de até 2 saltos, feito direto nas tabelas de arestas
  (sem montar o grafo inteiro em memória).

Documentos de pessoas físicas já chegam mascarados da silver (``***.123.456-**``).
Telefones e e-mails aparecem só como hash: a interface mostra que o contato é
compartilhado, nunca o contato em si.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

import lupa.signals.catalogo  # noqa: F401  (registra os sinais)
from lupa.graph.modelo import Grafo
from lupa.signals import dados
from lupa.signals.base import Sinal, sinais
from lupa.signals.executar import ranking_de

PORTES = {"00": "Não informado", "01": "Microempresa", "03": "Pequeno porte", "05": "Demais"}
SITUACOES_CNPJ = {"01": "Nula", "02": "Ativa", "03": "Suspensa", "04": "Inapta", "08": "Baixada"}
FAIXAS = {
    "0": "Não se aplica", "1": "0 a 12 anos", "2": "13 a 20 anos", "3": "21 a 30 anos",
    "4": "31 a 40 anos", "5": "41 a 50 anos", "6": "51 a 60 anos", "7": "61 a 70 anos",
    "8": "71 a 80 anos", "9": "mais de 80 anos",
}  # fmt: skip
URLS = {
    "receita_cnpj": dados.URL_RECEITA,
    "tse": dados.URL_TSE,
    "pncp": "https://pncp.gov.br/",
    "transparencia": "https://portaldatransparencia.gov.br/sancoes",
}


@dataclass(frozen=True)
class Evento:
    """Um fato datado da cronologia da entidade."""

    data: date
    descricao: str
    fonte: str
    url: str


@dataclass
class AlertaFicha:
    """Um sinal disparado, com suas evidências já prontas para exibir."""

    sinal_id: str
    nome: str
    categoria: str
    confianca: float
    limitacoes: str
    evidencias: list[dict[str, Any]]


@dataclass
class Ficha:
    """Tudo que a página da entidade e o dossiê mostram."""

    entidade: str
    tipo: str
    nome: str
    ranking: str
    posicao: int | None
    score: float
    por_categoria: dict[str, float]
    sinais: tuple[str, ...]
    cadastro: list[tuple[str, str]]
    alertas: list[AlertaFicha]
    cronologia: list[Evento]
    socios: pl.DataFrame
    vitorias: pl.DataFrame
    contratos: pl.DataFrame
    fontes: dict[str, str] = field(default_factory=dict)


@dataclass
class Subgrafo:
    """Nós e arestas em volta de uma entidade."""

    centro: str
    nos: pl.DataFrame  # no, rotulo, nome, salto, com_alerta
    arestas: pl.DataFrame  # o, d, tipo, probabilidade
    cortado: bool


def _texto(v: object) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    return str(v)


def _dominio(t: Mapping[str, pl.DataFrame], raiz_dados: Path, nome: str) -> dict[str, str]:
    caminho = raiz_dados / "silver" / "receita_cnpj" / f"dominio_{nome}.parquet"
    if not caminho.exists():
        return {}
    df = pl.read_parquet(caminho)
    return dict(zip(df["codigo"].cast(pl.String), df["descricao"], strict=True))


class Base:
    """Leitura única dos dados para consulta."""

    def __init__(
        self, raiz_dados: Path, uf: str | None = None, municipio: str | None = None
    ) -> None:
        self.raiz = raiz_dados
        self.t = dados.carregar(raiz_dados, uf, municipio)
        self.catalogo: dict[str, type[Sinal]] = sinais()
        vazio_scores = pl.DataFrame(
            schema={"ranking": pl.String, "posicao": pl.Int64, "entidade": pl.String,
                    "score": pl.Float64, "por_categoria": pl.String,
                    "sinais": pl.List(pl.String), "descartados": pl.List(pl.String)},
        )  # fmt: skip
        self.scores = self.t.get("alertas/scores", vazio_scores)
        self.alertas = self.t.get(
            "alertas/alertas",
            pl.DataFrame(schema={"sinal_id": pl.String, "entidade": pl.String,
                                 "confianca": pl.Float64, "n_evidencias": pl.Int64,
                                 "evidencias": pl.String}),
        )  # fmt: skip
        self.grafo = Grafo(
            nos={k.removeprefix("grafo/nos/"): v for k, v in self.t.items()
                 if k.startswith("grafo/nos/")},
            arestas={k.removeprefix("grafo/arestas/"): v for k, v in self.t.items()
                     if k.startswith("grafo/arestas/")},
        )  # fmt: skip
        self.dominios = {
            n: _dominio(self.t, raiz_dados, n)
            for n in ("naturezas", "cnaes", "municipios", "qualificacoes")
        }
        self.nomes: dict[str, str] = {}
        if (nomes := self.t.get("derivado/nomes")) is not None:
            self.nomes = dict(nomes.select("entidade", "nome").iter_rows())
        self._arestas = self._todas_arestas()
        self._grau: pl.DataFrame | None = None

    # ------------------------------------------------------------------ ranking

    def nome(self, entidade: str) -> str:
        """Nome de exibição da entidade."""
        return self.nomes.get(entidade) or entidade.partition(":")[2]

    def indice(self) -> pl.DataFrame:
        """Entidades com alerta e os campos usados nos filtros do ranking."""
        movimento = self._movimento()
        base = self.scores.with_columns(
            nome=pl.col("entidade").replace_strict(self.nomes, default=None),
            tipo=pl.col("entidade").str.split(":").list.first(),
            categorias=pl.col("por_categoria").map_elements(
                lambda s: sorted(json.loads(s)), return_dtype=pl.List(pl.String)
            ),
        )
        if movimento is not None:
            base = base.join(movimento, on="entidade", how="left")
        else:
            base = base.with_columns(
                orgaos=pl.lit(None, pl.List(pl.String)),
                data_min=pl.lit(None, pl.Date),
                data_max=pl.lit(None, pl.Date),
                valor_total=pl.lit(None, pl.Float64),
            )
        return base.with_columns(
            pl.col("orgaos").fill_null([]), pl.col("valor_total").fill_null(0.0)
        )

    def _movimento(self) -> pl.DataFrame | None:
        partes = []
        if (v := self.t.get("derivado/vitorias")) is not None:
            partes.append(v.select("entidade", "orgao_nome", "data", "valor"))
        if (c := self.t.get("derivado/contratos")) is not None:
            partes.append(c.select("entidade", "orgao_nome", "data", valor=pl.lit(0.0)))
        if not partes:
            return None
        return (
            pl.concat(partes)
            .group_by("entidade")
            .agg(
                pl.col("orgao_nome").drop_nulls().unique().sort().alias("orgaos"),
                pl.col("data").min().alias("data_min"),
                pl.col("data").max().alias("data_max"),
                pl.col("valor").sum().alias("valor_total"),
            )
        )

    # ------------------------------------------------------------------ ficha

    def ficha(self, entidade: str) -> Ficha:
        """Ficha completa de uma entidade (com ou sem alerta)."""
        tipo, _, ident = entidade.partition(":")
        linha = self.scores.filter(pl.col("entidade") == entidade)
        score = linha.row(0, named=True) if linha.height else None
        vitorias = self._de("derivado/vitorias", entidade)
        contratos = self._de("derivado/contratos", entidade)
        socios = self._socios(entidade)
        cadastro: list[tuple[str, str]]
        if tipo == "Empresa":
            cadastro, eventos = self._empresa(ident)
        elif tipo == "Pessoa":
            cadastro, eventos = self._pessoa(entidade)
        else:
            cadastro, eventos = self._candidatura(ident)
        eventos += self._eventos_movimento(vitorias, contratos)
        if tipo == "Empresa":
            eventos += self._eventos_socios(socios)
            eventos += self._eventos_sancoes(ident)
        alertas = self._alertas(entidade)
        fontes = {f["nome"]: f["url"] for a in alertas for ev in a.evidencias for f in ev["fontes"]}
        return Ficha(
            entidade=entidade,
            tipo=tipo,
            nome=self.nome(entidade),
            ranking=ranking_de(entidade),
            posicao=score["posicao"] if score else None,
            score=score["score"] if score else 0.0,
            por_categoria=json.loads(score["por_categoria"]) if score else {},
            sinais=tuple(score["sinais"]) if score else (),
            cadastro=cadastro,
            alertas=alertas,
            cronologia=sorted(eventos, key=lambda e: (e.data, e.descricao)),
            socios=socios,
            vitorias=vitorias,
            contratos=contratos,
            fontes=fontes,
        )

    def _de(self, tabela: str, entidade: str) -> pl.DataFrame:
        df = self.t.get(tabela)
        if df is None:
            return pl.DataFrame()
        return df.filter(pl.col("entidade") == entidade).sort("data", nulls_last=True)

    def _alertas(self, entidade: str) -> list[AlertaFicha]:
        saida = []
        for r in self.alertas.filter(pl.col("entidade") == entidade).iter_rows(named=True):
            classe = self.catalogo.get(r["sinal_id"])
            saida.append(
                AlertaFicha(
                    sinal_id=r["sinal_id"],
                    nome=classe.nome if classe else r["sinal_id"],
                    categoria=str(classe.categoria) if classe else "",
                    confianca=r["confianca"],
                    limitacoes=classe.limitacoes if classe else "",
                    evidencias=json.loads(r["evidencias"]),
                )
            )
        return sorted(saida, key=lambda a: a.sinal_id)

    def _socios(self, entidade: str) -> pl.DataFrame:
        socios = self.t.get("derivado/socios")
        if socios is None:
            return pl.DataFrame()
        coluna = "empresa" if entidade.startswith("Empresa:") else "socio"
        quali = self.dominios["qualificacoes"]
        return (
            socios.filter(pl.col(coluna) == entidade)
            .with_columns(
                qualificacao=pl.col("qualificacao").replace_strict(quali, default=None),
                faixa_etaria=pl.col("faixa_etaria").replace_strict(FAIXAS, default=None),
                nome_empresa=pl.col("empresa").replace_strict(self.nomes, default=None),
            )
            .select(
                "empresa", "nome_empresa", "socio", "nome_socio", "documento", "qualificacao",
                "faixa_etaria", "data_entrada",
            )
            .sort("data_entrada", nulls_last=True)
        )  # fmt: skip

    def _empresa(self, raiz: str) -> tuple[list[tuple[str, str]], list[Evento]]:
        cad: list[tuple[str, str]] = [("CNPJ raiz", raiz)]
        eventos: list[Evento] = []
        if (emp := self.t.get("receita_cnpj/empresas")) is not None:
            e = emp.filter(pl.col("cnpj_basico") == raiz)
            if e.height:
                r = e.row(0, named=True)
                natureza = self.dominios["naturezas"].get(str(r["natureza_juridica"]), "")
                cad += [
                    ("Razão social", _texto(r["razao_social"])),
                    ("Natureza jurídica", f"{r['natureza_juridica']} {natureza}".strip()),
                    ("Porte", PORTES.get(str(r["porte_empresa"]), _texto(r["porte_empresa"]))),
                    ("Capital social", _reais(r["capital_social"])),
                ]
        if (est := self.t.get("receita_cnpj/estabelecimentos")) is not None:
            e = est.filter(pl.col("cnpj_basico") == raiz).sort("identificador_matriz_filial")
            if e.height:
                r = e.row(0, named=True)
                cidade = self.dominios["municipios"].get(str(r["municipio"]), r["municipio"])
                cnae = self.dominios["cnaes"].get(str(r["cnae_fiscal_principal"]), "")
                cad += [
                    ("CNPJ (matriz)", _texto(r["cnpj"])),
                    ("Nome fantasia", _texto(r["nome_fantasia"])),
                    ("Situação cadastral", SITUACOES_CNPJ.get(
                        str(r["situacao_cadastral"]), _texto(r["situacao_cadastral"]))),
                    ("Início da atividade", _texto(r["data_inicio_atividade"])),
                    ("Atividade principal", f"{r['cnae_fiscal_principal']} {cnae}".strip()),
                    # Só bairro e cidade: em MEI o endereço costuma ser a casa do titular.
                    ("Localização", f"{_texto(r['bairro'])}, {cidade}/{r['uf']}"),
                    ("Estabelecimentos", str(e.height)),
                ]  # fmt: skip
                if isinstance(r["data_inicio_atividade"], date):
                    eventos.append(
                        Evento(r["data_inicio_atividade"], "Início da atividade (Receita)",
                               "receita_cnpj", dados.URL_RECEITA)
                    )  # fmt: skip
        return cad, eventos

    def _pessoa(self, entidade: str) -> tuple[list[tuple[str, str]], list[Evento]]:
        ident = entidade.partition(":")[2]
        cad: list[tuple[str, str]] = []
        eventos: list[Evento] = []
        pessoas = self.grafo.nos.get("Pessoa")
        if pessoas is not None:
            p = pessoas.filter(pl.col("id") == ident)
            if p.height:
                r = p.row(0, named=True)
                idade = (
                    f"{r['idade_min']}–{r['idade_max']} anos"
                    if r.get("idade_min") is not None else "—"
                )  # fmt: skip
                cad += [
                    ("Nome", _texto(r["nome"])),
                    ("Documento (mascarado)", _texto(r["documento"])),
                    ("Idade estimada", idade),
                    ("Origens", _origens(r.get("origens"))),
                ]
        cad.append(("Identificador interno", f"{ident[:8]}…"))
        for rel in ("CANDIDATO_EM",):
            arestas = self.grafo.arestas.get(rel)
            if arestas is None:
                continue
            for r in arestas.filter(pl.col("origem") == ident).iter_rows(named=True):
                cand = f"Candidatura:{r['destino']}"
                cad.append(("Candidatura", self.nome(cand)))
        mesma = self.t.get("derivado/mesma_pessoa")
        if mesma is not None:
            for r in mesma.filter(pl.col("a") == entidade).iter_rows(named=True):
                cad.append(
                    ("Mesma pessoa provável",
                     f"{self.nome(r['b'])} (confiança {r['probabilidade']:.2f}; {r['evidencia']})")
                )  # fmt: skip
        for s in self._socios(entidade).iter_rows(named=True):
            if isinstance(s["data_entrada"], date):
                empresa = s["nome_empresa"] or s["empresa"]
                eventos.append(
                    Evento(s["data_entrada"], f"Entrou como sócia de {empresa}", "receita_cnpj",
                           dados.URL_RECEITA)
                )  # fmt: skip
        return cad, eventos

    def _candidatura(self, ident: str) -> tuple[list[tuple[str, str]], list[Evento]]:
        cad: list[tuple[str, str]] = []
        eventos: list[Evento] = []
        cands = self.grafo.nos.get("Candidatura")
        if cands is not None:
            c = cands.filter(pl.col("id") == ident)
            if c.height:
                r = c.row(0, named=True)
                cad += [
                    ("Nome de urna", _texto(r["nome"])),
                    ("Cargo", _texto(r["cargo"])),
                    ("Eleição", _texto(r["ano_eleicao"])),
                    ("Partido", _texto(r.get("partido"))),
                    ("Número", _texto(r.get("numero"))),
                    ("Resultado", _texto(r.get("resultado"))),
                    ("Situação", _texto(r.get("situacao_candidatura"))),
                ]
                ano = str(r["ano_eleicao"])
                if ano.isdigit():
                    eventos.append(
                        Evento(date(int(ano), 10, 1), f"Eleição de {ano} (outubro)", "tse",
                               dados.URL_TSE)
                    )  # fmt: skip
        return cad, eventos

    def _eventos_movimento(self, vitorias: pl.DataFrame, contratos: pl.DataFrame) -> list[Evento]:
        eventos = []
        for r in vitorias.iter_rows(named=True):
            if isinstance(r.get("data"), date):
                eventos.append(
                    Evento(r["data"], f"Venceu licitação de {r['orgao_nome']} "
                           f"({_reais(r['valor'])}): {_curto(r['objeto'])}", "pncp", r["url_fonte"])
                )  # fmt: skip
        for r in contratos.iter_rows(named=True):
            if isinstance(r.get("data"), date):
                eventos.append(
                    Evento(r["data"], f"Assinou contrato com {r['orgao_nome']} "
                           f"({_reais(r['valor_global'])}): {_curto(r['objeto'])}", "pncp",
                           r["url_fonte"])
                )  # fmt: skip
        return eventos

    def _eventos_socios(self, socios: pl.DataFrame) -> list[Evento]:
        return [
            Evento(s["data_entrada"], f"Entrada do sócio {s['nome_socio']} ({s['documento']})",
                   "receita_cnpj", dados.URL_RECEITA)
            for s in socios.iter_rows(named=True)
            if isinstance(s["data_entrada"], date)
        ]  # fmt: skip

    def _eventos_sancoes(self, raiz: str) -> list[Evento]:
        sancoes = self.t.get("transparencia/sancoes")
        if sancoes is None:
            return []
        eventos = []
        for r in sancoes.filter(pl.col("cnpj_raiz") == raiz).iter_rows(named=True):
            url = r.get("arquivo_fonte") or URLS["transparencia"]
            texto = (
                f"{r['cadastro']}: {r['categoria']} ({r['orgao_sancionador']}; {r['abrangencia']})"
            )
            if isinstance(r["data_inicio"], date):
                eventos.append(
                    Evento(r["data_inicio"], f"Início de sanção {texto}", "transparencia", url)
                )
            if isinstance(r["data_fim"], date):
                eventos.append(
                    Evento(r["data_fim"], f"Fim de sanção {texto}", "transparencia", url)
                )
        return eventos  # fmt: skip

    # ------------------------------------------------------------------ subgrafo

    def _todas_arestas(self) -> pl.DataFrame:
        partes = []
        for tipo, df in self.grafo.arestas.items():
            prob = (
                pl.col("probabilidade").cast(pl.Float64)
                if "probabilidade" in df.columns
                else pl.lit(None, pl.Float64)
            )
            partes.append(
                df.select(
                    o=pl.col("rotulo_origem") + ":" + pl.col("origem"),
                    d=pl.col("rotulo_destino") + ":" + pl.col("destino"),
                    tipo=pl.lit(tipo),
                    probabilidade=prob,
                )
            )
        if not partes:
            return pl.DataFrame(
                schema={"o": pl.String, "d": pl.String, "tipo": pl.String,
                        "probabilidade": pl.Float64}
            )  # fmt: skip
        return pl.concat(partes).unique()

    def _unidades(self, nos: set[str], conhecidos: Mapping[str, int]) -> set[str]:
        """Empresas e estabelecimentos ligados por ``UNIDADE_DE`` a ``nos``."""
        u = self._arestas.filter(pl.col("tipo") == "UNIDADE_DE")
        toca = u.filter(pl.col("o").is_in(list(nos)) | pl.col("d").is_in(list(nos)))
        return (set(toca["o"]) | set(toca["d"])) - set(conhecidos) - nos

    def vizinhanca(
        self,
        entidade: str,
        saltos: int = 2,
        max_nos: int = 60,
        grau_max: int = 50,
        expandir_eventos: bool = False,
    ) -> Subgrafo:
        """Subgrafo de até ``saltos`` em volta da entidade.

        - Empresa e seus estabelecimentos contam como um nó só para os saltos, para que
          empresas no mesmo endereço ou com o mesmo contato apareçam em 2 saltos.
        - Licitações, contratos e órgãos entram, mas só são expandidos com
          ``expandir_eventos`` (senão o 2º salto vira a lista de todas as vencedoras).
        - Nós com mais de ``grau_max`` ligações (contador, centro empresarial) entram,
          mas não são expandidos.
        - Acima de ``max_nos`` ficam os mais próximos, com prioridade para os com alerta.
        """
        a = self._arestas
        if self._grau is None:
            self._grau = pl.concat([a.select(no="o"), a.select(no="d")]).group_by("no").len("grau")
        hubs = set(self._grau.filter(pl.col("grau") > grau_max)["no"])
        eventos = ("Licitacao:", "Contrato:", "OrgaoPublico:")
        salto = {entidade: 0}
        fronteira = {entidade}
        fronteira |= self._unidades(fronteira, salto)
        salto.update(dict.fromkeys(fronteira, 0))
        for passo in range(1, saltos + 1):
            expandir = [
                n for n in fronteira
                if salto[n] == 0
                or (n not in hubs and (expandir_eventos or not n.startswith(eventos)))
            ]  # fmt: skip
            if not expandir:
                break
            toca = a.filter(pl.col("o").is_in(expandir) | pl.col("d").is_in(expandir))
            novos = (set(toca["o"]) | set(toca["d"])) - set(salto)
            novos |= self._unidades(novos, salto)
            for n in novos:
                salto[n] = passo
            fronteira = novos
        com_alerta = set(self.scores["entidade"])
        nos = pl.DataFrame(
            {"no": list(salto), "salto": list(salto.values())},
            schema={"no": pl.String, "salto": pl.Int64},
        ).with_columns(
            rotulo=pl.col("no").str.split(":").list.first(),
            com_alerta=pl.col("no").is_in(list(com_alerta)),
        )
        cortado = nos.height > max_nos
        nos = nos.sort("salto", pl.col("com_alerta").not_(), "no").head(max_nos)
        ids = nos["no"].to_list()
        arestas = a.filter(pl.col("o").is_in(ids) & pl.col("d").is_in(ids)).sort("tipo", "o", "d")
        nos = nos.with_columns(nome=pl.col("no").map_elements(self._rotulo_no, pl.String))
        return Subgrafo(entidade, nos, arestas, cortado)

    def _rotulo_no(self, no: str) -> str:
        rotulo, _, ident = no.partition(":")
        if no in self.nomes:
            return self.nomes[no]
        df = self.grafo.nos.get(rotulo)
        if df is not None and rotulo not in ("Telefone", "Email"):
            coluna = next((c for c in ("nome", "texto", "objeto") if c in df.columns), None)
            if coluna:
                linha = df.filter(pl.col("id") == ident)
                if linha.height and linha[coluna][0]:
                    return str(linha[coluna][0])
        if rotulo in ("Telefone", "Email"):
            return f"{rotulo.lower()} (hash {ident[:6]})"
        return ident


def _reais(valor: object) -> str:
    if valor is None:
        return "R$ —"
    texto = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # type: ignore[arg-type]
    return f"R$ {texto}"


def _origens(v: object) -> str:
    """Origens do registro (texto separado por ``;`` ou lista)."""
    if isinstance(v, str):
        partes = v.split(";")
    elif isinstance(v, list | tuple):
        partes = [str(x) for x in v]
    else:
        return "—"
    return ", ".join(p.replace("_", " ") for p in partes if p) or "—"


def _curto(texto: object, n: int = 90) -> str:
    s = " ".join(str(texto or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"

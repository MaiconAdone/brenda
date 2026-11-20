"""Dossiê em PDF de uma entidade com alerta.

O conteúdo é montado primeiro como blocos simples (:func:`montar_conteudo`), que os
testes conferem, e só depois desenhado com o ReportLab (:func:`renderizar`).

Só sai PDF com revisão humana aprovada e vigente (:func:`lupa.revisao.exigir_aprovacao`).
O aviso legal aparece no topo e no rodapé de todas as páginas.
"""

from __future__ import annotations

import html
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from lupa import AVISO_LEGAL, __version__
from lupa.dossier.base import URLS, Ficha, Subgrafo, _reais
from lupa.revisao import Revisao, exigir_aprovacao

MAX_LINHAS_TABELA = 80
MAX_CONEXOES = 80
TIPOS_ARESTA = {
    "SOCIO_DE": "sócio(a) de",
    "VENCEU": "venceu",
    "CELEBRADO_POR": "contrato com",
    "PROMOVIDA_POR": "promovida por",
    "PARTE_DE": "parte de",
    "UNIDADE_DE": "unidade de",
    "LOCALIZADA_EM": "localizada em",
    "TEM_TELEFONE": "tem telefone",
    "TEM_EMAIL": "tem e-mail",
    "SANCIONADA": "sancionada em",
    "DOOU_PARA": "doou para",
    "FORNECEU_CAMPANHA": "prestou serviço à campanha de",
    "CANDIDATO_EM": "candidatura",
    "MESMA_ENTIDADE_PROVAVEL": "provável mesma pessoa que",
    "ORIGINADO_DE": "originado de",
}


@dataclass(frozen=True)
class Titulo:
    """Título de seção."""

    texto: str
    nivel: int = 1


@dataclass(frozen=True)
class Texto:
    """Parágrafo, com links opcionais ao final (nome, url)."""

    texto: str
    links: tuple[tuple[str, str], ...] = ()
    destaque: bool = False
    rotulo_links: str = "Fontes: "


@dataclass(frozen=True)
class Tabela:
    """Tabela simples; ``larguras`` em frações da largura útil."""

    cabecalho: tuple[str, ...]
    linhas: tuple[tuple[str, ...], ...]
    larguras: tuple[float, ...] | None = None
    nota: str = ""


Bloco = Titulo | Texto | Tabela


def _data(d: object) -> str:
    return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d or "—")


def _tabela(
    cabecalho: tuple[str, ...], linhas: Sequence[Sequence[object]], larguras: tuple[float, ...]
) -> Tabela:
    nota = ""
    if len(linhas) > MAX_LINHAS_TABELA:
        nota = f"Mostrando {MAX_LINHAS_TABELA} de {len(linhas)} linhas."
        linhas = linhas[:MAX_LINHAS_TABELA]
    texto = tuple(tuple("—" if c is None else str(c) for c in linha) for linha in linhas)
    return Tabela(cabecalho, texto, larguras, nota)


def montar_conteudo(
    ficha: Ficha,
    revisao: Revisao,
    subgrafo: Subgrafo | None = None,
    gerado_em: datetime | None = None,
) -> list[Bloco]:
    """Blocos do dossiê, na ordem em que aparecem."""
    gerado = (gerado_em or datetime.now(UTC)).strftime("%d/%m/%Y %H:%M UTC")
    b: list[Bloco] = [
        Titulo(f"Dossiê de alerta: {ficha.nome}"),
        Texto(AVISO_LEGAL, destaque=True),
        Texto(
            f"Gerado em {gerado} pela Lupa Cidadã {__version__}, plataforma de código aberto que "
            "usa só dados públicos. Documento de uso restrito, para encaminhamento a órgãos "
            "competentes (MP, TCE/TCU, CGU) ou a jornalistas. Documentos de pessoas físicas "
            "aparecem mascarados."
        ),
        Titulo("Identificação"),
        _tabela(("Campo", "Valor"), list(ficha.cadastro), (0.3, 0.7)),
        Titulo("Score de risco"),
        Texto(
            f"Score {ficha.score:g}"
            + (f", posição {ficha.posicao} no ranking de {ficha.ranking}" if ficha.posicao else "")
            + ". O score soma peso x confiança de cada sinal, com teto por categoria para "
            "evitar dupla contagem (pesos em config/regras.yaml)."
        ),
        _tabela(
            ("Categoria", "Pontos"),
            [(c, f"{v:g}") for c, v in sorted(ficha.por_categoria.items())],
            (0.6, 0.4),
        ),
        Titulo("Revisão humana"),
        _tabela(
            ("Campo", "Valor"),
            [
                ("Situação", revisao.rotulo),
                ("Revisor(a)", revisao.revisor),
                ("Data", revisao.em),
                ("Sinais revisados", ", ".join(revisao.sinais)),
                ("Justificativa", revisao.justificativa),
            ],
            (0.3, 0.7),
        ),
        Titulo("Sinais disparados e evidências"),
    ]
    for a in ficha.alertas:
        b.append(Titulo(f"{a.sinal_id} – {a.nome} (confiança {a.confianca:.2f})", nivel=2))
        for ev in a.evidencias:
            links = tuple(dict.fromkeys((f["nome"], f["url"]) for f in ev["fontes"]))
            b.append(Texto(ev["descricao"], links))
        if a.limitacoes:
            b.append(Texto(f"Limitação: {a.limitacoes}"))

    b.append(Titulo("Cronologia"))
    b.append(
        _tabela(
            ("Data", "Evento", "Fonte"),
            [(_data(e.data), e.descricao, f"{e.fonte}|{e.url}") for e in ficha.cronologia],
            (0.14, 0.68, 0.18),
        )
    )
    if ficha.vitorias.height:
        b.append(Titulo("Licitações vencidas"))
        b.append(
            _tabela(
                ("Data", "Órgão", "Valor", "Objeto"),
                [
                    (_data(r["data"]), r["orgao_nome"] or "—", _reais(r["valor"]),
                     f"{r['objeto'] or '—'}|{r['url_fonte']}")
                    for r in ficha.vitorias.iter_rows(named=True)
                ],
                (0.12, 0.28, 0.15, 0.45),
            )
        )  # fmt: skip
    if ficha.contratos.height:
        b.append(Titulo("Contratos"))
        b.append(
            _tabela(
                ("Assinatura", "Órgão", "Valor global", "Objeto"),
                [
                    (_data(r["data"]), r["orgao_nome"] or "—", _reais(r["valor_global"]),
                     f"{r['objeto'] or '—'}|{r['url_fonte']}")
                    for r in ficha.contratos.iter_rows(named=True)
                ],
                (0.12, 0.28, 0.15, 0.45),
            )
        )  # fmt: skip
    if ficha.socios.height:
        empresa = ficha.tipo == "Empresa"
        b.append(Titulo("Quadro societário" if empresa else "Participações societárias"))
        b.append(
            _tabela(
                ("Sócio" if empresa else "Empresa", "Documento", "Qualificação", "Faixa etária",
                 "Entrada"),
                [
                    ((r["nome_socio"] if empresa else (r["nome_empresa"] or r["empresa"])) or "—",
                     (r["documento"] if empresa else r["empresa"].partition(":")[2]) or "—",
                     r["qualificacao"] or "—", r["faixa_etaria"] or "—", _data(r["data_entrada"]))
                    for r in ficha.socios.iter_rows(named=True)
                ],
                (0.32, 0.18, 0.2, 0.15, 0.15),
            )
        )  # fmt: skip
    if subgrafo is not None and subgrafo.arestas.height:
        nomes = dict(zip(subgrafo.nos["no"], subgrafo.nos["nome"], strict=True))
        salto = dict(zip(subgrafo.nos["no"], subgrafo.nos["salto"], strict=True))
        arestas = subgrafo.arestas.with_columns(
            perto=pl.min_horizontal(
                pl.col("o").replace_strict(salto, default=9),
                pl.col("d").replace_strict(salto, default=9),
            )
        ).sort("perto", "tipo", "o", "d")
        b.append(Titulo("Conexões (até 2 saltos)"))
        linhas = [
            (nomes.get(r["o"], r["o"]), TIPOS_ARESTA.get(r["tipo"], r["tipo"]),
             nomes.get(r["d"], r["d"]),
             f"{r['probabilidade']:.2f}" if r["probabilidade"] is not None else "—")
            for r in arestas.iter_rows(named=True)
        ][:MAX_CONEXOES]  # fmt: skip
        b.append(_tabela(("De", "Ligação", "Para", "Confiança"), linhas, (0.36, 0.2, 0.32, 0.12)))
        if subgrafo.cortado:
            b.append(Texto("Subgrafo cortado nos nós mais próximos para caber no documento."))
        if subgrafo.arestas.height > MAX_CONEXOES:
            b.append(Texto(f"Mostrando {MAX_CONEXOES} de {subgrafo.arestas.height} ligações, "
                           "as mais próximas da entidade primeiro."))  # fmt: skip

    b.append(Titulo("Fontes e reprodutibilidade"))
    for nome, url in sorted(ficha.fontes.items()):
        geral = URLS.get(nome, url)
        b.append(Texto(f"{nome}:", ((geral, geral),), rotulo_links=""))
    b.append(
        Texto(
            "Os resultados podem ser reproduzidos com o código público do projeto e as mesmas "
            "fontes (ver docs/metodologia.md). Ligações entre pessoas usam CPF parcial e nome, "
            "nunca só o nome, e trazem a confiança da ligação. Os sinais são indícios; a "
            "apuração cabe aos órgãos competentes."
        )
    )
    return _numerar(b)


def _numerar(blocos: list[Bloco]) -> list[Bloco]:
    """Numera as seções (títulos de nível 1, menos o do documento)."""
    saida: list[Bloco] = []
    n = 0
    for i, bloco in enumerate(blocos):
        if isinstance(bloco, Titulo) and bloco.nivel == 1 and i > 0:
            n += 1
            bloco = Titulo(f"{n}. {bloco.texto}")
        saida.append(bloco)
    return saida


# ---------------------------------------------------------------- renderização

_TROCAS = {"≥": ">=", "≤": "<=", "Σ": "soma", "→": "->", "×": "x", "✓": "ok"}


def _cp1252(texto: str) -> str:
    """Restringe o texto ao que a fonte Helvetica padrão desenha."""
    for a, b in _TROCAS.items():
        texto = texto.replace(a, b)
    return texto.encode("cp1252", errors="replace").decode("cp1252")


def _seguro(texto: str) -> str:
    """Texto escapado para o Paragraph."""
    return html.escape(_cp1252(texto), quote=False)


def _link(nome: str, url: str) -> str:
    return f'<a href="{html.escape(url)}" color="#1a4f8b">{_seguro(nome)}</a>'


def _estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    corpo = ParagraphStyle("corpo", parent=base["BodyText"], fontSize=9, leading=12,
                           alignment=TA_LEFT)  # fmt: skip
    return {
        "corpo": corpo,
        "celula": ParagraphStyle("celula", parent=corpo, fontSize=7.5, leading=9.5),
        "cab": ParagraphStyle("cab", parent=corpo, fontSize=7.5, leading=9.5,
                              fontName="Helvetica-Bold"),
        "aviso": ParagraphStyle("aviso", parent=corpo, fontName="Helvetica-Bold",
                                backColor=colors.HexColor("#fff4ce"), borderPadding=6,
                                spaceBefore=4, spaceAfter=8),
        "h1": ParagraphStyle("h1", parent=base["Heading2"], fontSize=13, spaceBefore=10),
        "h2": ParagraphStyle("h2", parent=base["Heading3"], fontSize=10, spaceBefore=6),
    }  # fmt: skip


def _celula(texto: str, estilo: ParagraphStyle) -> Paragraph:
    """Célula; ``texto|url`` vira link."""
    if "|http" in texto:
        rotulo, _, url = texto.rpartition("|")
        return Paragraph(_link(rotulo, url), estilo)
    return Paragraph(_seguro(texto), estilo)


def _flowables(blocos: list[Bloco], largura: float) -> list[Flowable]:
    e = _estilos()
    saida: list[Flowable] = []
    for bloco in blocos:
        if isinstance(bloco, Titulo):
            saida.append(Paragraph(_seguro(bloco.texto), e["h1" if bloco.nivel == 1 else "h2"]))
        elif isinstance(bloco, Texto):
            texto = _seguro(bloco.texto)
            if bloco.links:
                texto += " " + bloco.rotulo_links + "; ".join(_link(n, u) for n, u in bloco.links)
            saida.append(Paragraph(texto, e["aviso" if bloco.destaque else "corpo"]))
        else:
            if not bloco.linhas:
                saida.append(Paragraph("Nenhum registro.", e["corpo"]))
                continue
            dados = [[Paragraph(_seguro(c), e["cab"]) for c in bloco.cabecalho]]
            dados += [[_celula(c, e["celula"]) for c in linha] for linha in bloco.linhas]
            larguras = [f * largura for f in bloco.larguras] if bloco.larguras else None
            tabela = Table(dados, colWidths=larguras, repeatRows=1)
            tabela.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf3")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c2cc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ])
            )  # fmt: skip
            saida.append(tabela)
            if bloco.nota:
                saida.append(Paragraph(_seguro(bloco.nota), e["celula"]))
        saida.append(Spacer(1, 3))
    return [KeepTogether(saida[:3]), *saida[3:]]


def renderizar(blocos: list[Bloco], rodape: str) -> bytes:
    """Desenha os blocos em PDF A4, com aviso legal e paginação no rodapé."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
        bottomMargin=20 * mm, title="Dossiê de alerta — Lupa Cidadã", author="Lupa Cidadã",
        subject=AVISO_LEGAL,
    )  # fmt: skip

    def pagina(canvas: Canvas, documento: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(15 * mm, 12 * mm, _cp1252(AVISO_LEGAL))
        canvas.drawString(15 * mm, 8 * mm, _cp1252(rodape))
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Página {documento.page}")
        canvas.restoreState()

    doc.build(_flowables(blocos, doc.width), onFirstPage=pagina, onLaterPages=pagina)
    return buffer.getvalue()


def gerar_pdf(
    ficha: Ficha,
    revisao: Revisao | None,
    subgrafo: Subgrafo | None = None,
    gerado_em: datetime | None = None,
) -> bytes:
    """PDF do dossiê. Levanta :class:`lupa.revisao.RevisaoPendenteError` sem aprovação."""
    aprovada = exigir_aprovacao(revisao, ficha.sinais)
    blocos = montar_conteudo(ficha, aprovada, subgrafo, gerado_em)
    rodape = f"Revisado por {aprovada.revisor} em {aprovada.em} · {ficha.entidade}"
    return renderizar(blocos, rodape)

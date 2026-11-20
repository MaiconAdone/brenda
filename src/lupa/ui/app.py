"""Interface Streamlit da Lupa Cidadã: ranking, ficha da entidade, revisão e dossiê.

Rodar com ``uv run streamlit run src/lupa/ui/app.py``. Uso interno: não há autenticação,
então o serviço só escuta em ``127.0.0.1`` (ver ``docker-compose.yml``). Quem consulta se
identifica na barra lateral; o nome vai para o log de acesso e para as revisões.
"""

from __future__ import annotations

import re
from datetime import date

import polars as pl
import streamlit as st

from lupa import AVISO_LEGAL, __version__, acesso, revisao
from lupa.config import PilotoNaoDefinidoError, carregar_env
from lupa.dossier import Base, gerar_dossie
from lupa.dossier.base import Ficha
from lupa.ingestion.contexto import contexto_padrao
from lupa.revisao import SITUACOES, RevisaoInvalidaError, RevisaoPendenteError
from lupa.ui import md
from lupa.ui.grafo import grafico

RANKINGS = {"fornecedores": "Fornecedores e pessoas", "campanhas": "Candidaturas"}


def aviso_fixo() -> None:
    """Aviso legal exibido em todas as páginas."""
    st.warning(AVISO_LEGAL, icon="⚖️")


@st.cache_resource(show_spinner="Carregando dados da gold…")
def carregar_base() -> Base | None:
    """Base de consulta (uma por processo)."""
    carregar_env()
    ctx = contexto_padrao()
    if not (ctx.raiz / "gold" / "alertas" / "scores.parquet").exists():
        return None
    piloto = ctx.config.piloto
    return Base(ctx.raiz, piloto.uf, piloto.nome_municipio)


def _usuario() -> str:
    with st.sidebar:
        st.subheader("Identificação")
        nome = st.text_input(
            "Seu nome",
            key="usuario",
            help="Obrigatório para abrir fichas. Vai para o log de acesso e para as revisões.",
        )
    return nome.strip()


def _abrir(entidade: str | None) -> None:
    if entidade:
        st.query_params["entidade"] = entidade
    else:
        st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------------- ranking


def _situacao(indice: pl.DataFrame, base: Base) -> pl.DataFrame:
    revs = revisao.atuais(base.raiz)
    rotulos = []
    for ent, sinais_ in zip(indice["entidade"], indice["sinais"], strict=True):
        r = revs.get(ent)
        if r is None:
            rotulos.append(SITUACOES["nao_revisado"])
        elif not revisao.vigente(r, sinais_):
            rotulos.append(f"{r.rotulo} (desatualizada)")
        else:
            rotulos.append(r.rotulo)
    return indice.with_columns(situacao=pl.Series(rotulos, dtype=pl.String))


def pagina_ranking(base: Base) -> None:
    """Ranking com filtros por órgão, período, categoria, valor e situação da revisão."""
    st.header("Ranking de alertas")
    escolha = st.radio(
        "Ranking", list(RANKINGS), format_func=RANKINGS.__getitem__, horizontal=True,
        help="Candidaturas ficam à parte: os sinais de campanha medem outra coisa.",
    )  # fmt: skip
    indice = _situacao(base.indice(), base).filter(pl.col("ranking") == escolha)

    c1, c2, c3 = st.columns(3)
    busca = c1.text_input("Nome contém")
    orgaos = sorted({o for lista in indice["orgaos"] for o in lista})
    sel_orgaos = c2.multiselect("Órgão", orgaos, disabled=not orgaos)
    categorias = sorted({c for lista in indice["categorias"] for c in lista})
    sel_categorias = c3.multiselect("Categoria de sinal", categorias)

    c4, c5, c6 = st.columns(3)
    datas = indice["data_min"].drop_nulls()
    periodo: tuple[date, ...] = ()
    if datas.len():
        inicio, fim = indice["data_min"].min(), indice["data_max"].max()
        periodo = tuple(c4.date_input("Período", (inicio, fim), format="DD/MM/YYYY"))  # type: ignore[arg-type]
    valor_min = c5.number_input("Valor vencido mínimo (R$)", min_value=0.0, step=50_000.0)
    sel_situacoes = c6.multiselect("Situação da revisão", sorted(set(indice["situacao"])))

    df = indice
    if busca:
        df = df.filter(pl.col("nome").str.contains(f"(?i){re.escape(busca)}"))
    if sel_orgaos:
        df = df.filter(pl.col("orgaos").list.eval(pl.element().is_in(sel_orgaos)).list.any())
    if sel_categorias:
        df = df.filter(
            pl.col("categorias").list.eval(pl.element().is_in(sel_categorias)).list.any()
        )
    if len(periodo) == 2 and datas.len():
        df = df.filter((pl.col("data_max") >= periodo[0]) & (pl.col("data_min") <= periodo[1]))
    if valor_min:
        df = df.filter(pl.col("valor_total") >= valor_min)
    if sel_situacoes:
        df = df.filter(pl.col("situacao").is_in(sel_situacoes))

    st.caption(f"{df.height} de {indice.height} entidades. Clique numa linha para abrir a ficha.")
    tabela = df.select(
        "posicao", "nome", "tipo", "score", "sinais", "valor_total",
        orgaos=pl.col("orgaos").list.join("; "), situacao="situacao", entidade="entidade",
    )  # fmt: skip
    maximo = max([1.0, *indice["score"].to_list()])
    evento = st.dataframe(
        tabela,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=f"tabela_{escolha}",
        column_config={
            "posicao": st.column_config.NumberColumn("#", width="small"),
            "nome": st.column_config.TextColumn("Entidade", width="large"),
            "score": st.column_config.ProgressColumn(
                "Score", min_value=0.0, max_value=maximo, format="%.1f"
            ),
            "sinais": st.column_config.ListColumn("Sinais"),
            "valor_total": st.column_config.NumberColumn("Valor vencido", format="R$ %.2f"),
            "orgaos": st.column_config.TextColumn("Órgãos"),
            "situacao": st.column_config.TextColumn("Revisão"),
            "entidade": None,
        },
    )
    linhas = evento.selection.rows if evento is not None else []
    if linhas:
        _abrir(tabela["entidade"][linhas[0]])


# ---------------------------------------------------------------- entidade


def _evidencias(ficha: Ficha) -> None:
    if not ficha.alertas:
        st.info("Nenhum sinal disparado para esta entidade.")
    for a in ficha.alertas:
        with st.expander(f"**{a.sinal_id} – {md(a.nome)}** · confiança {a.confianca:.2f}", True):
            for ev in a.evidencias:
                fontes = "; ".join(
                    f"[{md(f['nome'])}]({f['url']})"
                    + (f" (coleta {f['coletado_em']})" if f.get("coletado_em") else "")
                    for f in {(f["nome"], f["url"]): f for f in ev["fontes"]}.values()
                )
                st.markdown(f"- {md(ev['descricao'])}  \n  Fontes: {fontes}")
            if a.limitacoes:
                st.caption(f"Limitação: {a.limitacoes}")


def _cadastro(ficha: Ficha) -> None:
    st.dataframe(
        pl.DataFrame(ficha.cadastro, schema=["Campo", "Valor"], orient="row"),
        hide_index=True, width="stretch",
    )  # fmt: skip
    if ficha.socios.height:
        empresa = ficha.tipo == "Empresa"
        st.subheader("Quadro societário" if empresa else "Participações societárias")
        st.dataframe(
            ficha.socios.drop("socio" if empresa else "empresa"),
            hide_index=True,
            width="stretch",
            column_config={
                "data_entrada": st.column_config.DateColumn("Entrada", format="DD/MM/YYYY")
            },
        )


def _cronologia(ficha: Ficha) -> None:
    if not ficha.cronologia:
        st.info("Sem eventos datados.")
        return
    df = pl.DataFrame(
        [(e.data, e.descricao, e.fonte, e.url) for e in ficha.cronologia],
        schema=["data", "evento", "fonte", "url"], orient="row",
    )  # fmt: skip
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "evento": st.column_config.TextColumn("Evento", width="large"),
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )  # fmt: skip


def _movimento(ficha: Ficha) -> None:
    links = {"url_fonte": st.column_config.LinkColumn("Fonte", display_text="PNCP")}
    st.subheader("Licitações vencidas")
    if ficha.vitorias.height:
        st.dataframe(
            ficha.vitorias.select("data", "orgao_nome", "valor", "objeto", "url_fonte"),
            hide_index=True, width="stretch",
            column_config={**links, "valor": st.column_config.NumberColumn(format="R$ %.2f")},
        )  # fmt: skip
    else:
        st.caption("Nenhuma.")
    st.subheader("Contratos")
    if ficha.contratos.height:
        st.dataframe(
            ficha.contratos.select(
                "data", "orgao_nome", "valor_inicial", "valor_global", "objeto", "url_fonte"
            ),
            hide_index=True, width="stretch",
            column_config={
                **links,
                "valor_inicial": st.column_config.NumberColumn(format="R$ %.2f"),
                "valor_global": st.column_config.NumberColumn(format="R$ %.2f"),
            },
        )  # fmt: skip
    else:
        st.caption("Nenhum.")


def _conexoes(base: Base, entidade: str) -> None:
    c1, c2, c3 = st.columns(3)
    saltos = c1.radio("Saltos", [1, 2], index=1, horizontal=True)
    max_nos = c2.slider("Máximo de nós", 10, 150, 40, step=10)
    eventos = c3.checkbox(
        "Expandir licitações e contratos",
        help="Mostra as outras vencedoras das mesmas licitações (costuma poluir o grafo).",
    )
    sub = base.vizinhanca(entidade, saltos=saltos, max_nos=max_nos, expandir_eventos=eventos)
    if sub.cortado:
        st.caption("Subgrafo cortado: ficam os nós mais próximos e os que têm alerta.")
    st.altair_chart(grafico(sub), width="stretch")
    st.caption(
        "Círculo grande: entidade da ficha. Contorno vermelho: tem alerta. Linha tracejada: "
        "ligação provável (Splink), com a confiança. Role para dar zoom, arraste para mover "
        "e passe o mouse para ver detalhes."
    )
    with st.expander("Ligações em tabela"):
        st.dataframe(sub.arestas, hide_index=True, width="stretch")


def _revisao_e_dossie(base: Base, ficha: Ficha, usuario: str) -> None:
    historico = revisao.historico(base.raiz, ficha.entidade)
    atual = historico[-1] if historico else None
    if atual is None:
        st.info("Ainda não revisado.")
    else:
        vigente = revisao.vigente(atual, ficha.sinais)
        st.markdown(
            f"**Situação:** {atual.rotulo}"
            + ("" if vigente else " — **desatualizada** (os sinais mudaram depois da revisão)")
            + f"  \n**Por:** {md(atual.revisor)} em {atual.em}"
        )
    if historico:
        with st.expander(f"Histórico ({len(historico)})"):
            st.dataframe(
                pl.DataFrame(
                    [(r.em, r.rotulo, r.revisor, r.justificativa, ", ".join(r.sinais))
                     for r in reversed(historico)],
                    schema=["em", "situacao", "revisor", "justificativa", "sinais"], orient="row",
                ),
                hide_index=True, width="stretch",
            )  # fmt: skip

    with st.form("revisao", clear_on_submit=True):
        st.markdown("**Registrar revisão**")
        situacao = st.selectbox(
            "Decisão",
            ["em_revisao", "aprovado", "descartado"],
            format_func=SITUACOES.__getitem__,
        )
        justificativa = st.text_area(
            "Justificativa",
            help="O que foi conferido nas fontes e por que o alerta procede ou não. "
            f"Mínimo de {revisao.MIN_JUSTIFICATIVA} caracteres para aprovar ou descartar.",
        )
        if st.form_submit_button("Registrar"):
            try:
                revisao.registrar(
                    base.raiz,
                    revisao.Revisao(ficha.entidade, situacao, usuario, justificativa,
                                    ficha.sinais, ficha.score),
                )  # fmt: skip
                acesso.registrar("revisar", usuario, entidade=ficha.entidade, situacao=situacao)
                st.rerun()
            except RevisaoInvalidaError as erro:
                st.error(str(erro))

    st.subheader("Dossiê")
    try:
        revisao.exigir_aprovacao(atual, ficha.sinais)
    except RevisaoPendenteError as erro:
        st.warning(f"{erro} O dossiê só é gerado depois de uma revisão humana aprovada.")
        return
    if st.button("Gerar dossiê", type="primary"):
        st.session_state[f"pdf:{ficha.entidade}"] = gerar_dossie(base, ficha.entidade, usuario)
    if pdf := st.session_state.get(f"pdf:{ficha.entidade}"):
        st.download_button(
            "Baixar PDF", pdf, file_name=f"dossie_{ficha.entidade.replace(':', '_')}.pdf",
            mime="application/pdf",
        )  # fmt: skip


def pagina_entidade(base: Base, entidade: str, usuario: str) -> None:
    """Ficha da entidade: sinais, cadastro, cronologia, contratos, grafo, revisão e dossiê."""
    if st.button("← Voltar ao ranking"):
        _abrir(None)
    if not usuario:
        st.info("Informe seu nome na barra lateral para abrir a ficha (log de acesso, LGPD).")
        return
    chave = f"log:{entidade}"
    if not st.session_state.get(chave):
        acesso.registrar("ver_entidade", usuario, entidade=entidade)
        st.session_state[chave] = True

    ficha = base.ficha(entidade)
    st.header(ficha.nome)
    posicao = f" · posição {ficha.posicao} em {RANKINGS[ficha.ranking]}" if ficha.posicao else ""
    st.caption(f"{entidade}{posicao}")
    colunas = st.columns(1 + max(1, len(ficha.por_categoria)))
    colunas[0].metric("Score", f"{ficha.score:g}")
    for coluna, (cat, pontos) in zip(
        colunas[1:], sorted(ficha.por_categoria.items()), strict=False
    ):
        coluna.metric(cat.capitalize(), f"{pontos:g}")

    abas = st.tabs(
        ["Sinais e evidências", "Cadastro", "Cronologia", "Licitações e contratos", "Conexões",
         "Revisão e dossiê"]
    )  # fmt: skip
    with abas[0]:
        _evidencias(ficha)
    with abas[1]:
        _cadastro(ficha)
    with abas[2]:
        _cronologia(ficha)
    with abas[3]:
        _movimento(ficha)
    with abas[4]:
        _conexoes(base, entidade)
    with abas[5]:
        _revisao_e_dossie(base, ficha, usuario)


# ---------------------------------------------------------------- principal


def main() -> None:
    """Escolhe a página pelo parâmetro ``entidade`` da URL."""
    st.set_page_config(page_title="Lupa Cidadã", page_icon="🔎", layout="wide")
    aviso_fixo()
    usuario = _usuario()
    with st.sidebar:
        st.divider()
        st.caption(f"Lupa Cidadã {__version__} · código aberto (AGPL-3.0) · só dados públicos")
        try:
            municipio, uf = contexto_padrao().config.piloto.exigir_definido()
            st.caption(f"Piloto: município IBGE {municipio}/{uf}")
        except PilotoNaoDefinidoError as erro:
            st.caption(str(erro))

    base = carregar_base()
    if base is None:
        st.title("Lupa Cidadã")
        st.info(
            "Ainda não há alertas. Rode a ingestão, o grafo e os sinais "
            "(`python -m lupa.signals`) e recarregue a página."
        )
        return
    entidade = st.query_params.get("entidade")
    if entidade:
        pagina_entidade(base, entidade, usuario)
        return
    st.title("Lupa Cidadã")
    with st.sidebar:
        pagina = st.radio("Página", ["Ranking", "Avaliação"], key="pagina")
    if pagina == "Avaliação":
        pagina_avaliacao(base)
    else:
        pagina_ranking(base)


def pagina_avaliacao(base: Base) -> None:
    """Precisão@k e precisão por sinal, a partir das revisões humanas."""
    from lupa.ml.avaliacao import KS, precisao_k, precisao_por_sinal

    st.header("Avaliação")
    revs = revisao.atuais(base.raiz)
    finais = sum(r.situacao in ("aprovado", "descartado") for r in revs.values())
    st.caption(
        f"{finais} entidades com decisão final (aprovado ou descartado). Precisão@k: das k "
        "primeiras do ranking já revisadas, quantas foram aprovadas. Cobertura: quantas das k "
        "foram revisadas; com cobertura baixa a precisão ainda diz pouco."
    )
    if not finais:
        st.info("Ainda não há revisões finais. Revise alertas na ficha de cada entidade.")
        return
    pct = st.column_config.NumberColumn(format="percent")
    st.subheader(f"Precisão@k (k = {', '.join(map(str, KS))})")
    st.dataframe(
        precisao_k(base.scores, revs).with_columns(
            ranking=pl.col("ranking").replace_strict(RANKINGS, default=pl.col("ranking"))
        ),
        hide_index=True, width="stretch",
        column_config={"precisao": pct, "cobertura": pct},
    )  # fmt: skip
    st.subheader("Precisão por sinal")
    nomes = {sid: c.nome for sid, c in base.catalogo.items()}
    st.dataframe(
        precisao_por_sinal(base.alertas, revs).with_columns(
            nome=pl.col("sinal_id").replace_strict(nomes, default=None)
        ),
        hide_index=True, width="stretch", column_config={"precisao": pct},
    )  # fmt: skip


main()

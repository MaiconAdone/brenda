"""Interface Streamlit. Fase 0: página inicial com o aviso fixo."""

from __future__ import annotations

import streamlit as st

from lupa import AVISO_LEGAL, __version__
from lupa.config import PilotoNaoDefinidoError, carregar_piloto


def aviso_fixo() -> None:
    """Aviso legal exibido em todas as páginas."""
    st.warning(AVISO_LEGAL, icon="⚖️")


def main() -> None:
    """Página inicial."""
    st.set_page_config(page_title="Lupa Cidadã", page_icon="🔎", layout="wide")
    aviso_fixo()
    st.title("Lupa Cidadã")
    st.caption(f"Versão {__version__} · código aberto (AGPL-3.0) · somente dados públicos")

    cfg = carregar_piloto()
    try:
        municipio, uf = cfg.piloto.exigir_definido()
        inicio, fim = cfg.piloto.periodo()
        st.write(f"Piloto: município IBGE **{municipio}/{uf}**, de {inicio} a {fim}.")
    except PilotoNaoDefinidoError as erro:
        st.info(str(erro))

    st.markdown(
        "O ranking de alertas, a página de entidade e a geração de dossiê "
        "chegam na Fase 4. Nenhum alerta é divulgado sem revisão humana."
    )


main()

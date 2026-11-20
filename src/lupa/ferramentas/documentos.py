"""Ferramentas de documentos: diários oficiais (Querido Diário) e arquivos do PNCP.

Todo documento baixado vai para um cache (``<raiz>/casos/documentos``) com URL, data de
coleta e SHA-256; é isso que a evidência do tipo ``documento`` cita e o validador
confere. O texto sempre volta ao agente delimitado como **dado não confiável**
(``<documento_nao_confiavel>``): editais e diários podem conter texto escrito para
manipular o modelo, e o prompt dos agentes manda nunca seguir instruções de dentro desses
blocos.

Só domínios da lista ``DOMINIOS_PADRAO`` (ou da configuração) podem ser acessados.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lupa.ingestion.http import ClienteHttp

URL_QUERIDO_DIARIO = "https://api.queridodiario.org.br"
URL_PNCP_API = "https://pncp.gov.br/api/pncp/v1"
DOMINIOS_PADRAO: tuple[str, ...] = (
    "pncp.gov.br",
    "api.queridodiario.org.br",
    "queridodiario.ok.org.br",
    "data.queridodiario.ok.org.br",
    "querido-diario.nyc3.cdn.digitaloceanspaces.com",
    "portaldatransparencia.gov.br",
)
TAMANHO_MAX_BYTES = 30 * 1024 * 1024
TRECHO_PADRAO = 20_000
TAG = "documento_nao_confiavel"


class DocumentoRecusadoError(ValueError):
    """URL fora da lista permitida, documento grande demais ou formato não suportado."""


@dataclass(frozen=True)
class Documento:
    """Metadados de um documento no cache."""

    documento_id: str
    url: str
    coletado_em: str
    sha256: str
    tipo: str
    n_caracteres: int

    def referencia(self) -> dict[str, Any]:
        """Referência para uma evidência do tipo ``documento``."""
        return {"tipo": "documento", "documento_id": self.documento_id, "url": self.url,
                "coletado_em": self.coletado_em, "sha256": self.sha256}  # fmt: skip


def delimitar(texto: str, fonte: str) -> str:
    """Envolve texto externo como dado não confiável (sem permitir fechar o bloco antes)."""
    seguro = re.sub(rf"</?\s*{TAG}", "[marcador removido]", texto, flags=re.IGNORECASE)
    fonte_segura = fonte.replace('"', "'")
    return f'<{TAG} fonte="{fonte_segura}">\n{seguro}\n</{TAG}>'


class _Texto(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._ignorar += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3"):
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignorar:
            self.partes.append(data)


def extrair_texto(conteudo: bytes, tipo_http: str = "") -> tuple[str, str]:
    """Texto e tipo (``pdf``, ``html``, ``json``, ``texto``) de um conteúdo baixado."""
    if conteudo.startswith(b"%PDF"):
        from pypdf import PdfReader

        leitor = PdfReader(io.BytesIO(conteudo))
        paginas = [f"[página {i}]\n{p.extract_text() or ''}" for i, p in enumerate(leitor.pages, 1)]
        return "\n".join(paginas), "pdf"
    if conteudo.startswith(b"PK"):
        raise DocumentoRecusadoError("Arquivo compactado (ZIP): não suportado pela ferramenta.")
    texto = _decodificar(conteudo)
    inicio = texto.lstrip()[:200].lower()
    if "html" in tipo_http or inicio.startswith(("<!doctype html", "<html")):
        p = _Texto()
        p.feed(texto)
        return re.sub(r"\n\s*\n+", "\n\n", "".join(p.partes)).strip(), "html"
    if "json" in tipo_http or inicio.startswith(("{", "[")):
        return texto, "json"
    return texto, "texto"


def _decodificar(conteudo: bytes) -> str:
    for codificacao in ("utf-8", "latin-1"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("utf-8", errors="replace")


def normalizar_trecho(texto: str) -> str:
    """Forma usada para conferir se um trecho citado está no documento."""
    return re.sub(r"\s+", " ", texto).strip().lower()


class Documentos:
    """Busca, baixa e guarda documentos públicos para os agentes."""

    def __init__(
        self,
        raiz: Path,
        cliente: Callable[[], ClienteHttp] = ClienteHttp,
        dominios: tuple[str, ...] = DOMINIOS_PADRAO,
        tamanho_max: int = TAMANHO_MAX_BYTES,
        relogio: Callable[[], datetime] | None = None,
    ) -> None:
        self.pasta = raiz / "casos" / "documentos"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._cliente = cliente
        self.dominios = dominios
        self.tamanho_max = tamanho_max
        self._relogio = relogio or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ cache
    def _exigir_dominio(self, url: str) -> None:
        partes = urlsplit(url)
        host = (partes.hostname or "").lower()
        if partes.scheme != "https" or not any(
            host == d or host.endswith("." + d) for d in self.dominios
        ):
            raise DocumentoRecusadoError(
                f"Domínio não permitido: {host or url!r}. Permitidos: {', '.join(self.dominios)}."
            )

    def _caminhos(self, documento_id: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"[0-9a-f]{64}", documento_id):
            raise DocumentoRecusadoError("documento_id inválido.")
        return self.pasta / f"{documento_id}.json", self.pasta / f"{documento_id}.txt"

    def metadados(self, documento_id: str) -> Documento | None:
        """Metadados de um documento do cache (``None`` se não existir)."""
        meta, _ = self._caminhos(documento_id)
        if not meta.exists():
            return None
        return Documento(**json.loads(meta.read_text(encoding="utf-8")))

    def texto(self, documento_id: str) -> str:
        """Texto extraído de um documento do cache."""
        _, txt = self._caminhos(documento_id)
        return txt.read_text(encoding="utf-8")

    def _guardar(self, url: str, conteudo: bytes, texto: str, tipo: str) -> Documento:
        sha = hashlib.sha256(conteudo).hexdigest()
        documento_id = hashlib.sha256(f"{url}\n{sha}".encode()).hexdigest()
        meta, txt = self._caminhos(documento_id)
        existente = self.metadados(documento_id)
        if existente:
            return existente
        doc = Documento(documento_id, url, self._relogio().isoformat(timespec="seconds"), sha,
                        tipo, len(texto))  # fmt: skip
        txt.write_text(texto, encoding="utf-8")
        meta.write_text(json.dumps(doc.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
        return doc

    def _baixar(self, url: str, params: dict[str, Any] | None = None) -> tuple[bytes, str, str]:
        self._exigir_dominio(url)
        with self._cliente() as cliente:
            resposta = cliente.get(url, params=params)
        final = str(resposta.url)
        self._exigir_dominio(final)  # redirecionamento para fora da lista
        if resposta.status_code == 404:
            raise DocumentoRecusadoError(f"Documento não encontrado: {url}")
        conteudo = resposta.content
        if len(conteudo) > self.tamanho_max:
            raise DocumentoRecusadoError(
                f"Documento com {len(conteudo) / 1e6:.0f} MB "
                f"(limite {self.tamanho_max / 1e6:.0f} MB)."
            )
        return conteudo, resposta.headers.get("content-type", ""), final

    # ------------------------------------------------------------------ ferramentas
    def ler_documento(
        self,
        url: str | None = None,
        documento_id: str | None = None,
        inicio: int = 0,
        tamanho: int = TRECHO_PADRAO,
    ) -> dict[str, Any]:
        """Baixa (ou lê do cache) um documento e devolve um trecho do texto, delimitado."""
        if documento_id:
            doc = self.metadados(documento_id)
            if doc is None:
                raise DocumentoRecusadoError("documento_id não está no cache; informe a url.")
        elif url:
            conteudo, tipo_http, final = self._baixar(url)
            texto, tipo = extrair_texto(conteudo, tipo_http)
            doc = self._guardar(final, conteudo, texto, tipo)
        else:
            raise DocumentoRecusadoError("Informe url ou documento_id.")
        texto = self.texto(doc.documento_id)
        inicio = max(0, int(inicio))
        fim = min(len(texto), inicio + max(1, min(int(tamanho), TRECHO_PADRAO * 2)))
        aviso = "" if texto.strip() else (
            "Nenhum texto extraído (PDF escaneado?). Sem OCR, este documento não serve como "
            "evidência textual."
        )  # fmt: skip
        return {
            **doc.__dict__,
            "inicio": inicio,
            "fim": fim,
            "restante": len(texto) - fim,
            "aviso": aviso,
            "texto": delimitar(texto[inicio:fim], doc.url),
            "referencia": doc.referencia(),
        }

    def buscar_diario_oficial(
        self,
        termos: str,
        municipio_ibge: str | None = None,
        desde: date | str | None = None,
        ate: date | str | None = None,
        limite: int = 10,
    ) -> dict[str, Any]:
        """Busca nos diários oficiais do Querido Diário (trechos com os termos).

        A resposta inteira fica no cache como documento: os trechos devolvidos podem ser
        citados como evidência. Para o texto completo, use ``ler_documento(txt_url)``.
        """
        if not termos.strip():
            raise DocumentoRecusadoError("Informe os termos da busca.")
        params: dict[str, Any] = {
            "querystring": termos, "size": max(1, min(int(limite), 50)),
            "excerpt_size": 500, "number_of_excerpts": 3, "sort_by": "descending_date",
        }  # fmt: skip
        if municipio_ibge:
            if not re.fullmatch(r"\d{7}", municipio_ibge):
                raise DocumentoRecusadoError("municipio_ibge deve ter 7 dígitos.")
            params["territory_ids"] = municipio_ibge
        if desde:
            params["published_since"] = str(desde)
        if ate:
            params["published_until"] = str(ate)
        url = f"{URL_QUERIDO_DIARIO}/gazettes"
        conteudo, _, _ = self._baixar(url, params)
        dados = json.loads(conteudo)
        consulta = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        resultados = []
        linhas_texto = []
        for g in dados.get("gazettes", []):
            trechos = [str(t) for t in g.get("excerpts", [])]
            fonte = str(g.get("url"))
            resultados.append({"data": g.get("date"), "municipio": g.get("territory_name"),
                               "municipio_ibge": g.get("territory_id"),
                               "edicao": g.get("edition"), "extra": g.get("is_extra_edition"),
                               "url": g.get("url"), "txt_url": g.get("txt_url"),
                               "trechos": [delimitar(t, fonte) for t in trechos]})  # fmt: skip
            linhas_texto += [f"{g.get('date')} {g.get('url')}", *trechos, ""]
        doc = self._guardar(f"{url}?{consulta}", conteudo, "\n".join(linhas_texto), "json")
        return {"total": dados.get("total_gazettes", 0), "resultados": resultados,
                "documento_id": doc.documento_id, "referencia": doc.referencia(),
                "cobertura": "O Querido Diário não cobre todos os municípios; ausência de "
                             "resultado não prova ausência de publicação."}  # fmt: skip

    def listar_documentos_pncp(self, numero_controle_pncp: str) -> dict[str, Any]:
        """Arquivos (edital, termo de referência, contrato, atas...) de uma compra no PNCP."""
        m = re.fullmatch(r"(\d{14})-\d-(\d+)/(\d{4})", numero_controle_pncp.strip())
        if not m:
            raise DocumentoRecusadoError(
                "Use o número de controle da compra: <CNPJ>-1-<sequencial>/<ano>."
            )
        cnpj, seq, ano = m.group(1), int(m.group(2)), m.group(3)
        url = f"{URL_PNCP_API}/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos"
        conteudo, _, final = self._baixar(url)
        itens = json.loads(conteudo) if conteudo.strip() else []
        doc = self._guardar(final, conteudo, _decodificar(conteudo), "json")
        arquivos = [{"titulo": a.get("titulo"), "tipo": a.get("tipoDocumentoNome"),
                     "publicado_em": a.get("dataPublicacaoPncp"), "url": a.get("url"),
                     "ativo": a.get("statusAtivo")} for a in itens]  # fmt: skip
        return {"arquivos": arquivos, "documento_id": doc.documento_id,
                "referencia": doc.referencia()}  # fmt: skip

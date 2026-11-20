"""Leitura e escrita no arquivo do caso, com as regras que não dependem do LLM.

- **Regra de ouro:** uma hipótese só fica ``sustentada`` com pelo menos uma evidência
  válida a favor e com todas as objeções verificadas.
- **Permissões:** cada agente só faz as ações do seu papel (o Analista documental, por
  exemplo, não muda status de nada).
- **Transições de status** do caso seguem ``TRANSICOES``.
- **Linguagem:** textos escritos pelos agentes passam por ``linguagem.exigir_linguagem``.
- **Auditoria:** toda escrita gera um evento num log só de acréscimo, encadeado por hash
  (``verificar_log`` detecta adulteração), e toda alteração guarda a versão anterior
  em ``historico``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lupa.casos import schema
from lupa.casos.linguagem import exigir_linguagem

Agente = Literal[
    "coordenador", "monitor", "investigador", "analista_documental", "cetico", "redator",
    "humano", "sistema",
]  # fmt: skip
StatusCaso = Literal[
    "aberto", "em_investigacao", "em_revisao_cetica", "pronto_para_humano", "inconclusivo",
    "aprovado", "arquivado",
]  # fmt: skip
StatusHipotese = Literal["proposta", "sustentada", "refutada", "inconclusiva"]
TipoEvidencia = Literal["registro_banco", "documento", "consulta_grafo"]
Direcao = Literal["apoia", "contraria"]
Decisao = Literal["aprovar", "mais_investigacao", "arquivar"]

AGENTES: frozenset[str] = frozenset(Agente.__args__)  # type: ignore[attr-defined]
CATEGORIAS = frozenset("FLCKEM")
HASH_INICIAL = "0" * 64

TRANSICOES: dict[str, frozenset[str]] = {
    "aberto": frozenset({"em_investigacao", "arquivado"}),
    "em_investigacao": frozenset({"em_revisao_cetica", "inconclusivo", "arquivado"}),
    "em_revisao_cetica": frozenset(
        {"em_investigacao", "pronto_para_humano", "inconclusivo", "arquivado"}
    ),
    "pronto_para_humano": frozenset({"aprovado", "em_investigacao", "arquivado"}),
    "inconclusivo": frozenset({"em_investigacao", "pronto_para_humano", "arquivado"}),
    "aprovado": frozenset({"em_investigacao", "arquivado"}),
    "arquivado": frozenset({"em_investigacao"}),
}

# Quem pode fazer cada ação. "sistema" é o código do orquestrador (limites, validador).
PERMISSOES: dict[str, frozenset[str]] = {
    "abrir_caso": frozenset({"coordenador", "monitor", "sistema"}),
    "registrar_hipotese": frozenset({"investigador"}),
    "registrar_evidencia": frozenset({"investigador", "analista_documental", "cetico"}),
    "registrar_objecao": frozenset({"cetico"}),
    "verificar_objecao": frozenset({"investigador", "cetico"}),
    "atualizar_hipotese": frozenset({"investigador", "cetico", "coordenador", "sistema"}),
    "atualizar_status_caso": frozenset({"coordenador", "monitor", "sistema", "humano"}),
    "registrar_consumo": frozenset({"sistema"}),
    "registrar_parecer": frozenset({"humano"}),
    "invalidar_evidencia": frozenset({"sistema"}),
}
# Só o Cético declara uma hipótese sustentada (o Investigador não aprova a si mesmo).
QUEM_SUSTENTA = frozenset({"cetico"})
# Estados do caso que só o revisor humano define.
SO_HUMANO = frozenset({"aprovado"})


class RegraVioladaError(ValueError):
    """A operação violaria uma regra do arquivo do caso."""


class PermissaoNegadaError(PermissionError):
    """O agente não tem permissão para esta ação."""


class NaoEncontradoError(KeyError):
    """Registro inexistente."""


@dataclass(frozen=True)
class CasoCompleto:
    """Um caso com tudo que foi registrado nele."""

    caso: dict[str, Any]
    hipoteses: list[dict[str, Any]]
    evidencias: list[dict[str, Any]]
    objecoes: list[dict[str, Any]]
    eventos: list[dict[str, Any]]
    pareceres: list[dict[str, Any]]


def _id(prefixo: str) -> str:
    return f"{prefixo}_{uuid.uuid4().hex}"


def _json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)


def hash_evento(anterior: str, campos: dict[str, Any]) -> str:
    """Hash encadeado de um evento (SHA-256 de hash anterior + campos em JSON canônico)."""
    return hashlib.sha256((anterior + _json(campos)).encode("utf-8")).hexdigest()


_CAMPOS_EVENTO = (
    "caso_id",
    "agente",
    "acao",
    "ferramenta",
    "entrada",
    "saida",
    "tokens",
    "custo",
    "versao_prompt",
    "criado_em",
)


class ArquivoCasos:
    """Acesso ao arquivo do caso. Seguro para várias threads (escritas serializadas)."""

    def __init__(self, caminho: Path, relogio: Callable[[], datetime] | None = None) -> None:
        self.caminho = caminho
        self._con = schema.conectar(caminho)
        self._trava = threading.RLock()
        self._relogio = relogio or (lambda: datetime.now(UTC))

    def fechar(self) -> None:
        """Fecha a conexão."""
        self._con.close()

    def __enter__(self) -> ArquivoCasos:
        return self

    def __exit__(self, *_: object) -> None:
        self.fechar()

    # ------------------------------------------------------------------ infraestrutura
    def _agora(self) -> str:
        return self._relogio().isoformat(timespec="microseconds")

    @contextmanager
    def _transacao(self) -> Iterator[sqlite3.Connection]:
        with self._trava:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                yield self._con
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            self._con.execute("COMMIT")

    @staticmethod
    def _exigir(acao: str, agente: str) -> None:
        if agente not in AGENTES:
            raise PermissaoNegadaError(f"Agente desconhecido: {agente!r}.")
        if agente not in PERMISSOES[acao]:
            raise PermissaoNegadaError(f"O agente {agente!r} não pode {acao.replace('_', ' ')}.")

    def _linha(self, tabela: str, id_: str) -> dict[str, Any]:
        linha = self._con.execute(f"SELECT * FROM {tabela} WHERE id = ?", (id_,)).fetchone()
        if linha is None:
            raise NaoEncontradoError(f"{tabela} {id_} não existe.")
        return dict(linha)

    def _guardar_versao(self, tabela: str, linha: dict[str, Any], agente: str) -> None:
        self._con.execute(
            "INSERT INTO historico VALUES (?, ?, ?, ?, ?, ?)",
            (tabela, linha["id"], linha["versao"], _json(linha), agente, self._agora()),
        )

    def _evento(
        self,
        caso_id: str | None,
        agente: str,
        acao: str,
        *,
        ferramenta: str | None = None,
        entrada: Any = None,
        saida: Any = None,
        tokens: int = 0,
        custo: float = 0.0,
        versao_prompt: str | None = None,
    ) -> int:
        """Acrescenta um evento ao log (dentro de uma transação já aberta)."""
        ultimo = self._con.execute("SELECT hash FROM evento ORDER BY seq DESC LIMIT 1").fetchone()
        anterior = ultimo[0] if ultimo else HASH_INICIAL
        campos = {
            "caso_id": caso_id,
            "agente": agente,
            "acao": acao,
            "ferramenta": ferramenta,
            "entrada": None if entrada is None else _json(entrada),
            "saida": None if saida is None else (saida if isinstance(saida, str) else _json(saida)),
            "tokens": int(tokens),
            "custo": float(custo),
            "versao_prompt": versao_prompt,
            "criado_em": self._agora(),
        }
        cursor = self._con.execute(
            f"INSERT INTO evento ({', '.join(_CAMPOS_EVENTO)}, hash_anterior, hash) "
            f"VALUES ({', '.join('?' * (len(_CAMPOS_EVENTO) + 2))})",
            (*campos.values(), anterior, hash_evento(anterior, campos)),
        )
        return int(cursor.lastrowid or 0)

    # ------------------------------------------------------------------ escrita
    def abrir_caso(
        self,
        agente: str,
        entidades_foco: list[str],
        *,
        alerta_origem_id: str | None = None,
        prioridade: float = 0.0,
        orcamento_tokens: int,
    ) -> str:
        """Abre um caso (status ``aberto``)."""
        self._exigir("abrir_caso", agente)
        if not entidades_foco:
            raise RegraVioladaError("Um caso precisa de pelo menos uma entidade em foco.")
        if orcamento_tokens <= 0:
            raise RegraVioladaError("O orçamento de tokens deve ser positivo.")
        caso_id = _id("caso")
        agora = self._agora()
        with self._transacao() as con:
            con.execute(
                "INSERT INTO caso (id, alerta_origem_id, entidades_foco, status, prioridade, "
                "orcamento_tokens, criado_em, atualizado_em) "
                "VALUES (?, ?, ?, 'aberto', ?, ?, ?, ?)",
                (
                    caso_id,
                    alerta_origem_id,
                    _json(entidades_foco),
                    prioridade,
                    orcamento_tokens,
                    agora,
                    agora,
                ),
            )
            self._evento(
                caso_id,
                agente,
                "abrir_caso",
                entrada={"entidades_foco": entidades_foco, "prioridade": prioridade},
            )
        return caso_id

    def atualizar_status_caso(self, agente: str, caso_id: str, status: str, motivo: str) -> None:
        """Muda o status do caso, se a transição for permitida."""
        self._exigir("atualizar_status_caso", agente)
        if not motivo.strip():
            raise RegraVioladaError("Toda mudança de status precisa de motivo.")
        if agente != "humano":
            exigir_linguagem(motivo, "motivo")
        if status in SO_HUMANO and agente != "humano":
            raise PermissaoNegadaError(f"Só o revisor humano coloca um caso em {status!r}.")
        with self._transacao() as con:
            caso = self._linha("caso", caso_id)
            if status not in TRANSICOES[caso["status"]]:
                raise RegraVioladaError(f"Transição {caso['status']} -> {status} não é permitida.")
            if status == "pronto_para_humano":
                self._exigir_pronto(caso_id)
            self._guardar_versao("caso", caso, agente)
            con.execute(
                "UPDATE caso SET status = ?, motivo_status = ?, versao = versao + 1, "
                "atualizado_em = ?, ciclos_ceticos = ciclos_ceticos + ? WHERE id = ?",
                (
                    status,
                    motivo,
                    self._agora(),
                    int(caso["status"] == "em_revisao_cetica" and status == "em_investigacao"),
                    caso_id,
                ),
            )
            self._evento(
                caso_id,
                agente,
                "atualizar_status_caso",
                entrada={"de": caso["status"], "para": status, "motivo": motivo},
            )

    def _exigir_pronto(self, caso_id: str) -> None:
        sustentadas = self._con.execute(
            "SELECT count(*) FROM hipotese WHERE caso_id = ? AND status = 'sustentada'",
            (caso_id,),
        ).fetchone()[0]
        if not sustentadas:
            raise RegraVioladaError(
                "Um caso só vai para revisão humana com ao menos uma hipótese sustentada; "
                "sem isso, ele é inconclusivo ou arquivado."
            )

    def registrar_hipotese(
        self, agente: str, caso_id: str, enunciado: str, categoria_sinal: str, confianca: float
    ) -> str:
        """Registra uma hipótese (status ``proposta``)."""
        self._exigir("registrar_hipotese", agente)
        exigir_linguagem(enunciado, "enunciado")
        if categoria_sinal not in CATEGORIAS:
            raise RegraVioladaError(
                f"Categoria {categoria_sinal!r} inválida (use F, L, C, K, E ou M)."
            )
        _exigir_confianca(confianca)
        hip_id = _id("hip")
        agora = self._agora()
        with self._transacao() as con:
            caso = self._linha("caso", caso_id)
            _exigir_caso_ativo(caso)
            con.execute(
                "INSERT INTO hipotese (id, caso_id, enunciado, categoria_sinal, autor_agente, "
                "status, confianca, criado_em, atualizado_em) "
                "VALUES (?, ?, ?, ?, ?, 'proposta', ?, ?, ?)",
                (hip_id, caso_id, enunciado, categoria_sinal, agente, confianca, agora, agora),
            )
            self._evento(
                caso_id,
                agente,
                "registrar_hipotese",
                entrada={
                    "hipotese_id": hip_id,
                    "enunciado": enunciado,
                    "categoria": categoria_sinal,
                    "confianca": confianca,
                },
            )
        return hip_id

    def registrar_evidencia(
        self,
        agente: str,
        hipotese_id: str,
        tipo: str,
        referencia: dict[str, Any],
        trecho_relevante: str,
        apoia_ou_contraria: str,
    ) -> str:
        """Grava uma evidência **já conferida pelo validador** (ver ``casos.validador``).

        Este método não valida a referência: quem chama (a ferramenta ``registrar_evidencia``)
        roda o validador antes e descarta o que não passar.
        """
        self._exigir("registrar_evidencia", agente)
        if tipo not in ("registro_banco", "documento", "consulta_grafo"):
            raise RegraVioladaError(f"Tipo de evidência inválido: {tipo!r}.")
        if apoia_ou_contraria not in ("apoia", "contraria"):
            raise RegraVioladaError("apoia_ou_contraria deve ser 'apoia' ou 'contraria'.")
        if not trecho_relevante.strip():
            raise RegraVioladaError("A evidência precisa de um trecho relevante.")
        ev_id = _id("ev")
        agora = self._agora()
        with self._transacao() as con:
            hip = self._linha("hipotese", hipotese_id)
            _exigir_caso_ativo(self._linha("caso", hip["caso_id"]))
            con.execute(
                "INSERT INTO evidencia (id, hipotese_id, tipo, referencia, trecho_relevante, "
                "autor_agente, apoia_ou_contraria, valida, validada_em, criado_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    ev_id,
                    hipotese_id,
                    tipo,
                    _json(referencia),
                    trecho_relevante,
                    agente,
                    apoia_ou_contraria,
                    agora,
                    agora,
                ),
            )
            self._evento(
                hip["caso_id"],
                agente,
                "registrar_evidencia",
                entrada={
                    "evidencia_id": ev_id,
                    "hipotese_id": hipotese_id,
                    "tipo": tipo,
                    "direcao": apoia_ou_contraria,
                },
            )
        return ev_id

    def registrar_descarte(
        self, agente: str, hipotese_id: str, referencia: dict[str, Any], motivo: str
    ) -> None:
        """Registra no log uma evidência recusada pelo validador (não entra no caso)."""
        with self._transacao():
            hip = self._linha("hipotese", hipotese_id)
            self._evento(
                hip["caso_id"],
                "sistema",
                "evidencia_descartada",
                entrada={"autor": agente, "hipotese_id": hipotese_id, "referencia": referencia},
                saida=motivo,
            )

    def invalidar_evidencia(self, agente: str, evidencia_id: str, motivo: str) -> None:
        """Marca uma evidência como inválida (ex.: a fonte mudou numa revalidação)."""
        self._exigir("invalidar_evidencia", agente)
        with self._transacao() as con:
            ev = self._linha("evidencia", evidencia_id)
            hip = self._linha("hipotese", ev["hipotese_id"])
            con.execute(
                "UPDATE evidencia SET valida = 0, motivo_invalidacao = ?, validada_em = ? "
                "WHERE id = ?",
                (motivo, self._agora(), evidencia_id),
            )
            self._evento(
                hip["caso_id"],
                agente,
                "invalidar_evidencia",
                entrada={"evidencia_id": evidencia_id},
                saida=motivo,
            )
            if hip["status"] == "sustentada" and not self._sustentacao_ok(hip["id"])[0]:
                self._mudar_hipotese(hip, "proposta", "evidência invalidada", agente)

    def registrar_objecao(self, agente: str, hipotese_id: str, explicacao_alternativa: str) -> str:
        """Registra uma explicação legítima alternativa a ser verificada."""
        self._exigir("registrar_objecao", agente)
        exigir_linguagem(explicacao_alternativa, "explicacao_alternativa")
        ob_id = _id("obj")
        agora = self._agora()
        with self._transacao() as con:
            hip = self._linha("hipotese", hipotese_id)
            _exigir_caso_ativo(self._linha("caso", hip["caso_id"]))
            con.execute(
                "INSERT INTO objecao (id, hipotese_id, explicacao_alternativa, autor_agente, "
                "criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?)",
                (ob_id, hipotese_id, explicacao_alternativa, agente, agora, agora),
            )
            # Uma objeção nova reabre a questão: a hipótese deixa de estar sustentada.
            if hip["status"] == "sustentada":
                self._mudar_hipotese(hip, "proposta", "nova objeção a verificar", agente)
            self._evento(
                hip["caso_id"],
                agente,
                "registrar_objecao",
                entrada={
                    "objecao_id": ob_id,
                    "hipotese_id": hipotese_id,
                    "explicacao": explicacao_alternativa,
                },
            )
        return ob_id

    def verificar_objecao(
        self, agente: str, objecao_id: str, resultado: str, procede: bool
    ) -> None:
        """Registra o resultado da verificação de uma objeção.

        ``procede=True`` significa que a explicação legítima se confirmou (o que enfraquece
        ou derruba a hipótese).
        """
        self._exigir("verificar_objecao", agente)
        exigir_linguagem(resultado, "resultado")
        if not resultado.strip():
            raise RegraVioladaError("Descreva o que a verificação encontrou.")
        with self._transacao() as con:
            ob = self._linha("objecao", objecao_id)
            hip = self._linha("hipotese", ob["hipotese_id"])
            _exigir_caso_ativo(self._linha("caso", hip["caso_id"]))
            self._guardar_versao("objecao", ob, agente)
            con.execute(
                "UPDATE objecao SET verificada = 1, resultado = ?, procede = ?, "
                "verificada_por = ?, versao = versao + 1, atualizado_em = ? WHERE id = ?",
                (resultado, int(procede), agente, self._agora(), objecao_id),
            )
            self._evento(
                hip["caso_id"],
                agente,
                "verificar_objecao",
                entrada={"objecao_id": objecao_id, "procede": procede},
                saida=resultado,
            )

    def atualizar_hipotese(
        self,
        agente: str,
        hipotese_id: str,
        status: str,
        justificativa: str,
        confianca: float | None = None,
    ) -> None:
        """Muda o status de uma hipótese, aplicando a regra de ouro."""
        self._exigir("atualizar_hipotese", agente)
        exigir_linguagem(justificativa, "justificativa")
        if status not in ("proposta", "sustentada", "refutada", "inconclusiva"):
            raise RegraVioladaError(f"Status de hipótese inválido: {status!r}.")
        if status == "sustentada" and agente not in QUEM_SUSTENTA:
            raise PermissaoNegadaError("Só o Cético declara uma hipótese sustentada.")
        if confianca is not None:
            _exigir_confianca(confianca)
        with self._transacao():
            hip = self._linha("hipotese", hipotese_id)
            _exigir_caso_ativo(self._linha("caso", hip["caso_id"]))
            if status == "sustentada":
                ok, motivo = self._sustentacao_ok(hipotese_id)
                if not ok:
                    raise RegraVioladaError(f"Regra de ouro: {motivo}")
            self._mudar_hipotese(hip, status, justificativa, agente, confianca)

    def _sustentacao_ok(self, hipotese_id: str) -> tuple[bool, str]:
        favor = self._con.execute(
            "SELECT count(*) FROM evidencia WHERE hipotese_id = ? AND valida = 1 "
            "AND apoia_ou_contraria = 'apoia'",
            (hipotese_id,),
        ).fetchone()[0]
        if not favor:
            return False, "falta ao menos uma evidência válida que apoie a hipótese."
        abertas = self._con.execute(
            "SELECT count(*) FROM objecao WHERE hipotese_id = ? AND verificada = 0",
            (hipotese_id,),
        ).fetchone()[0]
        if abertas:
            return False, f"há {abertas} objeção(ões) ainda não verificada(s)."
        procedentes = self._con.execute(
            "SELECT count(*) FROM objecao WHERE hipotese_id = ? AND procede = 1",
            (hipotese_id,),
        ).fetchone()[0]
        if procedentes:
            return False, f"{procedentes} explicação(ões) legítima(s) se confirmaram."
        return True, ""

    def _mudar_hipotese(
        self,
        hip: dict[str, Any],
        status: str,
        justificativa: str,
        agente: str,
        confianca: float | None = None,
    ) -> None:
        self._guardar_versao("hipotese", hip, agente)
        self._con.execute(
            "UPDATE hipotese SET status = ?, justificativa_status = ?, confianca = ?, "
            "versao = versao + 1, atualizado_em = ? WHERE id = ?",
            (
                status,
                justificativa,
                hip["confianca"] if confianca is None else confianca,
                self._agora(),
                hip["id"],
            ),
        )
        self._evento(
            hip["caso_id"],
            agente,
            "atualizar_hipotese",
            entrada={
                "hipotese_id": hip["id"],
                "de": hip["status"],
                "para": status,
                "justificativa": justificativa,
            },
        )

    def registrar_consumo(
        self,
        caso_id: str,
        agente_autor: str,
        acao: str,
        *,
        tokens: int = 0,
        custo: float = 0.0,
        ferramenta: str | None = None,
        entrada: Any = None,
        saida: Any = None,
        versao_prompt: str | None = None,
    ) -> dict[str, int]:
        """Registra um passo de agente (chamada de modelo ou de ferramenta) e seu custo.

        É o orquestrador ("sistema") que chama, em nome do agente ``agente_autor``.
        Devolve os totais do caso, para o controle de limites.
        """
        if agente_autor not in AGENTES:
            raise PermissaoNegadaError(f"Agente desconhecido: {agente_autor!r}.")
        with self._transacao() as con:
            self._linha("caso", caso_id)
            con.execute(
                "UPDATE caso SET tokens_usados = tokens_usados + ?, "
                "passos_usados = passos_usados + 1 WHERE id = ?",
                (int(tokens), caso_id),
            )
            self._evento(
                caso_id,
                agente_autor,
                acao,
                ferramenta=ferramenta,
                entrada=entrada,
                saida=saida,
                tokens=tokens,
                custo=custo,
                versao_prompt=versao_prompt,
            )
            caso = self._linha("caso", caso_id)
        return {k: caso[k] for k in ("tokens_usados", "passos_usados", "ciclos_ceticos")}

    def registrar_parecer(
        self, agente: str, caso_id: str, revisor: str, decisao: str, comentario: str
    ) -> str:
        """Decisão do revisor humano; também move o caso de status."""
        self._exigir("registrar_parecer", agente)
        destino = {
            "aprovar": "aprovado",
            "mais_investigacao": "em_investigacao",
            "arquivar": "arquivado",
        }.get(decisao)
        if destino is None:
            raise RegraVioladaError(f"Decisão inválida: {decisao!r}.")
        if not revisor.strip() or not comentario.strip():
            raise RegraVioladaError("O parecer precisa de revisor e comentário.")
        par_id = _id("par")
        with self._transacao() as con:
            caso = self._linha("caso", caso_id)
            if destino not in TRANSICOES[caso["status"]]:
                raise RegraVioladaError(f"Não dá para {decisao} um caso em {caso['status']}.")
            con.execute(
                "INSERT INTO parecer_humano VALUES (?, ?, ?, ?, ?, ?)",
                (par_id, caso_id, revisor, decisao, comentario, self._agora()),
            )
            self._guardar_versao("caso", caso, agente)
            con.execute(
                "UPDATE caso SET status = ?, motivo_status = ?, versao = versao + 1, "
                "atualizado_em = ? WHERE id = ?",
                (destino, f"revisão humana: {comentario}", self._agora(), caso_id),
            )
            self._evento(
                caso_id,
                agente,
                "registrar_parecer",
                entrada={"revisor": revisor, "decisao": decisao},
                saida=comentario,
            )
        return par_id

    def registrar_evento(self, caso_id: str | None, agente: str, acao: str, **campos: Any) -> int:
        """Acrescenta um evento avulso ao log (ex.: consulta a uma ferramenta de leitura)."""
        if agente not in AGENTES:
            raise PermissaoNegadaError(f"Agente desconhecido: {agente!r}.")
        with self._transacao():
            return self._evento(caso_id, agente, acao, **campos)

    # ------------------------------------------------------------------ leitura
    def caso(self, caso_id: str) -> CasoCompleto:
        """Tudo sobre um caso."""
        caso = self._linha("caso", caso_id)
        caso["entidades_foco"] = json.loads(caso["entidades_foco"])
        hips = self._todas("SELECT * FROM hipotese WHERE caso_id = ? ORDER BY criado_em", caso_id)
        ids = [h["id"] for h in hips]
        marcas = ", ".join("?" * len(ids))
        evs = self._todas(
            f"SELECT * FROM evidencia WHERE hipotese_id IN ({marcas}) ORDER BY criado_em", *ids
        )
        for e in evs:
            e["referencia"] = json.loads(e["referencia"])
        objs = self._todas(
            f"SELECT * FROM objecao WHERE hipotese_id IN ({marcas}) ORDER BY criado_em", *ids
        )
        return CasoCompleto(
            caso=caso,
            hipoteses=hips,
            evidencias=evs,
            objecoes=objs,
            eventos=self._todas("SELECT * FROM evento WHERE caso_id = ? ORDER BY seq", caso_id),
            pareceres=self._todas(
                "SELECT * FROM parecer_humano WHERE caso_id = ? ORDER BY criado_em", caso_id
            ),
        )

    def listar_casos(self, status: str | None = None) -> list[dict[str, Any]]:
        """Casos, do mais prioritário para o menos."""
        sql = "SELECT * FROM caso"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        casos = self._todas(sql + " ORDER BY prioridade DESC, criado_em", *args)
        for c in casos:
            c["entidades_foco"] = json.loads(c["entidades_foco"])
        return casos

    def historico(self, tabela: str, registro_id: str) -> list[dict[str, Any]]:
        """Versões anteriores de um registro."""
        linhas = self._todas(
            "SELECT * FROM historico WHERE tabela = ? AND registro_id = ? ORDER BY versao",
            tabela,
            registro_id,
        )
        for h in linhas:
            h["dados"] = json.loads(h["dados"])
        return linhas

    def evidencias_validas(self) -> list[dict[str, Any]]:
        """Todas as evidências válidas (para revalidação periódica)."""
        evs = self._todas("SELECT * FROM evidencia WHERE valida = 1")
        for e in evs:
            e["referencia"] = json.loads(e["referencia"])
        return evs

    def verificar_log(self) -> list[int]:
        """Eventos cujo hash não confere (vazio = log íntegro)."""
        anterior = HASH_INICIAL
        quebrados = []
        for linha in self._con.execute("SELECT * FROM evento ORDER BY seq"):
            ev = dict(linha)
            campos = {k: ev[k] for k in _CAMPOS_EVENTO}
            if ev["hash_anterior"] != anterior or ev["hash"] != hash_evento(anterior, campos):
                quebrados.append(ev["seq"])
            anterior = ev["hash"]
        return quebrados

    def _todas(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in self._con.execute(sql, args).fetchall()]


def _exigir_confianca(confianca: float) -> None:
    if not 0 <= confianca <= 1:
        raise RegraVioladaError("A confiança deve estar entre 0 e 1.")


def _exigir_caso_ativo(caso: dict[str, Any]) -> None:
    if caso["status"] in ("arquivado", "aprovado", "pronto_para_humano"):
        raise RegraVioladaError(
            f"O caso está {caso['status']}; reabra-o (em_investigacao) antes de alterá-lo."
        )

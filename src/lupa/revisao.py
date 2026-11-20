"""Revisão humana dos alertas.

Nenhum dossiê sai sem uma revisão registrada que aprove o encaminhamento. As revisões
ficam num arquivo só de acréscimo (``gold/revisoes/revisoes.jsonl``): cada mudança de
situação vira uma linha nova, e o histórico nunca é reescrito.

A aprovação vale para o conjunto de sinais que a pessoa revisora viu. Se os sinais
recalculados mudarem (dados novos, regra nova), a aprovação deixa de valer e o alerta
precisa ser revisto.

As revisões também são a base para medir a precisão@k dos sinais (Fase 5).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SITUACOES: dict[str, str] = {
    "nao_revisado": "Não revisado",
    "em_revisao": "Em revisão",
    "aprovado": "Aprovado para encaminhamento",
    "descartado": "Descartado (falso positivo)",
}
MIN_JUSTIFICATIVA = 20


class RevisaoInvalidaError(ValueError):
    """Revisão sem os dados mínimos."""


class RevisaoPendenteError(RuntimeError):
    """Tentativa de exportar sem revisão aprovada e atual."""


@dataclass(frozen=True)
class Revisao:
    """Uma decisão de revisão sobre uma entidade."""

    entidade: str
    situacao: str
    revisor: str
    justificativa: str
    sinais: tuple[str, ...]
    score: float
    em: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def rotulo(self) -> str:
        """Nome legível da situação."""
        return SITUACOES.get(self.situacao, self.situacao)


def arquivo(raiz: Path) -> Path:
    """Arquivo de revisões dentro da pasta de dados."""
    return raiz / "gold" / "revisoes" / "revisoes.jsonl"


def validar(r: Revisao) -> None:
    """Exige revisor, situação conhecida e justificativa para decisões finais."""
    if r.situacao not in SITUACOES or r.situacao == "nao_revisado":
        raise RevisaoInvalidaError(f"Situação inválida: {r.situacao}")
    if not r.revisor.strip():
        raise RevisaoInvalidaError("Informe quem está revisando.")
    if r.situacao in ("aprovado", "descartado") and len(r.justificativa.strip()) < (
        MIN_JUSTIFICATIVA
    ):
        raise RevisaoInvalidaError(
            f"Justifique a decisão com pelo menos {MIN_JUSTIFICATIVA} caracteres."
        )


def registrar(raiz: Path, r: Revisao) -> None:
    """Valida e acrescenta a revisão ao arquivo."""
    validar(r)
    destino = arquivo(raiz)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def ler(raiz: Path) -> list[Revisao]:
    """Todas as revisões, na ordem em que foram registradas."""
    origem = arquivo(raiz)
    if not origem.exists():
        return []
    revisoes = []
    with origem.open(encoding="utf-8") as f:
        for linha in f:
            if linha.strip():
                d = json.loads(linha)
                revisoes.append(Revisao(**{**d, "sinais": tuple(d["sinais"])}))
    return revisoes


def historico(raiz: Path, entidade: str) -> list[Revisao]:
    """Revisões de uma entidade, da mais antiga à mais recente."""
    return [r for r in ler(raiz) if r.entidade == entidade]


def atuais(raiz: Path) -> dict[str, Revisao]:
    """Última revisão de cada entidade."""
    return {r.entidade: r for r in ler(raiz)}


def vigente(r: Revisao | None, sinais_atuais: Iterable[str]) -> bool:
    """A revisão ainda se refere aos sinais que a entidade tem hoje?"""
    return r is not None and set(r.sinais) == set(sinais_atuais)


def exigir_aprovacao(r: Revisao | None, sinais_atuais: Iterable[str]) -> Revisao:
    """Devolve a revisão se ela aprova a exportação; senão levanta erro explicando."""
    if r is None:
        raise RevisaoPendenteError("Alerta ainda não revisado: o dossiê exige revisão humana.")
    if r.situacao != "aprovado":
        raise RevisaoPendenteError(f"Situação da revisão: {r.rotulo}. O dossiê exige aprovação.")
    if not vigente(r, sinais_atuais):
        raise RevisaoPendenteError(
            "Os sinais mudaram depois da revisão; é preciso revisar de novo."
        )
    return r

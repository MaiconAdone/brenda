"""Ferramentas de escrita no arquivo do caso (as únicas escritas permitidas aos agentes).

O agente que escreve é fixado na criação (``FerramentasCaso(agente=...)``), e não vem como
argumento da ferramenta: o modelo não consegue se passar por outro papel. As regras
(permissões, regra de ouro, linguagem, transições) estão em ``ArquivoCasos``; aqui entra o
validador de evidências.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from lupa.casos.repositorio import ArquivoCasos
from lupa.casos.validador import Validador


class FerramentasCaso:
    """Operações sobre o arquivo do caso em nome de um agente."""

    def __init__(self, arquivo: ArquivoCasos, validador: Validador, agente: str) -> None:
        self.arquivo = arquivo
        self.validador = validador
        self.agente = agente

    def ler_caso(self, caso_id: str) -> dict[str, Any]:
        """Estado atual do caso (sem o log completo, que pode ser longo)."""
        c = asdict(self.arquivo.caso(caso_id))
        c["n_eventos"] = len(c.pop("eventos"))
        return c

    def registrar_hipotese(
        self, caso_id: str, enunciado: str, categoria_sinal: str, confianca: float
    ) -> dict[str, Any]:
        """Propõe uma hipótese."""
        hip = self.arquivo.registrar_hipotese(self.agente, caso_id, enunciado, categoria_sinal,
                                              confianca)  # fmt: skip
        return {"hipotese_id": hip}

    def registrar_evidencia(
        self,
        hipotese_id: str,
        tipo: str,
        referencia: dict[str, Any],
        trecho_relevante: str,
        apoia_ou_contraria: str,
    ) -> dict[str, Any]:
        """Valida e registra uma evidência; se não passar, ela é descartada (e registrada)."""
        veredito = self.validador.validar(tipo, referencia, trecho_relevante)
        if not veredito.valida:
            self.arquivo.registrar_descarte(self.agente, hipotese_id, referencia, veredito.motivo)
            return {"registrada": False, "motivo": veredito.motivo}
        ev = self.arquivo.registrar_evidencia(self.agente, hipotese_id, tipo, referencia,
                                              trecho_relevante, apoia_ou_contraria)  # fmt: skip
        return {"registrada": True, "evidencia_id": ev}

    def registrar_objecao(self, hipotese_id: str, explicacao_alternativa: str) -> dict[str, Any]:
        """Registra uma explicação legítima alternativa a verificar."""
        return {"objecao_id": self.arquivo.registrar_objecao(self.agente, hipotese_id,
                                                             explicacao_alternativa)}  # fmt: skip

    def verificar_objecao(self, objecao_id: str, resultado: str, procede: bool) -> dict[str, Any]:
        """Registra o que a verificação de uma objeção encontrou."""
        self.arquivo.verificar_objecao(self.agente, objecao_id, resultado, procede)
        return {"ok": True}

    def atualizar_hipotese(
        self, hipotese_id: str, status: str, justificativa: str, confianca: float | None = None
    ) -> dict[str, Any]:
        """Muda o status de uma hipótese (a regra de ouro vale para 'sustentada')."""
        self.arquivo.atualizar_hipotese(self.agente, hipotese_id, status, justificativa, confianca)
        return {"ok": True}

    def atualizar_status_caso(self, caso_id: str, status: str, motivo: str) -> dict[str, Any]:
        """Muda o status do caso."""
        self.arquivo.atualizar_status_caso(self.agente, caso_id, status, motivo)
        return {"ok": True}

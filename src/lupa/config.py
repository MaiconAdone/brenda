"""Carregamento e validação das configurações em ``config/``."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class PilotoNaoDefinidoError(RuntimeError):
    """O município do piloto ainda não foi configurado em ``config/piloto.yaml``."""


class Piloto(BaseModel):
    """Escopo geográfico e temporal.

    O escopo é uma lista de municípios (``municipios``, códigos IBGE) ou a UF inteira
    (``estado_inteiro: true``). ``municipio_ibge`` é a forma antiga, de um município só, e
    continua aceita. Nomes e códigos da Receita são obtidos do IBGE e das tabelas da
    Receita; ``nome_municipio`` e ``municipio_receita`` só valem para conferir ou forçar o
    código quando há um único município.
    """

    uf: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    municipios: list[str] = Field(default_factory=list)
    estado_inteiro: bool = False
    municipio_ibge: str | None = Field(default=None, pattern=r"^\d{7}$")
    nome_municipio: str | None = None
    municipio_receita: str | None = Field(default=None, pattern=r"^\d{4}$")
    periodo_anos: int = Field(default=4, ge=1, le=20)
    data_referencia: date | None = None

    @field_validator("municipio_ibge", "municipio_receita", mode="before")
    @classmethod
    def _como_texto(cls, valor: Any) -> Any:
        """Aceita códigos escritos como número no YAML."""
        return str(valor) if isinstance(valor, int) else valor

    @field_validator("municipios", mode="before")
    @classmethod
    def _lista_texto(cls, valor: Any) -> Any:
        if isinstance(valor, list):
            return [str(v) for v in valor]
        return valor

    def codigos_ibge(self) -> list[str]:
        """Municípios listados (vazio quando o escopo é a UF inteira)."""
        if self.estado_inteiro:
            return []
        codigos = list(dict.fromkeys(self.municipios))
        if not codigos and self.municipio_ibge:
            codigos = [self.municipio_ibge]
        return codigos

    def exigir_escopo(self) -> tuple[list[str], str]:
        """``(códigos IBGE, uf)``; lista vazia significa a UF inteira."""
        codigos = self.codigos_ibge()
        if self.uf is None or (not codigos and not self.estado_inteiro):
            raise PilotoNaoDefinidoError(
                "Defina 'uf' e 'municipios' (ou 'estado_inteiro: true') em "
                "config/piloto.yaml antes da ingestão."
            )
        for codigo in codigos:
            if not re.fullmatch(r"\d{7}", codigo):
                raise ValueError(f"Código IBGE inválido: {codigo!r}.")
            if not codigo.startswith(_PREFIXO_UF.get(self.uf, "??")):
                raise ValueError(f"Código IBGE {codigo} não pertence à UF {self.uf}.")
        return codigos, self.uf

    def descricao(self) -> str:
        """Escopo em texto curto (para telas e relatórios)."""
        codigos = self.codigos_ibge()
        if self.estado_inteiro:
            return f"{self.uf} (estado inteiro)"
        if len(codigos) == 1:
            return f"município IBGE {codigos[0]}/{self.uf}"
        return f"{len(codigos)} municípios de {self.uf}"

    def periodo(self, hoje: date | None = None) -> tuple[date, date]:
        """Intervalo ``(início, fim)`` analisado."""
        fim = self.data_referencia or hoje or date.today()
        try:
            inicio = fim.replace(year=fim.year - self.periodo_anos)
        except ValueError:  # 29 de fevereiro
            inicio = fim.replace(year=fim.year - self.periodo_anos, day=28)
        return inicio, fim


class ConfigPncp(BaseModel):
    """Parâmetros de coleta do PNCP."""

    modalidades: list[int] = Field(default_factory=lambda: list(range(1, 14)))
    intervalo_min_s: float = Field(default=0.25, ge=0)
    # "todos": itens e vencedores de toda contratação. "seletivo": só de valor a partir de
    # itens_valor_min ou ligadas a fornecedores com alerta (necessário no estado inteiro).
    itens: Literal["todos", "seletivo"] = "todos"
    itens_valor_min: float = Field(default=500_000, ge=0)
    # Acima deste número de órgãos, os contratos são varridos no país inteiro, por mês.
    max_orgaos_contratos: int = Field(default=300, ge=1)
    # Requisições simultâneas. O PNCP leva ~2 s por resposta; o intervalo mínimo entre
    # requisições (intervalo_min_s) continua valendo entre todas elas.
    trabalhadores: int = Field(default=8, ge=1, le=32)
    # A API de consulta (contratações, contratos) responde 429 com poucas dezenas de
    # requisições por minuto: poucas simultâneas e janelas anuais.
    trabalhadores_consulta: int = Field(default=2, ge=1, le=8)
    intervalo_itens_s: float = Field(default=0.1, ge=0)


class ConfigReceitaCnpj(BaseModel):
    """Parâmetros de coleta do CNPJ da Receita."""

    mes: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    # Guarda só estabelecimentos ativos do escopo (as fornecedoras entram sempre). Útil no
    # estado inteiro, em que os baixados são metade das linhas e nenhum sinal os usa.
    somente_ativas: bool = False


class ConfigTse(BaseModel):
    """Parâmetros de coleta do TSE."""

    anos: list[int] = Field(default_factory=lambda: [2020, 2024])


class ConfigTransparencia(BaseModel):
    """Parâmetros de coleta do Portal da Transparência."""

    # Vazio = todos os meses desde ``bolsa_familia_desde`` até o mês atual.
    bolsa_familia_meses: list[str] = Field(default_factory=list)
    bolsa_familia_desde: str = Field(default="202303", pattern=r"^\d{6}$")
    intervalo_min_s: float = Field(default=0.7, ge=0)


class ConfigIngestao(BaseModel):
    """Seção ``ingestao`` de ``piloto.yaml``."""

    pncp: ConfigPncp = Field(default_factory=ConfigPncp)
    receita_cnpj: ConfigReceitaCnpj = Field(default_factory=ConfigReceitaCnpj)
    tse: ConfigTse = Field(default_factory=ConfigTse)
    transparencia: ConfigTransparencia = Field(default_factory=ConfigTransparencia)


class ConfigPiloto(BaseModel):
    """Conteúdo de ``config/piloto.yaml``."""

    piloto: Piloto
    ingestao: ConfigIngestao = Field(default_factory=ConfigIngestao)
    finalidade: str = Field(min_length=20)


class ConfigCategoria(BaseModel):
    """Teto de pontuação de uma categoria de sinais."""

    teto: float = Field(gt=0)


class ConfigSinal(BaseModel):
    """Peso e parâmetros de um sinal."""

    peso: float = Field(ge=0)
    ativo: bool = True
    somente_combinado: bool = False
    parametros: dict[str, Any] = Field(default_factory=dict)


class ConfigResolucao(BaseModel):
    """Parâmetros da resolução probabilística de pessoas (Splink)."""

    limiar_probabilidade: float = Field(default=0.8, gt=0, lt=1)
    fracao_esperada_duplicatas: float = Field(default=0.02, gt=0, lt=1)
    semente: int = 42


class ConfigRegras(BaseModel):
    """Conteúdo de ``config/regras.yaml``."""

    categorias: dict[str, ConfigCategoria]
    sinais: dict[str, ConfigSinal]
    resolucao_entidades: ConfigResolucao = Field(default_factory=ConfigResolucao)


Servidor = Literal["dados", "documentos", "caso"]


class ConfigFerramentas(BaseModel):
    """Limites das ferramentas dos agentes."""

    limite_linhas: int = Field(default=200, ge=1, le=5000)
    timeout_s: float = Field(default=30, gt=0, le=600)
    documento_max_mb: float = Field(default=30, gt=0, le=500)
    dominios_permitidos: list[str] = Field(min_length=1)


class ConfigLimitesCaso(BaseModel):
    """Limites de um caso (ao estourar, o caso vai para inconclusivo)."""

    orcamento_tokens: int = Field(default=200_000, gt=0)
    max_passos: int = Field(default=40, gt=0)
    max_ciclos_ceticos: int = Field(default=3, ge=0)


class ConfigAgente(BaseModel):
    """Um papel da equipe."""

    servidores: list[Servidor] = Field(default_factory=list)


class ConfigAgentes(BaseModel):
    """Conteúdo de ``config/agentes.yaml``."""

    arquivo_casos: str = "casos/casos.sqlite"
    ferramentas: ConfigFerramentas
    limites_caso: ConfigLimitesCaso = Field(default_factory=ConfigLimitesCaso)
    agentes: dict[str, ConfigAgente]

    @field_validator("agentes")
    @classmethod
    def _analista_sem_escrita(cls, valor: dict[str, ConfigAgente]) -> dict[str, ConfigAgente]:
        analista = valor.get("analista_documental")
        if analista and "caso" in analista.servidores:
            raise ValueError("O Analista documental não pode ter o servidor 'caso' (escrita).")
        return valor


def carregar_agentes(caminho: Path | None = None) -> ConfigAgentes:
    """Lê e valida ``agentes.yaml``."""
    return ConfigAgentes.model_validate(_ler_yaml(caminho or diretorio_config() / "agentes.yaml"))


# Dois primeiros dígitos do código IBGE de município por UF.
_PREFIXO_UF = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27",
    "SE": "28", "BA": "29", "MG": "31", "ES": "32", "RJ": "33", "SP": "35", "PR": "41",
    "SC": "42", "RS": "43", "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}  # fmt: skip


def carregar_env(caminho: Path = Path(".env")) -> None:
    """Carrega ``CHAVE=valor`` de um ``.env`` sem sobrescrever o ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            if valor.strip():
                os.environ.setdefault(chave.strip(), valor.strip())


def diretorio_config() -> Path:
    """Diretório de configuração (``LUPA_CONFIG_DIR`` ou ``./config``)."""
    return Path(os.environ.get("LUPA_CONFIG_DIR", "config"))


def _ler_yaml(caminho: Path) -> dict[str, Any]:
    with caminho.open(encoding="utf-8") as arquivo:
        conteudo = yaml.safe_load(arquivo)
    if not isinstance(conteudo, dict):
        raise ValueError(f"{caminho} deve conter um mapeamento YAML.")
    return conteudo


def carregar_piloto(caminho: Path | None = None) -> ConfigPiloto:
    """Lê e valida ``piloto.yaml``."""
    return ConfigPiloto.model_validate(_ler_yaml(caminho or diretorio_config() / "piloto.yaml"))


def carregar_regras(caminho: Path | None = None) -> ConfigRegras:
    """Lê e valida ``regras.yaml``."""
    return ConfigRegras.model_validate(_ler_yaml(caminho or diretorio_config() / "regras.yaml"))

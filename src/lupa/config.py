"""Carregamento e validação das configurações em ``config/``."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class PilotoNaoDefinidoError(RuntimeError):
    """O município do piloto ainda não foi configurado em ``config/piloto.yaml``."""


class Piloto(BaseModel):
    """Escopo geográfico e temporal do piloto."""

    municipio_ibge: str | None = Field(default=None, pattern=r"^\d{7}$")
    uf: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    nome_municipio: str | None = None
    municipio_receita: str | None = Field(default=None, pattern=r"^\d{4}$")
    periodo_anos: int = Field(default=4, ge=1, le=20)
    data_referencia: date | None = None

    @field_validator("municipio_ibge", "municipio_receita", mode="before")
    @classmethod
    def _como_texto(cls, valor: Any) -> Any:
        """Aceita códigos escritos como número no YAML."""
        return str(valor) if isinstance(valor, int) else valor

    def exigir_definido(self) -> tuple[str, str]:
        """Retorna ``(municipio_ibge, uf)`` ou falha com mensagem clara."""
        if self.municipio_ibge is None or self.uf is None:
            raise PilotoNaoDefinidoError(
                "Defina 'municipio_ibge' e 'uf' em config/piloto.yaml antes da ingestão."
            )
        if not self.municipio_ibge.startswith(_PREFIXO_UF.get(self.uf, "??")):
            raise ValueError(f"Código IBGE {self.municipio_ibge} não pertence à UF {self.uf}.")
        return self.municipio_ibge, self.uf

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


class ConfigReceitaCnpj(BaseModel):
    """Parâmetros de coleta do CNPJ da Receita."""

    mes: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class ConfigTse(BaseModel):
    """Parâmetros de coleta do TSE."""

    anos: list[int] = Field(default_factory=lambda: [2020, 2024])


class ConfigTransparencia(BaseModel):
    """Parâmetros de coleta do Portal da Transparência."""

    bolsa_familia_meses: list[str] = Field(default_factory=list)
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


class ConfigRegras(BaseModel):
    """Conteúdo de ``config/regras.yaml``."""

    categorias: dict[str, ConfigCategoria]
    sinais: dict[str, ConfigSinal]


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

"""Validação e normalização de CPF e CNPJ, e mascaramento de CPF (LGPD)."""

from __future__ import annotations

import re

_NAO_DIGITO = re.compile(r"\D")
# Formato em que a Receita Federal publica o CPF de sócios: ***123456**
_CPF_MASCARADO_RF = re.compile(r"^\*{3}(\d{6})\*{2}$")


def somente_digitos(valor: str) -> str:
    """Remove tudo que não é dígito."""
    return _NAO_DIGITO.sub("", valor)


def _dv(digitos: str, pesos: range | list[int]) -> int:
    soma = sum(int(d) * p for d, p in zip(digitos, pesos, strict=False))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def cpf_valido(cpf: str) -> bool:
    """Verifica os dígitos verificadores de um CPF completo."""
    d = somente_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    return _dv(d[:9], range(10, 1, -1)) == int(d[9]) and _dv(d[:10], range(11, 1, -1)) == int(d[10])


def cnpj_valido(cnpj: str) -> bool:
    """Verifica os dígitos verificadores de um CNPJ numérico."""
    d = somente_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    return _dv(d[:12], p1) == int(d[12]) and _dv(d[:13], [6, *p1]) == int(d[13])


def cnpj_raiz(cnpj: str) -> str:
    """Os 8 primeiros dígitos (identificam a empresa; o resto, o estabelecimento)."""
    d = somente_digitos(cnpj)
    if len(d) != 14:
        raise ValueError("CNPJ deve ter 14 dígitos.")
    return d[:8]


def miolo_cpf(cpf: str) -> str:
    """Extrai os 6 dígitos centrais (posições 4 a 9), únicos publicados pela Receita.

    Aceita CPF completo (com ou sem pontuação) ou no formato mascarado da Receita
    (``***123456**``).
    """
    texto = cpf.strip()
    rf = _CPF_MASCARADO_RF.match(texto)
    if rf:
        return rf.group(1)
    d = somente_digitos(texto)
    if len(d) == 11:
        return d[3:9]
    raise ValueError("CPF em formato não reconhecido.")


def mascarar_cpf(cpf: str) -> str:
    """Mascara um CPF no padrão de exibição ``***.123.456-**``.

    Única forma permitida de mostrar CPF na interface, relatórios e logs.
    """
    m = miolo_cpf(cpf)
    return f"***.{m[:3]}.{m[3:]}-**"

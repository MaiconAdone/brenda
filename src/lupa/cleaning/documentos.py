"""Validação e normalização de CPF e CNPJ, e mascaramento de CPF (LGPD)."""

from __future__ import annotations

import re

_NAO_DIGITO = re.compile(r"\D")
# CPF já mascarado: ***123456** (Receita) ou ***.123.456-** (Portal da Transparência)
_CPF_MASCARADO_RF = re.compile(r"^\*{3}\.?(\d{3})\.?(\d{3})-?\*{2}$")


def somente_digitos(valor: str) -> str:
    """Remove tudo que não é dígito."""
    return _NAO_DIGITO.sub("", valor)


_NAO_ALFANUM = re.compile(r"[^0-9A-Z]")


def _dv(digitos: str, pesos: range | list[int]) -> int:
    # Para o CNPJ alfanumérico (a partir de jul/2026), o valor de cada caractere é
    # ord(c) - 48; para dígitos isso equivale a int(c).
    soma = sum((ord(d) - 48) * p for d, p in zip(digitos, pesos, strict=False))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def normalizar_cnpj(cnpj: str) -> str:
    """Remove pontuação e põe em caixa alta. Aceita CNPJ numérico e alfanumérico."""
    return _NAO_ALFANUM.sub("", cnpj.upper())


def cpf_valido(cpf: str) -> bool:
    """Verifica os dígitos verificadores de um CPF completo."""
    d = somente_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    return _dv(d[:9], range(10, 1, -1)) == int(d[9]) and _dv(d[:10], range(11, 1, -1)) == int(d[10])


def cnpj_valido(cnpj: str) -> bool:
    """Verifica os dígitos verificadores de um CNPJ (numérico ou alfanumérico)."""
    d = normalizar_cnpj(cnpj)
    if len(d) != 14 or not d[12:].isdigit() or d == d[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    return _dv(d[:12], p1) == int(d[12]) and _dv(d[:13], [6, *p1]) == int(d[13])


def cnpj_raiz(cnpj: str) -> str:
    """Os 8 primeiros caracteres (identificam a empresa; o resto, o estabelecimento)."""
    d = normalizar_cnpj(cnpj)
    if len(d) != 14:
        raise ValueError("CNPJ deve ter 14 caracteres.")
    return d[:8]


def miolo_cpf(cpf: str) -> str:
    """Extrai os 6 dígitos centrais (posições 4 a 9), únicos publicados pela Receita.

    Aceita CPF completo (com ou sem pontuação) ou já mascarado (``***123456**`` ou
    ``***.123.456-**``).
    """
    texto = cpf.strip()
    rf = _CPF_MASCARADO_RF.match(texto)
    if rf:
        return rf.group(1) + rf.group(2)
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

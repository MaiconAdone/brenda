"""Ferramentas de leitura: SQL nas visões, grafo, perfil e sinais de uma entidade.

Tudo é somente leitura. Cada resposta traz uma ``referencia`` pronta para virar evidência
no arquivo do caso: o validador (``lupa.casos.validador``) repete a consulta e confere o
hash do resultado, então o agente não consegue citar um dado que não existe.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

import lupa.signals.catalogo  # noqa: F401  (registra os sinais)
from lupa.ferramentas.anonimizar import anonimizar
from lupa.ferramentas.sql_seguro import ConsultaRecusadaError, validar_cypher, validar_sql
from lupa.ferramentas.visoes import VISOES, Visao, visoes_grafo
from lupa.signals.base import sinais

LIMITE_LINHAS = 200
TIMEOUT_S = 30.0


class EntidadeInvalidaError(ValueError):
    """Identificador de entidade fora dos formatos aceitos."""


def _normal(valor: Any) -> Any:
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (list, tuple)):
        return [_normal(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _normal(v) for k, v in valor.items()}
    return valor


def hash_resultado(colunas: Sequence[str], linhas: Sequence[Sequence[Any]]) -> str:
    """SHA-256 do resultado em JSON canônico (o que o validador compara)."""
    corpo = json.dumps(
        {"colunas": list(colunas), "linhas": _normal(list(map(list, linhas)))},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(corpo.encode("utf-8")).hexdigest()


def entidade_canonica(entidade: str) -> str:
    """Aceita ``Empresa:<raiz>``, ``Pessoa:<id>``, ``Candidatura:<ano-sq>`` ou um CNPJ."""
    texto = entidade.strip()
    digitos = re.sub(r"\D", "", texto)
    if ":" not in texto and len(digitos) in (8, 14):
        return f"Empresa:{digitos[:8]}"
    rotulo, _, ident = texto.partition(":")
    if rotulo == "Empresa" and re.fullmatch(r"[0-9A-Z]{8}", ident):
        return texto
    if rotulo == "Pessoa" and re.fullmatch(r"[0-9a-f]{16,64}", ident):
        return texto
    if rotulo == "Candidatura" and re.fullmatch(r"\d{4}-\d+", ident):
        return texto
    raise EntidadeInvalidaError(
        f"Entidade {entidade!r} inválida. Use Empresa:<CNPJ raiz>, Pessoa:<id_pessoa>, "
        "Candidatura:<ano>-<sequencial> ou um CNPJ."
    )


@dataclass(frozen=True)
class Resultado:
    """Linhas de uma consulta e a referência verificável."""

    colunas: list[str]
    linhas: list[list[Any]]
    truncado: bool
    referencia: dict[str, Any]

    def como_dict(self) -> dict[str, Any]:
        """Formato devolvido pelas ferramentas MCP."""
        return {
            "colunas": self.colunas,
            "linhas": self.linhas,
            "n_linhas": len(self.linhas),
            "truncado": self.truncado,
            "referencia": self.referencia,
        }


class Dados:
    """Conexão DuckDB isolada, só com as visões permitidas.

    A conexão é em memória, com acesso a arquivos restrito a ``silver/`` e ``gold/`` e a
    configuração travada depois de criar as visões. A consulta do agente ainda passa por
    ``validar_sql`` (só SELECT, só visões), porque o DuckDB permitiria escrever dentro
    das pastas liberadas.
    """

    def __init__(
        self, raiz: Path, limite_linhas: int = LIMITE_LINHAS, timeout_s: float = TIMEOUT_S
    ) -> None:
        self.raiz = raiz.resolve()
        self.limite_linhas = limite_linhas
        self.timeout_s = timeout_s
        self._con = duckdb.connect()
        self.visoes: dict[str, Visao] = {}
        base = self.raiz.as_posix()
        self._con.execute(f"SET allowed_directories = ['{base}/silver/', '{base}/gold/']")
        self._con.execute("SET enable_external_access = false")
        for visao in (*VISOES, *visoes_grafo(self.raiz)):
            if (self.raiz / visao.arquivo).exists() and self._juncao_ok(visao):
                self._con.execute(self._sql_visao(visao))
                self.visoes[visao.nome] = visao
        self._criar_arestas()
        self._con.execute("SET lock_configuration = true")
        self._catalogo = sinais()

    # ------------------------------------------------------------------ visões
    def _juncao_ok(self, visao: Visao) -> bool:
        m = re.search(r"\{raiz\}/([^']+)", visao.juncao)
        return m is None or (self.raiz / m.group(1)).exists()

    def _sql_visao(self, visao: Visao) -> str:
        caminho = (self.raiz / visao.arquivo).as_posix()
        colunas = self._colunas(caminho)
        partes = []
        for c in colunas:
            if c in visao.remover:
                continue
            partes.append(f"{visao.trocas[c]} AS {c}" if c in visao.trocas else f"t.{c}")
        partes += [f"{e} AS {c}" for c, e in visao.trocas.items() if c not in colunas]
        juncao = visao.juncao.replace("{raiz}", self.raiz.as_posix())
        return (
            f"CREATE VIEW {visao.nome} AS SELECT {', '.join(partes)} "
            f"FROM read_parquet('{caminho}') t {juncao}"
        )

    def _colunas(self, caminho: str) -> list[str]:
        return [r[0] for r in self._con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{caminho}')").fetchall()]  # fmt: skip

    def _criar_arestas(self) -> None:
        partes = [
            f"SELECT rotulo_origem || ':' || origem AS origem, rotulo_destino || ':' || destino "
            f"AS destino, '{v.arquivo.rsplit('/', 1)[1].removesuffix('.parquet')}' AS tipo "
            f"FROM {v.nome}"
            for v in self.visoes.values() if v.nome.startswith("grafo_aresta_")
        ]  # fmt: skip
        if partes:
            self._con.execute(f"CREATE VIEW grafo_arestas AS {' UNION ALL '.join(partes)}")
            self.visoes["grafo_arestas"] = Visao(
                "grafo_arestas", "", "Todas as arestas do grafo: origem, destino (Rotulo:id), tipo."
            )

    def descrever_tabelas(self) -> list[dict[str, Any]]:
        """Visões disponíveis, com descrição e colunas."""
        saida = []
        for nome, visao in sorted(self.visoes.items()):
            cols = self._con.execute(f"DESCRIBE {nome}").fetchall()
            saida.append(
                {
                    "visao": nome,
                    "descricao": visao.descricao,
                    "colunas": [f"{c[0]} {c[1]}" for c in cols],
                }
            )
        return saida

    # ------------------------------------------------------------------ SQL
    def consultar_tabelas(self, sql: str, limite: int | None = None) -> Resultado:
        """Roda um SELECT nas visões (somente leitura, com limite de linhas e timeout)."""
        validar_sql(sql, self.visoes)
        n = min(limite or self.limite_linhas, self.limite_linhas)
        colunas, linhas = self._executar(f"SELECT * FROM ({sql.strip().rstrip(';')}) LIMIT {n + 1}")
        truncado = len(linhas) > n
        linhas = linhas[:n]
        ref = {
            "tipo": "registro_banco",
            "sql": sql.strip().rstrip(";"),
            "limite": n,
            "sha_resultado": hash_resultado(colunas, linhas),
        }
        return Resultado(colunas, _normal(linhas), truncado, ref)

    def _executar(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[list[Any]]]:
        cursor = self._con.cursor()
        timer = threading.Timer(self.timeout_s, cursor.interrupt)
        timer.start()
        try:
            rel = cursor.execute(sql, list(params))
            colunas = [d[0] for d in rel.description or []]
            linhas = [list(r) for r in rel.fetchall()]
        except duckdb.InterruptException as erro:
            raise ConsultaRecusadaError(
                f"A consulta passou de {self.timeout_s:.0f} s; filtre mais ou agregue."
            ) from erro
        except duckdb.Error as erro:
            raise ConsultaRecusadaError(f"Erro na consulta: {erro}") from erro
        finally:
            timer.cancel()
            cursor.close()
        return colunas, linhas

    # ------------------------------------------------------------------ grafo
    def vizinhanca_grafo(
        self, no: str, saltos: int = 1, grau_max: int = 50, tipos: Iterable[str] | None = None
    ) -> Resultado:
        """Arestas a até ``saltos`` (1 ou 2) do nó; nós com grau acima de ``grau_max`` não
        são expandidos (ex.: um órgão com milhares de fornecedores)."""
        if "grafo_arestas" not in self.visoes:
            raise ConsultaRecusadaError("O grafo ainda não foi gerado (python -m lupa.graph).")
        saltos = max(1, min(int(saltos), 2))
        filtro_tipos = sorted(set(tipos or []))
        visitados = {no}
        fronteira = [no]
        arestas: set[tuple[str, str, str]] = set()
        cortados: list[str] = []
        for _ in range(saltos):
            if not fronteira:
                break
            sql = (
                "SELECT origem, destino, tipo FROM grafo_arestas "
                "WHERE (list_contains(?, origem) OR list_contains(?, destino))"
            )
            params: list[Any] = [fronteira, fronteira]
            if filtro_tipos:
                sql += " AND list_contains(?, tipo)"
                params.append(filtro_tipos)
            _, linhas = self._executar(sql, params)
            grau: dict[str, int] = {}
            for o, d, _t in linhas:
                for x in (o, d):
                    if x in fronteira:
                        grau[x] = grau.get(x, 0) + 1
            excesso = {n for n, g in grau.items() if g > grau_max and n != no}
            cortados += sorted(excesso)
            proxima = []
            for o, d, t in linhas:
                if o in excesso or d in excesso:
                    continue
                arestas.add((o, d, t))
                for x in (o, d):
                    if x not in visitados:
                        visitados.add(x)
                        proxima.append(x)
            if len([a for a in arestas]) > grau_max * 20:
                break
            fronteira = proxima
        nomes = self._nomes(visitados)
        linhas_saida = sorted([o, nomes.get(o), t, d, nomes.get(d)] for o, d, t in arestas)
        colunas = ["origem", "nome_origem", "tipo", "destino", "nome_destino"]
        args = {"no": no, "saltos": saltos, "grau_max": grau_max, "tipos": filtro_tipos}
        ref = {
            "tipo": "consulta_grafo",
            "ferramenta": "vizinhanca_grafo",
            "argumentos": args,
            "sha_resultado": hash_resultado(colunas, linhas_saida),
            "nos_nao_expandidos": cortados,
        }
        return Resultado(colunas, linhas_saida, bool(cortados), ref)

    def caminho_grafo(self, a: str, b: str, max_saltos: int = 4, grau_max: int = 200) -> Resultado:
        """Menor caminho entre dois nós (busca em largura, sem passar por nós de grau alto)."""
        if "grafo_arestas" not in self.visoes:
            raise ConsultaRecusadaError("O grafo ainda não foi gerado (python -m lupa.graph).")
        max_saltos = max(1, min(int(max_saltos), 6))
        pai: dict[str, tuple[str, str] | None] = {a: None}
        fronteira = [a]
        achou = a == b
        for _ in range(max_saltos):
            if achou or not fronteira:
                break
            _, linhas = self._executar(
                "SELECT origem, destino, tipo FROM grafo_arestas "
                "WHERE list_contains(?, origem) OR list_contains(?, destino)",
                [fronteira, fronteira],
            )
            grau: dict[str, int] = {}
            for o, d, _t in linhas:
                for x in (o, d):
                    grau[x] = grau.get(x, 0) + 1
            proxima = []
            for o, d, t in linhas:
                for x, y in ((o, d), (d, o)):
                    if x in pai and y not in pai and (grau.get(x, 0) <= grau_max or x == a):
                        pai[y] = (x, t)
                        proxima.append(y)
                        achou = achou or y == b
            fronteira = proxima
        passos: list[list[Any]] = []
        if achou:
            atual = b
            while pai[atual] is not None:
                anterior, tipo = pai[atual]  # type: ignore[misc]
                passos.append([anterior, tipo, atual])
                atual = anterior
            passos.reverse()
        nomes = self._nomes({x for p in passos for x in (p[0], p[2])})
        linhas_saida = [[o, nomes.get(o), t, d, nomes.get(d)] for o, t, d in passos]
        colunas = ["de", "nome_de", "tipo", "para", "nome_para"]
        args = {"a": a, "b": b, "max_saltos": max_saltos, "grau_max": grau_max}
        ref = {
            "tipo": "consulta_grafo",
            "ferramenta": "caminho_grafo",
            "argumentos": args,
            "sha_resultado": hash_resultado(colunas, linhas_saida),
        }
        return Resultado(colunas, linhas_saida, False, ref)

    def _nomes(self, nos: Iterable[str]) -> dict[str, str | None]:
        por_rotulo: dict[str, list[str]] = {}
        for n in nos:
            rotulo, _, ident = n.partition(":")
            por_rotulo.setdefault(rotulo, []).append(ident)
        saida: dict[str, str | None] = {}
        for rotulo, ids in por_rotulo.items():
            visao = f"grafo_no_{rotulo.lower()}"
            if visao not in self.visoes or "nome" not in self._colunas_visao(visao):
                continue
            _, linhas = self._executar(
                f"SELECT id, nome FROM {visao} WHERE list_contains(?, id)", [ids]
            )
            saida.update({f"{rotulo}:{i}": nm for i, nm in linhas})
        return saida

    def _colunas_visao(self, visao: str) -> list[str]:
        return [r[0] for r in self._con.execute(f"DESCRIBE {visao}").fetchall()]

    def consultar_grafo(self, cypher: str, limite: int | None = None) -> Resultado:
        """Cypher somente leitura no Neo4j (se ``NEO4J_URI`` estiver configurado)."""
        consulta = validar_cypher(cypher)
        uri = os.environ.get("NEO4J_URI")
        if not uri:
            raise ConsultaRecusadaError(
                "Neo4j não está configurado nesta instalação. Use vizinhanca_grafo, "
                "caminho_grafo ou consultar_tabelas nas visões grafo_*."
            )
        from neo4j import READ_ACCESS, GraphDatabase  # import tardio: só com Neo4j

        n = min(limite or self.limite_linhas, self.limite_linhas)
        auth = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))
        with GraphDatabase.driver(uri, auth=auth) as driver, \
                driver.session(default_access_mode=READ_ACCESS) as sessao:  # fmt: skip
            res = sessao.run(
                f"{consulta} LIMIT {n + 1}" if "LIMIT" not in consulta.upper() else consulta,
                timeout=self.timeout_s,
            )
            registros = list(res)
        colunas = list(registros[0].keys()) if registros else []
        linhas = [[_normal(r[c]) for c in colunas] for r in registros]
        truncado = len(linhas) > n
        linhas = anonimizar(linhas[:n])
        ref = {
            "tipo": "consulta_grafo",
            "ferramenta": "consultar_grafo",
            "argumentos": {"cypher": consulta},
            "sha_resultado": hash_resultado(colunas, linhas),
        }
        return Resultado(colunas, linhas, truncado, ref)

    # ------------------------------------------------------------------ entidade
    def sinais_da_entidade(self, entidade: str) -> dict[str, Any]:
        """Sinais disparados para a entidade, com evidências sem nomes de pessoas físicas."""
        ent = entidade_canonica(entidade)
        arquivo = self.raiz / "gold" / "alertas" / "alertas.parquet"
        if not arquivo.exists():
            return {"entidade": ent, "sinais": []}
        # As evidências citam sócios pelo nome; lidas aqui direto do Parquet e anonimizadas
        # antes de sair (a visão "alertas" não traz a coluna).
        linhas = (
            duckdb.connect()
            .execute(
                "SELECT sinal_id, confianca, n_evidencias, evidencias FROM read_parquet(?) "
                "WHERE entidade = ? ORDER BY sinal_id",
                [arquivo.as_posix(), ent],
            )
            .fetchall()
        )
        nomes = self._nomes_socios_pf(ent)
        saida = []
        for sinal_id, confianca, n_ev, evidencias in linhas:
            classe = self._catalogo.get(sinal_id)
            saida.append(
                {
                    "sinal_id": sinal_id,
                    "nome": classe.nome if classe else sinal_id,
                    "categoria": str(classe.categoria) if classe else "",
                    "descricao_sinal": classe.descricao if classe else "",
                    "limitacoes": classe.limitacoes if classe else "",
                    "confianca": confianca,
                    "n_evidencias": n_ev,
                    "evidencias": anonimizar(json.loads(evidencias), nomes),
                }
            )
        ref = {
            "tipo": "registro_banco",
            "sql": "SELECT sinal_id, entidade, confianca FROM alertas "
            f"WHERE entidade = '{ent}' ORDER BY sinal_id",
            "limite": self.limite_linhas,
            "sha_resultado": self.consultar_tabelas(
                f"SELECT sinal_id, entidade, confianca FROM alertas WHERE entidade = '{ent}' "
                "ORDER BY sinal_id"
            ).referencia["sha_resultado"],
        }
        return {"entidade": ent, "sinais": saida, "referencia": ref}

    def _nomes_socios_pf(self, ent: str) -> dict[str, str]:
        """Nomes das pessoas físicas sócias (só para anonimizar; não saem da ferramenta)."""
        arquivo = self.raiz / "silver" / "receita_cnpj" / "socios.parquet"
        if not ent.startswith("Empresa:") or not arquivo.exists():
            return {}
        linhas = (
            duckdb.connect()
            .execute(
                "SELECT nome_socio, id_pessoa FROM read_parquet(?) WHERE cnpj_basico = ? AND eh_pf",
                [arquivo.as_posix(), ent.split(":", 1)[1]],
            )
            .fetchall()
        )
        return {n: i for n, i in linhas if n and i}

    def perfil_entidade(self, entidade: str) -> dict[str, Any]:
        """Ficha consolidada (cadastro, sócios, score, vitórias, contratos, sanções...)."""
        ent = entidade_canonica(entidade)
        rotulo, _, ident = ent.partition(":")
        perfil: dict[str, Any] = {
            "entidade": ent,
            "score": self._tabela(
                "SELECT ranking, posicao, score, por_categoria, sinais FROM scores "
                f"WHERE entidade = '{ent}'"
            ),
        }
        perfil["sinais"] = [s["sinal_id"] for s in self.sinais_da_entidade(ent)["sinais"]]
        if rotulo == "Empresa":
            perfil |= self._perfil_empresa(ident)
        elif rotulo == "Pessoa":
            perfil |= self._perfil_pessoa(ident)
        else:
            perfil |= self._perfil_candidatura(ident)
        return perfil

    def _tabela(self, sql: str, limite: int = 50) -> dict[str, Any]:
        """Executa uma consulta interna já confiável e devolve linhas + referência."""
        if not all(v in self.visoes for v in re.findall(r"(?:FROM|JOIN) (\w+)", sql)):
            return {"linhas": [], "observacao": "tabela indisponível nesta base"}
        try:
            r = self.consultar_tabelas(sql, limite)
        except ConsultaRecusadaError as erro:
            return {"linhas": [], "observacao": str(erro)}
        return {
            "colunas": r.colunas,
            "linhas": r.linhas,
            "truncado": r.truncado,
            "referencia": r.referencia,
        }

    def _perfil_empresa(self, raiz: str) -> dict[str, Any]:
        return {
            "cadastro": self._tabela(
                f"SELECT * FROM receita_empresas WHERE cnpj_basico = '{raiz}'"
            ),
            "simples": self._tabela(f"SELECT * FROM receita_simples WHERE cnpj_basico = '{raiz}'"),
            "estabelecimentos": self._tabela(
                "SELECT cnpj, identificador_matriz_filial, situacao_cadastral, "
                "data_situacao_cadastral, data_inicio_atividade, cnae_fiscal_principal, "
                "cnae_fiscal_secundaria, bairro, cep, uf, municipio, endereco_hash "
                f"FROM receita_estabelecimentos WHERE cnpj_basico = '{raiz}'"
            ),
            "socios": self._tabela(
                "SELECT identificador_socio, eh_pf, id_pessoa, socio_documento, nome_socio, "
                "qualificacao_socio, data_entrada_sociedade, faixa_etaria "
                f"FROM receita_socios WHERE cnpj_basico = '{raiz}'"
            ),
            "vitorias_por_orgao": self._tabela(
                "SELECT c.orgao_cnpj, c.orgao_nome, count(DISTINCT r.numero_controle_pncp) AS "
                "licitacoes, sum(r.valor_total_homologado) AS valor, min(r.data_resultado) AS "
                "primeira, max(r.data_resultado) AS ultima FROM pncp_resultados r JOIN "
                "pncp_contratacoes c USING (numero_controle_pncp) WHERE "
                f"substr(r.fornecedor_documento, 1, 8) = '{raiz}' GROUP BY 1, 2 ORDER BY valor DESC"
            ),
            "contratos": self._tabela(
                "SELECT numero_controle_pncp, orgao_nome, objeto, valor_inicial, valor_global, "
                "data_assinatura, url_fonte FROM pncp_contratos WHERE "
                f"substr(fornecedor_documento, 1, 8) = '{raiz}' ORDER BY data_assinatura DESC"
            ),
            "sancoes": self._tabela(
                "SELECT cadastro, categoria, data_inicio, data_fim, orgao_sancionador, abrangencia "
                f"FROM transparencia_sancoes WHERE cnpj_raiz = '{raiz}'"
            ),
            "campanhas": self._tabela(
                "SELECT d.ano_eleicao, d.sq_candidato, c.nome_urna, c.cargo, c.resultado, "
                "sum(d.valor) AS valor FROM tse_despesas d LEFT JOIN tse_candidatos c USING "
                f"(sq_candidato, ano_eleicao) WHERE substr(d.fornecedor_documento, 1, 8) = '{raiz}' "
                "GROUP BY ALL"
            ),
            "anomalia": self._tabela(
                f"SELECT percentil, anomala, explicacao FROM ml_anomalias WHERE entidade = 'Empresa:{raiz}'"
            ),
            "nucleo": self._tabela(
                "SELECT nucleo, tamanho_comunidade, membros, orgao_comum, co_vitorias, valor_nucleo "
                f"FROM ml_nucleos WHERE entidade = 'Empresa:{raiz}'"
            ),
        }

    def _perfil_pessoa(self, ident: str) -> dict[str, Any]:
        return {
            "pessoa": self._tabela(
                "SELECT no_id, documento, idade_min, idade_max, origens, n_mencoes "
                f"FROM pessoas WHERE no_id = '{ident}'"
            ),
            "sociedades": self._tabela(
                "SELECT s.cnpj_basico, e.razao_social, s.qualificacao_socio, "
                "s.data_entrada_sociedade, s.faixa_etaria FROM receita_socios s LEFT JOIN "
                f"receita_empresas e USING (cnpj_basico) WHERE s.id_pessoa = '{ident}'"
            ),
            "empresas_no_pais": self._tabela(
                f"SELECT n_empresas FROM receita_socios_contagem_nacional WHERE id_pessoa = '{ident}'"
            ),
            "doacoes": self._tabela(
                "SELECT ano_eleicao, sq_candidato, valor, data FROM tse_receitas "
                f"WHERE doador_id_pessoa = '{ident}'"
            ),
            "servicos_a_campanhas": self._tabela(
                "SELECT ano_eleicao, sq_candidato, valor, data, descricao FROM tse_despesas "
                f"WHERE fornecedor_id_pessoa = '{ident}'"
            ),
            "bolsa_familia": self._tabela(
                f"SELECT mes, valor_total FROM transparencia_bolsa_familia WHERE id_pessoa = '{ident}' "
                "ORDER BY mes"
            ),
            "mesma_pessoa_provavel": self._tabela(
                "SELECT id_a, id_b, probabilidade, evidencia FROM ligacoes_pessoas "
                f"WHERE id_a = '{ident}' OR id_b = '{ident}'"
            ),
        }

    def _perfil_candidatura(self, ident: str) -> dict[str, Any]:
        ano, _, sq = ident.partition("-")
        return {
            "candidatura": self._tabela(
                f"SELECT * FROM tse_candidatos WHERE CAST(ano_eleicao AS VARCHAR) = '{ano}' AND CAST(sq_candidato AS VARCHAR) = '{sq}'"
            ),
            "receitas_por_tipo": self._tabela(
                "SELECT doador_tipo, origem, count(*) AS n, sum(valor) AS valor FROM tse_receitas "
                f"WHERE CAST(ano_eleicao AS VARCHAR) = '{ano}' AND CAST(sq_candidato AS VARCHAR) = '{sq}' GROUP BY ALL"
            ),
            "maiores_despesas": self._tabela(
                "SELECT fornecedor_tipo, fornecedor_documento, fornecedor_nome, fornecedor_id_pessoa, "
                "sum(valor) AS valor FROM tse_despesas "
                f"WHERE CAST(ano_eleicao AS VARCHAR) = '{ano}' AND CAST(sq_candidato AS VARCHAR) = '{sq}' GROUP BY ALL "
                "ORDER BY valor DESC",
                limite=30,
            ),
        }

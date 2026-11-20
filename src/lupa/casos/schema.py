"""Schema e migrações do arquivo do caso (SQLite).

SQLite em modo WAL em vez de DuckDB: a interface (Streamlit) e os agentes rodam em
processos diferentes, e o DuckDB não deixa um processo ler enquanto outro escreve. O SQL
é portável para Postgres (tipos simples, chaves de texto, JSON guardado como texto).

Cada migração é aplicada uma vez, em ordem; a versão fica em ``PRAGMA user_version``.
Migrações nunca são editadas depois de publicadas: mudanças entram como uma nova.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRACOES: tuple[str, ...] = (
    # 1: tabelas do quadro-negro
    """
    CREATE TABLE caso (
        id TEXT PRIMARY KEY,
        alerta_origem_id TEXT,
        entidades_foco TEXT NOT NULL,          -- JSON: lista de entidades ("Empresa:...")
        status TEXT NOT NULL CHECK (status IN (
            'aberto', 'em_investigacao', 'em_revisao_cetica', 'pronto_para_humano',
            'inconclusivo', 'aprovado', 'arquivado')),
        prioridade REAL NOT NULL DEFAULT 0,
        orcamento_tokens INTEGER NOT NULL,
        tokens_usados INTEGER NOT NULL DEFAULT 0,
        passos_usados INTEGER NOT NULL DEFAULT 0,
        ciclos_ceticos INTEGER NOT NULL DEFAULT 0,
        motivo_status TEXT,
        versao INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL
    );
    CREATE TABLE hipotese (
        id TEXT PRIMARY KEY,
        caso_id TEXT NOT NULL REFERENCES caso(id),
        enunciado TEXT NOT NULL,
        categoria_sinal TEXT NOT NULL CHECK (categoria_sinal IN ('F', 'L', 'C', 'K', 'E', 'M')),
        autor_agente TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'proposta', 'sustentada', 'refutada', 'inconclusiva')),
        confianca REAL NOT NULL CHECK (confianca BETWEEN 0 AND 1),
        justificativa_status TEXT,
        versao INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL
    );
    CREATE TABLE evidencia (
        id TEXT PRIMARY KEY,
        hipotese_id TEXT NOT NULL REFERENCES hipotese(id),
        tipo TEXT NOT NULL CHECK (tipo IN ('registro_banco', 'documento', 'consulta_grafo')),
        referencia TEXT NOT NULL,              -- JSON conferido pelo validador
        trecho_relevante TEXT NOT NULL,
        autor_agente TEXT NOT NULL,
        apoia_ou_contraria TEXT NOT NULL CHECK (apoia_ou_contraria IN ('apoia', 'contraria')),
        valida INTEGER NOT NULL DEFAULT 1,
        validada_em TEXT NOT NULL,
        motivo_invalidacao TEXT,
        criado_em TEXT NOT NULL
    );
    CREATE TABLE objecao (
        id TEXT PRIMARY KEY,
        hipotese_id TEXT NOT NULL REFERENCES hipotese(id),
        explicacao_alternativa TEXT NOT NULL,
        verificada INTEGER NOT NULL DEFAULT 0,
        resultado TEXT,                        -- o que a verificação encontrou
        procede INTEGER,                       -- 1: a explicação legítima se confirmou
        autor_agente TEXT NOT NULL,
        verificada_por TEXT,
        versao INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL
    );
    CREATE TABLE evento (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        caso_id TEXT,
        agente TEXT NOT NULL,
        acao TEXT NOT NULL,
        ferramenta TEXT,
        entrada TEXT,                          -- JSON resumido
        saida TEXT,                            -- resumo
        tokens INTEGER NOT NULL DEFAULT 0,
        custo REAL NOT NULL DEFAULT 0,
        versao_prompt TEXT,
        criado_em TEXT NOT NULL,
        hash_anterior TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE TABLE historico (
        tabela TEXT NOT NULL,
        registro_id TEXT NOT NULL,
        versao INTEGER NOT NULL,
        dados TEXT NOT NULL,                   -- JSON da linha antes da mudança
        alterado_por TEXT NOT NULL,
        alterado_em TEXT NOT NULL,
        PRIMARY KEY (tabela, registro_id, versao)
    );
    CREATE TABLE parecer_humano (
        id TEXT PRIMARY KEY,
        caso_id TEXT NOT NULL REFERENCES caso(id),
        revisor TEXT NOT NULL,
        decisao TEXT NOT NULL CHECK (decisao IN (
            'aprovar', 'mais_investigacao', 'arquivar')),
        comentario TEXT NOT NULL,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX ix_hipotese_caso ON hipotese(caso_id);
    CREATE INDEX ix_evidencia_hipotese ON evidencia(hipotese_id);
    CREATE INDEX ix_objecao_hipotese ON objecao(hipotese_id);
    CREATE INDEX ix_evento_caso ON evento(caso_id, seq);
    CREATE INDEX ix_caso_status ON caso(status, prioridade);
    CREATE TRIGGER evento_sem_update BEFORE UPDATE ON evento
        BEGIN SELECT RAISE(ABORT, 'o log de eventos é só de acréscimo'); END;
    CREATE TRIGGER evento_sem_delete BEFORE DELETE ON evento
        BEGIN SELECT RAISE(ABORT, 'o log de eventos é só de acréscimo'); END;
    CREATE TRIGGER historico_sem_update BEFORE UPDATE ON historico
        BEGIN SELECT RAISE(ABORT, 'o histórico é só de acréscimo'); END;
    CREATE TRIGGER historico_sem_delete BEFORE DELETE ON historico
        BEGIN SELECT RAISE(ABORT, 'o histórico é só de acréscimo'); END;
    """,
)


def versao_atual(con: sqlite3.Connection) -> int:
    """Versão do schema gravada no banco."""
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def conectar(caminho: Path) -> sqlite3.Connection:
    """Abre (e migra, se preciso) o arquivo do caso.

    ``check_same_thread=False``: o repositório serializa as escritas com uma trava
    própria, e os agentes rodam casos em paralelo em threads.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    migrar(con)
    return con


def migrar(con: sqlite3.Connection) -> int:
    """Aplica as migrações pendentes; devolve a versão final."""
    atual = versao_atual(con)
    if atual > len(MIGRACOES):
        raise RuntimeError(
            f"Banco na versão {atual}, mais nova que este código ({len(MIGRACOES)})."
        )
    for numero, sql in enumerate(MIGRACOES[atual:], start=atual + 1):
        con.execute("BEGIN")
        try:
            for comando in _comandos(sql):
                con.execute(comando)
            con.execute(f"PRAGMA user_version = {numero}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return versao_atual(con)


def _comandos(sql: str) -> list[str]:
    """Separa os comandos de uma migração (respeitando ``BEGIN ... END`` dos gatilhos)."""
    comandos: list[str] = []
    atual: list[str] = []
    for linha in sql.splitlines():
        atual.append(linha)
        texto = "\n".join(atual).strip()
        if texto.endswith(";") and sqlite3.complete_statement(texto):
            comandos.append(texto)
            atual = []
    if "\n".join(atual).strip():
        raise ValueError("Migração com comando incompleto.")
    return comandos

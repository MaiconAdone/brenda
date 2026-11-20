from __future__ import annotations

import pytest

from lupa.ferramentas.anonimizar import anonimizar_texto
from lupa.ferramentas.dados import Dados, EntidadeInvalidaError, entidade_canonica
from lupa.ferramentas.sql_seguro import ConsultaRecusadaError, validar_cypher, validar_sql
from tests.agentes.conftest import EMPRESA, ID_ANA

VISOES = {"scores", "receita_socios"}


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM scores",
        "DROP VIEW scores",
        "INSERT INTO scores VALUES (1)",
        "COPY scores TO 'saida.csv'",
        "ATTACH 'x.db'",
        "PRAGMA database_list",
        "SET threads = 1",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_parquet('data/silver/x.parquet')",
        "SELECT * FROM read_csv('.env')",
        "SELECT getenv('LUPA_SALT')",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM tabela_secreta",
        "SELECT * FROM glob('*')",
        "CREATE TABLE t AS SELECT 1",
        "SELECT * INTO nova FROM scores",
    ],
)
def test_sql_recusado(sql: str) -> None:
    with pytest.raises(ConsultaRecusadaError):
        validar_sql(sql, VISOES)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM scores",
        "WITH x AS (SELECT entidade FROM scores) SELECT * FROM x",
        "SELECT s.entidade, count(*) FROM scores s JOIN receita_socios r ON true GROUP BY 1",
        "SELECT entidade FROM scores UNION SELECT cnpj_basico FROM receita_socios",
    ],
)
def test_sql_aceito(sql: str) -> None:
    validar_sql(sql, VISOES)


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) SET n.x = 1 RETURN n",
        "CREATE (n:Empresa) RETURN n",
        "MERGE (n:Empresa {id: '1'}) RETURN n",
        "CALL db.labels()",
        "LOAD CSV FROM 'file:///x' AS l RETURN l",
        "MATCH (n) RETURN n; MATCH (m) DELETE m",
        "RETURN 1",
    ],
)
def test_cypher_recusado(cypher: str) -> None:
    with pytest.raises(ConsultaRecusadaError):
        validar_cypher(cypher)


def test_cypher_aceito_ignora_palavras_em_textos() -> None:
    q = "MATCH (e:Empresa {nome: 'SET DELETE LTDA'}) RETURN e.id LIMIT 5"
    assert validar_cypher(q) == q


def test_visoes_escondem_dados_pessoais(dados: Dados) -> None:
    socios = dados.consultar_tabelas(
        "SELECT nome_socio, nome_normalizado, nome_representante, id_pessoa FROM receita_socios"
    )
    assert {tuple(linha[:3]) for linha in socios.linhas} == {(None, None, None)}
    mei = dados.consultar_tabelas(
        "SELECT razao_social FROM receita_empresas WHERE cnpj_basico = '33333333'"
    )
    assert mei.linhas == [["ANA FICTICIA [CPF omitido]"]]
    est = dados.consultar_tabelas(
        "SELECT cnpj, logradouro, numero, endereco_hash FROM receita_estabelecimentos ORDER BY cnpj"
    )
    assert est.linhas[0][1] == "RUA DAS FLORES"
    assert est.linhas[1][1:3] == [None, None]  # MEI: casa do titular
    assert est.linhas[1][3]  # mas o endereço continua comparável por hash
    colunas = {c.split()[0] for t in dados.descrever_tabelas() for c in t["colunas"]}
    assert not colunas & {"telefone_1", "correio_eletronico", "evidencias"}
    pessoas = dados.consultar_tabelas("SELECT nome FROM grafo_no_pessoa")
    assert {linha[0] for linha in pessoas.linhas} == {None}


def test_limite_e_referencia(dados: Dados) -> None:
    r = dados.consultar_tabelas("SELECT * FROM receita_socios", limite=2)
    assert r.truncado and len(r.linhas) == 2
    assert r.referencia["tipo"] == "registro_banco"
    assert dados.consultar_tabelas(r.referencia["sql"], 2).referencia == r.referencia


def test_sinais_saem_anonimizados(dados: Dados) -> None:
    s = dados.sinais_da_entidade("11111111000191")
    ev = s["sinais"][0]["evidencias"][0]
    assert "ANA" not in ev["descricao"] and "BETO" not in ev["descricao"]
    assert "[pessoa ***.456.789-**]" in ev["descricao"]
    assert ev["dados"]["socio"].startswith("[pessoa ")
    assert s["sinais"][0]["nome"]  # metadado do catálogo


def test_perfil_empresa(dados: Dados) -> None:
    p = dados.perfil_entidade(EMPRESA)
    assert p["sinais"] == ["L01"]
    assert len(p["socios"]["linhas"]) == 2
    assert p["vitorias_por_orgao"]["linhas"] == []  # sem PNCP nesta base
    pessoa = dados.perfil_entidade(f"Pessoa:{ID_ANA}")
    assert len(pessoa["sociedades"]["linhas"]) == 2
    assert pessoa["bolsa_familia"]["linhas"] == [["202303", 600.0]]


def test_entidade_invalida() -> None:
    assert entidade_canonica("11.111.111/0001-91") == EMPRESA
    with pytest.raises(EntidadeInvalidaError):
        entidade_canonica("Empresa:1' OR '1'='1")


def test_grafo(dados: Dados) -> None:
    v = dados.vizinhanca_grafo(EMPRESA, saltos=2)
    assert {(o, d) for o, _, _, d, _ in v.linhas} >= {
        (f"Pessoa:{ID_ANA}", EMPRESA), (f"Pessoa:{ID_ANA}", "Empresa:22222222"),
    }  # fmt: skip
    c = dados.caminho_grafo(EMPRESA, "Empresa:22222222")
    assert [p[0] for p in c.linhas] == [EMPRESA, f"Pessoa:{ID_ANA}"]
    assert c.linhas[1][1] is None  # nome da pessoa não sai


def test_cypher_sem_neo4j(dados: Dados, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEO4J_URI", raising=False)
    with pytest.raises(ConsultaRecusadaError, match="Neo4j"):
        dados.consultar_grafo("MATCH (n) RETURN n")


def test_anonimizar_texto() -> None:
    texto = "Sócio JOSÉ DA SILVA (***.123.456-**) e MARIA SOUZA entraram."
    saida = anonimizar_texto(texto, {"MARIA SOUZA": "c" * 32})
    assert saida == "Sócio [pessoa ***.123.456-**] e [pessoa cccccccccccc] entraram."

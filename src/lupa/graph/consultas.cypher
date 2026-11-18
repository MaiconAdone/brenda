// Consultas básicas equivalentes às de consultas.py (Neo4j 5).
// Parâmetros entre $chaves. Ids seguem docs/arquitetura.md (seção Grafo).

// Vizinhança de 2 saltos de uma empresa (CNPJ raiz)
MATCH p = (e:Empresa {id: $cnpj_raiz})-[*1..2]-(x)
RETURN p LIMIT 500;

// Sócios de uma empresa
MATCH (s)-[r:SOCIO_DE]->(e:Empresa {id: $cnpj_raiz})
RETURN labels(s)[0] AS tipo, s.id AS id, s.nome AS nome, r.qualificacao, r.data_entrada;

// Empresas de uma pessoa
MATCH (p:Pessoa {id: $id_pessoa})-[:SOCIO_DE]->(e:Empresa)
RETURN e.id, e.nome;

// Sócios em comum entre duas empresas
MATCH (a:Empresa {id: $cnpj_a})<-[:SOCIO_DE]-(s)-[:SOCIO_DE]->(b:Empresa {id: $cnpj_b})
RETURN s.id, s.nome;

// Mesma pessoa provável (resolução de entidades)
MATCH (p:Pessoa {id: $id_pessoa})-[r:MESMA_ENTIDADE_PROVAVEL]-(o:Pessoa)
RETURN o.id, o.nome, r.probabilidade, r.evidencia ORDER BY r.probabilidade DESC;

// Fornecedores que mais venceram licitações de um órgão
MATCH (f)-[:VENCEU]->(l:Licitacao)-[:PROMOVIDA_POR]->(o:OrgaoPublico {id: $cnpj_orgao})
RETURN f.id, f.nome, count(DISTINCT l) AS licitacoes_vencidas
ORDER BY licitacoes_vencidas DESC LIMIT 50;

// Menor caminho entre dois nós (até 6 saltos)
MATCH (a {id: $id_a}), (b {id: $id_b})
MATCH p = shortestPath((a)-[*..6]-(b))
RETURN p;

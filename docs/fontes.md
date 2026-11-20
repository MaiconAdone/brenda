# Fontes de dados

Verificação feita em **2026-11-17** (Fase 1). Piloto: **Mauá/SP**, IBGE `3529401`,
Receita `6689`, TSE `66893`.

| Fonte | Acesso verificado | Formato / volume | Chave | Módulo |
|---|---|---|---|---|
| IBGE: municípios | `servicodados.ibge.gov.br/api/v1/localidades/estados/{UF}/municipios` | JSON, 645 municípios em SP | não | `ingestion/ibge.py` |
| PNCP: contratações | `pncp.gov.br/api/consulta/v1/contratacoes/publicacao` (filtro `codigoMunicipioIbge`, por modalidade 1–13 e mês) | JSON paginado | não | `ingestion/pncp.py` |
| PNCP: itens e resultados | `pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens[/{n}/resultados]` | JSON | não | `ingestion/pncp.py` |
| PNCP: contratos | `pncp.gov.br/api/consulta/v1/contratos` (filtro `cnpjOrgao`; sem filtro de município) | JSON paginado | não | `ingestion/pncp.py` |
| Receita: CNPJ | `arquivos.receitafederal.gov.br` → link público Nextcloud `index.php/s/gn672Ad4CF8N6TK`, pasta `Dados/Cadastros/CNPJ/AAAA-MM/`, listagem por WebDAV | 37 ZIPs, **~7,8 GB/mês** (Estabelecimentos0.zip sozinho: 2,3 GB) | não | `ingestion/receita_cnpj.py` |
| CGU: CEIS / CNEP | `portaldatransparencia.gov.br/download-de-dados/{ceis,cnep}/AAAAMMDD` → redireciona para `dadosabertos-download.cgu.gov.br` | ZIP/CSV latin-1, ~3,5 MB | não | `ingestion/transparencia.py` |
| CGU: Novo Bolsa Família | `api.portaldatransparencia.gov.br/api-de-dados/novo-bolsa-familia-sacado-beneficiario-por-municipio` | JSON paginado | **sim** | `ingestion/transparencia.py` |
| TSE: candidatos | `cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_{ano}.zip` (catálogo CKAN `dadosabertos.tse.jus.br`) | ZIP por ano, 65–85 MB, um CSV por UF | não | `ingestion/tse.py` |
| TSE: prestação de contas | `cdn.tse.jus.br/.../prestacao_contas/prestacao_de_contas_eleitorais_candidatos_{ano}.zip` | ZIP por ano, **~1,3–1,4 GB** | não | `ingestion/tse.py` |

## Limitações encontradas

1. **CNPJ sem recorte por município.** É preciso baixar ~7,8 GB por mês e filtrar localmente.
   O endereço mudou em 2026: o antigo `.../dados_abertos_cnpj/` dá 404. Agora o acesso é
   pelo compartilhamento Nextcloud. Se o token público mudar, o download falha com mensagem clara.
2. **CNPJ alfanumérico** (desde jul/2026): a validação de CNPJ já aceita letras nas 12
   primeiras posições.
3. **CPF parcial nos sócios.** A Receita publica `***123456**`. A identidade de pessoa usa o
   miolo do CPF + o nome normalizado (ver `docs/lgpd.md`).
4. **TSE não publica CPF de candidatos** (`NR_CPF_CANDIDATO = -4` em 2024). Ligar candidato a
   sócio ou servidor fica dependente de resolução probabilística de entidades (Fase 2), com
   nome + ano de nascimento × faixa etária do QSA. Isso afeta C03 e C04. Doações de PJ têm CNPJ
   e ligam de forma exata.
5. **CEIS/CNEP só têm o arquivo do dia.** Arquivos de datas passadas retornam 403, então não
   dá para reconstruir o histórico de sanções retroativamente. A data disponível é lida da
   página de download.
6. **CEIS publica o CPF completo de pessoas físicas.** O bronze guarda o arquivo oficial
   (local, nunca versionado). No silver o CPF é mascarado e só ficam as PF que são sócias
   ou fornecedoras no piloto.
7. **PNCP: só vencedores.** Os resultados trazem fornecedores homologados, não todas as
   propostas, o que limita o K03. A cobertura também depende da adesão do município ao
   PNCP: contratações anteriores à Lei 14.133 podem faltar.
8. **PNCP: contratos sem filtro de município.** São consultados por CNPJ dos órgãos que
   publicaram contratações em Mauá e depois filtrados pela unidade (`codigoIbge`).
9. **Bolsa Família exige chave e é volumoso.** Cerca de 1.235 páginas de 15 registros por mês
   em Mauá (~18,5 mil saques), de 03/2023 em diante: ~53 mil requisições, cerca de 3,5 horas
   com 0,2 s entre requisições (limite da API: 400/min de dia). A coleta é retomável por mês
   e só guarda quem coincide com sócios, doadores ou fornecedores PF de campanha.
   Não há base aberta de óbitos com identificação para cruzar com beneficiários.
10. **RAIS por estabelecimento não é pública.** O F03 continua inativo.
11. **Servidores municipais** não estão no Portal da Transparência (que é federal). O C01 e o C02
    dependem do portal do TCE-SP ou da prefeitura de Mauá, a avaliar.

## Linhagem e idempotência

- Cada arquivo baixado ou resposta gravada gera um registro em `data/linhagem.jsonl`
  (`lupa.linhagem.Linhagem`) com fonte, URL, data de coleta, tamanho e SHA-256.
- Downloads são gravados em `.part` e retomados com `Range`. Um arquivo completo (mesmo
  tamanho do servidor) não é baixado de novo.
- No PNCP, meses encerrados há mais de 45 dias não são recoletados, e os itens de uma
  contratação só são recoletados se `dataAtualizacaoGlobal` mudou.
- O retry exponencial (até 7 tentativas) cobre 429, 5xx e erros de rede. O intervalo mínimo
  entre requisições é configurável por fonte em `piloto.yaml`.

## Como rodar

```bash
uv run python -m lupa.ingestion                 # todas as etapas
uv run python -m lupa.ingestion ibge pncp       # só algumas
uv run dagster dev -m pipelines.definitions     # pela interface do Dagster
```

Ordem: `ibge` → `pncp` → `receita_cnpj` (precisa dos fornecedores do PNCP) → `sancoes` e
`bolsa_familia` (precisam dos sócios); `tse` é independente.

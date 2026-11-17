# Fontes de dados

> **Estado:** esqueleto da Fase 0. As URLs abaixo são os pontos de partida conhecidos e
> **ainda não foram verificadas**. A verificação (URL atual, formato, tamanho, limites,
> termos de uso) é o primeiro passo da Fase 1, e cada linha passa a ter a data da verificação.

| Fonte | Ponto de partida | Formato | Acesso | Verificado em |
|---|---|---|---|---|
| Receita Federal: CNPJ | https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj | ZIP/CSV mensal (vários GB) | Download livre | — |
| PNCP | https://pncp.gov.br/api/consulta/swagger-ui/index.html | API REST/JSON | Livre, com limite de página | — |
| Portal da Transparência | https://api.portaldatransparencia.gov.br/swagger-ui/index.html | API REST/JSON | Chave gratuita, limite por minuto | — |
| Portal da Transparência: downloads | https://portaldatransparencia.gov.br/download-de-dados | ZIP/CSV | Download livre | — |
| TSE | https://dadosabertos.tse.jus.br/ | ZIP/CSV por eleição | Download livre | — |
| Querido Diário | https://queridodiario.ok.org.br/api/docs | API REST/JSON | Livre | — |
| TCE do estado do piloto | depende da UF | varia | varia | — |
| IBGE: municípios | https://servicodados.ibge.gov.br/api/docs/localidades | API REST/JSON | Livre | — |
| RAIS/CAGED | http://pdet.mte.gov.br/microdados-rais-e-caged | microdados sem identificação | Livre | — |

## Limitações conhecidas (a confirmar na verificação)

1. **CNPJ:** não há download por município; é preciso baixar os arquivos nacionais e filtrar.
   A Receita usa código de município próprio (tabela `Municipios`), diferente do IBGE.
2. **CPF parcial:** o QSA e o Portal da Transparência publicam só os 6 dígitos centrais do CPF.
3. **RAIS:** os microdados públicos não identificam o estabelecimento, o que inviabiliza o F03.
4. **PNCP:** cobertura obrigatória a partir da Lei 14.133/2021; contratações anteriores ou de
   municípios que ainda não publicam podem faltar. Propostas de todos os licitantes nem sempre
   estão disponíveis (afeta o K03).
5. **TSE:** a publicação de CPF de candidatos e doadores mudou ao longo dos anos; confirmar por eleição.
6. **Querido Diário:** cobre um subconjunto de municípios.

## Linhagem

Cada arquivo baixado gera um registro em `data/linhagem.jsonl` (`lupa.linhagem.Linhagem`) com
fonte, URL, data de coleta, tamanho e SHA-256. Um conteúdo com hash já registrado não é
reprocessado.

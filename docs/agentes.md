# Equipe de agentes investigadores (Fase 7)

A equipe recebe os alertas do score, investiga cada caso com as ferramentas da plataforma,
tenta derrubar as próprias hipóteses e entrega um dossiê para **revisão humana**. Os
agentes não substituem o pipeline determinístico: usam os dados e os sinais dele como fonte
da verdade, e não acusam ninguém (ver `docs/etica.md`).

> Estado: **7.1 concluída** (arquivo do caso, ferramentas MCP, validador). Os agentes em si
> entram a partir da 7.2.

## Por que LangGraph (e não OpenClaw, Ruflo ou Claude Agent SDK)

| Opção | O que é | Por que não é a base |
|---|---|---|
| **OpenClaw** | Assistente pessoal autônomo, ligado a apps de mensagem (WhatsApp, Telegram…), com acesso amplo ao computador | Feito para agir em nome de uma pessoa, com shell e navegador. Aqui precisamos do contrário: ferramentas mínimas, só leitura e trilha de auditoria. |
| **Ruflo** (antigo Claude Flow) | Orquestração de enxames de agentes para o Claude Code, em Node | Voltado a desenvolvimento de software (agentes que escrevem código). Não traz máquina de estados auditável nem limites por caso. |
| **Claude Agent SDK** | Laço de agente do Claude Code como biblioteca | Ótimo com MCP, mas centrado no Claude. Ollama e um Cético de outro fornecedor ficariam como adaptação. |
| **LangGraph** (escolhido) | Grafo de estados com ciclos, *checkpoints* e qualquer modelo | O fluxo Investigador ⇄ Cético é um grafo com arestas condicionais, e os limites entram como condições testáveis sem LLM. Funciona com Ollama, Anthropic e OpenAI. |

Como as ferramentas são **servidores MCP**, qualquer cliente MCP (Claude Code, Claude
Desktop, OpenClaw, Ruflo) consegue usá-las para uma investigação manual assistida. A
equipe automática fica no LangGraph, isolada em `lupa.agents.fluxo` (a partir da 7.2).

## Arquitetura

```
alertas (gold) ─► Coordenador ─► Investigador ⇄ Analista documental ─► Cético ─┐
                                      ▲                                         │
                                      └──── objeções não verificadas ◄──────────┤
                                                                                ▼
                                                     Redator ─► revisão humana (UI)
```

Os agentes não conversam entre si. Eles leem e escrevem no **arquivo do caso**
(`src/lupa/casos/`), um quadro-negro em SQLite (`data/casos/casos.sqlite`, fora do git).

### Arquivo do caso

| Tabela | Conteúdo |
|---|---|
| `caso` | entidades em foco, status, prioridade, orçamento e consumo (tokens, passos, ciclos) |
| `hipotese` | enunciado, categoria (F, L, C, K, E, M), status, confiança |
| `evidencia` | referência verificável, trecho, a favor ou contra; inválidas ficam marcadas |
| `objecao` | explicação legítima alternativa, se foi verificada e se procede |
| `evento` | log **só de acréscimo** e encadeado por hash: agente, ferramenta, entrada, saída, tokens, custo, versão do prompt |
| `historico` | versão anterior de cada linha alterada |
| `parecer_humano` | decisão do revisor (vira rótulo para a avaliação) |

**SQLite, não DuckDB:** a UI e os agentes rodam em processos diferentes, e o DuckDB não
deixa um processo ler enquanto outro escreve. O SQL é portável para Postgres. As
migrações ficam em `casos/schema.py` (versão em `PRAGMA user_version`).

### Regras garantidas em código (não dependem do LLM)

- **Regra de ouro:** uma hipótese só fica `sustentada` com pelo menos uma evidência
  válida a favor, com todas as objeções verificadas e com nenhuma explicação legítima
  confirmada. Só o **Cético** pode sustentar. Uma objeção nova devolve a hipótese a
  `proposta`.
- **Permissões por papel** (`PERMISSOES` em `casos/repositorio.py`). O agente que escreve é
  fixado quando o servidor `caso` sobe, então o modelo não consegue se passar por outro.
- **Transições de status** (`TRANSICOES`):
  - Só vai para `pronto_para_humano` um caso com hipótese sustentada.
  - Só o revisor humano coloca um caso em `aprovado`.
  - Um caso fechado precisa ser reaberto antes de ser alterado.
- **Linguagem:** enunciados, objeções, resultados e motivos com "fraude", "corrupto",
  "culpado" (e variações) são recusados (`casos/linguagem.py`). Trechos citados de
  documentos ficam como estão.
- **Auditoria:**
  - Triggers impedem `UPDATE` e `DELETE` no log de eventos.
  - `ArquivoCasos.verificar_log()` recalcula a cadeia de hashes e aponta qualquer evento
    adulterado.

### Ferramentas (servidores MCP)

`python -m lupa.mcp <servidor> --agente <papel>` (stdio). Os servidores que cada papel
recebe estão em `config/agentes.yaml`.

| Servidor | Ferramentas | Quem recebe |
|---|---|---|
| `dados` | `descrever_tabelas`, `consultar_tabelas`, `perfil_entidade`, `sinais_da_entidade`, `vizinhanca_grafo`, `caminho_grafo`, `consultar_grafo` | todos menos o Analista |
| `documentos` | `buscar_diario_oficial`, `listar_documentos_pncp`, `ler_documento` | Investigador, Analista, Cético |
| `caso` | `ler_caso`, `registrar_hipotese`, `registrar_evidencia`, `registrar_objecao`, `verificar_objecao`, `atualizar_hipotese`, `atualizar_status_caso` | Coordenador, Monitor, Investigador, Cético (**nunca** o Analista) |

- **SQL somente leitura:**
  - O texto passa pelo sqlglot e só é aceito um único `SELECT` sobre as visões permitidas.
  - Funções de arquivo, rede e ambiente (`read_*`, `glob`, `getenv`…) são recusadas.
  - A conexão DuckDB fica em memória, com acesso a arquivos só em `silver/` e `gold/` e
    configuração travada. Há limite de linhas e timeout.
- **Grafo:**
  - Por padrão, roda sobre as tabelas de nós e arestas no DuckDB: vizinhança (até 2 saltos,
    sem expandir nós de grau alto) e menor caminho.
  - Com `NEO4J_URI`, `consultar_grafo` aceita Cypher sem `CREATE`, `MERGE`, `DELETE`, `SET`,
    `CALL` etc. e abre a sessão do Neo4j em modo de leitura.
- **Documentos:**
  - Só HTTPS e só os domínios de `dominios_permitidos`, conferidos também depois de
    redirecionamento.
  - Tudo vai para um cache com URL, data e SHA-256.
  - O texto volta dentro de `<documento_nao_confiavel>`, e o marcador de fechamento é
    neutralizado dentro do conteúdo.

### Privacidade (o que um modelo pode ver)

As ferramentas leem os Parquet só pelas visões de `ferramentas/visoes.py`:

- **Pessoa física:** o nome vira nulo; ela é identificada por `id_pessoa` (HMAC) e pelo CPF
  mascarado `***.123.456-**`.
- **MEI:**
  - A razão social perde o CPF que a Receita põe no fim.
  - Logradouro e número viram nulos; o endereço entra como hash comparável.
- **Contatos:** telefone e e-mail não entram.
- **Evidências dos alertas:** só saem por `sinais_da_entidade`, que troca nomes de sócios
  por `[pessoa …]`.

Ficam visíveis:
- **Candidatos, órgãos e empresas:** pelo nome, por serem informação pública.
- **MEI:** o nome do titular continua na razão social, porque é o nome da empresa.

A reidentificação só acontece na revisão humana, com registro em `logs/acesso.jsonl`.

### Validador de evidências

Toda resposta de ferramenta traz uma `referencia`. Antes de gravar uma evidência,
`casos/validador.py` refaz a referência:

| Tipo | Conferência |
|---|---|
| `registro_banco` | roda o SQL de novo; o SHA-256 do resultado tem de bater |
| `consulta_grafo` | chama a mesma ferramenta com os mesmos argumentos; o hash tem de bater |
| `documento` | o documento tem de estar no cache com o mesmo hash e URL, e o trecho citado tem de aparecer no texto |

O que não passa é **descartado** e vira evento `evidencia_descartada`. `revalidar()`
confere de novo todas as evidências (ex.: depois de atualizar os dados); a hipótese que
perde a base volta a `proposta`.

## Como auditar um caso

1. `ArquivoCasos.caso(id)` traz hipóteses, evidências, objeções, eventos e pareceres.
2. `verificar_log()` vazio significa que o log não foi adulterado.
3. Cada evidência traz a consulta ou o documento: rode o SQL nas mesmas visões, ou abra o
   documento pelo `documento_id` em `data/casos/documentos/`.
4. `historico("hipotese", id)` mostra cada versão e quem mudou.

## Limitações conhecidas

- **Querido Diário:**
  - Cobre só Santo André e São Bernardo entre os 7 municípios do ABC.
  - Ficou fora do ar (503) durante os testes.
  - Ausência de resultado não prova ausência de publicação. Os arquivos do PNCP cobrem
    todos os municípios.
- **PDF escaneado:** sem OCR, o texto sai vazio e a ferramenta avisa.
- **Neo4j:** não roda nesta máquina (falta Docker). O modo Cypher está implementado e
  testado só na validação de texto.
- **Validação por hash:** é estrita. Qualquer atualização dos dados invalida evidências de
  consultas cujo resultado mudou, o que é intencional.

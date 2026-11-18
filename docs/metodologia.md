# Metodologia

## Score por regras (Fase 1 do scoring)

```
contribuição(alerta) = peso(sinal) × confiança(alerta)
score_categoria      = min(teto_categoria, Σ contribuições da categoria)
score_total          = Σ score_categoria
```

- **Confiança** é a maior confiança entre as evidências do alerta. Evidências que dependem de
  ligar a mesma pessoa em duas bases herdam o score do Splink.
- **Teto por categoria** evita dupla contagem de sinais correlacionados.
- **Sinais somente combinados** (C04) contam apenas se outro sinal disparar para a mesma
  entidade. Quando estão sozinhos, são listados em `descartados` para transparência.
- Pesos e parâmetros: `config/regras.yaml`.

## Catálogo de sinais

| Id | Sinal | Categoria | Estado | Limitação conhecida |
|---|---|---|---|---|
| F01 | Empresa aberta pouco antes da 1ª vitória | fachada | planejado | — |
| F02 | Capital social baixo × valor contratado | fachada | planejado | Capital social é declaratório |
| F03 | Sem funcionários com contratos relevantes | fachada | **inativo** | RAIS identificada por estabelecimento não é pública |
| F04 | Endereço compartilhado / residencial | fachada | planejado | Centros empresariais e contabilidades geram falso positivo |
| F05 | CNAE incompatível com o objeto | fachada | planejado | Exige mapear texto do objeto para CNAE |
| F06 | Telefone/e-mail compartilhado entre concorrentes | fachada | planejado | Contadores compartilham contato |
| L01 | Sócio beneficiário de programa social | laranja | planejado | Ligação por CPF parcial + nome |
| L02 | Sócio de número anômalo de empresas | laranja | planejado | — |
| L03 | Faixa etária extrema + contratos altos | laranja | planejado | QSA publica só faixa etária |
| L04 | Troca de QSA perto de licitações | laranja | planejado | Exige histórico de snapshots mensais |
| C01 | Sócio é servidor do órgão contratante | conflito | planejado | Servidores municipais não estão na CGU; depende de TCE-SP/prefeitura |
| C02 | Sócio compartilha endereço com servidor | conflito | planejado | Endereço de servidor raramente é público |
| C03 | Doação de campanha ao agente do ente | conflito | planejado | Doador PJ liga por CNPJ; candidato sem CPF publicado (TSE -4) |
| C04 | Sobrenome comum com agente político | conflito | planejado | Sinal fraco, somente combinado |
| K01 | Mesmos concorrentes repetidamente | conluio | planejado | — |
| K02 | Rodízio de vencedores | conluio | planejado | — |
| K03 | Propostas próximas / desconto padronizado | conluio | planejado | PNCP nem sempre traz todas as propostas |
| K04 | Concorrentes com sócios/contatos em comum | conluio | planejado | — |
| E01 | Aditivos acima do limite | execução | planejado | Limite de 25% (50% em reforma), Lei 14.133 art. 125 |
| E02 | Dispensas repetidas ao mesmo fornecedor | execução | planejado | — |
| E03 | Fracionamento abaixo do limite de dispensa | execução | planejado | Limites atualizados anualmente por decreto |
| E04 | Contratada com sanção ativa | execução | planejado | Só há o arquivo do dia; histórico depende de coletas recorrentes |

## Avaliação

- **precisão@k** (k = 10, 50) em amostras revisadas manualmente, por sinal e no ranking geral.
- Cada revisão registra: alerta, revisor (pseudônimo), veredito (procedente / explicação
  legítima / dado errado) e justificativa.
- Meta principal: reduzir falsos positivos.

## Registro de mudanças nos pesos

| Data | Sinal | De → Para | Motivo |
|---|---|---|---|
| 2026-11-17 | todos | — → valores iniciais | Calibração inicial por julgamento, a revisar com precisão@k |

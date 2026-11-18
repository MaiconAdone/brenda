# Metodologia

## Resolução de entidades (pessoas)

Código: `lupa.entity_resolution.pessoas`. Parâmetros: `resolucao_entidades` em
`config/regras.yaml`.

1. **Determinística.** Menções com o mesmo `id_pessoa` (miolo do CPF + nome normalizado) são a
   mesma pessoa. Candidatos sem CPF publicado (TSE 2024) recebem o id `tse-<ano>-<sq>`.
2. **Probabilística (Splink 5, DuckDB).** Pares de pessoas com ids diferentes são comparados em:

   | Comparação | Níveis | m (fixo) |
   |---|---|---|
   | Nome (Jaro-Winkler, com ajuste por frequência do nome) | idêntico / ≥ 0,95 / ≥ 0,88 / diferente | 0,80 / 0,12 / 0,05 / 0,03 |
   | Miolo do CPF | igual / diferente | 0,97 / 0,03 |
   | Idade (ano de nascimento do TSE; faixa etária do QSA) | mesmo ano / compatível / incompatível | 0,85 / 0,12 / 0,03 |

   - As probabilidades **m** são fixadas por julgamento (não há pares rotulados para treinar).
     As **u** são estimadas por amostragem aleatória dos próprios dados, com semente fixa.
   - Probabilidade a priori: `2 × fracao_esperada_duplicatas / (n − 1)`, supondo que 2% das
     pessoas tenham uma duplicata.
   - Blocagem: mesmo miolo; mesmo nome; mesmo primeiro e último nome.
   - Só entram ligações com probabilidade ≥ `limiar_probabilidade` (0,8).
3. **Nunca só pelo nome.** O par precisa ter miolo do CPF nos dois lados, **ou** idade nos dois
   lados **e** nome idêntico/quase idêntico (≥ 0,95). No piloto, aceitar "nome parecido +
   mesmo ano" sem miolo ligou pessoas diferentes que só compartilhavam prenome e último
   sobrenome. Por isso a regra foi endurecida.
4. Cada ligação guarda a probabilidade, o peso de cada comparação (`mw_*`) e uma evidência em
   texto (ex.: "nome quase idêntico; miolo do CPF igual"). Sinais que usam a ligação herdam a
   probabilidade como confiança.

Resultado no piloto (Mauá, 2026-11): 40.368 pessoas, 350 ligações (157 candidatos de 2024 ligados
à candidatura de 2020 por nome e ano de nascimento; 193 por miolo do CPF igual com nome
idêntico ou parecido, como abreviações e nomes de casada).

Limitações:

- Um candidato de 2024 sem CPF só se liga a um sócio por nome + faixa etária, o que fica
  abaixo do limiar (a faixa é larga). Na prática, a ligação passa pela candidatura de 2020,
  que tem CPF.
- A frequência dos nomes é medida nos dados do piloto, não na população. Nomes raros aqui
  podem ser comuns no país, então a probabilidade de nomes "raros" tende a ser otimista.
  Isso é compensado pela exigência de miolo ou idade.
- 22 sócios estrangeiros sem documento ficam fora do grafo.

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

Código: `src/lupa/signals/catalogo/` (um módulo por categoria, uma classe por sinal, teste
em `tests/sinais/`). Execução: `uv run python -m lupa.signals`.

| Id | Sinal | Categoria | Estado | Limitação conhecida |
|---|---|---|---|---|
| F01 | Empresa aberta pouco antes da 1ª vitória (≤ 180 dias) | fachada | **ativo** | Empresas novas vencem legitimamente |
| F02 | Valor vencido ≥ 20× o capital social | fachada | **ativo** | Capital social é declaratório; capital zero fica de fora |
| F03 | Sem funcionários com contratos relevantes | fachada | inativo | RAIS por estabelecimento não é pública |
| F04 | Endereço (prédio) com ≥ 5 empresas ativas | fachada | **ativo** | Centros empresariais e contabilidades geram falso positivo |
| F05 | CNAE incompatível com o objeto | fachada | não implementado | Exige mapear o texto do objeto para CNAE |
| F06 | Telefone/e-mail compartilhado entre fornecedoras do mesmo órgão | fachada | **ativo** | Ignora contatos usados por > 10 empresas (contadores, centrais) |
| L01 | Sócio beneficiário de programa social | laranja | aguardando dados | Bolsa Família ainda não coletado |
| L02 | Sócio PF em número anômalo de empresas (≥ p99 e ≥ 10) | laranja | **ativo** | Holdings e grupos familiares |
| L03 | Sócio de até 20 ou mais de 80 anos + ≥ R$ 500 mil vencidos | laranja | **ativo** | QSA publica só a faixa etária |
| L04 | ≥ 2 sócios entraram até 90 dias antes de uma vitória | laranja | **ativo** | Só a foto atual do QSA; saídas não aparecem |
| C01 | Sócio é agente político eleito no município (parcial) | conflito | **ativo (parcial)** | Servidores municipais não têm fonte aberta; cobre só eleitos |
| C02 | Sócio compartilha endereço com servidor | conflito | não implementado | Sem fonte de servidores |
| C03 | Sócio doou, ou empresa/sócio prestou serviço, à campanha de eleito | conflito | **ativo** | Doação é legal; aponta proximidade |
| C04 | Sobrenome comum com agente político | conflito | não implementado | Sinal fraco; fica para depois |
| K01–K03 | Concorrentes repetidos, rodízio, propostas próximas | conluio | não implementados | O PNCP só publica vencedores |
| K04 | Fornecedoras do mesmo órgão com sócio em comum | conluio | **ativo** | Grupos econômicos; sócio PJ em comum tem confiança 0,5 |
| E01 | Valor global > 125% do inicial | execução | **ativo** | Aproximação sem ler os termos aditivos |
| E02 | ≥ 3 dispensas do mesmo órgão ao mesmo fornecedor em 365 dias | execução | **ativo** | Emergências e pequenas compras distintas |
| E03 | Dispensas por valor (art. 75, I/II) que somam acima do limite em 90 dias | execução | **ativo** | Aproxima "mesma natureza" por órgão + fornecedor |
| E04 | Vitória/contrato com sanção vigente que alcança o contratante | execução | **ativo** | Só sanções ainda listadas no CEIS/CNEP |

São 14 sinais ativos (13 completos e o C01 parcial).

### Filtros comuns

- **Naturezas jurídicas excluídas** dos sinais de fachada, laranja e conluio: órgãos públicos
  (1xxx), empresa pública (2011), economia mista (2038), S.A. aberta (2046) e consórcio de
  sociedades (2151). Na primeira rodada, bancos e estatais apareceram no topo por causa de
  diretores no QSA, agências em prédios compartilhados e centrais de atendimento; consórcios
  são criados para a licitação e têm as consorciadas como sócias.
- **E04 e abrangência da sanção.** Só dispara quando a sanção alcançava o contratante:
  todas as esferas (confiança 1); esfera do sancionador igual à do contratante, com estado
  ou município do piloto (1, ou 0,8 se também restrita ao poder); só no órgão sancionador e
  esse órgão é do município (0,8); sem informação (0,5). Uma sanção federal não alcança um
  órgão municipal, por exemplo.
- **Limites de dispensa (E03)** por ano em `config/regras.yaml`: 2025 (Decreto 12.343/2024)
  e 2026 (Decreto 12.807/2025) conferidos no texto oficial; 2023 e 2024 ainda precisam ser
  conferidos no Planalto.

### Saídas

- `data/gold/alertas/alertas.parquet`: um alerta por linha, com as evidências em JSON.
- `data/gold/alertas/scores.parquet`: score e decomposição por entidade.
- `data/gold/relatorios/top50.md`: ranking com evidências, fontes e limitações, marcado
  como **não revisado** e de uso interno.

## Avaliação

- **precisão@k** (k = 10, 50) em amostras revisadas manualmente, por sinal e no ranking geral.
- Cada revisão registra: alerta, revisor (pseudônimo), veredito (procedente / explicação
  legítima / dado errado) e justificativa.
- Meta principal: reduzir falsos positivos.

## Registro de mudanças nos pesos

| Data | Sinal | De → Para | Motivo |
|---|---|---|---|
| 2026-11-17 | todos | — → valores iniciais | Calibração inicial por julgamento, a revisar com precisão@k |
| 2026-11-18 | F06 | — → `max_empresas_por_contato: 10` | Contatos de contadores e centrais dominavam o sinal |
| 2026-11-18 | L02 | — → `min_empresas: 10` | Piso para o corte por percentil |
| 2026-11-18 | E03 | `margem_abaixo_limite` → `limites` por ano | Limites legais de dispensa por decreto |

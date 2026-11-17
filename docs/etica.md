# Ética e uso responsável

## O que o sistema faz

Aponta **indícios** (padrões nos dados públicos que justificam um olhar mais atento) em
contratações públicas. Não investiga, não julga e não conclui.

## Regras de linguagem

| Use | Não use |
|---|---|
| alerta, indício, sinal de risco | fraude, fraudador, corrupto, culpado |
| "o sinal C01 disparou porque..." | "a empresa X fraudou..." |
| "possível vínculo (confiança 0,82)" | "o dono da empresa é o servidor Y" |

Revisores de código devem recusar PRs que violem esta tabela em UI, relatórios, logs ou nomes
de variáveis.

## Aviso obrigatório

Toda página, relatório e dossiê exibe:

> Alertas são indícios estatísticos para orientar apuração e não constituem acusação.

A constante fica em `lupa.AVISO_LEGAL`. Não duplique o texto: importe a constante.

## Explicabilidade

1. Todo alerta lista os sinais que dispararam, os registros usados e links para a fonte original.
2. Modelos de ML (anomalia, comunidades, supervisionados) são sinais **adicionais**, com
   explicação (SHAP ou equivalente). Um alerta nunca existe só porque um modelo o produziu.
3. Pesos e parâmetros ficam em `config/regras.yaml`, versionados, e cada mudança é
   justificada em `docs/metodologia.md`.

## Homônimos e ligações entre entidades

- Pessoas nunca são ligadas só pelo nome.
- Toda ligação inferida (`MESMA_ENTIDADE_PROVAVEL`) mostra o score de confiança, e esse score
  pondera a contribuição do alerta no score de risco.
- Sinais fracos (ex.: C04, sobrenome em comum) nunca disparam sozinhos.

## Revisão humana

Nenhum dossiê é exportado ou divulgado sem que uma pessoa revise:

1. se as evidências estão corretas na fonte original;
2. se há explicação legítima evidente (ex.: endereço compartilhado por ser um centro empresarial);
3. se a ligação entre pessoas é plausível.

O destino padrão do dossiê revisado é um órgão competente (MP, TCE/TCU, CGU) ou um jornalista
que fará apuração própria. O sistema não publica listas de pessoas físicas.

## Falsos positivos

São o principal risco de dano. A avaliação (`precisão@k` em amostras revisadas) é parte do
produto, não um extra. Uma regra com precisão baixa é desativada ou recalibrada, e a decisão
fica registrada.

## Uso indevido

Não é permitido usar o projeto para assédio, perseguição política, exposição de pessoas físicas
ou qualquer finalidade alheia ao interesse público. A licença AGPL-3.0 garante que versões
modificadas oferecidas como serviço também tenham o código aberto e auditável.

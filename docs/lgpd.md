# LGPD: tratamento de dados pessoais

> Este documento descreve as salvaguardas técnicas do projeto. Ele não substitui parecer
> jurídico. Recomenda-se revisão por profissional antes de qualquer uso em produção.

## Finalidade e base legal

- **Finalidade:** controle social de contratações públicas: identificar indícios de
  irregularidade para encaminhamento, após revisão humana, a órgãos de controle ou à imprensa.
  A finalidade está registrada em `config/piloto.yaml` (`finalidade`).
- **Bases legais consideradas:** art. 7º, §§ 3º e 4º (dados tornados manifestamente públicos,
  respeitando a finalidade, a boa-fé e o interesse público que justificaram a publicação);
  art. 7º, IX (legítimo interesse, com o teste de balanceamento abaixo); Lei de Acesso à
  Informação (Lei 12.527/2011) quanto à natureza pública dos dados.
- **Atividade jornalística:** o art. 4º, II, "a" exclui o tratamento para fins exclusivamente
  jornalísticos, mas o projeto **não** se apoia nessa exceção: aplica as salvaguardas a todos.

## Minimização

| Dado | Tratamento |
|---|---|
| CPF | Só o miolo de 6 dígitos (o que as fontes publicam). Exibido como `***.123.456-**` via `lupa.cleaning.documentos.mascarar_cpf`. |
| Identidade de pessoa | `lupa.privacidade.id_pessoa`: HMAC-SHA256 com sal secreto (`LUPA_SALT`) sobre miolo + nome normalizado. Não é reversível sem o sal. |
| Benefícios sociais | Só de quem é sócio, doador ou fornecedor PF de campanha; o arquivo nacional é apagado depois da leitura, os demais beneficiários não são gravados e o NIS nunca é gravado. Nos sinais de campanha, o alerta recai sobre a candidatura. |
| Faixa etária | Só quando publicada pela fonte (QSA da Receita); nunca data de nascimento. |
| Telefone e e-mail | No grafo viram HMAC com sal (`lupa.privacidade.hash_contato`): permitem achar contatos compartilhados sem guardar o contato. |
| Miolo do CPF na resolução | Lido do CPF mascarado só em memória, durante o Splink; não é gravado à parte. |
| Endereço de pessoa física | Usado para ligação (C02); exibido apenas em dossiê revisado. |

Colunas que não alimentam nenhum sinal não passam da camada bronze para a silver.

## Exposição

- Nenhuma lista pública de pessoas físicas.
- A interface do MVP é de uso interno (portas ligadas só em `127.0.0.1` no Docker Compose).
- Pessoas físicas aparecem em dossiês só após revisão humana.
- A interface exige que a pessoa se identifique antes de abrir qualquer ficha.
- Endereço aparece só como bairro e cidade (em MEI costuma ser a casa do titular); telefone e
  e-mail aparecem só como hash, para mostrar que são compartilhados.

## Registro de acesso

A API grava em `logs/acesso.jsonl` data, método, caminho, status e usuário de cada requisição.
A query string **não** é gravada, para não registrar termos de busca com dados pessoais.
A interface grava no mesmo arquivo quem abriu cada ficha (`ver_entidade`), quem registrou
revisão (`revisar`) e quem gerou dossiê (`gerar_dossie`). Filtros e buscas não são gravados.
As revisões (`data/gold/revisoes/revisoes.jsonl`) guardam o nome de quem revisou, como trilha
de auditoria.
Os logs ficam fora do repositório e devem ser retidos por prazo definido (sugestão: 6 meses,
art. 15 do Marco Civil como referência) e depois apagados.

## Retenção e atualização

- Os dados são recarregados das fontes oficiais. Se a fonte corrigir ou remover um registro, a
  próxima carga reflete a mudança.
- `data/` nunca é versionado (`.gitignore`, hook de pre-commit e verificação no CI).

## Direitos do titular

Um canal de contato (a definir) recebe pedidos de acesso, correção e oposição. Como os dados
vêm de fontes oficiais, correções na origem devem ser pedidas ao órgão responsável; o projeto
reprocessa após a correção.

## Teste de balanceamento (legítimo interesse)

1. **Interesse legítimo:** fiscalização do uso de recursos públicos.
2. **Necessidade:** só os campos usados por sinais; CPF parcial; pseudonimização.
3. **Expectativa do titular:** os dados já são publicados pelo poder público para controle social.
4. **Salvaguardas:** revisão humana, linguagem de indício, log de acesso, não exposição pública.

## Segredos

Chaves de API, sal e senhas ficam só no `.env` (nunca versionado). Use `.env.example` como modelo.

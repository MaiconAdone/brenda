"""Arquivo do caso: o quadro-negro compartilhado pelos agentes investigadores.

Os agentes não conversam entre si: leem e escrevem registros estruturados (caso,
hipóteses, evidências, objeções) e todo passo vira um evento num log só de acréscimo,
encadeado por hash. As regras que não podem depender do LLM (a regra de ouro, as
transições de status, as permissões por agente e a linguagem) ficam aqui, em código.
"""

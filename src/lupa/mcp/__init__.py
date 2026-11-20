"""Servidores MCP com as ferramentas dos agentes investigadores.

Três servidores, para que cada agente receba só o que o seu papel permite:

- ``dados``: SQL nas visões, grafo, perfil e sinais (somente leitura);
- ``documentos``: diários oficiais e arquivos do PNCP (com cache e marcação de não confiável);
- ``caso``: hipóteses, evidências, objeções e status (a única escrita, só no arquivo do caso).

Uso: ``python -m lupa.mcp <dados|documentos|caso> --agente <papel>``.
"""

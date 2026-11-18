"""Ingestão: um módulo por fonte, idempotente e incremental.

Cada módulo expõe funções ``coletar_*`` (fonte → bronze) e ``silver_*`` (bronze → silver).
Downloads e respostas de API vão para ``data/bronze/<fonte>/`` com registro de linhagem;
tabelas normalizadas vão para ``data/silver/<fonte>/`` em Parquet.
"""

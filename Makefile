.PHONY: setup ingest run run-local test lint format typecheck check clean

setup:  ## Instala dependências e hooks
	uv sync --extra api --extra ui --extra orquestracao --extra ml
	uv run pre-commit install
	@test -f .env || (cp .env.example .env && echo "Criado .env a partir de .env.example: preencha LUPA_SALT e a chave da API.")

ingest:  ## Materializa todos os assets (Fase 1 em diante)
	uv run dagster asset materialize -m pipelines.definitions --select '*'

run:  ## Sobe Neo4j, API, UI e orquestrador
	docker compose up --build

run-local:  ## API e UI sem Docker
	uv run uvicorn lupa.api.main:app --reload & uv run streamlit run src/lupa/ui/app.py

test:
	uv run pytest --cov=lupa --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

check: lint typecheck test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage

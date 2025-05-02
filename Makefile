.PHONY: venv install install-dev test lint format clean run docker-build docker-up docker-down docker-test docker-exec

PYTHON := python3
VENV := venv
BIN := $(VENV)/bin

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(BIN)/pip install -r requirements.txt

install-dev: install
	$(BIN)/pip install -r requirements-dev.txt
	$(BIN)/pip install -e .

test: install-dev
	$(BIN)/pytest -v

lint: install-dev
	$(BIN)/black --check src tests
	$(BIN)/isort --check src tests
	$(BIN)/mypy src tests

format: install-dev
	$(BIN)/black src tests
	$(BIN)/isort src tests

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

run: install
	$(BIN)/python -m mssql_mcp_server

# Docker commands
docker-build:
	docker-compose build

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-test:
	docker-compose exec mcp_server pytest -v

docker-exec:
	docker-compose exec mcp_server bash

# Test MSSQL connection
test-connection:
	$(PYTHON) test_connection.py --server $${MSSQL_SERVER:-localhost} --port $${HOST_SQL_PORT:-1434} --user $${MSSQL_USER:-sa} --password $${MSSQL_PASSWORD:-StrongPassword123!} --database $${MSSQL_DATABASE:-master}

# Set environment variables for testing
test-env:
	@echo "Export your database credentials before running tests:"
	@echo "export MSSQL_SERVER=your_server"
	@echo "export MSSQL_PORT=1433"
	@echo "export MSSQL_USER=your_username"
	@echo "export MSSQL_PASSWORD=your_password"
	@echo "export MSSQL_DATABASE=your_database"
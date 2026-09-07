VENV := .venv
PY   := $(VENV)/bin/python

.PHONY: setup models examples test chain demo clean run ui

run:
	./start.sh

ui:
	./start.sh ui


setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

models:
	$(PY) -m faceproof.cli fetch-models

examples:
	$(PY) scripts/fetch_example.py

test:
	$(PY) -m pytest tests/ -q

chain:
	npx --yes ganache@7 --wallet.deterministic --database.dbPath .chaindata \
		--chain.chainId 1337 --server.port 8545

demo: models examples
	./scripts/demo.sh

clean:
	rm -rf out .chaindata .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +

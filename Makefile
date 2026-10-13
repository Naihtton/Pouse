.PHONY: setup dev check test build compose-up compose-down

setup:
	./scripts/setup.sh

dev:
	./scripts/dev.sh

check:
	./scripts/check.sh

test:
	cd backend && ../.venv/bin/python -m pytest -q

build:
	cd frontend && npm run build

compose-up:
	docker compose up --build

compose-down:
	docker compose down


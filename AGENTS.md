# Pouse repository guide

## Project shape

- `backend/` is a FastAPI and SQLAlchemy service. It defaults to SQLite when
  `DATABASE_URL` is unset and uses PostgreSQL through `compose.yaml`.
- `frontend/` is a Next.js 15, React 19, TypeScript, and Phaser application.
- JIB and Advice are the only live retailer sources. Do not add mock offers or
  additional retailers unless the task explicitly requests them.
- Product matching, compatibility, build ranking, and deal scoring are
  deterministic. Do not add an LLM framework to those decisions.

## Setup and commands

- Bootstrap a fresh checkout with `./scripts/setup.sh`.
- Start the local SQLite development stack with `./scripts/dev.sh`.
- Run all offline checks with `./scripts/check.sh`.
- Start the PostgreSQL stack with `docker compose up --build` when Docker is
  available.
- The frontend uses port 3000 and the single API service uses port 8000. Do not
  start a second API on port 8010.

## Working agreements

- Adapt changes to the existing Manager → Scout → Analyst → Builder → Manager
  workflow instead of redesigning it.
- Keep collector and retailer parsing code under `backend/app/collectors/`.
- Build and deal endpoints must use stored inventory. Only explicit refresh or
  collector debug endpoints may contact retailers.
- Keep tests deterministic and fixture-based. The normal test suite must not
  require JIB or Advice to be reachable.
- Preserve current uncommitted work and do not reset or replace unrelated
  changes.

## Verification

- Backend-only changes: run `cd backend && ../.venv/bin/python -m pytest -q`.
- Frontend-only changes: run `cd frontend && npm run typecheck && npm run build`.
- Cross-stack changes: run `./scripts/check.sh`.
- For runtime verification, check `http://localhost:8000/health` and
  `http://localhost:3000` after startup.


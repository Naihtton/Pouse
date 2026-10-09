# Pouse — Virtual AI Office

A first working PC component sourcing office built with Next.js/TypeScript, FastAPI/Python, PostgreSQL, WebSocket and Phaser. Four deterministic agents coordinate a mock sourcing workflow without an LLM framework or live scraping.

## Run the full stack

Install Docker Desktop with Compose, then from this folder:

```powershell
docker compose up --build
```

Open http://localhost:3000. API docs: http://localhost:8000/docs. Database data persists in the `pgdata` volume. Use `docker compose down` to stop without deleting data. The browser connects directly to port 8000.

On a Mac, install and open Docker Desktop, clone this repository, and run the same Compose command from the repository folder. Docker provides Node.js, Python and PostgreSQL; separate runtime installations are optional for development outside containers.

**Verification status:** The initial implementation has not yet been compiled or tested. The Windows authoring environment had no usable Node.js, Python or Docker runtime, and portable runtime downloads did not complete. Run the validation commands below when continuing development.

## Development without Docker

Requires Node.js 22 and Python 3.12. Start PostgreSQL and set `DATABASE_URL` to a SQLAlchemy PostgreSQL URL (`postgresql+psycopg://user:password@localhost:5432/office`). If it is omitted, the backend uses SQLite for a lightweight local run; Compose always uses PostgreSQL.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

For remote deployment, set `NEXT_PUBLIC_API_URL` before building the frontend and `FRONTEND_ORIGIN` on the backend. Use matching HTTPS/WSS origins. V1 is intended for local development; it has no authentication.

## Validation

```powershell
cd backend
python -m pytest -q
```

```powershell
cd frontend
npm run typecheck
npm run build
```

Tests cover delivered-price normalization, stock exclusion, budgets, margins, compatibility, request validation, completed WebSocket workflows and failure workflows. To manually check the UI, submit ฿25,000 / 20% / 1080p gaming, observe all four agents complete, compare offers and refresh to restore the build. A ฿10,000 / 1440p request should show an explicit failure and allow another request.

## Architecture and incremental milestones

1. **Backend core:** typed listings, collector protocol, mock retailers, price normalization and compatible build ranking. Run tests or call functions independently.
2. **API and persistence:** create/read requests, save each agent transition, stream versioned snapshots by WebSocket. Run FastAPI and inspect `/docs`.
3. **Dashboard:** request form, agent statuses, recommendation, costs/profit and activity feed. Run Next.js alongside the API.
4. **Virtual office:** client-only Phaser scene tracks the same agent states. Compose starts the complete stack.

`backend/app/collectors.py` exposes `Collector.collect() -> list[Listing]`. Add a real API/feed collector and register it in `default_collectors()` later. Collector output must use stable product SKUs, normalized specs, THB prices, per-item shipping and stock state. No scraping is implemented.

The workflow is Manager → Market Scout → Market Analyst → PC Builder → Manager. It saves full snapshots to `build_jobs`; WebSocket clients receive changes and reconnect automatically. The last request ID is stored in the browser. Tasks run in-process with a **single API worker**; interrupted requests are marked failed on restart. A durable queue and migrations are future work.

## Pricing and compatibility assumptions

- Every JIB, Advice, Shopee and Lazada listing is synthetic. Prices are not current market quotes.
- Cost includes one CPU, motherboard, discrete GPU, RAM kit, NVMe drive, PSU and case, plus per-item shipping. Stock cooler and case fans are included in the mock catalog.
- Socket, RAM type/capacity, case form factor, GPU clearance and PSU headroom are checked. The curated catalog assumes board BIOS, M.2 slots and power connectors support its parts; real collectors will need richer compatibility metadata.
- Workload scoring favors GPU performance for gaming and CPU/RAM for office work. 1440p requires the mock RX 7700 XT tier. Scores are illustrative, not benchmark claims. Office builds currently include a discrete GPU.
- Margin means `(selling price − cost) / selling price`. Suggested selling price is rounded up to the next baht. Tax, assembly, OS licenses, payment/platform fees and warranty provisions are excluded.

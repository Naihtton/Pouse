# Pouse — Virtual AI Office

A PC component sourcing office built with Next.js/TypeScript, FastAPI/Python, PostgreSQL, WebSocket and Phaser. Four deterministic agents analyze stored live listings from JIB and Advice without an LLM framework.

## Use with Codex

Codex reads the repository instructions in `AGENTS.md`. For a fresh local
checkout or Codex worktree, run:

```bash
./scripts/setup.sh
./scripts/check.sh
./scripts/dev.sh
```

The setup installs the backend into the root `.venv` and installs the locked
frontend dependencies. The development command uses SQLite, starts one API on
port 8000, and starts Next.js on port 3000. PostgreSQL remains available through
Docker Compose.

For a Codex Cloud environment, use `./scripts/setup.sh` as the install command
and `./scripts/dev.sh` as the start command. Package-manager access is enough
for installation. Live price refreshes additionally require outbound HTTPS to
`www.jib.co.th` and `www.advice.co.th`; offline tests do not require retailer
access.

## Run the full stack

Install Docker Desktop with Compose, then from this folder:

```powershell
docker compose up --build
```

Open http://localhost:3000. API docs: http://localhost:8000/docs. Database data persists in the `pgdata` volume. Use `docker compose down` to stop without deleting data. The browser connects directly to port 8000.

On a Mac, install and open Docker Desktop, clone this repository, and run the same Compose command from the repository folder. Docker provides Node.js, Python and PostgreSQL; separate runtime installations are optional for development outside containers.

**Verification status:** Verified on macOS with Docker Compose. `docker compose up --build` starts PostgreSQL, one FastAPI service on port 8000, and Next.js. JIB and Advice are the only enabled sources. Collection is persisted before the four-agent workflow analyzes current data.

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

## Build Intelligence V1

Build requests accept optional `max_cost`, `target_sell_price`, `minimum_margin_pct`, `target_resolution`, CPU-brand/GPU-vendor preferences, and required RAM/storage capacity. `use_case` defaults to `gaming`. The older workload labels and `target_margin`, `minimum_target_margin`, `target_selling_price`, `preferred_cpu`, and `preferred_gpu` fields remain accepted for existing clients. Preferences are required case-insensitive matches when supplied.

The deterministic Builder returns as many as five distinct candidates, including Best Performance, Best Value, and Best Margin when enough builds exist. It first chooses the lowest effective live JIB or Advice offer for each canonical product, then checks CPU socket/platform, motherboard/RAM generation and capacity, motherboard/case fit, GPU clearance when dimensions are known, and PSU capacity with 40% headroom. Every rule returns `pass`, `fail`, or `unknown`; a hard failure or unknown critical power/socket/RAM fact rejects the build. Workload floors prevent undersized builds: gaming requires tier-2 CPU/GPU, 16GB RAM, and 500GB storage; office requires tier-2 CPU, tier-1 GPU, 8GB RAM, and 256GB storage; workstation requires tier-3 CPU, tier-2 GPU, 32GB RAM, and 1TB storage. Resolution raises the gaming GPU floor for 1440p and 4K.

Search uses configurable bounded pools (`BUILD_POOL_LIMIT`, default 8), compatibility-specific branches, and early rejection instead of a full inventory Cartesian product. Ranking uses isolated CPU, GPU, memory, storage, gaming, performance, and value scores plus cost and margin feasibility. `ASSEMBLY_COST` (default ฿500), `PACKAGING_COST` (default ฿150), and `WARRANTY_RESERVE_PCT` (default 2%) control the business cost model. Each result separates parts, shipping, assembly, packaging, warranty reserve, and landed cost.

`POST /builds/recommend` returns recommendations immediately from stored data. `POST /debug/build-search` adds pool sizes, evaluated combination count, rejection reasons, compatibility failures, and final ranking inputs in development. The existing asynchronous `POST /api/builds` Manager workflow uses the same engine. None of these endpoints contacts a retailer.

## Deal Hunter V1

Deal Hunter runs after a successful price refresh or on demand through `POST /admin/run-deal-scan`. It reads only the stored JIB and Advice inventory and price history; a deal scan never calls a collector. `POST /debug/deal-scan` returns scan counts, rejection reasons, targeted build evaluations, and timing in development. `GET /deals` supports `category`, `source`, `grade`, `min_score`, and `active` filters, `GET /deals/{deal_id}` returns the stored detail, and `GET /deals/status` exposes the latest scan state for the UI.

For each retailer listing, source-specific history produces the previous price, 7-day and 30-day minimum and median, all-time observed minimum, percentage changes, observation counts, and a confidence label. Medians remain null until `MIN_HISTORY_POINTS_FOR_MEDIAN` observations exist. Current stale, expired, out-of-stock, zero-price, extreme-drop, and suspicious canonical groups are excluded. Active opportunities update in place; opportunities become resolved when their discount ends and expired when their current offer becomes invalid. A later recurrence creates a new event.

The score is deterministic. Historical discount contributes up to 35 points, change from the previous observation 20, cross-store gap 15, history confidence 10, usefulness in a compatible target build 15, and profit improvement 5. Grades default to good at 50, strong at 70, and exceptional at 90. Build impact is evaluated only for the highest-scoring shortlist (12 by default), using a locked component against three bounded profiles: ฿25,000 1080p, ฿35,000 1080p, and ฿50,000 1440p. A listing with no meaningful target-build impact is capped below the good grade.

Candidate thresholds are configured with `DEAL_MIN_DROP_VS_PREVIOUS_PCT`, `DEAL_MIN_DROP_VS_7D_MEDIAN_PCT`, `DEAL_MIN_DROP_VS_30D_MEDIAN_PCT`, `DEAL_MIN_CROSS_STORE_GAP_PCT`, `DEAL_MIN_NEW_LOW_DROP_PCT`, and `DEAL_MAX_SAFE_SINGLE_DROP_PCT`. The shortlist uses `DEAL_BUILD_IMPACT_MIN_SCORE` and `DEAL_MAX_BUILD_IMPACT_CANDIDATES`. Profile budgets use `DEAL_PROFILE_1080P_BUDGET`, `DEAL_PROFILE_1080P_MID`, and `DEAL_PROFILE_1440P`. Score caps and multipliers use the `DEAL_SCORE_*` variables defined in `app/deal_hunter.py`, and grade boundaries use `DEAL_GRADE_GOOD`, `DEAL_GRADE_STRONG`, and `DEAL_GRADE_EXCEPTIONAL`.

## Architecture and incremental milestones

1. **Backend core:** typed live listings, collector protocol, price normalization and compatible build ranking.
2. **API and persistence:** create/read requests, save each agent transition, stream versioned snapshots by WebSocket. Run FastAPI and inspect `/docs`.
3. **Dashboard:** request form, agent statuses, recommendation, costs/profit and activity feed. Run Next.js alongside the API.
4. **Virtual office:** client-only Phaser scene tracks the same agent states. Compose starts the complete stack.

`backend/app/collectors/` exposes `Collector.collect() -> list[Listing]`. `JibCollector` reads JIB's dedicated public category pages for CPU, VGA, motherboard, desktop RAM, internal 2.5-inch/M.2 SSD, PSU and case, following offset pagination. `AdviceCollector` reads Advice's complete public branch price-list view for each category. Both use normal HTTP requests and BeautifulSoup; Playwright and LLM extraction are not used. Results and collection metadata are cached in process for 15 minutes by default (`COLLECTOR_CACHE_TTL_SECONDS`, or the retailer-specific `JIB_CACHE_TTL_SECONDS` and `ADVICE_CACHE_TTL_SECONDS`). Requests are sequential, delayed, and retried with backoff. JIB fetches at most 10 pages per category by default; configure `JIB_MAX_PAGES_PER_CATEGORY` to lower that limit.

Run `POST /admin/refresh-prices` to collect both sources and persist canonical products, current retailer listings, price history and per-source refresh status. Build requests read the stored listings and do not scrape retailer sites. `GET /admin/refresh-status` reports success, empty and error states. Freshness defaults are 30 minutes for fresh data and 6 hours before expiry; configure them with `MARKET_FRESH_MINUTES` and `MARKET_EXPIRED_HOURS`.

Normal refreshes use stable `source + retailer SKU` rows, identity fingerprints, and indexed category-specific blocking keys. Price-only and stock-only updates retain their canonical product without running the detailed matcher. New or identity-changed listings compare only with canonical candidates in the same block. `POST /admin/rebuild-product-index` performs the slower manual full rebuild when parser or matcher rules change; it preserves retailer listings and price history. A process-level refresh lock prevents overlapping refresh and rebuild jobs.

Development and market endpoints include `GET /debug/jib?category=gpu`, `GET /debug/advice?category=gpu`, `GET /debug/source-coverage`, `GET /debug/match-performance`, `GET /debug/market-match?category=gpu`, `GET /debug/data-quality`, `GET /market/products?category=gpu`, and `POST /debug/normalize`. `GET /debug/match-review?category=case&status=possible&manufacturer=Montech` filters review pairs and returns their blocking key, matching/unknown/conflicting fields, and decision reason. Collector debug output includes reported totals, pages fetched, page size, and truncation state. Cross-store output preserves retailer identities alongside canonical product IDs, effective prices, match confidence and reasons. The Builder excludes out-of-stock offers, unknown shipping, and expired data.

`GET /debug/match-coverage` reports JIB, Advice, matched, possible, and unmatched counts by component category. `GET /debug/match-review?category=cpu` exposes candidate-pair confidence plus matching, unknown, and conflicting fields for tuning. Refreshes safely re-cluster the current inventory with deterministic canonical IDs, while historical price rows remain intact.

Canonical matching is category-specific and conservative. Hard conflicts in model, capacity, speed, kit configuration, chipset, VRAM, DDR generation, or wattage reject a pair before scoring. Only scores of 0.95 or higher share a canonical product; scores from 0.85 through 0.94 are reported as possible matches. Debug output also reports conflicts, price spread, duplicate-retailer groups, missing critical identity fields, and suspicious groups. Suspicious groups are retained for review but excluded from Builder sourcing; groups over a 2x spread are also excluded from market-price selection.

The workflow is Manager → Market Scout → Market Analyst → PC Builder → Manager. It saves full snapshots to `build_jobs`; WebSocket clients receive changes and reconnect automatically. The last request ID is stored in the browser. Tasks run in-process with a **single API worker**; interrupted requests are marked failed on restart. A durable queue and migrations are future work.

## Pricing and compatibility assumptions

- JIB prices and availability come from its public category pages. Advice prices come from its public branch price-list pages and can vary by branch or change at any time. Advice rows do not expose delivery stock or shipping; V1 records active catalog availability and zero-cost store pickup.
- Cost includes one CPU, motherboard, discrete GPU, RAM kit, NVMe drive, PSU and case, plus known per-item shipping.
- Socket, RAM type/capacity, case form-factor fit, GPU clearance and PSU headroom are checked. The current structured source data does not prove BIOS revision, cooler clearance, motherboard storage slots, or exact power connectors, so products missing required deterministic fields are excluded.
- Workload scoring favors GPU performance for gaming and CPU/RAM for office work. Scores are illustrative, and Office builds currently include a discrete GPU.
- Margin means `(selling price − cost) / selling price`. Suggested selling price is rounded up to the next baht. Tax, assembly, OS licenses, payment/platform fees and warranty provisions are excluded.

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .builder import build, normalize, search_builds
from .collectors import AdviceCollector, JibCollector
from .collectors.advice import map_category as map_advice_category
from .collectors.jib import map_category as map_jib_category
from .database import engine, ensure_market_schema, read_job, save_job
from .deal_hunter import deal_scan_status, get_deal, list_deals, run_deal_scan
from .domain import BuildRequest
from .market_store import (data_quality_report, load_latest_listings, market_products,
                           backfill_matching_indexes, match_coverage_report,
                           match_performance_report, match_review, rebuild_product_index,
                           refresh_prices, refresh_status, RefreshInProgressError,
                           source_coverage_report)
from .product_identity import parse_product_identity

AGENTS = ("Manager", "Market Scout", "PC Builder", "Market Analyst")
tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_market_schema()
    backfill_matching_indexes()
    # Recover interrupted work; this V1 deliberately runs one API worker.
    from .database import Session, Job
    with Session.begin() as session:
        for row in session.query(Job).all():
            if row.data["status"] in ("queued", "working"):
                data = dict(row.data)
                data["status"] = "error"
                data["error"] = "Server restarted during this request. Please submit again."
                data["agents"] = {a: "error" if s == "working" else s for a, s in data["agents"].items()}
                row.data = data
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Virtual AI Office", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


class NormalizeRequest(BaseModel):
    category: str
    title: str


def event(data: dict, agent: str, status: str, message: str):
    data["agents"][agent] = status
    data["events"].append({"id": len(data["events"]) + 1, "agent": agent, "message": message,
                           "at": datetime.now(timezone.utc).isoformat()})
    data["revision"] += 1
    save_job(data)


async def run_job(data: dict):
    active = "Manager"
    try:
        request = BuildRequest(**data["request"])
        data["status"] = "working"
        event(data, active, "working", "Request accepted. Assigning component research.")
        await asyncio.sleep(0.5)
        active = "Market Scout"
        event(data, active, "working", "Reading the latest stored live JIB and Advice listings.")
        listings = load_latest_listings()
        if not listings:
            raise ValueError("No fresh live market data is stored. Refresh prices and try again.")
        await asyncio.sleep(0.7)
        live_counts = {
            source: sum(listing.source == source and listing.data_mode == "live" for listing in listings)
            for source in ("JIB", "Advice")
        }
        event(data, active, "completed",
              f"Loaded {len(listings)} stored listings, including {live_counts['JIB']} live JIB and "
              f"{live_counts['Advice']} live Advice listings.")
        active = "Market Analyst"
        event(data, active, "working", "Comparing in-stock offers including per-item shipping.")
        normalized = normalize(listings)
        await asyncio.sleep(0.7)
        event(data, active, "completed", f"Compared prices for {len(normalized)} unique components.")
        active = "PC Builder"
        event(data, active, "working", "Checking compatibility and ranking builds within budget.")
        recommendation = build(request, normalized)
        await asyncio.sleep(0.7)
        event(data, active, "completed",
              f"Ranked {len(recommendation.candidates)} compatible build candidates.")
        active = "Manager"
        # Build jobs are stored in a JSON column, so serialize timestamped live
        # listings at the API boundary before saving the completed snapshot.
        data["result"] = recommendation.model_dump(mode="json")
        data["status"] = "completed"
        event(data, active, "completed",
              "Recommendation ready with live purchase links, selling price, profit, and margin.")
    except Exception as exc:
        logging.exception("Build job failed")
        data["status"] = "error"
        data["error"] = str(exc) if isinstance(exc, ValueError) else "The build failed. Please try again."
        data["agents"]["Manager"] = "error"
        event(data, active, "error", data["error"])


@app.get("/health")
def health():
    from sqlalchemy import text
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/debug/jib")
async def debug_jib(category: str | None = None):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    try:
        collector = JibCollector()
        listings = await collector.collect(category)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"source": "JIB", "category": map_jib_category(category) if category else None,
            "count": len(listings), "coverage": collector.coverage,
            "listings": [listing.model_dump(mode="json") for listing in listings]}


@app.get("/debug/advice")
async def debug_advice(category: str | None = None):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    try:
        collector = AdviceCollector()
        listings = await collector.collect(category)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"source": "Advice", "category": map_advice_category(category) if category else None,
            "count": len(listings), "coverage": collector.coverage,
            "listings": [listing.model_dump(mode="json") for listing in listings]}


@app.get("/debug/source-coverage")
def debug_source_coverage():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return source_coverage_report()


@app.get("/debug/market-match")
async def debug_market_match(category: str | None = None):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    try:
        normalized_category = map_advice_category(category) if category else None
        if category and normalized_category is None:
            raise ValueError(f"Unsupported product category: {category}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    matches = market_products(normalized_category, cross_store_only=True)
    return {"category": normalized_category, "count": len(matches), "matches": matches}


@app.get("/market/products")
def get_market_products(category: str | None = None):
    try:
        normalized_category = map_advice_category(category) if category else None
        if category and normalized_category is None:
            raise ValueError(f"Unsupported product category: {category}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    products = market_products(normalized_category)
    return {"category": normalized_category, "count": len(products), "products": products}


@app.post("/admin/refresh-prices")
async def admin_refresh_prices():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    try:
        return {"sources": await refresh_prices(), "performance": match_performance_report()}
    except RefreshInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/admin/refresh-status")
def admin_refresh_status():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return {"sources": refresh_status()}


@app.get("/debug/data-quality")
def debug_data_quality():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return data_quality_report()


@app.get("/debug/match-coverage")
def debug_match_coverage():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return match_coverage_report()


@app.get("/debug/match-performance")
def debug_match_performance():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return match_performance_report()


@app.post("/admin/rebuild-product-index")
async def admin_rebuild_product_index():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    try:
        return {"performance": await rebuild_product_index()}
    except RefreshInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/debug/match-review")
def debug_match_review(category: str, status: str | None = None,
                       source: str | None = None, manufacturer: str | None = None):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    normalized = map_advice_category(category)
    if normalized is None:
        raise HTTPException(422, f"Unsupported product category: {category}")
    try:
        return match_review(normalized, status, source, manufacturer)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/debug/normalize")
def debug_normalize(request: NormalizeRequest):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    try:
        return parse_product_identity(request.category, request.title).model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _recommend_builds(request: BuildRequest, debug: bool = False):
    listings = load_latest_listings()
    if not listings:
        raise HTTPException(503, "No current live market data is stored. Refresh prices and try again.")
    inventory_debug: dict = {}
    components = normalize(listings, inventory_debug if debug else None)
    try:
        recommendation, diagnostics = search_builds(request, components)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response = {
        "request": request.model_dump(mode="json"),
        "inventory": {
            "stored_listings": len(listings),
            "eligible_canonical_components": len(components),
            "sources": {source: sum((item.source or item.retailer) == source for item in listings)
                        for source in ("JIB", "Advice")},
        },
        "recommendations": [candidate.model_dump(mode="json")
                            for candidate in recommendation.candidates],
    }
    if debug:
        diagnostics.update(inventory_debug)
        response["debug"] = diagnostics
    return response


@app.post("/builds/recommend")
def recommend_builds(request: BuildRequest):
    """Search the stored market snapshot without calling retailer websites."""
    return _recommend_builds(request)


@app.post("/debug/build-search")
def debug_build_search(request: BuildRequest):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return _recommend_builds(request, debug=True)


@app.get("/deals")
def deals(category: str | None = None, source: str | None = None,
          grade: str | None = None, min_score: float = 0, active: bool | None = True):
    normalized_category = map_advice_category(category) if category else None
    if category and normalized_category is None:
        raise HTTPException(422, f"Unsupported product category: {category}")
    normalized_source = ({"jib": "JIB", "advice": "Advice"}.get(source.strip().lower())
                         if source else None)
    if source and normalized_source is None:
        raise HTTPException(422, "source must be JIB or Advice")
    normalized_grade = grade.strip().lower() if grade else None
    if normalized_grade and normalized_grade not in {"weak", "good", "strong", "exceptional"}:
        raise HTTPException(422, "Unsupported deal grade")
    if not 0 <= min_score <= 100:
        raise HTTPException(422, "min_score must be between 0 and 100")
    return {"count": len(rows := list_deals(normalized_category, normalized_source,
                                             normalized_grade, min_score, active)),
            "deals": rows, "scan": deal_scan_status()}


@app.get("/deals/status")
def deals_status():
    return deal_scan_status()


@app.get("/deals/{deal_id}")
def deal_detail(deal_id: str):
    deal = get_deal(deal_id)
    if deal is None:
        raise HTTPException(404, "Deal opportunity not found")
    return deal


@app.post("/admin/run-deal-scan")
def admin_run_deal_scan():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return {"scan": run_deal_scan()}


@app.post("/debug/deal-scan")
def debug_deal_scan():
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(404, "Not found")
    return {"scan": run_deal_scan(), "deals": list_deals(active=True)}


@app.post("/api/builds", status_code=202)
async def create_build(request: BuildRequest):
    data = {"id": str(uuid4()), "request": request.model_dump(), "status": "queued", "revision": 0,
            "agents": {a: "idle" for a in AGENTS}, "events": [], "result": None, "error": None}
    save_job(data)
    task = asyncio.create_task(run_job(data))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return data


@app.get("/api/builds/{job_id}")
def get_build(job_id: str):
    data = read_job(job_id)
    if data is None:
        raise HTTPException(404, "Build request not found")
    return data


@app.websocket("/ws/builds/{job_id}")
async def stream_build(websocket: WebSocket, job_id: str):
    await websocket.accept()
    revision = -1
    try:
        while True:
            data = read_job(job_id)
            if data is None:
                await websocket.close(code=1008, reason="Build request not found")
                return
            if data["revision"] != revision:
                await websocket.send_json(data)
                revision = data["revision"]
            if data["status"] in ("completed", "error"):
                await websocket.close(code=1000)
                return
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass

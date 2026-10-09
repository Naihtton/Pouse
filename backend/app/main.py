import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .builder import build, normalize
from .collectors import default_collectors
from .database import Base, engine, read_job, save_job
from .domain import BuildRequest

AGENTS = ("Manager", "Market Scout", "PC Builder", "Market Analyst")
tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
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
        event(data, active, "working", "Gathering mock listings from JIB, Advice, Shopee and Lazada.")
        listings = [listing for batch in await asyncio.gather(*(c.collect() for c in default_collectors())) for listing in batch]
        await asyncio.sleep(0.7)
        event(data, active, "completed", f"Collected {len(listings)} synthetic listings.")
        active = "Market Analyst"
        event(data, active, "working", "Comparing in-stock offers including per-item shipping.")
        normalized = normalize(listings)
        await asyncio.sleep(0.7)
        event(data, active, "completed", f"Compared prices for {len(normalized)} unique components.")
        active = "PC Builder"
        event(data, active, "working", "Checking compatibility and ranking builds within budget.")
        recommendation = build(request, normalized)
        await asyncio.sleep(0.7)
        event(data, active, "completed", "Compatible build selected.")
        active = "Manager"
        data["result"] = recommendation.model_dump()
        data["status"] = "completed"
        event(data, active, "completed", "Recommendation ready with selling price and estimated profit.")
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

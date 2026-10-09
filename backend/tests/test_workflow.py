import asyncio
import pytest
from fastapi.testclient import TestClient
from app.builder import build, compatible, normalize
from app.collectors import default_collectors
from app.domain import BuildRequest
from app.main import app


def offers():
    async def collect():
        return [item for batch in await asyncio.gather(*(c.collect() for c in default_collectors())) for item in batch]
    return asyncio.run(collect())


def test_comparison_includes_shipping_and_ignores_out_of_stock():
    listings = offers()
    cheapest = next(x for x in listings if x.sku == "r5600" and x.retailer == "Shopee")
    cheapest.in_stock = False
    component = next(x for x in normalize(listings) if x.listing.sku == "r5600")
    assert component.listing.retailer == "Lazada"
    assert component.delivered_price == component.listing.price + component.listing.shipping
    assert len(component.alternatives) == 3


@pytest.mark.parametrize("use_case,budget", [("1080p gaming", 25000), ("1440p gaming", 35000), ("Office & productivity", 20000)])
def test_build_has_all_parts_is_compatible_and_respects_margin(use_case, budget):
    result = build(BuildRequest(max_cost=budget, target_margin=20, use_case=use_case), normalize(offers()))
    assert len(result.components) == 7
    assert result.total_cost <= budget
    assert compatible({c.listing.category: c for c in result.components})
    assert result.total_cost == sum(c.delivered_price for c in result.components)
    assert result.estimated_profit == result.suggested_selling_price - result.total_cost
    assert result.actual_margin >= 20


def test_incompatible_socket_rejected():
    parts = build(BuildRequest(max_cost=25000, target_margin=0, use_case="1080p gaming"), normalize(offers())).components
    mapping = {c.listing.category: c for c in parts}
    mapping["CPU"].listing.specs["socket"] = "AM5"
    assert not compatible(mapping)


def test_insufficient_budget_is_explicit():
    with pytest.raises(ValueError, match="No compatible build"):
        build(BuildRequest(max_cost=10000, target_margin=20, use_case="1440p gaming"), normalize(offers()))


def test_api_validation_and_complete_websocket_workflow():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/api/builds", json={"max_cost": 0, "target_margin": 100, "use_case": "unknown"}).status_code == 422
        response = client.post("/api/builds", json={"max_cost": 25000, "target_margin": 20, "use_case": "1080p gaming"})
        assert response.status_code == 202
        job_id = response.json()["id"]
        with client.websocket_connect(f"/ws/builds/{job_id}") as socket:
            while True:
                job = socket.receive_json()
                if job["status"] in ("completed", "error"):
                    break
        assert job["status"] == "completed"
        assert all(status == "completed" for status in job["agents"].values())
        assert len(job["events"]) == 8
        assert client.get(f"/api/builds/{job_id}").json()["result"] == job["result"]
        assert client.get("/api/builds/missing").status_code == 404


def test_api_failure_reaches_terminal_error():
    with TestClient(app) as client:
        response = client.post("/api/builds", json={"max_cost": 10000, "target_margin": 20, "use_case": "1440p gaming"})
        with client.websocket_connect(f"/ws/builds/{response.json()['id']}") as socket:
            while True:
                job = socket.receive_json()
                if job["status"] in ("completed", "error"):
                    break
        assert job["status"] == "error"
        assert job["agents"]["PC Builder"] == "error"
        assert job["agents"]["Manager"] == "error"

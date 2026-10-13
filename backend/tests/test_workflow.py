import asyncio
from datetime import datetime, timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from app.builder import build, check_compatibility, compatible, normalize, search_builds
from app.collectors import AdviceCollector, CATALOG, JibCollector, default_collectors
from app.collectors.jib import map_category, normalize_brand_model, parse_coverage, parse_price
from app.domain import BuildRequest, Listing
from app.main import app
from app.product_identity import parse_product_identity


def offers():
    rows = []
    for source, offset, shipping in (("JIB", 100, 0), ("Advice", 0, 60)):
        for sku, name, category, price, specs in CATALOG:
            rows.append(Listing(sku=f"{source}-{sku}", name=name, title=name,
                category=category, retailer=source, source=source, price=price + offset,
                shipping=shipping, specs=dict(specs), collected_at=datetime.now(timezone.utc),
                product_identity=parse_product_identity(category, name)))
    return rows


def test_default_collectors_use_live_jib_and_advice_only():
    collectors = default_collectors()
    assert isinstance(collectors[0], JibCollector)
    assert isinstance(collectors[1], AdviceCollector)
    assert [collector.retailer for collector in collectors] == ["JIB", "Advice"]


def test_jib_price_category_and_brand_model_normalization():
    assert parse_price("฿23,900.-") == 23900
    assert parse_price("N/A.-") is None
    assert map_category("VGA / Graphics Card") == "GPU"
    assert map_category("Mainboard") == "Motherboard"
    assert normalize_brand_model("CPU AMD RYZEN 5 9600X") == ("AMD", "Ryzen 5 9600X")
    assert normalize_brand_model("VGA GIGABYTE GEFORCE RTX 5070 GAMING OC 12G") == (
        "Gigabyte", "RTX 5070 Gaming OC 12GB"
    )
    assert normalize_brand_model("VGA GALAX GEFORCE RTX 5070 TI 12GB GDDR7") == (
        "GALAX", "RTX 5070 Ti 12GB GDDR7"
    )


def test_jib_collector_parses_real_product_card_and_skips_malformed_items():
    html = """
    <div class="divboxpro">
      <a href="/web/product/readProduct/52538/1237/cpu"><span class="promo_name">CPU (ซีพียู) AMD RYZEN 5 5600 3.5 GHz (SOCKET AM4)</span></a>
      <div class="buy_promo">ซื้อเลย</div><p class="price_total">4,890.-</p><p>ส่งฟรี</p>
    </div>
    <div class="divboxpro">
      <a href="/web/product/readProduct/99/1237/cpu"><span class="promo_name">CPU AMD RYZEN 5 5600X</span></a>
      <div class="buy_promo">ซื้อเลย</div><p class="price_total">N/A.-</p>
    </div>
    <div class="divboxpro"><span class="promo_name">CPU without a product link</span></div>
    """
    listings = JibCollector.parse_results(html, "cpu")
    assert len(listings) == 1
    assert listings[0].source == "JIB"
    assert listings[0].category == "CPU"
    assert listings[0].brand == "AMD"
    assert listings[0].model == "Ryzen 5 5600 3.5 GHz (Socket AM4)"
    assert listings[0].title == "CPU (ซีพียู) AMD RYZEN 5 5600 3.5 GHz (SOCKET AM4)"
    assert listings[0].price == 4890
    assert listings[0].shipping == 0
    assert listings[0].in_stock is True
    assert listings[0].url == "https://www.jib.co.th/web/product/readProduct/52538/1237/cpu"
    assert listings[0].collected_at is not None


def test_jib_collector_retains_unknown_stock_and_requires_known_shipping_for_comparison():
    html = """
    <div class="divboxpro">
      <a href="/web/product/readProduct/52538/1237/cpu"><span class="promo_name">CPU AMD RYZEN 5 5600 3.5 GHz (SOCKET AM4)</span></a>
      <p class="price_total">4,890.-</p>
    </div>
    """
    listing = JibCollector.parse_results(html, "cpu")[0]
    assert listing.in_stock is None
    assert listing.shipping is None
    assert normalize([listing]) == []


def test_jib_collector_fetches_each_category_with_limited_sequential_requests():
    requested = []

    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(200, text="<html><body>No product cards</body></html>")

    async def collect():
        return await JibCollector(httpx.MockTransport(handler)).collect("gpu")

    assert asyncio.run(collect()) == []
    assert requested == ["/web/product/product_list/2/51/0"]


def test_jib_category_pagination_metadata_and_sku_deduplication(monkeypatch):
    monkeypatch.setenv("JIB_MAX_PAGES_PER_CATEGORY", "3")
    requested = []

    def card(product_id, title):
        return f'''<div class="divboxpro">
          <a href="/web/product/readProduct/{product_id}/53/ram"><span class="promo_name">{title}</span></a>
          <div class="buy_promo">ซื้อเลย</div><p class="price_total">2,490.-</p><p>ส่งฟรี</p>
        </div>'''

    def handler(request):
        requested.append(request.url.path)
        if request.url.path.endswith("/0"):
            return httpx.Response(200, text=(
                "<p>ทั้งหมด 101 รายการ | จำนวน 2 หน้า | หน้าละ 100 รายการ</p>"
                + card(10, "16GB DDR5 KINGSTON FURY BEAST 5600MT/S")
            ))
        return httpx.Response(200, text=(
            card(10, "16GB DDR5 KINGSTON FURY BEAST 5600MT/S")
            + card(11, "32GB DDR5 CORSAIR VENGEANCE 6000MT/S")
        ))

    collector = JibCollector(httpx.MockTransport(handler))
    listings = asyncio.run(collector.collect("ram"))
    assert len(listings) == 2
    assert requested == [
        "/web/product/product_list/2/53/0",
        "/web/product/product_list/2/53/100",
    ]
    assert parse_coverage("ทั้งหมด 101 รายการ | จำนวน 2 หน้า | หน้าละ 100 รายการ") == (
        101, 2, 100
    )
    assert collector.coverage["RAM"] == {
        "source": "JIB", "category": "RAM", "collected": 2,
        "reported_total": 101, "pages_fetched": 2, "page_size": 100,
        "truncated": False, "status": "success",
    }


def test_jib_collector_returns_empty_when_a_category_request_fails():
    def handler(request):
        raise httpx.ConnectTimeout("JIB timed out", request=request)

    async def collect():
        return await JibCollector(httpx.MockTransport(handler)).collect("cpu")

    assert asyncio.run(collect()) == []


def test_real_format_jib_offer_participates_in_builder_price_comparison():
        html = """
        <div class="divboxpro">
            <a href="/web/product/readProduct/1/1/psu"><span class="promo_name">POWER SUPPLY CORSAIR CX650 - 650W 80 PLUS BRONZE BLACK ATX</span></a>
            <div class="buy_promo">ซื้อเลย</div><p class="price_total">1,650.-</p><p>ส่งฟรี</p>
        </div>
        """
        jib_listings = JibCollector.parse_results(html, "psu")
        fixture_offers = [item for item in offers() if item.source == "Advice"]
        components = normalize(jib_listings + fixture_offers)
        psu = next(component for component in components if component.listing.category == "PSU")
        assert psu.listing.retailer == "JIB"
        assert psu.delivered_price == 1650
        result = build(BuildRequest(max_cost=25000, target_margin=20, use_case="1080p gaming"), components)
        selected_psu = next(part for part in result.components if part.listing.category == "PSU")
        assert selected_psu.listing.retailer == "JIB"


def test_jib_debug_endpoint_returns_normalized_category_results(monkeypatch):
    async def fake_collect(self, category=None):
        return JibCollector.parse_results("""
          <div class="divboxpro">
            <a href="/web/product/readProduct/1/1/gpu"><span class="promo_name">VGA GIGABYTE GEFORCE RTX 5070 GAMING OC 12G</span></a>
            <div class="buy_promo">ซื้อเลย</div><p class="price_total">23,900.-</p><p>ส่งฟรี</p>
          </div>
        """, category or "gpu")

    monkeypatch.setattr(JibCollector, "collect", fake_collect)
    with TestClient(app) as client:
        response = client.get("/debug/jib?category=gpu")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "JIB"
    assert data["category"] == "GPU"
    assert data["count"] == 1
    assert data["listings"][0]["brand"] == "Gigabyte"
    assert data["listings"][0]["model"] == "RTX 5070 Gaming OC 12GB"
    assert data["listings"][0]["price"] == 23900
    assert data["listings"][0]["product_identity"]["manufacturer"] == "Gigabyte"
    assert data["listings"][0]["product_identity"]["chipset"] == "RTX 5070"
    assert data["listings"][0]["product_identity"]["identity_key"]


def test_jib_debug_endpoint_rejects_unknown_category():
    with TestClient(app) as client:
        response = client.get("/debug/jib?category=monitor")
    assert response.status_code == 422


def test_normalize_debug_endpoint_returns_product_identity():
    with TestClient(app) as client:
        response = client.post("/debug/normalize", json={
            "category": "gpu",
            "title": "ASUS TUF GAMING GEFORCE RTX 5070 TI 16GB GDDR7 OC EDITION "
                     "(TUF-RTX5070TI-O16G-GAMING)",
        })
    assert response.status_code == 200
    data = response.json()
    assert data["manufacturer"] == "ASUS"
    assert data["model_number"] == "TUF-RTX5070TI-O16G-GAMING"
    assert data["identity_key"] == "asus:tufrtx5070tio16ggaming"


def test_comparison_includes_shipping_and_ignores_out_of_stock():
    listings = offers()
    cheapest = next(x for x in listings if x.sku == "Advice-r5600")
    cheapest.in_stock = False
    component = next(x for x in normalize(listings) if x.listing.product_identity.model_number == "5600")
    assert component.listing.retailer == "JIB"
    assert component.delivered_price == component.listing.price + component.listing.shipping
    assert len(component.alternatives) == 1


@pytest.mark.parametrize("use_case,budget", [("1080p gaming", 25000), ("1440p gaming", 35000), ("Office & productivity", 20000)])
def test_build_has_all_parts_is_compatible_and_respects_margin(use_case, budget):
    result = build(BuildRequest(max_cost=budget, target_margin=20, use_case=use_case), normalize(offers()))
    assert len(result.components) == 7
    assert result.total_cost <= budget
    assert compatible({c.listing.category: c for c in result.components})
    assert result.total_cost == result.candidates[0].landed_cost
    assert result.candidates[0].total_parts_cost == sum(c.listing.price for c in result.components)
    assert result.candidates[0].shipping_cost == sum(
        c.delivered_price - c.listing.price for c in result.components
    )
    assert result.estimated_profit == result.suggested_selling_price - result.total_cost
    assert result.actual_margin >= 20


@pytest.mark.parametrize("use_case", ["gaming", "office", "workstation"])
def test_build_intelligence_returns_ranked_compatible_candidates(use_case):
    result = build(BuildRequest(
        max_cost=40000, minimum_target_margin=20, use_case=use_case
    ), normalize(offers()))
    assert 2 <= len(result.candidates) <= 5
    assert [candidate.rank for candidate in result.candidates] == list(
        range(1, len(result.candidates) + 1)
    )
    assert result.components == result.candidates[0].components
    assert all(candidate.total_cost <= 40000 for candidate in result.candidates)
    assert all(candidate.actual_margin >= 20 for candidate in result.candidates)
    assert all(compatible({part.listing.category: part for part in candidate.components})
               for candidate in result.candidates)
    assert all(part.listing.source in {"JIB", "Advice"}
               for candidate in result.candidates for part in candidate.components)
    assert all(candidate.compatibility_checks for candidate in result.candidates)
    minimums = {
        "gaming": (2, 2, 16, 500),
        "office": (2, 1, 8, 256),
        "workstation": (3, 2, 32, 1000),
    }[use_case]
    for candidate in result.candidates:
        specs = {part.listing.category: part.listing.specs for part in candidate.components}
        assert specs["CPU"]["tier"] >= minimums[0]
        assert specs["GPU"]["tier"] >= minimums[1]
        assert specs["RAM"]["capacity"] >= minimums[2]
        assert specs["Storage"]["capacity"] >= minimums[3]


def test_locked_component_without_required_specs_is_rejected_cleanly():
    components = normalize(offers())
    locked = next(part for part in components if part.listing.category == "GPU")
    locked.listing.canonical_product_id = "locked-gpu"
    locked.listing.specs.pop("tier")

    with pytest.raises(ValueError, match="lacks required compatibility data"):
        search_builds(
            BuildRequest(max_cost=40000, use_case="gaming"),
            components,
            locked_component_id="locked-gpu",
        )


def test_target_selling_price_drives_profit_margin_and_filters_costs():
    result = build(BuildRequest(
        max_cost=40000, target_selling_price=26000,
        minimum_target_margin=20, use_case="gaming",
    ), normalize(offers()))
    for candidate in result.candidates:
        assert candidate.suggested_selling_price == 26000
        assert candidate.estimated_profit == 26000 - candidate.total_cost
        assert candidate.actual_margin >= 20

    with pytest.raises(ValueError, match="No compatible build"):
        build(BuildRequest(
            max_cost=40000, target_selling_price=15000,
            minimum_target_margin=20, use_case="gaming",
        ), normalize(offers()))


def test_preferred_cpu_and_gpu_are_enforced_deterministically():
    result = build(BuildRequest(
        max_cost=40000, minimum_target_margin=10, use_case="gaming",
        preferred_cpu="Ryzen 7 5700G", preferred_gpu="RX 7600",
    ), normalize(offers()))
    for candidate in result.candidates:
        selected = {part.listing.category: part.listing.name for part in candidate.components}
        assert "5700G" in selected["CPU"]
        assert "RX 7600" in selected["GPU"]

    vendor_result = build(BuildRequest(
        max_cost=40000, use_case="gaming", preferred_cpu_brand="AMD",
        preferred_gpu_vendor="AMD",
    ), normalize(offers()))
    for candidate in vendor_result.candidates:
        selected = {part.listing.category: part for part in candidate.components}
        assert selected["CPU"].listing.product_identity.manufacturer == "AMD"
        assert selected["GPU"].listing.product_identity.gpu_vendor == "AMD"


def test_builder_uses_only_live_jib_and_advice_offers():
    rows = offers()
    fake = rows[0].model_copy(deep=True)
    fake.sku = "Mock-cheapest"
    fake.retailer = fake.source = "Mock"
    fake.price = 1
    components = normalize(rows + [fake])
    assert all(component.listing.retailer in {"JIB", "Advice"} for component in components)


def test_case_category_accessories_are_not_build_components():
    rows = offers()
    accessory = rows[-1].model_copy(deep=True)
    accessory.sku = "ADVICE-FAN-SPLITTER"
    accessory.name = accessory.title = "ARCTIC 4-PIN PWM FAN SPLITTER CABLE"
    accessory.price = 100
    accessory.specs = {}
    components = normalize(rows + [accessory])
    assert all("FAN SPLITTER" not in component.listing.name for component in components)


def test_case_form_factor_is_retained_when_clearance_is_unknown():
    listing = AdviceCollector.parse_results("""
      <table><tr data-stock="true">
        <td><a href="/nescen-n001/product/productdetail/A0182214">MINI-ITX CASE VIKINGS VK4</a></td>
        <td class="legacy-price-value">490</td>
      </tr></table>
    """, "case")[0]
    assert listing.specs["form"] == "MINI-ITX"
    assert "gpu_clearance" not in listing.specs


def test_incompatible_socket_rejected():
    parts = build(BuildRequest(max_cost=25000, target_margin=0, use_case="1080p gaming"), normalize(offers())).components
    mapping = {c.listing.category: c for c in parts}
    mapping["CPU"].listing.specs["socket"] = "AM5"
    assert not compatible(mapping)


def _compatible_fixture_parts():
    result = build(BuildRequest(max_cost=30000, use_case="gaming"), normalize(offers()))
    return {part.listing.category: part.model_copy(deep=True) for part in result.components}


def test_structured_socket_and_ram_compatibility_results():
    parts = _compatible_fixture_parts()
    result = check_compatibility(parts)
    assert result.compatible
    assert next(check for check in result.checks if check.rule == "cpu_socket").status == "pass"
    assert next(check for check in result.checks if check.rule == "ram_generation").status == "pass"

    parts["Motherboard"].listing.specs["socket"] = "AM5"
    result = check_compatibility(parts)
    assert not result.compatible
    assert next(check for check in result.checks if check.rule == "cpu_socket").status == "fail"

    parts = _compatible_fixture_parts()
    parts["RAM"].listing.specs["memory"] = "DDR5"
    result = check_compatibility(parts)
    assert not result.compatible
    assert next(check for check in result.checks if check.rule == "ram_generation").status == "fail"


def test_structured_case_psu_and_unknown_gpu_clearance_rules():
    parts = _compatible_fixture_parts()
    parts["Motherboard"].listing.specs["form"] = "mATX"
    parts["Case"].listing.specs["form"] = "ATX"
    assert check_compatibility(parts).compatible

    parts["Motherboard"].listing.specs["form"] = "ATX"
    parts["Case"].listing.specs["form"] = "mATX"
    result = check_compatibility(parts)
    assert not result.compatible
    assert next(check for check in result.checks if check.rule == "case_form_factor").status == "fail"

    parts = _compatible_fixture_parts()
    parts["PSU"].listing.specs["watts"] = 100
    result = check_compatibility(parts)
    assert not result.compatible
    assert next(check for check in result.checks if check.rule == "psu_capacity").status == "fail"

    parts = _compatible_fixture_parts()
    parts["GPU"].listing.specs.pop("length")
    result = check_compatibility(parts)
    assert result.compatible
    assert next(check for check in result.checks if check.rule == "gpu_case_clearance").status == "unknown"


def test_business_cost_profit_and_debug_diagnostics(monkeypatch):
    monkeypatch.setenv("ASSEMBLY_COST", "500")
    monkeypatch.setenv("PACKAGING_COST", "200")
    monkeypatch.setenv("WARRANTY_RESERVE_PCT", "2")
    request = BuildRequest(max_cost=40000, target_sell_price=39900,
                           minimum_margin_pct=10, use_case="gaming",
                           target_resolution="1080p")
    recommendation, debug = search_builds(request, normalize(offers()))
    candidate = recommendation.candidates[0]
    assert candidate.landed_cost == (candidate.total_parts_cost + candidate.shipping_cost
                                     + candidate.assembly_cost + candidate.packaging_cost
                                     + candidate.warranty_reserve)
    assert candidate.estimated_profit == 39900 - candidate.landed_cost
    assert candidate.actual_margin == round(candidate.estimated_profit / 39900 * 100, 2)
    assert candidate.required_sell_price >= candidate.landed_cost
    assert debug["combinations_evaluated"] > 0
    assert debug["candidate_pool_sizes"]
    assert debug["ranking_inputs"]
    badges = {badge for item in recommendation.candidates for badge in item.badges}
    assert badges >= {"Best Overall", "Best Performance", "Best Value", "Best Margin"}
    core_sets = [tuple(part.listing.sku for part in item.components
                       if part.listing.category in {"CPU", "GPU", "RAM", "Storage"})
                 for item in recommendation.candidates]
    assert len(set(core_sets)) == len(core_sets)


def test_direct_build_and_debug_endpoints_use_stored_inventory(monkeypatch):
    monkeypatch.setattr("app.main.load_latest_listings", offers)
    request = {"max_cost": 30000, "use_case": "gaming", "target_resolution": "1080p"}
    with TestClient(app) as client:
        response = client.post("/builds/recommend", json=request)
        debug = client.post("/debug/build-search", json=request)
    assert response.status_code == 200
    body = response.json()
    assert len(body["recommendations"]) >= 3
    assert body["inventory"]["sources"] == {"JIB": 13, "Advice": 13}
    assert all(item["compatibility"]["compatible"] for item in body["recommendations"])
    assert debug.status_code == 200
    assert debug.json()["debug"]["combinations_evaluated"] > 0


def test_insufficient_budget_is_explicit():
    with pytest.raises(ValueError, match="No compatible build"):
        build(BuildRequest(max_cost=10000, target_margin=20, use_case="1440p gaming"), normalize(offers()))


def test_api_validation_and_complete_websocket_workflow(monkeypatch):
    monkeypatch.setattr("app.main.load_latest_listings", offers)
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


def test_api_workflow_serializes_timestamped_jib_listing(monkeypatch):
    jib_psu = JibCollector.parse_results("""
              <div class="divboxpro">
                <a href="/web/product/readProduct/1/1/psu"><span class="promo_name">POWER SUPPLY CORSAIR CX650 - 650W 80 PLUS BRONZE BLACK ATX</span></a>
                <div class="buy_promo">ซื้อเลย</div><p class="price_total">1,650.-</p><p>ส่งฟรี</p>
              </div>
            """, "psu")
    fixture_rows = [row for row in offers() if row.category != "PSU"] + jib_psu
    monkeypatch.setattr("app.main.load_latest_listings", lambda: fixture_rows)
    with TestClient(app) as client:
        response = client.post("/api/builds", json={
            "max_cost": 25000, "target_margin": 20, "use_case": "1080p gaming"
        })
        with client.websocket_connect(f"/ws/builds/{response.json()['id']}") as socket:
            while True:
                job = socket.receive_json()
                if job["status"] in ("completed", "error"):
                    break
    assert job["status"] == "completed"
    psu = next(part for part in job["result"]["components"] if part["listing"]["category"] == "PSU")
    assert psu["listing"]["retailer"] == "JIB"
    assert isinstance(psu["listing"]["collected_at"], str)


def test_api_failure_reaches_terminal_error(monkeypatch):
    monkeypatch.setattr("app.main.load_latest_listings", offers)
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

import asyncio

import httpx
from fastapi.testclient import TestClient

from app.builder import normalize
from app.collectors import AdviceCollector, JibCollector
from app.collectors.advice import (
    extract_product_id,
    map_category,
    parse_pagination,
    parse_price,
)
from app.main import app
from app.product_identity import match_products, parse_product_identity


SAMPLE_HTML = """
<table><tbody>
  <tr>
    <td class="catalog-product-info">
      <a href="/nescen-n001/product/productdetail/A0179591">RTX5070TI/16GB GIGABYTE GAMING (OC/D7)</a>
      <div class="catalog-product-spec">GeForce RTX 5070 Ti, 16GB GDDR7</div>
    </td>
    <td class="catalog-product-price"><del>฿44,900</del><strong class="sale-price">฿43,600</strong></td>
  </tr>
  <tr data-stock="unknown">
    <td class="catalog-product-info">
      <a href="/nescen-n001/product/productdetail/A0123000">ASUS DUAL-RTX5070-O12G</a>
      <div class="catalog-product-spec">12GB GDDR7</div>
    </td>
    <td class="catalog-product-price">฿28,900</td>
  </tr>
  <tr><td class="catalog-product-info"><a href="/bad/url">Malformed</a></td></tr>
  <tr><td class="catalog-product-info"><a href="/nescen-n001/product/productdetail/A0999999">No price</a></td></tr>
</tbody></table>
<nav class="pagination-wrap"><button>1</button><button>2</button><button>8</button></nav>
"""

PRICE_LIST_HTML = """
<table><tbody>
  <tr>
    <td><a href="/nescen-n001/product/productdetail/A0182470">AMD RYZEN 5 9600X</a><small>Box</small></td>
    <td>6 Cores, Socket AM5</td>
    <td class="legacy-price-value text-end">฿7,790</td><td>3 ปี</td>
  </tr>
  <tr><td><a href="/nescen-n001/product/productdetail/A0182471">Malformed price</a></td>
      <td>Unknown</td><td class="legacy-price-value">สอบถามราคา</td></tr>
</tbody></table>
"""


def test_advice_price_category_sku_and_pagination_helpers():
    assert parse_price("฿43,600") == 43600
    assert parse_price("price unavailable") is None
    assert map_category("VGA") == "GPU"
    assert map_category("Power Supply") == "PSU"
    assert map_category("SSD") == "Storage"
    assert extract_product_id(
        "https://www.advice.co.th/nescen-n001/product/productdetail/A0179591"
    ) == "A0179591"
    assert parse_pagination(SAMPLE_HTML) == 8


def test_advice_parses_sale_regular_price_identity_and_malformed_rows():
    listings = AdviceCollector.parse_results(SAMPLE_HTML, "gpu")
    assert len(listings) == 2
    sale, unknown_stock = listings
    assert sale.sku == "ADVICE-A0179591"
    assert sale.source == "Advice"
    assert sale.data_mode == "live"
    assert sale.category == "GPU"
    assert sale.title == "RTX5070TI/16GB GIGABYTE GAMING (OC/D7)"
    assert sale.price == 43600
    assert sale.regular_price == 44900
    assert sale.shipping == 0
    assert sale.in_stock is True
    assert sale.product_identity.manufacturer == "Gigabyte"
    assert sale.product_identity.chipset == "RTX 5070 Ti"
    assert sale.product_identity.variant == "Gaming OC"
    assert sale.product_identity.vram_gb == 16
    assert sale.product_identity.memory_type == "GDDR7"
    assert sale.product_identity.model_number is None
    assert unknown_stock.in_stock is None


def test_advice_parses_complete_legacy_price_list_and_skips_malformed_rows():
    listings = AdviceCollector.parse_results(PRICE_LIST_HTML, "cpu")
    assert len(listings) == 1
    assert listings[0].sku == "ADVICE-A0182470"
    assert listings[0].price == 7790
    assert listings[0].specs["retailer_specification"] == "6 Cores, Socket AM5"


def test_advice_collection_reports_full_price_list_coverage():
    requested = []

    def handler(request):
        requested.append(dict(request.url.params))
        return httpx.Response(200, text=PRICE_LIST_HTML)

    collector = AdviceCollector(httpx.MockTransport(handler))
    listings = asyncio.run(collector.collect("cpu"))
    assert len(listings) == 1
    assert requested == [{"cate": "computer-hardware", "sub": "00023"}]
    assert collector.coverage["CPU"]["reported_total"] == 2
    assert collector.coverage["CPU"]["collected"] == 1
    assert collector.coverage["CPU"]["truncated"] is False


def test_advice_collector_returns_empty_on_network_failure():
    def handler(request):
        raise httpx.ConnectTimeout("Advice timed out", request=request)

    result = asyncio.run(AdviceCollector(httpx.MockTransport(handler)).collect("cpu"))
    assert result == []


def test_shared_normalizer_matches_exact_model_and_rejects_variants():
    jib = parse_product_identity(
        "gpu", "ASUS DUAL GEFORCE RTX 5070 12GB GDDR7 OC EDITION (DUAL-RTX5070-O12G)"
    )
    advice = parse_product_identity("gpu", "ASUS DUAL-RTX5070-O12G")
    assert match_products(jib, advice).matched is True
    assert match_products(
        parse_product_identity("gpu", "ASUS DUAL RTX 5070 12GB GDDR7"),
        parse_product_identity("gpu", "ASUS PRIME RTX 5070 12GB GDDR7"),
    ).matched is False
    assert match_products(
        parse_product_identity("gpu", "MSI VENTUS RTX 5060 TI 8GB GDDR7"),
        parse_product_identity("gpu", "MSI VENTUS RTX 5060 TI 16GB GDDR7"),
    ).matched is False


def test_builder_can_choose_advice_from_cross_store_group():
    advice = AdviceCollector.parse_results(SAMPLE_HTML, "gpu")[1]
    advice.in_stock = True
    jib = JibCollector.parse_results("""
      <div class="divboxpro">
        <a href="/web/product/readProduct/1/1/gpu"><span class="promo_name">VGA ASUS GEFORCE RTX 5070 12GB GDDR7 OC EDITION (DUAL-RTX5070-O12G)</span></a>
        <div class="buy_promo">ซื้อเลย</div><p class="price_total">29,900.-</p><p>ส่งฟรี</p>
      </div>
    """, "gpu")[0]
    components = normalize([jib, advice])
    assert len(components) == 1
    assert components[0].listing.source == "Advice"
    assert len(components[0].alternatives) == 2


def test_advice_debug_endpoint(monkeypatch):
    async def fake_collect(self, category=None):
        return AdviceCollector.parse_results(SAMPLE_HTML, category or "gpu")

    monkeypatch.setattr(AdviceCollector, "collect", fake_collect)
    with TestClient(app) as client:
        response = client.get("/debug/advice?category=gpu")
    assert response.status_code == 200
    assert response.json()["source"] == "Advice"
    assert response.json()["count"] == 2


def test_market_match_endpoint_uses_live_collectors_and_shared_matching(monkeypatch):
    advice_listing = AdviceCollector.parse_results(SAMPLE_HTML, "gpu")[1]
    advice_listing.in_stock = True
    jib_listing = JibCollector.parse_results("""
      <div class="divboxpro">
        <a href="/web/product/readProduct/1/1/gpu"><span class="promo_name">VGA ASUS GEFORCE RTX 5070 12GB GDDR7 OC EDITION (DUAL-RTX5070-O12G)</span></a>
        <div class="buy_promo">ซื้อเลย</div><p class="price_total">29,900.-</p><p>ส่งฟรี</p>
      </div>
    """, "gpu")[0]

    match = match_products(jib_listing.product_identity, advice_listing.product_identity)
    monkeypatch.setattr("app.main.market_products", lambda category, cross_store_only: [{
        "canonical_product_id": "fixture", "canonical_name": advice_listing.product_identity.canonical_name,
        "offers": [{"source": "JIB", "price": 29900}, {"source": "Advice", "price": 28900}],
        "best_source": "Advice", "best_sources": ["Advice"], "best_price": 28900,
        "match": match.model_dump(),
    }])
    with TestClient(app) as client:
        response = client.get("/debug/market-match?category=gpu")
    data = response.json()
    assert response.status_code == 200
    assert data["count"] == 1
    assert data["matches"][0]["best_source"] == "Advice"
    assert {row["source"] for row in data["matches"][0]["offers"]} == {"JIB", "Advice"}

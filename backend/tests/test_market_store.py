import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import market_store
from app.database import (Base, CanonicalProduct, MatchCandidate, PriceHistory,
                          RetailerListing, SourceCoverage)
from app.domain import Listing
from app.product_identity import parse_product_identity
from app.market_quality import assess_listing_group
from app.builder import normalize


def isolated_store(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(engine)
    monkeypatch.setattr(market_store, "Session", session)
    return session


def gpu(source, sku, title, price, shipping=0, collected_at=None):
    return Listing(sku=sku, name=title, title=title, category="GPU", retailer=source,
        source=source, price=price, shipping=shipping, in_stock=True, specs={},
        collected_at=collected_at or datetime.now(timezone.utc),
        product_identity=parse_product_identity("gpu", title))


def test_persistence_canonicalizes_cross_store_products_and_deduplicates_history(monkeypatch):
    session = isolated_store(monkeypatch)
    timestamp = datetime.now(timezone.utc)
    listings = [
        gpu("JIB", "JIB-1", "ASUS DUAL GEFORCE RTX 3050 6GB GDDR6 OC EDITION (DUAL-RTX3050-O6G)", 8990, 0, timestamp),
        gpu("Advice", "ADVICE-A1", "RTX3050/6GB ASUS DUAL O6G (OC/D6) 6GB GDDR6", 8390, 0, timestamp),
    ]
    market_store.persist_listings(listings)
    market_store.persist_listings(listings)
    with session() as db:
        assert db.scalar(select(func.count()).select_from(CanonicalProduct)) == 1
        assert db.scalar(select(func.count()).select_from(PriceHistory)) == 2
    products = market_store.market_products("GPU", cross_store_only=True)
    assert len(products) == 1
    assert products[0]["best_source"] == "Advice"
    assert products[0]["best_price"] == 8390
    assert products[0]["match"]["confidence"] >= .95
    assert len({offer["retailer_identity"]["identity_key"] for offer in products[0]["offers"]}) == 2


def test_equal_effective_prices_report_both_best_sources(monkeypatch):
    isolated_store(monkeypatch)
    title = "GIGABYTE GEFORCE RTX 5060 WINDFORCE OC 8GB GDDR7"
    market_store.persist_listings([
        gpu("JIB", "JIB-2", title, 15500, 0),
        gpu("Advice", "ADVICE-A2", title, 15400, 100),
    ])
    product = market_store.market_products("GPU", cross_store_only=True)[0]
    assert product["best_source"] is None
    assert product["best_sources"] == ["Advice", "JIB"]
    assert product["best_price"] == 15500


def test_freshness_thresholds(monkeypatch):
    monkeypatch.setenv("MARKET_FRESH_MINUTES", "30")
    monkeypatch.setenv("MARKET_EXPIRED_HOURS", "6")
    now = datetime.now(timezone.utc)
    assert market_store.freshness_at(now - timedelta(minutes=10), now) == "fresh"
    assert market_store.freshness_at(now - timedelta(hours=2), now) == "stale"
    assert market_store.freshness_at(now - timedelta(hours=7), now) == "expired"


def test_refresh_status_distinguishes_collector_error_from_empty(monkeypatch):
    isolated_store(monkeypatch)

    class Collector:
        retailer = "JIB"
        last_error = "network timeout"

        async def collect(self):
            return []

    result = asyncio.run(market_store.refresh_prices([Collector()]))
    assert result[0]["status"] == "error"
    assert result[0]["message"] == "network timeout"


def test_refresh_persists_category_source_coverage(monkeypatch):
    session = isolated_store(monkeypatch)

    class Collector:
        retailer = "JIB"
        last_error = None
        coverage = {"RAM": {"source": "JIB", "category": "RAM", "collected": 0,
            "reported_total": 129, "pages_fetched": 2, "page_size": 100,
            "truncated": False, "status": "empty"}}

        async def collect(self):
            return []

    asyncio.run(market_store.refresh_prices([Collector()]))
    with session() as db:
        row = db.get(SourceCoverage, ("JIB", "RAM"))
        assert row.reported_total == 129
        assert row.pages_fetched == 2
    report = market_store.source_coverage_report()
    assert report["JIB"]["RAM"]["reported_total"] == 129
    assert report["JIB"]["RAM"]["status"] == "empty"


def test_suspicious_price_group_is_flagged_and_builder_excludes_it():
    rows = [gpu("Advice", "A1", "ASUS DUAL RTX 5060 8GB GDDR7", 10000),
            gpu("Advice", "A2", "ASUS DUAL RTX 5060 8GB GDDR7", 26000)]
    for row in rows:
        row.effective_price = row.price
        row.freshness = "fresh"
    quality = assess_listing_group(rows)
    assert quality["suspicious"]
    assert quality["same_retailer_price_spread"]
    assert quality["exclude_from_market_median"]
    assert normalize(rows) == []


def test_incremental_price_stock_and_unchanged_updates_skip_matching(monkeypatch):
    session = isolated_store(monkeypatch)
    listing = gpu("JIB", "JIB-STABLE", "ASUS DUAL RTX 5060 8GB GDDR7", 15000)
    first = market_store._persist_listings_incremental([listing], "JIB")
    with session() as db:
        original_product = db.get(RetailerListing, "JIB:JIB-STABLE").canonical_product_id
    listing.price = 14500
    listing.in_stock = False
    listing.collected_at = listing.collected_at + timedelta(minutes=1)
    price_only = market_store._persist_listings_incremental([listing], "JIB")
    assert price_only["updated_listings"] == 1
    assert price_only["identity_changes"] == 0
    assert price_only["pair_comparisons"] == 0
    listing.collected_at = listing.collected_at + timedelta(minutes=1)
    unchanged = market_store._persist_listings_incremental([listing], "JIB")
    assert unchanged["unchanged_listings"] == 1
    assert unchanged["pair_comparisons"] == 0
    with session() as db:
        row = db.get(RetailerListing, "JIB:JIB-STABLE")
        assert row.canonical_product_id == original_product
        assert row.price == 14500 and row.stock is False


def test_incremental_reuses_match_and_splits_identity_change(monkeypatch):
    session = isolated_store(monkeypatch)
    jib = gpu("JIB", "JIB-1", "ASUS DUAL RTX 5060 8GB GDDR7", 15000)
    advice = gpu("Advice", "ADV-1", "ASUS DUAL RTX 5060 8GB GDDR7", 14900)
    market_store._persist_listings_incremental([jib], "JIB")
    reused = market_store._persist_listings_incremental([advice], "Advice")
    assert reused["candidate_pairs"] == reused["pair_comparisons"] == 1
    assert reused["canonical_products_reused"] == 1
    with session() as db:
        assert db.get(RetailerListing, "JIB:JIB-1").canonical_product_id == db.get(
            RetailerListing, "Advice:ADV-1"
        ).canonical_product_id

    advice.title = advice.name = "ASUS TUF GAMING RTX 5060 8GB GDDR7"
    advice.product_identity = parse_product_identity("gpu", advice.title)
    advice.collected_at = advice.collected_at + timedelta(minutes=1)
    changed = market_store._persist_listings_incremental([advice], "Advice")
    assert changed["identity_changes"] == 1
    assert changed["canonical_products_created"] == 1
    with session() as db:
        assert db.get(RetailerListing, "JIB:JIB-1").canonical_product_id != db.get(
            RetailerListing, "Advice:ADV-1"
        ).canonical_product_id
        rejection = db.scalar(select(MatchCandidate).where(MatchCandidate.status == "rejected"))
        assert rejection and "different variant" in rejection.conflicts


def test_incremental_records_possible_match_reason(monkeypatch):
    session = isolated_store(monkeypatch)
    market_store._persist_listings_incremental([
        gpu("JIB", "J1", "ASUS DUAL RTX 5060 8GB GDDR7 OC EDITION", 15000)
    ], "JIB")
    possible = gpu("Advice", "A1", "ASUS GEFORCE RTX 5060 O8G A OC - 8GB GDDR7", 14900)
    stats = market_store._persist_listings_incremental([possible], "Advice")
    assert stats["pair_comparisons"] == 1
    with session() as db:
        row = db.scalar(select(MatchCandidate).where(MatchCandidate.status == "possible"))
        assert row is not None
        assert "missing variant" in row.decision_reason
    review = market_store.match_review("GPU", status="possible",
                                       source="Advice", manufacturer="ASUS")
    assert review["count"] == 1
    assert review["matches"][0]["decision_reason"] == "missing variant; missing model number"


def test_duplicate_sku_batch_and_repeated_refresh_are_idempotent(monkeypatch):
    session = isolated_store(monkeypatch)
    first = gpu("JIB", "DUP", "MSI RTX 5060 8GB GDDR7", 15000)
    second = first.model_copy(deep=True)
    second.price = 14900
    stats = market_store._persist_listings_incremental([first, second], "JIB")
    assert stats["listings_processed"] == 1
    with session() as db:
        assert db.scalar(select(func.count()).select_from(RetailerListing)) == 1
        assert db.get(RetailerListing, "JIB:DUP").price == 14900


def test_full_rebuild_blocking_avoids_cartesian_comparisons(monkeypatch):
    isolated_store(monkeypatch)
    jib, advice = [], []
    for index in range(30):
        title = f"ASUS DUAL RTX {3000 + index} 8GB GDDR7"
        jib.append(gpu("JIB", f"J{index}", title, 15000 + index))
        advice.append(gpu("Advice", f"A{index}", title, 14900 + index))
    market_store._persist_listings_incremental(jib, "JIB")
    market_store._persist_listings_incremental(advice, "Advice")
    metrics = market_store.full_rebuild_product_index()
    assert metrics["candidate_pairs"] == 30
    assert metrics["candidate_pairs"] < len(jib) * len(advice) / 10


def test_refresh_lock_rejects_overlapping_jobs(monkeypatch):
    isolated_store(monkeypatch)

    class SlowCollector:
        retailer = "JIB"
        last_error = None
        coverage = {}

        async def collect(self):
            await asyncio.sleep(.05)
            return []

    async def overlap():
        return await asyncio.gather(
            market_store.refresh_prices([SlowCollector()]),
            market_store.refresh_prices([SlowCollector()]),
            return_exceptions=True,
        )

    results = asyncio.run(overlap())
    assert sum(isinstance(item, market_store.RefreshInProgressError) for item in results) == 1

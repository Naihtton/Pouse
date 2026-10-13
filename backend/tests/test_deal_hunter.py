from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import deal_hunter
from app.database import (Base, CanonicalProduct, CurrentInventory, DealOpportunity,
                          PriceHistory, RetailerListing)
from app.deal_hunter import (_apply_build_impact_score, _candidate, _cheap_score,
                             calculate_history_metrics, run_deal_scan)
from app.domain import Listing
from app.product_identity import parse_product_identity


def isolated_deals(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(engine)
    monkeypatch.setattr(deal_hunter, "Session", session)
    return session


def test_price_history_previous_and_window_statistics():
    now = datetime.now(timezone.utc)
    metrics = calculate_history_metrics([
        (now - timedelta(days=8), 12000),
        (now - timedelta(days=4), 11000),
        (now - timedelta(days=2), 10000),
        (now, 9000),
    ], now, minimum_points=3)
    assert metrics["previous_price"] == 10000
    assert metrics["low_7d"] == 9000
    assert metrics["median_7d"] == 10000
    assert metrics["low_30d"] == 9000
    assert metrics["median_30d"] == 10500
    assert metrics["change_vs_previous_pct"] == -10
    assert metrics["all_time_low"] == 9000


def test_insufficient_history_and_tiny_fluctuation_are_not_fabricated_deals():
    now = datetime.now(timezone.utc)
    metrics = calculate_history_metrics([
        (now - timedelta(days=1), 10000), (now, 9900)
    ], now, minimum_points=3)
    assert metrics["median_7d"] is None
    assert metrics["median_30d"] is None
    assert metrics["history_confidence"] == "reduced"
    assert _candidate(metrics, 0)[0] == []


def test_meaningful_drop_new_low_and_deterministic_score(monkeypatch):
    monkeypatch.setenv("DEAL_MIN_NEW_LOW_DROP_PCT", "3")
    now = datetime.now(timezone.utc)
    metrics = calculate_history_metrics([
        (now - timedelta(days=2), 10000),
        (now - timedelta(days=1), 10000),
        (now, 9000),
    ], now, minimum_points=3)
    types, reasons = _candidate(metrics, 8)
    assert {"price_drop", "new_30d_low", "cross_store_gap"} <= set(types)
    assert reasons
    score, dimensions = _cheap_score(metrics, 8, 3)
    assert 0 < score <= 80
    assert dimensions["historical_discount_score"] > 0


def _seed_offer(session, now, prices):
    identity = parse_product_identity("gpu", "ASUS DUAL RTX 5060 8GB GDDR7")
    listing = Listing(
        sku="J1", name="ASUS DUAL RTX 5060 8GB GDDR7",
        title="ASUS DUAL RTX 5060 8GB GDDR7", category="GPU",
        retailer="JIB", source="JIB", price=prices[-1], shipping=0,
        in_stock=True, specs={"tier": 3, "watts": 145, "length": 230},
        product_identity=identity, collected_at=now,
        url="https://example.test/j1",
    )
    listing.canonical_product_id = "product-1"
    listing.effective_price = prices[-1]
    with session.begin() as db:
        db.add(CanonicalProduct(
            id="product-1", group_identity_key="gpu:test", category="GPU",
            manufacturer="ASUS", canonical_name="ASUS RTX 5060", chipset="RTX 5060",
            variant=None, vram_gb=8, memory_type="GDDR7", model_number="DUAL",
            normalized_model="dual", blocking_key="gpu:rtx5060:8", capacity_gb=None,
            wattage=None, identity=identity.model_dump(mode="json"),
            created_at=now, updated_at=now,
        ))
        db.add(RetailerListing(
            id="JIB:J1", canonical_product_id="product-1", source="JIB",
            retailer_sku="J1", category="GPU", title=listing.title,
            price=prices[-1], shipping=0, effective_price=prices[-1], stock=True,
            url=listing.url, collected_at=now,
            payload=listing.model_dump(mode="json"), identity_fingerprint="x",
            blocking_key="gpu:rtx5060:8",
        ))
        db.add(CurrentInventory(source="JIB", retailer_skus=["J1"], updated_at=now))
        for index, price in enumerate(prices):
            at = now - timedelta(days=len(prices) - index - 1)
            db.add(PriceHistory(
                id=f"h-{index}", canonical_product_id="product-1",
                retailer_listing_id="JIB:J1", source="JIB", price=price,
                effective_price=price, stock=True, collected_at=at,
            ))


def test_deal_lifecycle_activation_repeat_resolution_and_reactivation(monkeypatch):
    session = isolated_deals(monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_offer(session, now, [10000, 10000, 9000])

    first = run_deal_scan()
    assert first["deal_candidates"] == 1
    run_deal_scan()
    with session() as db:
        assert db.scalar(select(func.count()).select_from(DealOpportunity)) == 1
        assert db.scalar(select(DealOpportunity)).active is True

    later = now + timedelta(minutes=1)
    with session.begin() as db:
        row = db.get(RetailerListing, "JIB:J1")
        row.price = row.effective_price = 10000
        row.collected_at = later
        db.add(PriceHistory(id="h-rise", canonical_product_id="product-1",
                            retailer_listing_id="JIB:J1", source="JIB", price=10000,
                            effective_price=10000, stock=True, collected_at=later))
    resolved = run_deal_scan()
    assert resolved["deal_candidates"] == 0
    with session() as db:
        assert db.scalar(select(DealOpportunity)).status == "resolved"

    newest = later + timedelta(minutes=1)
    with session.begin() as db:
        row = db.get(RetailerListing, "JIB:J1")
        row.price = row.effective_price = 8500
        row.collected_at = newest
        db.add(PriceHistory(id="h-return", canonical_product_id="product-1",
                            retailer_listing_id="JIB:J1", source="JIB", price=8500,
                            effective_price=8500, stock=True, collected_at=newest))
    run_deal_scan()
    with session() as db:
        assert db.scalar(select(func.count()).select_from(DealOpportunity)) == 2
        assert db.scalar(select(func.count()).select_from(DealOpportunity).where(
            DealOpportunity.active.is_(True))) == 1


def test_extreme_drop_is_anomaly_not_active_deal(monkeypatch):
    session = isolated_deals(monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_offer(session, now, [10000, 10000, 500])
    result = run_deal_scan()
    assert result["deal_candidates"] == 0
    assert result["rejection_reasons"]["price_anomaly"] == 1


def _score_candidate(base_score):
    return {
        "score": base_score,
        "score_dimensions": {
            "historical_discount_score": base_score,
            "price_drop_score": 0,
            "cross_store_discount_score": 0,
            "inventory_confidence_score": 0,
            "build_usefulness_score": 0,
            "margin_impact_score": 0,
        },
        "opportunity_types": [],
        "reasons": [],
    }


def test_build_impact_reduces_cost_and_improves_margin_score():
    candidate = _score_candidate(45)
    impact = {
        "profile": "gaming_1080p_budget",
        "included_in_baseline": False,
        "cost_improvement": 1200,
        "profit_improvement": 1200,
        "performance_score_change": 0,
        "build_usefulness": 64,
    }
    _apply_build_impact_score(candidate, impact)
    assert candidate["score"] > 45
    assert candidate["score_dimensions"]["build_usefulness_score"] > 0
    assert candidate["score_dimensions"]["margin_impact_score"] > 0
    assert "margin_opportunity" in candidate["opportunity_types"]
    assert "฿1,200" in candidate["reasons"][-1]


def test_no_useful_build_impact_is_capped_below_good():
    candidate = _score_candidate(75)
    impact = {
        "profile": "gaming_1080p_budget",
        "included_in_baseline": False,
        "cost_improvement": -500,
        "profit_improvement": -500,
        "performance_score_change": 0,
        "build_usefulness": 10,
    }
    _apply_build_impact_score(candidate, impact)
    assert candidate["score"] == 49
    assert candidate["score_dimensions"]["build_usefulness_score"] == 0
    assert candidate["opportunity_types"] == []


def test_useful_deal_ranks_above_large_but_meaningless_discount():
    meaningless = _score_candidate(80)
    _apply_build_impact_score(meaningless, None)
    useful = _score_candidate(40)
    _apply_build_impact_score(useful, {
        "profile": "gaming_1080p_mid",
        "included_in_baseline": True,
        "cost_improvement": 0,
        "profit_improvement": 0,
        "performance_score_change": 0,
        "build_usefulness": 80,
    })
    assert useful["score"] > meaningless["score"]

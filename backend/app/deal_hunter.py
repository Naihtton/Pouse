"""Stored-history Deal Hunter. This module never contacts retailer websites."""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from time import monotonic
from uuid import uuid4

from sqlalchemy import func, select

from .builder import normalize, search_builds
from .database import (CanonicalProduct, CurrentInventory, DealOpportunity, DealScanState,
                       PriceHistory, RetailerListing, Session)
from .domain import BuildRequest, Component, Listing
from .market_quality import assess_listing_group


PROFILES = {
    "gaming_1080p_budget": ("DEAL_PROFILE_1080P_BUDGET", 25000, "1080p"),
    "gaming_1080p_mid": ("DEAL_PROFILE_1080P_MID", 35000, "1080p"),
    "gaming_1440p": ("DEAL_PROFILE_1440P", 50000, "1440p"),
}


def _setting(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _pct_change(current: int | float, reference: int | float | None) -> float | None:
    if not reference or reference <= 0:
        return None
    return round((current - reference) / reference * 100, 2)


def calculate_history_metrics(history: list[tuple[datetime, int]], now: datetime | None = None,
                              minimum_points: int | None = None) -> dict:
    """Calculate references without inventing medians from insufficient history."""
    now = now or datetime.now(timezone.utc)
    minimum_points = minimum_points or int(_setting("MIN_HISTORY_POINTS_FOR_MEDIAN", 3))
    valid = sorted(((_aware(at), price) for at, price in history if price and price > 0),
                   key=lambda item: item[0])
    current = valid[-1][1] if valid else None
    previous = valid[-2][1] if len(valid) >= 2 else None

    def window(days: int) -> tuple[int | None, float | None, int]:
        values = [price for at, price in valid if at >= now - timedelta(days=days)]
        return (min(values) if values else None,
                float(median(values)) if len(values) >= minimum_points else None,
                len(values))

    low_7d, median_7d, points_7d = window(7)
    low_30d, median_30d, points_30d = window(30)
    return {
        "current_price": current, "previous_price": previous,
        "low_7d": low_7d, "median_7d": median_7d, "points_7d": points_7d,
        "low_30d": low_30d, "median_30d": median_30d, "points_30d": points_30d,
        "all_time_low": min((price for _, price in valid), default=None),
        "all_time_points": len(valid), "minimum_points_for_median": minimum_points,
        "change_vs_previous_pct": _pct_change(current, previous) if current else None,
        "change_vs_7d_median_pct": _pct_change(current, median_7d) if current else None,
        "change_vs_30d_median_pct": _pct_change(current, median_30d) if current else None,
        "history_confidence": "sufficient" if points_30d >= minimum_points else "reduced",
    }


def _listing(row: RetailerListing, now: datetime) -> Listing:
    listing = Listing.model_validate(row.payload)
    listing.canonical_product_id = row.canonical_product_id
    listing.effective_price = row.effective_price
    listing.shipping_known = row.shipping is not None
    age_hours = (now - _aware(row.collected_at)).total_seconds() / 3600
    expired_hours = _setting("MARKET_EXPIRED_HOURS", 6)
    listing.freshness = "expired" if age_hours > expired_hours else (
        "fresh" if age_hours <= _setting("MARKET_FRESH_MINUTES", 30) / 60 else "stale"
    )
    return listing


def _grade(score: float) -> str:
    if score >= _setting("DEAL_GRADE_EXCEPTIONAL", 90):
        return "exceptional"
    if score >= _setting("DEAL_GRADE_STRONG", 70):
        return "strong"
    if score >= _setting("DEAL_GRADE_GOOD", 50):
        return "good"
    return "weak"


def _cheap_score(metrics: dict, cross_discount: float, history_points: int) -> tuple[float, dict]:
    discounts = [max(0.0, -(metrics.get(key) or 0.0)) for key in (
        "change_vs_previous_pct", "change_vs_7d_median_pct", "change_vs_30d_median_pct"
    )]
    historical = min(_setting("DEAL_SCORE_HISTORY_MAX", 35),
                     max(discounts) * _setting("DEAL_SCORE_HISTORY_MULTIPLIER", 3))
    price_drop = min(_setting("DEAL_SCORE_PREVIOUS_MAX", 20),
                     discounts[0] * _setting("DEAL_SCORE_PREVIOUS_MULTIPLIER", 2))
    cross_store = min(_setting("DEAL_SCORE_CROSS_STORE_MAX", 15),
                      max(0.0, cross_discount)
                      * _setting("DEAL_SCORE_CROSS_STORE_MULTIPLIER", 1.5))
    required = max(1, int(_setting("MIN_HISTORY_POINTS_FOR_MEDIAN", 3)))
    confidence_max = _setting("DEAL_SCORE_CONFIDENCE_MAX", 10)
    confidence = min(confidence_max, history_points / required * confidence_max)
    dimensions = {"historical_discount_score": round(historical, 2),
                  "price_drop_score": round(price_drop, 2),
                  "cross_store_discount_score": round(cross_store, 2),
                  "inventory_confidence_score": round(confidence, 2),
                  "build_usefulness_score": 0.0, "margin_impact_score": 0.0}
    return round(sum(dimensions.values()), 2), dimensions


def _candidate(metrics: dict, cross_discount: float) -> tuple[list[str], list[str]]:
    types: list[str] = []
    reasons: list[str] = []
    previous_drop = -(metrics.get("change_vs_previous_pct") or 0)
    discount_7d = -(metrics.get("change_vs_7d_median_pct") or 0)
    discount_30d = -(metrics.get("change_vs_30d_median_pct") or 0)
    if previous_drop >= _setting("DEAL_MIN_DROP_VS_PREVIOUS_PCT", 5):
        types.append("price_drop")
        reasons.append(f"price dropped {previous_drop:.1f}% since the previous observation")
    if discount_7d >= _setting("DEAL_MIN_DROP_VS_7D_MEDIAN_PCT", 5):
        types.append("price_drop")
        reasons.append(f"price is {discount_7d:.1f}% below the 7-day median")
    if discount_30d >= _setting("DEAL_MIN_DROP_VS_30D_MEDIAN_PCT", 7):
        types.append("price_drop")
        reasons.append(f"price is {discount_30d:.1f}% below the 30-day median")
    if (metrics.get("low_30d") is not None and metrics.get("current_price") == metrics["low_30d"]
            and metrics.get("points_30d", 0) >= 2
            and previous_drop >= _setting("DEAL_MIN_NEW_LOW_DROP_PCT", 3)):
        types.append("new_30d_low")
        reasons.append("price is a new 30-day observed low")
    if cross_discount >= _setting("DEAL_MIN_CROSS_STORE_GAP_PCT", 5):
        types.append("cross_store_gap")
        reasons.append(f"price is {cross_discount:.1f}% below the other current retailer offer")
    return sorted(set(types)), reasons


def _build_impact(candidate: dict, components: list[Component], listing_by_id: dict[str, Listing],
                  baselines: dict[str, object]) -> dict | None:
    canonical_id = candidate["canonical_product_id"]
    target = listing_by_id.get(candidate["retailer_listing_id"])
    if target is None:
        return None
    targeted = [item.model_copy(deep=True) for item in components]
    locked = next((item for item in targeted
                   if item.listing.canonical_product_id == canonical_id), None)
    if locked is None:
        return None
    locked.listing = target
    locked.delivered_price = target.effective_price or target.price
    impacts = []
    for name, (budget_setting, default_budget, resolution) in PROFILES.items():
        budget = int(_setting(budget_setting, default_budget))
        request = BuildRequest(max_cost=budget, target_sell_price=budget,
                               use_case="gaming", target_resolution=resolution)
        baseline = baselines.get(name)
        if baseline is None:
            try:
                baseline = search_builds(request, components, candidate_limit=1)[0].candidates[0]
                baselines[name] = baseline
            except ValueError:
                baselines[name] = False
                continue
        if baseline is False:
            continue
        try:
            deal_build = search_builds(request, targeted, candidate_limit=1,
                                       locked_component_id=canonical_id)[0].candidates[0]
        except ValueError:
            continue
        cost_improvement = baseline.landed_cost - deal_build.landed_cost
        profit_improvement = deal_build.estimated_profit - baseline.estimated_profit
        margin_change = round(deal_build.actual_margin - baseline.actual_margin, 2)
        performance_change = round(deal_build.performance_score - baseline.performance_score, 2)
        included = any(part.listing.canonical_product_id == canonical_id
                       for part in baseline.components)
        beneficial = included or cost_improvement > 0 or profit_improvement > 0 or performance_change > 0
        usefulness = (80 if included else
                      max(40, min(100, 40 + cost_improvement / 50
                                  + max(0, performance_change) * 2)) if beneficial else 10)
        impacts.append({
            "profile": name, "baseline_landed_cost": baseline.landed_cost,
            "deal_build_landed_cost": deal_build.landed_cost,
            "cost_improvement": cost_improvement,
            "baseline_profit": baseline.estimated_profit,
            "deal_profit": deal_build.estimated_profit,
            "profit_improvement": profit_improvement,
            "baseline_margin": baseline.actual_margin,
            "deal_margin": deal_build.actual_margin,
            "margin_change": margin_change,
            "performance_score_change": performance_change,
            "included_in_baseline": included, "build_usefulness": round(usefulness, 2),
            "affected_build": deal_build.model_dump(mode="json"),
        })
    if not impacts:
        return None
    return max(impacts, key=lambda item: (item["build_usefulness"], item["profit_improvement"],
                                          item["performance_score_change"]))


def _apply_build_impact_score(candidate: dict, impact: dict | None) -> None:
    """Merge targeted build value into the score and opportunity explanation."""
    meaningful = bool(impact and (
        impact["included_in_baseline"]
        or impact["cost_improvement"] > 0
        or impact["profit_improvement"] > 0
        or impact["performance_score_change"] > 0
    ))
    usefulness = impact["build_usefulness"] if impact and meaningful else 0
    profit = max(0, impact["profit_improvement"]) if impact and meaningful else 0
    candidate["build_impact"] = impact
    candidate["score_dimensions"]["build_usefulness_score"] = round(
        usefulness * _setting("DEAL_SCORE_USEFULNESS_MAX", 15) / 100, 2
    )
    candidate["score_dimensions"]["margin_impact_score"] = round(min(
        _setting("DEAL_SCORE_MARGIN_MAX", 5),
        profit / max(1, _setting("DEAL_SCORE_MARGIN_BAHT_PER_POINT", 250)),
    ), 2)
    candidate["score"] = round(min(100, sum(candidate["score_dimensions"].values())), 2)
    if not meaningful:
        candidate["score"] = min(candidate["score"], 49.0)
        candidate["reasons"].append(
            "no meaningful improvement for the target build profiles"
            if impact else "no valid target build uses this component"
        )
        return
    if profit > 0:
        candidate["opportunity_types"].append("margin_opportunity")
        candidate["reasons"].append(
            f"improves {impact['profile']} expected profit by ฿{profit:,}"
        )
    if impact["performance_score_change"] > 0:
        candidate["opportunity_types"].append("build_upgrade_opportunity")
        candidate["reasons"].append(
            f"raises {impact['profile']} performance score by "
            f"{impact['performance_score_change']:.1f}"
        )


def run_deal_scan() -> dict:
    started = monotonic()
    now = datetime.now(timezone.utc)
    with Session.begin() as session:
        state = session.get(DealScanState, 1)
        if state:
            state.status, state.started_at = "running", now
        else:
            session.add(DealScanState(id=1, status="running", started_at=now,
                                      completed_at=None, summary={}))

    diagnostics = {"history_rows_analyzed": 0, "products_evaluated": 0,
                   "deal_candidates": 0, "rejected_candidates": 0,
                   "rejection_reasons": Counter(), "build_impact_evaluations": 0}
    try:
        with Session() as session:
            inventories = {row.source: set(row.retailer_skus)
                           for row in session.scalars(select(CurrentInventory)).all()}
            all_rows = session.scalars(select(RetailerListing)).all()
            rows = [row for row in all_rows if row.source in {"JIB", "Advice"}
                    and (row.source not in inventories or row.retailer_sku in inventories[row.source])]
            histories = session.scalars(select(PriceHistory).order_by(
                PriceHistory.retailer_listing_id, PriceHistory.collected_at)).all()
            products = {row.id: row for row in session.scalars(select(CanonicalProduct)).all()}
        by_listing: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
        for item in histories:
            if item.effective_price and item.effective_price > 0 and item.stock is not False:
                by_listing[item.retailer_listing_id].append((item.collected_at, item.effective_price))
                diagnostics["history_rows_analyzed"] += 1

        listings = [_listing(row, now) for row in rows]
        listing_by_id = {row.id: listing for row, listing in zip(rows, listings)}
        groups: dict[str, list[Listing]] = defaultdict(list)
        row_groups: dict[str, list[RetailerListing]] = defaultdict(list)
        for row, listing in zip(rows, listings):
            groups[row.canonical_product_id].append(listing)
            row_groups[row.canonical_product_id].append(row)
        suspicious = {key for key, offers in groups.items()
                      if assess_listing_group(offers)["exclude_from_builder"]}

        candidates: list[dict] = []
        valid_listing_ids: set[str] = set()
        min_points = int(_setting("MIN_HISTORY_POINTS_FOR_MEDIAN", 3))
        for canonical_id, current_rows in row_groups.items():
            diagnostics["products_evaluated"] += 1
            diagnostics["rejection_reasons"]["zero_price"] += sum(
                not row.effective_price or row.effective_price <= 0 for row in current_rows
            )
            diagnostics["rejection_reasons"]["out_of_stock"] += sum(
                row.stock is False for row in current_rows
            )
            diagnostics["rejection_reasons"]["stock_unknown"] += sum(
                row.stock is None for row in current_rows
            )
            diagnostics["rejection_reasons"]["stale_or_expired"] += sum(
                listing_by_id[row.id].freshness != "fresh" for row in current_rows
            )
            current_valid = [row for row in current_rows if row.stock is True
                             and row.effective_price and row.effective_price > 0
                             and listing_by_id[row.id].freshness == "fresh"]
            valid_listing_ids.update(row.id for row in current_valid)
            if canonical_id in suspicious:
                diagnostics["rejection_reasons"]["suspicious_product_group"] += len(current_valid)
                continue
            for row in current_valid:
                history = by_listing.get(row.id, [])
                metrics = calculate_history_metrics(history, now, min_points)
                metrics["current_price"] = row.effective_price
                others = [item for item in current_valid if item.source != row.source]
                other = min(others, key=lambda item: item.effective_price) if others else None
                cross_discount = (round((other.effective_price - row.effective_price)
                                        / other.effective_price * 100, 2) if other else 0.0)
                cross = ({"source": other.source, "price": other.effective_price,
                          "price_difference": other.effective_price - row.effective_price,
                          "price_difference_pct": cross_discount,
                          "best_source": row.source if row.effective_price < other.effective_price
                          else other.source} if other else None)
                previous = metrics.get("previous_price")
                if row.effective_price <= 0:
                    diagnostics["rejection_reasons"]["zero_price"] += 1
                    continue
                if previous and row.effective_price <= previous * (
                        1 - _setting("DEAL_MAX_SAFE_SINGLE_DROP_PCT", 70) / 100):
                    diagnostics["rejection_reasons"]["price_anomaly"] += 1
                    continue
                types, reasons = _candidate(metrics, cross_discount)
                if not types:
                    diagnostics["rejected_candidates"] += 1
                    diagnostics["rejection_reasons"]["below_deal_threshold"] += 1
                    continue
                score, dimensions = _cheap_score(metrics, cross_discount,
                                                 metrics["all_time_points"])
                product = products.get(canonical_id)
                candidates.append({
                    "canonical_product_id": canonical_id, "retailer_listing_id": row.id,
                    "source": row.source, "category": row.category,
                    "title": row.title, "url": row.url, "current_price": row.effective_price,
                    "metrics": metrics, "cross_store": cross, "opportunity_types": types,
                    "reasons": reasons, "score": score, "score_dimensions": dimensions,
                    "product_identity": product.identity if product else {},
                })

        components = normalize(listings)
        baselines: dict[str, object] = {}
        impact_threshold = _setting("DEAL_BUILD_IMPACT_MIN_SCORE", 20)
        shortlist = sorted((item for item in candidates if item["score"] >= impact_threshold),
                           key=lambda item: item["score"], reverse=True)
        shortlist = shortlist[:int(_setting("DEAL_MAX_BUILD_IMPACT_CANDIDATES", 12))]
        for candidate in shortlist:
            diagnostics["build_impact_evaluations"] += 1
            impact = _build_impact(candidate, components, listing_by_id, baselines)
            _apply_build_impact_score(candidate, impact)
        for candidate in candidates:
            candidate.setdefault("build_impact", None)
            candidate["grade"] = _grade(candidate["score"])
            candidate["opportunity_types"] = sorted(set(candidate["opportunity_types"]))

        detected_at = datetime.now(timezone.utc)
        seen: set[str] = set()
        with Session.begin() as session:
            active_rows = session.scalars(select(DealOpportunity).where(
                DealOpportunity.active.is_(True))).all()
            active_by_listing = {row.retailer_listing_id: row for row in active_rows}
            for candidate in candidates:
                listing_id = candidate["retailer_listing_id"]
                seen.add(listing_id)
                row = active_by_listing.get(listing_id)
                metrics = candidate["metrics"]
                values = dict(
                    canonical_product_id=candidate["canonical_product_id"],
                    retailer_listing_id=listing_id, source=candidate["source"],
                    category=candidate["category"], status="active", active=True,
                    current_price=candidate["current_price"], previous_price=metrics["previous_price"],
                    median_7d=metrics["median_7d"], median_30d=metrics["median_30d"],
                    low_7d=metrics["low_7d"], low_30d=metrics["low_30d"],
                    all_time_low=metrics["all_time_low"],
                    change_vs_previous_pct=metrics["change_vs_previous_pct"],
                    discount_vs_7d_pct=(max(0, -metrics["change_vs_7d_median_pct"])
                                        if metrics["change_vs_7d_median_pct"] is not None else None),
                    discount_vs_30d_pct=(max(0, -metrics["change_vs_30d_median_pct"])
                                         if metrics["change_vs_30d_median_pct"] is not None else None),
                    deal_score=candidate["score"], grade=candidate["grade"],
                    opportunity_types=candidate["opportunity_types"], reasons=candidate["reasons"],
                    history_metrics={**metrics, "score_dimensions": candidate["score_dimensions"]},
                    cross_store=candidate["cross_store"], build_impact=candidate["build_impact"],
                    product_identity=candidate["product_identity"], title=candidate["title"],
                    url=candidate["url"], last_seen_at=detected_at, resolved_at=None,
                )
                if row:
                    for key, value in values.items():
                        setattr(row, key, value)
                else:
                    session.add(DealOpportunity(id=str(uuid4()), first_detected_at=detected_at,
                                                **values))
            for row in active_rows:
                if row.retailer_listing_id not in seen:
                    row.active = False
                    row.status = "resolved" if row.retailer_listing_id in valid_listing_ids else "expired"
                    row.resolved_at = detected_at
            diagnostics["deal_candidates"] = len(candidates)
            diagnostics["active_deals"] = len(candidates)
            diagnostics["strong_opportunities"] = sum(item["grade"] in {"strong", "exceptional"}
                                                       for item in candidates)
            diagnostics["scan_ms"] = round((monotonic() - started) * 1000, 3)
            diagnostics["rejection_reasons"] = dict(diagnostics["rejection_reasons"])
            state = session.get(DealScanState, 1)
            state.status, state.completed_at, state.summary = "completed", detected_at, diagnostics
        return diagnostics
    except Exception as exc:
        with Session.begin() as session:
            state = session.get(DealScanState, 1)
            if state:
                state.status, state.completed_at = "error", datetime.now(timezone.utc)
                state.summary = {"error": str(exc)}
        raise


def list_deals(category: str | None = None, source: str | None = None,
               grade: str | None = None, min_score: float = 0, active: bool | None = True) -> list[dict]:
    with Session() as session:
        query = select(DealOpportunity)
        if category:
            query = query.where(func.lower(DealOpportunity.category) == category.lower())
        if source:
            query = query.where(func.lower(DealOpportunity.source) == source.lower())
        if grade:
            query = query.where(func.lower(DealOpportunity.grade) == grade.lower())
        if active is not None:
            query = query.where(DealOpportunity.active.is_(active))
        query = query.where(DealOpportunity.deal_score >= min_score).order_by(
            DealOpportunity.deal_score.desc(), DealOpportunity.last_seen_at.desc())
        return [_deal_dict(row, detail=False) for row in session.scalars(query).all()]


def get_deal(deal_id: str) -> dict | None:
    with Session() as session:
        row = session.get(DealOpportunity, deal_id)
        return _deal_dict(row, detail=True) if row else None


def deal_scan_status() -> dict:
    with Session() as session:
        row = session.get(DealScanState, 1)
        return ({"status": "never_run", "summary": {}} if row is None else {
            "status": row.status,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "summary": row.summary,
        })


def _deal_dict(row: DealOpportunity, detail: bool) -> dict:
    result = {
        "id": row.id, "canonical_product_id": row.canonical_product_id,
        "retailer_listing_id": row.retailer_listing_id, "source": row.source,
        "category": row.category, "title": row.title, "url": row.url,
        "current_price": row.current_price, "previous_price": row.previous_price,
        "median_7d": row.median_7d, "median_30d": row.median_30d,
        "low_7d": row.low_7d, "low_30d": row.low_30d,
        "all_time_low": row.all_time_low,
        "change_vs_previous_pct": row.change_vs_previous_pct,
        "discount_vs_7d_pct": row.discount_vs_7d_pct,
        "discount_vs_30d_pct": row.discount_vs_30d_pct,
        "deal_score": row.deal_score, "grade": row.grade,
        "opportunity_types": row.opportunity_types, "reasons": row.reasons,
        "cross_store": row.cross_store, "build_impact": row.build_impact,
        "status": row.status, "active": row.active,
        "first_detected_at": row.first_detected_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
    }
    if detail:
        result.update(product_identity=row.product_identity, history_metrics=row.history_metrics,
                      resolved_at=row.resolved_at.isoformat() if row.resolved_at else None)
    return result

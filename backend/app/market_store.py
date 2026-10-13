"""Persisted live market snapshots and incremental canonical matching."""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from time import monotonic
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, func, or_, select, update

from .collectors import default_collectors
from .database import (CanonicalProduct, CurrentInventory, MatchCandidate, MatchPerformance,
                       PriceHistory, RetailerListing, Session, SourceCoverage, SourceRefresh)
from .domain import Listing, ProductIdentity
from .market_quality import assess_listing_group
from .product_identity import (candidate_key, decision_reason, group_identity_key,
                               match_products, parse_product_identity)

_REFRESH_LOCK = asyncio.Lock()


class RefreshInProgressError(RuntimeError):
    pass


def freshness_at(collected_at: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    age = (now - collected_at).total_seconds()
    fresh_seconds = float(os.getenv("MARKET_FRESH_MINUTES", "30")) * 60
    expired_seconds = float(os.getenv("MARKET_EXPIRED_HOURS", "6")) * 3600
    if age < fresh_seconds:
        return "fresh"
    if age <= expired_seconds:
        return "stale"
    return "expired"


def _identity_from_product(row: CanonicalProduct) -> ProductIdentity:
    return ProductIdentity.model_validate(row.identity)


def _normalized_model(identity: ProductIdentity) -> str | None:
    value = identity.model_number or identity.chipset or identity.product_family
    normalized = "".join(character for character in (value or "").lower() if character.isalnum())
    return normalized[:160] or None


def _identity_fingerprint(listing: Listing, identity: ProductIdentity) -> str:
    relevant = {
        "source": listing.source or listing.retailer,
        "retailer_sku": listing.sku,
        "category": listing.category,
        "title": " ".join((listing.title or listing.name).upper().split()),
        "identity": identity.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def _product_values(identity: ProductIdentity, now: datetime) -> dict:
    return {
        "group_identity_key": group_identity_key(identity)[:255],
        "category": identity.category,
        "manufacturer": identity.manufacturer[:80] if identity.manufacturer else None,
        "canonical_name": identity.canonical_name[:255],
        "chipset": identity.chipset[:80] if identity.chipset else None,
        "variant": identity.variant[:120] if identity.variant else None,
        "vram_gb": identity.vram_gb,
        "memory_type": identity.memory_type[:40] if identity.memory_type else None,
        "model_number": identity.model_number[:160] if identity.model_number else None,
        "normalized_model": _normalized_model(identity),
        "blocking_key": candidate_key(identity)[:255],
        "capacity_gb": identity.capacity_gb,
        "wattage": identity.wattage,
        "identity": identity.model_dump(mode="json"),
        "updated_at": now,
    }


def _update_product(product: CanonicalProduct, identity: ProductIdentity, now: datetime) -> None:
    for key, value in _product_values(identity, now).items():
        setattr(product, key, value)


def _candidate_id(left_id: str, right_id: str) -> str:
    return hashlib.sha256("|".join(sorted((left_id, right_id))).encode()).hexdigest()


def _store_candidate(session, left: RetailerListing, right: RetailerListing,
                     left_identity: ProductIdentity, right_identity: ProductIdentity,
                     block: str, match, now: datetime) -> None:
    first, second = (left, right) if left.id < right.id else (right, left)
    status = "matched" if match.matched else "possible" if match.possible_match else "rejected"
    values = {
        "category": left.category,
        "status": status,
        "source_a": first.source,
        "source_b": second.source,
        "listing_a_id": first.id,
        "listing_b_id": second.id,
        "manufacturer": left_identity.manufacturer or right_identity.manufacturer,
        "blocking_key": block[:255],
        "confidence": match.confidence,
        "matching_fields": match.matching_fields,
        "unknown_fields": match.unknown_fields,
        "conflicts": match.conflicts,
        "decision_reason": decision_reason(match)[:500],
        "updated_at": now,
    }
    row = session.get(MatchCandidate, _candidate_id(left.id, right.id))
    if row:
        for key, value in values.items():
            setattr(row, key, value)
    else:
        session.add(MatchCandidate(id=_candidate_id(left.id, right.id), **values))


def backfill_matching_indexes() -> None:
    """Populate new index columns without changing existing canonical assignments."""
    now = datetime.now(timezone.utc)
    with Session.begin() as session:
        for product in session.scalars(select(CanonicalProduct)).all():
            identity = _identity_from_product(product)
            _update_product(product, identity, now)
        for row in session.scalars(select(RetailerListing)).all():
            payload = dict(row.payload)
            identity_data = payload.get("product_identity")
            if identity_data:
                identity = ProductIdentity.model_validate(identity_data)
            else:
                specification = payload.get("specs", {}).get("retailer_specification", "")
                identity = parse_product_identity(row.category, f"{row.title} {specification}".strip())
                payload["product_identity"] = identity.model_dump(mode="json")
                row.payload = payload
            listing = Listing.model_validate(payload)
            row.blocking_key = candidate_key(identity)[:255]
            row.identity_fingerprint = _identity_fingerprint(listing, identity)
        active_ids = {row.id for row in _active_rows(session)}
        for candidate in session.scalars(select(MatchCandidate)).all():
            if candidate.listing_a_id not in active_ids or candidate.listing_b_id not in active_ids:
                session.delete(candidate)


def _resolve_product_incremental(session, row: RetailerListing | None, listing: Listing,
                                 identity: ProductIdentity, now: datetime, stats: dict) -> CanonicalProduct:
    started = monotonic()
    block = candidate_key(identity)[:255]
    candidates = session.scalars(select(CanonicalProduct).where(
        CanonicalProduct.category == identity.category,
        CanonicalProduct.blocking_key == block,
    )).all()
    stats["canonical_lookup_ms"] += round((monotonic() - started) * 1000, 3)
    listing_id = f"{listing.source or listing.retailer}:{listing.sku}"
    candidate_ids = [candidate.id for candidate in candidates]
    offers = session.scalars(select(RetailerListing).where(
        RetailerListing.canonical_product_id.in_(candidate_ids)
    )).all() if candidate_ids else []
    offers_by_product: dict[str, list[RetailerListing]] = {}
    for offer in offers:
        offers_by_product.setdefault(offer.canonical_product_id, []).append(offer)

    matches = []
    session.execute(delete(MatchCandidate).where(or_(
        MatchCandidate.listing_a_id == listing_id,
        MatchCandidate.listing_b_id == listing_id,
    )))
    for candidate in candidates:
        product_offers = offers_by_product.get(candidate.id, [])
        if any(offer.source == (listing.source or listing.retailer) and offer.id != listing_id
               for offer in product_offers):
            continue
        other = next((offer for offer in product_offers
                      if offer.source != (listing.source or listing.retailer)), None)
        candidate_identity = _identity_from_product(candidate)
        stats["candidate_pairs"] += 1
        match_started = monotonic()
        result = match_products(candidate_identity, identity)
        stats["pair_comparisons"] += 1
        stats["matching_ms"] += round((monotonic() - match_started) * 1000, 3)
        if other:
            other_identity = ProductIdentity.model_validate(other.payload["product_identity"])
            review_result = match_products(identity, other_identity)
            _store_candidate(session, row or RetailerListing(
                id=listing_id, canonical_product_id=candidate.id,
                source=listing.source or listing.retailer, retailer_sku=listing.sku,
                category=listing.category, title=listing.title or listing.name,
                price=listing.price, shipping=listing.shipping, effective_price=None,
                stock=listing.in_stock, url=listing.url, collected_at=listing.collected_at or now,
                payload=listing.model_dump(mode="json"), identity_fingerprint=None,
                blocking_key=block,
            ), other, identity, other_identity, block, review_result, now)
        if result.matched and result.confidence >= .95:
            matches.append((result.confidence, candidate))
    if matches:
        product = max(matches, key=lambda item: item[0])[1]
        product.updated_at = now
        stats["canonical_products_reused"] += 1
        return product

    if row:
        others = session.scalar(select(func.count()).select_from(RetailerListing).where(
            RetailerListing.canonical_product_id == row.canonical_product_id,
            RetailerListing.id != row.id,
        )) or 0
        if not others:
            product = session.get(CanonicalProduct, row.canonical_product_id)
            if product:
                _update_product(product, identity, now)
                stats["canonical_products_reused"] += 1
                return product

    create_started = monotonic()
    product = CanonicalProduct(id=str(uuid4()), created_at=now, **_product_values(identity, now))
    session.add(product)
    session.flush()
    stats["canonical_creation_ms"] += round((monotonic() - create_started) * 1000, 3)
    stats["canonical_products_created"] += 1
    return product


def _persist_listings_incremental(listings: list[Listing], source: str | None = None) -> dict:
    started = monotonic()
    now = datetime.now(timezone.utc)
    stats = {
        "listings_processed": 0, "new_listings": 0, "updated_listings": 0,
        "unchanged_listings": 0, "identity_changes": 0, "candidate_pairs": 0,
        "pair_comparisons": 0, "canonical_products_created": 0,
        "canonical_products_reused": 0, "normalization_ms": 0.0,
        "canonical_lookup_ms": 0.0, "canonical_creation_ms": 0.0,
        "matching_ms": 0.0, "database_ms": 0.0,
    }
    unique = {(item.source or item.retailer, item.sku): item for item in listings
              if item.data_mode == "live"}
    with Session.begin() as session:
        inventory: dict[str, list[str]] = {source: []} if source else {}
        for listing in unique.values():
            stats["listings_processed"] += 1
            row_id = f"{listing.source or listing.retailer}:{listing.sku}"
            row = session.get(RetailerListing, row_id)
            normalize_started = monotonic()
            identity = listing.product_identity or parse_product_identity(
                listing.category, listing.title or listing.name
            )
            fingerprint = _identity_fingerprint(listing, identity)
            block = candidate_key(identity)[:255]
            stats["normalization_ms"] += round((monotonic() - normalize_started) * 1000, 3)
            identity_changed = row is None or row.identity_fingerprint != fingerprint
            offer_changed = row is None or any((
                row.price != listing.price, row.shipping != listing.shipping,
                row.stock != listing.in_stock, row.url != listing.url,
            ))
            if row is None:
                stats["new_listings"] += 1
            elif identity_changed or offer_changed:
                stats["updated_listings"] += 1
            else:
                stats["unchanged_listings"] += 1
            if row is not None and identity_changed:
                stats["identity_changes"] += 1

            product = (_resolve_product_incremental(session, row, listing, identity, now, stats)
                       if identity_changed else session.get(CanonicalProduct, row.canonical_product_id))
            if product is None:
                product = _resolve_product_incremental(session, row, listing, identity, now, stats)
            listing.product_identity = identity
            listing.canonical_product_id = product.id
            listing.shipping_known = listing.shipping is not None
            listing.effective_price = listing.price + listing.shipping if listing.shipping is not None else None
            listing.freshness = freshness_at(listing.collected_at or now, now)
            inventory.setdefault(listing.source or listing.retailer, []).append(listing.sku)
            payload = listing.model_dump(mode="json")
            collected_at = listing.collected_at or now
            if row is None:
                row = RetailerListing(id=row_id, canonical_product_id=product.id,
                    source=listing.source or listing.retailer, retailer_sku=listing.sku,
                    category=listing.category, title=(listing.title or listing.name)[:500],
                    price=listing.price, shipping=listing.shipping,
                    effective_price=listing.effective_price, stock=listing.in_stock,
                    url=listing.url[:1000] if listing.url else None, collected_at=collected_at,
                    payload=payload, identity_fingerprint=fingerprint, blocking_key=block)
                session.add(row)
            else:
                row.canonical_product_id = product.id
                row.category, row.title, row.price = listing.category, (listing.title or listing.name)[:500], listing.price
                row.shipping, row.effective_price, row.stock = listing.shipping, listing.effective_price, listing.in_stock
                row.url, row.collected_at, row.payload = (listing.url[:1000] if listing.url else None), collected_at, payload
                row.identity_fingerprint, row.blocking_key = fingerprint, block
            session.flush()
            history_id = hashlib.sha256(f"{row_id}|{collected_at.isoformat()}".encode()).hexdigest()
            if session.get(PriceHistory, history_id) is None:
                session.add(PriceHistory(id=history_id, canonical_product_id=product.id,
                    retailer_listing_id=row_id, source=listing.source or listing.retailer,
                    price=listing.price, effective_price=listing.effective_price,
                    stock=listing.in_stock, collected_at=collected_at))
        for retailer, skus in inventory.items():
            current = session.get(CurrentInventory, retailer)
            if current:
                current.retailer_skus, current.updated_at = sorted(set(skus)), now
            else:
                session.add(CurrentInventory(source=retailer, retailer_skus=sorted(set(skus)), updated_at=now))
    stats["database_ms"] = round((monotonic() - started) * 1000 - stats["matching_ms"], 3)
    stats["total_ms"] = round((monotonic() - started) * 1000, 3)
    return stats


def persist_listings(listings: list[Listing], source: str | None = None) -> int:
    return _persist_listings_incremental(listings, source)["listings_processed"]


def _save_performance(mode: str, metrics: dict) -> None:
    now = datetime.now(timezone.utc)
    clean = {key: round(value, 3) if isinstance(value, float) else value
             for key, value in metrics.items()}
    with Session.begin() as session:
        row = session.get(MatchPerformance, 1)
        if row:
            row.last_run, row.mode, row.metrics = now, mode, clean
        else:
            session.add(MatchPerformance(id=1, last_run=now, mode=mode, metrics=clean))


def match_performance_report() -> dict:
    with Session() as session:
        row = session.get(MatchPerformance, 1)
        if row is None:
            return {"last_run": None, "mode": None}
        metrics = dict(row.metrics)
        metrics["canonical_products"] = len({
            listing.canonical_product_id for listing in _active_rows(session)
        })
        return {"last_run": row.last_run.isoformat(), "mode": row.mode, **metrics}


async def refresh_prices(collectors=None) -> list[dict]:
    if _REFRESH_LOCK.locked():
        raise RefreshInProgressError("A price refresh or product-index rebuild is already running")
    async with _REFRESH_LOCK:
        total_started = monotonic()
        results = []
        aggregate = {
            "listings_processed": 0, "new_listings": 0, "updated_listings": 0,
            "unchanged_listings": 0, "identity_changes": 0, "candidate_pairs": 0,
            "pair_comparisons": 0, "canonical_products_created": 0,
            "canonical_products_reused": 0, "normalization_ms": 0.0,
            "canonical_lookup_ms": 0.0, "canonical_creation_ms": 0.0,
            "matching_ms": 0.0, "database_ms": 0.0, "collection_ms": 0.0,
        }
        for collector in collectors or default_collectors():
            started = monotonic()
            refreshed_at = datetime.now(timezone.utc)
            persist_stats = None
            try:
                collect_started = monotonic()
                listings = await collector.collect()
                aggregate["collection_ms"] += (monotonic() - collect_started) * 1000
                error = getattr(collector, "last_error", None)
                status = "partial" if error and listings else (
                    "error" if error else ("success" if listings else "empty")
                )
                if listings:
                    persist_stats = _persist_listings_incremental(listings, collector.retailer)
                    for key in aggregate:
                        if key in persist_stats:
                            aggregate[key] += persist_stats[key]
                message = error
            except Exception as exc:
                listings, status, message = [], "error", str(exc)
            duration_ms = round((monotonic() - started) * 1000)
            with Session.begin() as session:
                row = session.get(SourceRefresh, collector.retailer)
                last_success = refreshed_at if status in {"success", "partial"} else (
                    row.last_success if row else None
                )
                values = dict(status=status, listing_count=len(listings), last_refresh=refreshed_at,
                              last_success=last_success, duration_ms=duration_ms,
                              message=message[:500] if message else None)
                if row:
                    for key, value in values.items():
                        setattr(row, key, value)
                else:
                    session.add(SourceRefresh(source=collector.retailer, **values))
                for metadata in getattr(collector, "coverage", {}).values():
                    category = metadata["category"]
                    coverage = session.get(SourceCoverage, (collector.retailer, category))
                    coverage_values = {
                        "collected": metadata["collected"],
                        "reported_total": metadata.get("reported_total"),
                        "pages_fetched": metadata["pages_fetched"],
                        "page_size": metadata.get("page_size"),
                        "truncated": metadata["truncated"],
                        "status": metadata.get("status", status),
                        "last_refresh": refreshed_at,
                    }
                    if coverage:
                        for key, value in coverage_values.items():
                            setattr(coverage, key, value)
                    else:
                        session.add(SourceCoverage(
                            source=collector.retailer, category=category, **coverage_values
                        ))
            results.append({"source": collector.retailer, "status": status,
                            "listing_count": len(listings), "last_refresh": refreshed_at.isoformat(),
                            "last_success": last_success.isoformat() if last_success else None,
                            "duration_ms": duration_ms, "message": message,
                            "matching": persist_stats})
        aggregate["total_refresh_ms"] = round((monotonic() - total_started) * 1000, 3)
        with Session() as session:
            aggregate["canonical_products"] = len({
                row.canonical_product_id for row in _active_rows(session)
            })
        _save_performance("incremental", aggregate)
        if any(item["status"] in {"success", "partial"} for item in results):
            try:
                from .deal_hunter import run_deal_scan
                await asyncio.to_thread(run_deal_scan)
            except Exception:
                # A history-analysis failure must never invalidate a successful
                # retailer refresh. Its error is retained in DealScanState.
                import logging
                logging.exception("Automatic Deal Hunter scan failed")
        return results


def _active_rows(session) -> list[RetailerListing]:
    rows = session.scalars(select(RetailerListing)).all()
    inventory = {row.source: set(row.retailer_skus)
                 for row in session.scalars(select(CurrentInventory)).all()}
    return [row for row in rows if row.source not in inventory
            or row.retailer_sku in inventory[row.source]]


def full_rebuild_product_index() -> dict:
    """Manually rebuild active canonical groups while preserving all history rows."""
    total_started = monotonic()
    now = datetime.now(timezone.utc)
    metrics = {
        "listings_processed": 0, "new_listings": 0, "updated_listings": 0,
        "unchanged_listings": 0, "identity_changes": 0, "candidate_pairs": 0,
        "pair_comparisons": 0, "canonical_products_created": 0,
        "canonical_products_reused": 0, "normalization_ms": 0.0,
        "matching_ms": 0.0, "database_ms": 0.0,
    }
    database_started = monotonic()
    with Session.begin() as session:
        rows = _active_rows(session)
        metrics["listings_processed"] = len(rows)
        indexed: dict[str, list[tuple[RetailerListing, ProductIdentity]]] = {}
        for row in rows:
            started = monotonic()
            payload = dict(row.payload)
            specification = payload.get("specs", {}).get("retailer_specification", "")
            identity = parse_product_identity(row.category, f"{row.title} {specification}".strip())
            payload["product_identity"] = identity.model_dump(mode="json")
            row.payload = payload
            listing = Listing.model_validate(payload)
            row.identity_fingerprint = _identity_fingerprint(listing, identity)
            row.blocking_key = candidate_key(identity)[:255]
            indexed.setdefault(row.blocking_key, []).append((row, identity))
            metrics["normalization_ms"] += (monotonic() - started) * 1000

        session.execute(delete(MatchCandidate))
        clusters: list[list[tuple[RetailerListing, ProductIdentity]]] = []
        for block, items in indexed.items():
            pair_results = {}
            for index, (left, left_identity) in enumerate(items):
                for right, right_identity in items[index + 1:]:
                    if left.source == right.source:
                        continue
                    metrics["candidate_pairs"] += 1
                    started = monotonic()
                    result = match_products(left_identity, right_identity)
                    metrics["matching_ms"] += (monotonic() - started) * 1000
                    metrics["pair_comparisons"] += 1
                    pair_results[frozenset((left.id, right.id))] = result
                    _store_candidate(session, left, right, left_identity, right_identity,
                                     block, result, now)
            block_clusters: list[list[tuple[RetailerListing, ProductIdentity]]] = []
            for row, identity in sorted(items, key=lambda item: (item[0].source != "JIB", item[0].id)):
                target = None
                for cluster in block_clusters:
                    if any(other.source == row.source for other, _ in cluster):
                        continue
                    if all(pair_results.get(frozenset((row.id, other.id))) and
                           pair_results[frozenset((row.id, other.id))].matched
                           for other, _ in cluster):
                        target = cluster
                        break
                if target is None:
                    block_clusters.append([(row, identity)])
                else:
                    target.append((row, identity))
            clusters.extend(block_clusters)

        used_product_ids = set()
        for cluster in clusters:
            existing_ids = [row.canonical_product_id for row, _ in cluster
                            if row.canonical_product_id not in used_product_ids]
            product_id = existing_ids[0] if existing_ids else str(uuid5(
                NAMESPACE_URL, "pouse-canonical:" + "|".join(sorted(row.id for row, _ in cluster))
            ))
            used_product_ids.add(product_id)
            identity = cluster[0][1]
            product = session.get(CanonicalProduct, product_id)
            if product is None:
                product = CanonicalProduct(id=product_id, created_at=now,
                                           **_product_values(identity, now))
                session.add(product)
                metrics["canonical_products_created"] += 1
            else:
                _update_product(product, identity, now)
                metrics["canonical_products_reused"] += 1
            for row, _ in cluster:
                row.canonical_product_id = product_id
                session.execute(update(PriceHistory).where(
                    PriceHistory.retailer_listing_id == row.id
                ).values(canonical_product_id=product_id))
        metrics["canonical_products"] = len(clusters)
    metrics["database_ms"] = max(0, (monotonic() - database_started) * 1000
                                 - metrics["normalization_ms"] - metrics["matching_ms"])
    metrics["total_rebuild_ms"] = (monotonic() - total_started) * 1000
    _save_performance("full_rebuild", metrics)
    return {key: round(value, 3) if isinstance(value, float) else value
            for key, value in metrics.items()}


async def rebuild_product_index() -> dict:
    if _REFRESH_LOCK.locked():
        raise RefreshInProgressError("A price refresh or product-index rebuild is already running")
    async with _REFRESH_LOCK:
        return full_rebuild_product_index()


def rebuild_canonical_products() -> int:
    """Compatibility wrapper for tests and development scripts."""
    return full_rebuild_product_index()["canonical_products"]


def load_latest_listings(include_expired: bool = False) -> list[Listing]:
    with Session() as session:
        rows = session.scalars(select(RetailerListing)).all()
        inventory = {row.source: set(row.retailer_skus)
                     for row in session.scalars(select(CurrentInventory)).all()}
        result = []
        for row in rows:
            if row.source in inventory and row.retailer_sku not in inventory[row.source]:
                continue
            listing = Listing.model_validate(row.payload)
            listing.canonical_product_id = row.canonical_product_id
            listing.effective_price = row.effective_price
            listing.shipping_known = row.shipping is not None
            listing.freshness = freshness_at(row.collected_at)
            if include_expired or listing.freshness != "expired":
                result.append(listing)
        return result


def refresh_status() -> list[dict]:
    with Session() as session:
        rows = session.scalars(select(SourceRefresh).order_by(SourceRefresh.source)).all()
        return [{"source": row.source, "status": row.status, "listing_count": row.listing_count,
                 "last_refresh": row.last_refresh.isoformat(),
                 "last_success": row.last_success.isoformat() if row.last_success else None,
                 "duration_ms": row.duration_ms, "message": row.message} for row in rows]


def source_coverage_report() -> dict:
    """Return persisted per-category collection coverage and source refresh state."""
    with Session() as session:
        refreshes = {row.source: row for row in session.scalars(select(SourceRefresh)).all()}
        rows = session.scalars(select(SourceCoverage).order_by(
            SourceCoverage.source, SourceCoverage.category
        )).all()
        report: dict[str, dict] = {"JIB": {}, "Advice": {}}
        for row in rows:
            refresh = refreshes.get(row.source)
            report.setdefault(row.source, {})[row.category] = {
                "source": row.source,
                "category": row.category,
                "collected": row.collected,
                "reported_total": row.reported_total,
                "pages_fetched": row.pages_fetched,
                "page_size": row.page_size,
                "truncated": row.truncated,
                "last_refresh": row.last_refresh.isoformat(),
                "status": row.status,
                "source_status": refresh.status if refresh else None,
            }
        return report


def market_products(category: str | None = None, cross_store_only: bool = False) -> list[dict]:
    with Session() as session:
        statement = select(RetailerListing)
        if category:
            statement = statement.where(RetailerListing.category == category)
        rows = session.scalars(statement).all()
        inventory = {row.source: set(row.retailer_skus)
                     for row in session.scalars(select(CurrentInventory)).all()}
        rows = [row for row in rows if row.source not in inventory
                or row.retailer_sku in inventory[row.source]]
        grouped: dict[str, list[RetailerListing]] = {}
        for row in rows:
            grouped.setdefault(row.canonical_product_id, []).append(row)
        products = {row.id: row for row in session.scalars(select(CanonicalProduct)).all()}
        result = []
        for product_id, offers in grouped.items():
            sources = {offer.source for offer in offers}
            if cross_store_only and not {"JIB", "Advice"}.issubset(sources):
                continue
            listing_offers = []
            for offer in offers:
                listing = Listing.model_validate(offer.payload)
                listing.effective_price = offer.effective_price
                listing.in_stock = offer.stock
                listing_offers.append(listing)
            quality = assess_listing_group(listing_offers)
            valid = [offer for offer in offers if offer.stock is True and offer.effective_price is not None
                     and freshness_at(offer.collected_at) != "expired"]
            if quality["exclude_from_market_median"]:
                valid = []
            valid.sort(key=lambda offer: offer.effective_price)
            best_price = valid[0].effective_price if valid else None
            best_sources = sorted({offer.source for offer in valid if offer.effective_price == best_price})
            product = products[product_id]
            match = None
            jib_offer = next((offer for offer in offers if offer.source == "JIB"), None)
            advice_offer = next((offer for offer in offers if offer.source == "Advice"), None)
            if jib_offer and advice_offer:
                match = match_products(
                    ProductIdentity.model_validate(jib_offer.payload["product_identity"]),
                    ProductIdentity.model_validate(advice_offer.payload["product_identity"]),
                ).model_dump()
            prices = [offer.effective_price for offer in valid if offer.effective_price is not None]
            result.append({
                "canonical_product_id": product_id,
                "group_identity_key": product.group_identity_key,
                "canonical_name": product.canonical_name,
                "category": product.category,
                "canonical_product_identity": product.identity,
                "match": match,
                "confidence": match["confidence"] if match else None,
                "match_reasons": match["reasons"] if match else [],
                "match_conflicts": match["conflicts"] if match else [],
                "offers": [{"source": offer.source, "retailer_sku": offer.retailer_sku,
                    "title": offer.title, "price": offer.price, "shipping": offer.shipping,
                    "shipping_known": offer.shipping is not None,
                    "effective_price": offer.effective_price, "stock": offer.stock,
                    "url": offer.url, "collected_at": offer.collected_at.isoformat(),
                    "freshness": freshness_at(offer.collected_at),
                    "retailer_identity": offer.payload.get("product_identity")}
                    for offer in offers],
                "best_price": best_price,
                "best_source": best_sources[0] if len(best_sources) == 1 else None,
                "best_sources": best_sources,
                "price_difference": max(prices) - min(prices) if len(prices) > 1 else 0,
                "price_difference_pct": (round((max(prices) - min(prices)) / min(prices) * 100, 2)
                                         if len(prices) > 1 and min(prices) else 0),
                "price_spread_pct": quality["price_spread_pct"],
                "price_ratio": quality["price_ratio"],
                "suspicious": quality["suspicious"],
                "suspicious_reasons": quality["suspicious_reasons"],
                "excluded_from_market_median": quality["exclude_from_market_median"],
            })
        return result


def data_quality_report() -> dict:
    listings = load_latest_listings(include_expired=True)
    products = market_products()
    matched = [product for product in products if len({o["source"] for o in product["offers"]}) > 1]
    unmatched = {
        source: sum(1 for listing in listings if listing.source == source and
                    not any(listing.sku == offer["retailer_sku"] for product in matched for offer in product["offers"]))
        for source in ("JIB", "Advice")
    }
    missing = {
        "manufacturer": sum(not listing.product_identity or not listing.product_identity.manufacturer for listing in listings),
        "model_number": sum(not listing.product_identity or not listing.product_identity.model_number for listing in listings),
        "gpu_chipset": sum(listing.category == "GPU" and (not listing.product_identity or not listing.product_identity.chipset) for listing in listings),
        "gpu_vram": sum(listing.category == "GPU" and (not listing.product_identity or not listing.product_identity.vram_gb) for listing in listings),
        "gpu_memory_type": sum(listing.category == "GPU" and (not listing.product_identity or not listing.product_identity.memory_type) for listing in listings),
        "unknown_shipping": sum(listing.shipping is None for listing in listings),
    }
    candidate_decisions = _candidate_decisions()
    possible_matches = sum(item["possible_match"] for item in candidate_decisions)
    hard_conflict_rejections = sum(bool(item["conflicts"]) for item in candidate_decisions)
    category_report = {}
    for category in sorted({listing.category for listing in listings}):
        identity_category = "ssd" if category == "Storage" else category.lower()
        category_products = [product for product in products if product["category"].lower() == identity_category]
        category_matches = [product for product in category_products
                            if len({offer["source"] for offer in product["offers"]}) > 1]
        category_report[category] = {
            "canonical_products": len(category_products), "matched_products": len(category_matches),
            "match_rate_pct": round(len(category_matches) / len(category_products) * 100, 2)
                              if category_products else 0,
        }
    with Session() as session:
        history_count = session.scalar(select(func.count()).select_from(PriceHistory)) or 0
    possible_over_merges = sum(any(
        sum(offer["source"] == source for offer in product["offers"]) > 1
        for source in ("JIB", "Advice")) for product in products)
    keys: dict[str, int] = {}
    for product in products:
        keys[product["group_identity_key"]] = keys.get(product["group_identity_key"], 0) + 1
    suspicious = [product for product in products if product["suspicious"]]
    return {
        "jib_listings": sum(listing.source == "JIB" for listing in listings),
        "advice_listings": sum(listing.source == "Advice" for listing in listings),
        "canonical_products": len(products), "matched_products": len(matched),
        "unmatched_jib": unmatched["JIB"], "unmatched_advice": unmatched["Advice"],
        "low_confidence_matches": possible_matches,
        "possible_matches": possible_matches,
        "rejected_matches_hard_conflicts": hard_conflict_rejections,
        "suspicious_product_groups": len(suspicious),
        "same_retailer_duplicate_groups": sum(any(
            reason.startswith("same retailer has multiple offers")
            for reason in product["suspicious_reasons"]) for product in products),
        "groups_over_25_percent_price_spread": sum(product["price_spread_pct"] > 25 for product in products),
        "groups_missing_critical_identity_fields": sum(any(
            reason.startswith("missing critical identity fields")
            for reason in product["suspicious_reasons"]) for product in products),
        "missing_fields": missing,
        "match_rate_by_category": category_report,
        "duplicate_retailer_skus": 0,
        "canonical_candidate_collisions": sum(count > 1 for count in keys.values()),
        "possible_over_merges": possible_over_merges,
        "expired_listings": sum(listing.freshness == "expired" for listing in listings),
        "price_history_rows": history_count,
    }


def _candidate_decisions(category: str | None = None, status: str | None = None,
                         source: str | None = None, manufacturer: str | None = None) -> list[dict]:
    listings = load_latest_listings(include_expired=False)
    if category:
        listings = [listing for listing in listings if listing.category == category]
    jib = [item for item in listings if item.source == "JIB" and item.product_identity]
    advice_buckets: dict[str, list[Listing]] = {}
    for item in listings:
        if item.source == "Advice" and item.product_identity:
            advice_buckets.setdefault(candidate_key(item.product_identity), []).append(item)
    decisions = []
    for left in jib:
        key = candidate_key(left.product_identity)
        for right in advice_buckets.get(key, []):
            if source and source.lower() not in {
                (left.source or left.retailer).lower(), (right.source or right.retailer).lower()
            }:
                continue
            makers = {value.lower() for value in (
                left.product_identity.manufacturer, right.product_identity.manufacturer
            ) if value}
            if manufacturer and manufacturer.lower() not in makers:
                continue
            match = match_products(left.product_identity, right.product_identity)
            decision_status = "matched" if match.matched else (
                "possible" if match.possible_match else "rejected"
            )
            if status and status != decision_status:
                continue
            decisions.append({
                "status": decision_status,
                "candidate_key": key,
                "blocking_key": key,
                "jib": left.title or left.name,
                "jib_sku": left.sku,
                "advice": right.title or right.name,
                "advice_sku": right.sku,
                "listing_a": {"source": left.source, "sku": left.sku,
                              "title": left.title or left.name},
                "listing_b": {"source": right.source, "sku": right.sku,
                              "title": right.title or right.name},
                "manufacturer": (left.product_identity.manufacturer or
                                 right.product_identity.manufacturer),
                "confidence": match.confidence,
                "matched": match.matched,
                "possible_match": match.possible_match,
                "matching_fields": match.matching_fields,
                "unknown_fields": match.unknown_fields,
                "conflicts": match.conflicts,
                "decision_reason": decision_reason(match),
            })
    return decisions


def match_review(category: str, status: str | None = None, source: str | None = None,
                 manufacturer: str | None = None) -> dict:
    if status and status not in {"matched", "possible", "rejected"}:
        raise ValueError("status must be matched, possible, or rejected")
    decisions = _candidate_decisions(category, status, source, manufacturer)
    return {
        "category": "SSD" if category == "Storage" else category,
        "filters": {"status": status, "source": source, "manufacturer": manufacturer},
        "count": len(decisions),
        "matches": decisions,
        "matched_pairs": [item for item in decisions if item["matched"]],
        "possible_matches": [item for item in decisions if item["possible_match"]],
        "rejected_likely_candidates": [item for item in decisions
                                        if not item["matched"] and not item["possible_match"]],
    }


def match_coverage_report() -> dict:
    listings = load_latest_listings(include_expired=False)
    products = market_products()
    report = {}
    for category in ("CPU", "GPU", "Motherboard", "RAM", "Storage", "PSU", "Case"):
        category_listings = [item for item in listings if item.category == category]
        matched_groups = [product for product in products
                          if product["category"] == ("ssd" if category == "Storage" else category.lower())
                          and {offer["source"] for offer in product["offers"]} >= {"JIB", "Advice"}]
        matched_skus = {offer["retailer_sku"] for product in matched_groups
                        for offer in product["offers"]}
        decisions = _candidate_decisions(category)
        label = "SSD" if category == "Storage" else category
        report[label] = {
            "jib": sum(item.source == "JIB" for item in category_listings),
            "advice": sum(item.source == "Advice" for item in category_listings),
            "cross_store_matches": len(matched_groups),
            "possible_matches": sum(item["possible_match"] for item in decisions),
            "rejected_likely_candidates": sum(bool(item["conflicts"]) for item in decisions),
            "unmatched": {
                "jib": sum(item.source == "JIB" and item.sku not in matched_skus
                           for item in category_listings),
                "advice": sum(item.source == "Advice" and item.sku not in matched_skus
                              for item in category_listings),
            },
        }
    return report

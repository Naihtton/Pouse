"""Shared safety checks for canonical offer groups."""
from collections import Counter

from .domain import Listing, ProductIdentity


CRITICAL_FIELDS = {
    "ram": ("manufacturer", "ram_type", "capacity_gb", "speed_mhz"),
    "cpu": ("manufacturer", "model_number"),
    "motherboard": ("manufacturer", "model_number", "chipset"),
    "gpu": ("manufacturer", "chipset", "vram_gb", "variant"),
    "psu": ("manufacturer", "model_number", "wattage"),
    "ssd": ("manufacturer", "model_number", "capacity_gb"),
}


def missing_critical(identity: ProductIdentity | None) -> list[str]:
    if identity is None:
        return ["product_identity"]
    return [field for field in CRITICAL_FIELDS.get(identity.category, ())
            if getattr(identity, field, None) is None]


def assess_listing_group(offers: list[Listing]) -> dict:
    usable = [offer for offer in offers if offer.in_stock is True and offer.effective_price is not None]
    prices = [offer.effective_price for offer in usable if offer.effective_price and offer.effective_price > 0]
    counts = Counter(offer.source or offer.retailer for offer in offers)
    duplicate_sources = sorted(source for source, count in counts.items() if count > 1)
    reasons: list[str] = []
    same_retailer_spread = False
    for source in duplicate_sources:
        source_prices = [offer.effective_price for offer in usable
                         if (offer.source or offer.retailer) == source and offer.effective_price]
        if len(source_prices) > 1 and max(source_prices) / min(source_prices) > 1.25:
            same_retailer_spread = True
            reasons.append(f"suspicious_same_retailer_price_spread: {source}")
    ratio = max(prices) / min(prices) if len(prices) > 1 else 1.0
    if ratio > 1.5:
        reasons.append("price spread exceeds 50%")
    missing = sorted({field for offer in offers for field in missing_critical(offer.product_identity)})
    if missing and len(offers) > 1:
        reasons.append("missing critical identity fields: " + ", ".join(missing))
    suspicious = bool(duplicate_sources or ratio > 1.5)
    if duplicate_sources:
        reasons.insert(0, "same retailer has multiple offers: " + ", ".join(duplicate_sources))
    return {
        "suspicious": suspicious,
        "suspicious_reasons": reasons,
        "same_retailer_duplicate": bool(duplicate_sources),
        "same_retailer_price_spread": same_retailer_spread,
        "price_ratio": round(ratio, 3),
        "price_spread_pct": round((ratio - 1) * 100, 2),
        "exclude_from_builder": suspicious,
        "exclude_from_market_median": ratio > 2,
        "missing_critical_fields": missing,
    }

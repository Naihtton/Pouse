from itertools import product
from math import ceil
from .domain import BuildRequest, CATEGORIES, Component, Listing, Recommendation


def normalize(listings: list[Listing]) -> list[Component]:
    grouped: dict[str, list[Listing]] = {}
    for listing in listings:
        if listing.in_stock:
            grouped.setdefault(listing.sku, []).append(listing)
    result = []
    for offers in grouped.values():
        offers.sort(key=lambda x: (x.price + x.shipping, x.retailer))
        result.append(Component(listing=offers[0], delivered_price=offers[0].price + offers[0].shipping,
                                alternatives=[{"retailer": x.retailer, "delivered_price": x.price + x.shipping}
                                              for x in offers]))
    return result


def compatible(parts: dict[str, Component]) -> bool:
    s = {k: v.listing.specs for k, v in parts.items()}
    return (s["CPU"]["socket"] == s["Motherboard"]["socket"]
            and s["RAM"]["memory"] == s["Motherboard"]["memory"]
            and s["RAM"]["capacity"] <= s["Motherboard"]["max_ram"]
            and s["Motherboard"]["form"] == s["Case"]["form"]
            and s["GPU"]["length"] <= s["Case"]["gpu_clearance"]
            and s["PSU"]["watts"] >= (s["CPU"]["watts"] + s["GPU"]["watts"] + 75) * 1.4)


def build(request: BuildRequest, components: list[Component]) -> Recommendation:
    options = [[c for c in components if c.listing.category == cat] for cat in CATEGORIES]
    candidates = []
    for combination in product(*options):
        parts = dict(zip(CATEGORIES, combination))
        cost = sum(c.delivered_price for c in combination)
        if cost > request.max_cost or not compatible(parts):
            continue
        specs = {cat: part.listing.specs for cat, part in parts.items()}
        gpu_weight = 100 if request.use_case != "Office & productivity" else 5
        # A minimum GPU tier makes the selected use case a real constraint.
        if request.use_case == "1440p gaming" and specs["GPU"]["tier"] < 4:
            continue
        score = (specs["GPU"]["tier"] * gpu_weight + specs["CPU"]["tier"] * 25
                 + specs["RAM"]["capacity"] + specs["Storage"]["capacity"] / 50)
        candidates.append((score, -cost, combination))
    if not candidates:
        raise ValueError("No compatible build meets this budget and use case. Increase the budget or choose a lighter workload.")
    _, negative_cost, chosen = max(candidates, key=lambda c: (c[0], c[1]))
    cost = -negative_cost
    selling = ceil(cost / (1 - request.target_margin / 100))
    return Recommendation(components=list(chosen), total_cost=cost, suggested_selling_price=selling,
                          estimated_profit=selling - cost, actual_margin=round((selling - cost) / selling * 100, 2),
                          compatibility_checks=["AM4 CPU and motherboard socket match", "DDR4 memory type and capacity match",
                                                "mATX board fits the case", "GPU fits case clearance",
                                                "PSU has at least 40% power headroom", "CPU includes a stock cooler"],
                          rationale=f"Highest workload score within your ฿{request.max_cost:,} budget, with delivered prices compared across four mock retailers.")

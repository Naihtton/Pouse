from collections import Counter
from math import ceil
import os
from .domain import (BuildCandidate, BuildRequest, CATEGORIES, CompatibilityCheck,
                     CompatibilityResult, Component, Listing, Recommendation)
from .compatibility import extract_compatibility
from .product_identity import match_products, parse_product_identity
from .market_quality import assess_listing_group


def group_market_listings(listings: list[Listing], require_available: bool = True) -> list[list[Listing]]:
    """Group conservatively using the shared identity matcher."""
    groups: list[list[Listing]] = []
    canonical_groups: dict[str, list[Listing]] = {}
    unmatched_groups: list[list[Listing]] = []
    for listing in listings:
        if require_available and (listing.in_stock is not True or listing.shipping is None):
            continue
        if listing.product_identity is None:
            listing.product_identity = parse_product_identity(
                listing.category, listing.title or listing.name
            )
        if listing.canonical_product_id:
            target = canonical_groups.get(listing.canonical_product_id)
            if target is None:
                target = []
                canonical_groups[listing.canonical_product_id] = target
                groups.append(target)
            target.append(listing)
            continue
        target = None
        # Collector fixtures and newly parsed rows without persisted canonical
        # IDs retain the conservative matcher fallback. Stored possible matches
        # remain separate because their canonical IDs differ.
        for group in unmatched_groups:
            representative = group[0]
            if match_products(listing.product_identity, representative.product_identity).matched:
                target = group
                break
        if target is None:
            target = [listing]
            groups.append(target)
            unmatched_groups.append(target)
        else:
            target.append(listing)
    return groups


def _looks_like_required_component(listing: Listing) -> bool:
    text = (listing.title or listing.name).upper()
    if listing.category == "Case":
        if any(marker in text for marker in ("FAN SPLITTER", "CASE FAN", "RISER CABLE",
                                              "UPGRADE KIT", "FRONT PANEL", "BRACKET")):
            return False
        return "CASE" in text or "CHASSIS" in text
    return True


def normalize(listings: list[Listing], diagnostics: dict | None = None) -> list[Component]:
    for listing in listings:
        if listing.product_identity is None:
            listing.product_identity = parse_product_identity(
                listing.category, listing.title or listing.name
            )
        current = extract_compatibility(
            listing.category, listing.title or listing.name,
            str(listing.specs.get("retailer_specification", "")), listing.product_identity,
        )
        for key, value in current.items():
            listing.specs.setdefault(key, value)
        # Older snapshots contain a parser default rather than published board
        # capacity. Do not treat that value as a compatibility fact.
        if listing.category == "Motherboard" and "max_ram_source" not in listing.specs:
            listing.specs.pop("max_ram", None)
    if diagnostics is not None:
        diagnostics["inventory_rejections"] = {
            "unsupported_source": sum((item.source or item.retailer) not in {"JIB", "Advice"}
                                      for item in listings),
            "out_of_stock": sum(item.in_stock is not True for item in listings),
            "unknown_shipping": sum(item.shipping is None for item in listings),
            "expired_offer": sum(item.freshness == "expired" for item in listings),
            "suspicious_product_group": 0,
            "accessory_or_wrong_product": sum(not _looks_like_required_component(item)
                                               for item in listings),
        }
    result = []
    for offers in group_market_listings([item for item in listings if _looks_like_required_component(item)]):
        offers = [offer for offer in offers
                  if offer.data_mode == "live" and offer.freshness != "expired"
                  and (offer.source or offer.retailer) in {"JIB", "Advice"}]
        if not offers:
            continue
        quality = assess_listing_group(offers)
        if quality["exclude_from_builder"]:
            if diagnostics is not None:
                diagnostics["inventory_rejections"]["suspicious_product_group"] += 1
            continue
        preferred = os.getenv("PREFERRED_RETAILER", "JIB")
        offers.sort(key=lambda x: (x.price + x.shipping, 0 if x.retailer == preferred else 1, x.retailer))
        best_price = offers[0].price + offers[0].shipping
        best_sources = sorted({x.retailer for x in offers if x.price + x.shipping == best_price})
        result.append(Component(listing=offers[0], delivered_price=best_price,
                    alternatives=[{"retailer": x.retailer, "source": x.source,
                                   "data_mode": x.data_mode, "sku": x.sku,
                                   "price": x.price,
                                   "delivered_price": x.price + (x.shipping or 0),
                                   "url": x.url, "freshness": x.freshness}
                                              for x in offers], best_sources=best_sources,
                    suspicious=quality["suspicious"],
                    suspicious_reasons=quality["suspicious_reasons"]))
    return result


CASE_FIT = {
    "MINI-ITX": {"MINI-ITX", "MATX", "ATX", "E-ATX"},
    "MATX": {"MATX", "ATX", "E-ATX"},
    "ATX": {"ATX", "E-ATX"},
    "E-ATX": {"E-ATX"},
}


def _form(value: str) -> str:
    return value.upper().replace("MICRO-ATX", "MATX").replace("M-ATX", "MATX")


def check_compatibility(parts: dict[str, Component]) -> CompatibilityResult:
    if set(parts) != set(CATEGORIES):
        missing = sorted(set(CATEGORIES) - set(parts))
        return CompatibilityResult(compatible=False, checks=[CompatibilityCheck(
            rule="required_components", status="fail", critical=True,
            details=f"Missing required components: {', '.join(missing)}",
        )])
    s = {key: value.listing.specs for key, value in parts.items()}
    checks: list[CompatibilityCheck] = []

    def compare(rule: str, left, right, label: str, critical: bool = True):
        if left is None or right is None:
            checks.append(CompatibilityCheck(rule=rule, status="unknown", critical=critical,
                                             details=f"{label} data is incomplete"))
        elif left == right:
            checks.append(CompatibilityCheck(rule=rule, status="pass", critical=critical,
                                             details=f"{label}: {left} matches"))
        else:
            checks.append(CompatibilityCheck(rule=rule, status="fail", critical=critical,
                                             details=f"{label}: {left} does not match {right}"))

    compare("cpu_socket", s["CPU"].get("socket"), s["Motherboard"].get("socket"),
            "CPU and motherboard socket")
    compare("ram_generation", s["RAM"].get("memory"), s["Motherboard"].get("memory"),
            "RAM and motherboard generation")

    capacity, maximum = s["RAM"].get("capacity"), s["Motherboard"].get("max_ram")
    if capacity is None or maximum is None:
        checks.append(CompatibilityCheck(rule="ram_capacity", status="unknown", critical=False,
                                         details="Motherboard maximum RAM capacity is not known"))
    else:
        checks.append(CompatibilityCheck(
            rule="ram_capacity", status="pass" if capacity <= maximum else "fail", critical=True,
            details=f"{capacity}GB RAM is {'within' if capacity <= maximum else 'above'} the {maximum}GB board limit",
        ))

    board_form, case_form = s["Motherboard"].get("form"), s["Case"].get("form")
    if not board_form or not case_form:
        checks.append(CompatibilityCheck(rule="case_form_factor", status="unknown", critical=False,
                                         details="Board or case form-factor support is unavailable"))
    else:
        fits = _form(case_form) in CASE_FIT.get(_form(board_form), set())
        checks.append(CompatibilityCheck(
            rule="case_form_factor", status="pass" if fits else "fail", critical=True,
            details=f"{board_form} motherboard {'fits' if fits else 'does not fit'} the {case_form} case",
        ))

    gpu_length, clearance = s["GPU"].get("length"), s["Case"].get("gpu_clearance")
    if gpu_length is None or clearance is None:
        checks.append(CompatibilityCheck(rule="gpu_case_clearance", status="unknown", critical=False,
                                         details="Reliable GPU length or case clearance is unavailable"))
    else:
        fits = gpu_length <= clearance
        checks.append(CompatibilityCheck(
            rule="gpu_case_clearance", status="pass" if fits else "fail", critical=True,
            details=f"{gpu_length}mm GPU {'fits within' if fits else 'exceeds'} {clearance}mm clearance",
        ))

    cpu_watts, gpu_watts, psu_watts = (s["CPU"].get("watts"), s["GPU"].get("watts"),
                                       s["PSU"].get("watts"))
    if None in (cpu_watts, gpu_watts, psu_watts):
        checks.append(CompatibilityCheck(rule="psu_capacity", status="unknown", critical=True,
                                         details="CPU, GPU, or PSU power data is unavailable"))
    else:
        required = ceil((cpu_watts + gpu_watts + 75) * 1.4)
        sufficient = psu_watts >= required
        checks.append(CompatibilityCheck(
            rule="psu_capacity", status="pass" if sufficient else "fail", critical=True,
            details=f"{psu_watts}W PSU {'meets' if sufficient else 'is below'} the {required}W requirement",
        ))

    # Platform-family support is reported only when both collectors provide it.
    cpu_platform, board_platform = s["CPU"].get("platform"), s["Motherboard"].get("platform")
    if cpu_platform is None or board_platform is None:
        checks.append(CompatibilityCheck(rule="platform_family", status="unknown", critical=False,
                                         details="Platform-family support was not published by both products"))
    else:
        compare("platform_family", cpu_platform, board_platform, "CPU and motherboard platform")

    valid = not any(check.status == "fail" or (check.status == "unknown" and check.critical)
                    for check in checks)
    return CompatibilityResult(compatible=valid, checks=checks)


def compatible(parts: dict[str, Component]) -> bool:
    return check_compatibility(parts).compatible


def _workload(use_case: str) -> str:
    if "gaming" in use_case.lower():
        return "gaming"
    if "office" in use_case.lower():
        return "office"
    return "workstation"


def _matches_preference(component: Component, preference: str | None) -> bool:
    if not preference:
        return True
    listing = component.listing
    identity = listing.product_identity
    text = " ".join(filter(None, (
        listing.title, listing.name, listing.brand, listing.model,
        identity.canonical_name if identity else None,
        identity.model_number if identity else None,
        identity.chipset if identity else None,
        identity.gpu_vendor if identity else None,
    ))).lower()
    return preference.lower() in text


def _scores(parts: dict[str, Component], workload: str, landed_cost: int,
            required_ram: int, required_storage: int) -> dict[str, float]:
    specs = {key: value.listing.specs for key, value in parts.items()}
    cpu = min(100.0, specs["CPU"]["tier"] * 20.0)
    gpu = min(100.0, specs["GPU"]["tier"] * 20.0)
    memory = min(100.0, specs["RAM"]["capacity"] / required_ram * 70.0)
    storage = min(100.0, specs["Storage"]["capacity"] / required_storage * 70.0)
    weights = {
        "gaming": (0.25, 0.55, 0.12, 0.08),
        "office": (0.50, 0.05, 0.25, 0.20),
        "workstation": (0.45, 0.25, 0.18, 0.12),
    }
    performance = cpu * weights[workload][0] + gpu * weights[workload][1]
    performance += memory * weights[workload][2] + storage * weights[workload][3]
    value = performance / landed_cost * 10000
    return {"cpu": round(cpu, 2), "gpu": round(gpu, 2), "memory": round(memory, 2),
            "storage": round(storage, 2), "gaming": round(0.3 * cpu + 0.7 * gpu, 2),
            "performance": round(performance, 2), "value": round(value, 2)}


def _workload_minimums(request: BuildRequest, workload: str) -> dict[str, int]:
    resolution = request.target_resolution
    if request.use_case == "1440p gaming":
        resolution = "1440p"
    gpu_tiers = {None: 2, "1080p": 2, "1440p": 4, "4k": 5}
    defaults = {
        "gaming": {"cpu_tier": 2, "gpu_tier": gpu_tiers[resolution], "ram_gb": 16, "storage_gb": 500},
        "office": {"cpu_tier": 2, "gpu_tier": 1, "ram_gb": 8, "storage_gb": 256},
        "workstation": {"cpu_tier": 3, "gpu_tier": 2, "ram_gb": 32, "storage_gb": 1000},
    }[workload]
    return {**defaults, "ram_gb": request.required_ram_gb or defaults["ram_gb"],
            "storage_gb": request.required_storage_gb or defaults["storage_gb"]}


def _meets_workload(parts: dict[str, Component], minimums: dict[str, int]) -> bool:
    specs = {key: value.listing.specs for key, value in parts.items()}
    return (
        specs["CPU"]["tier"] >= minimums["cpu_tier"]
        and specs["GPU"]["tier"] >= minimums["gpu_tier"]
        and specs["RAM"]["capacity"] >= minimums["ram_gb"]
        and specs["Storage"]["capacity"] >= minimums["storage_gb"]
    )


def _business_cost(parts: dict[str, Component]) -> dict[str, int]:
    parts_cost = sum(item.listing.price for item in parts.values())
    shipping = sum(item.delivered_price - item.listing.price for item in parts.values())
    assembly = max(0, int(os.getenv("ASSEMBLY_COST", "500")))
    packaging = max(0, int(os.getenv("PACKAGING_COST", "150")))
    reserve_pct = max(0.0, float(os.getenv("WARRANTY_RESERVE_PCT", "2")))
    reserve = ceil((parts_cost + shipping) * reserve_pct / 100)
    return {"parts": parts_cost, "shipping": shipping, "assembly": assembly,
            "packaging": packaging, "warranty": reserve,
            "landed": parts_cost + shipping + assembly + packaging + reserve}


def _selling_metrics(request: BuildRequest, landed_cost: int) -> tuple[int, int, int, float]:
    minimum_margin = request.minimum_margin
    minimum_selling = ceil(landed_cost / (1 - minimum_margin / 100))
    selling = request.selling_price_target or minimum_selling
    profit = selling - landed_cost
    margin = round(profit / selling * 100, 2)
    return minimum_selling, selling, profit, margin


def _bounded_pool(items: list[Component], category: str, limit: int) -> list[Component]:
    def tier(item: Component) -> int:
        return int(item.listing.specs.get("tier", 0))
    cheapest = sorted(items, key=lambda item: (item.delivered_price, item.listing.sku))
    if category not in {"CPU", "GPU"}:
        return cheapest[:limit]
    value = sorted(items, key=lambda item: (item.delivered_price / max(1, tier(item)),
                                             item.delivered_price, item.listing.sku))
    performance = sorted(items, key=lambda item: (-tier(item), item.delivered_price, item.listing.sku))
    selected: list[Component] = []
    for item in value[:max(1, limit // 2)] + performance[:max(1, limit // 2)] + cheapest:
        if item not in selected:
            selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def search_builds(request: BuildRequest, components: list[Component],
                  candidate_limit: int = 5,
                  locked_component_id: str | None = None) -> tuple[Recommendation, dict]:
    compatibility_fields = {
        "CPU": {"socket", "tier", "watts"},
        "Motherboard": {"socket", "memory"},
        "GPU": {"tier", "watts"},
        "RAM": {"memory", "capacity"},
        "Storage": {"capacity"},
        "PSU": {"watts"},
        "Case": set(),
    }
    fields_for_signature = {
        "CPU": ("socket", "tier", "watts"),
        "Motherboard": ("socket", "memory", "form"),
        "GPU": ("tier", "watts", "length"), "RAM": ("memory", "capacity"),
        "Storage": ("capacity",), "PSU": ("watts",),
        "Case": ("form", "gpu_clearance"),
    }
    options: dict[str, list[Component]] = {}
    for category in CATEGORIES:
        best: dict[tuple, Component] = {}
        for component in components:
            if component.listing.category != category or not compatibility_fields[category].issubset(component.listing.specs):
                continue
            signature = tuple(component.listing.specs.get(field) for field in fields_for_signature[category])
            if signature not in best or component.delivered_price < best[signature].delivered_price:
                best[signature] = component
        options[category] = list(best.values())
    if locked_component_id:
        locked = [item for item in components
                  if item.listing.canonical_product_id == locked_component_id]
        if not locked:
            raise ValueError("Locked component is not available in the current valid inventory")
        locked_category = locked[0].listing.category
        required_fields = compatibility_fields.get(locked_category)
        if required_fields is None:
            raise ValueError("Locked component has an unsupported category")
        locked = [item for item in locked
                  if required_fields.issubset(item.listing.specs)]
        if not locked:
            raise ValueError("Locked component lacks required compatibility data")
        options[locked_category] = locked
    workload = _workload(request.use_case)
    minimums = _workload_minimums(request, workload)
    cpu_preference = request.preferred_cpu or request.preferred_cpu_brand
    gpu_preference = request.preferred_gpu or request.preferred_gpu_vendor
    if cpu_preference:
        options["CPU"] = [item for item in options["CPU"] if _matches_preference(item, cpu_preference)]
    if gpu_preference:
        options["GPU"] = [item for item in options["GPU"] if _matches_preference(item, gpu_preference)]
    options["RAM"] = [item for item in options["RAM"]
                      if item.listing.specs["capacity"] >= minimums["ram_gb"]]
    options["Storage"] = [item for item in options["Storage"]
                          if item.listing.specs["capacity"] >= minimums["storage_gb"]]
    options["CPU"] = [item for item in options["CPU"]
                      if item.listing.specs["tier"] >= minimums["cpu_tier"]]
    options["GPU"] = [item for item in options["GPU"]
                      if item.listing.specs["tier"] >= minimums["gpu_tier"]]
    pool_limit = max(3, min(20, int(os.getenv("BUILD_POOL_LIMIT", "8"))))
    for category in CATEGORIES:
        options[category] = _bounded_pool(options[category], category, pool_limit)
    diagnostics = {"candidate_pool_sizes": {key: len(value) for key, value in options.items()},
                   "combinations_evaluated": 0, "rejection_counts": Counter(),
                   "compatibility_failures": Counter(), "ranking_inputs": []}
    raw_candidates: list[dict] = []
    branch_limit = max(2, pool_limit // 2)
    for cpu in options["CPU"]:
      boards = [item for item in options["Motherboard"]
                if item.listing.specs.get("socket") == cpu.listing.specs.get("socket")]
      diagnostics["rejection_counts"]["cpu_socket_mismatch"] += len(options["Motherboard"]) - len(boards)
      for board in sorted(boards, key=lambda item: item.delivered_price)[:branch_limit]:
        ram_options = [item for item in options["RAM"]
                       if item.listing.specs.get("memory") == board.listing.specs.get("memory")]
        diagnostics["rejection_counts"]["ram_generation_mismatch"] += len(options["RAM"]) - len(ram_options)
        for ram in ram_options[:branch_limit]:
          case_options = []
          for case in options["Case"]:
            board_form, case_form = board.listing.specs.get("form"), case.listing.specs.get("form")
            if board_form and case_form and _form(case_form) not in CASE_FIT.get(_form(board_form), set()):
                diagnostics["rejection_counts"]["case_form_factor_mismatch"] += 1
            else:
                case_options.append(case)
          for case in case_options[:branch_limit]:
            for gpu in options["GPU"]:
              if workload == "gaming" and cpu.listing.specs["tier"] > gpu.listing.specs["tier"] + 1:
                  diagnostics["rejection_counts"]["unbalanced_cpu_gpu"] += 1
                  continue
              length, clearance = gpu.listing.specs.get("length"), case.listing.specs.get("gpu_clearance")
              if length and clearance and length > clearance:
                  diagnostics["rejection_counts"]["gpu_too_long"] += 1
                  continue
              required_watts = (cpu.listing.specs["watts"] + gpu.listing.specs["watts"] + 75) * 1.4
              psus = [item for item in options["PSU"] if item.listing.specs["watts"] >= required_watts]
              if not psus:
                  diagnostics["rejection_counts"]["insufficient_psu"] += 1
                  continue
              appropriate = [item for item in psus
                             if item.listing.specs["watts"] <= required_watts + 400]
              psus = appropriate or psus
              psus.sort(key=lambda item: (item.delivered_price,
                                          item.listing.specs["watts"] - required_watts,
                                          item.listing.sku))
              psu = psus[0]
              for storage in options["Storage"]:
                chosen = (cpu, board, gpu, ram, storage, psu, case)
                diagnostics["combinations_evaluated"] += 1
                mapping = {item.listing.category: item for item in chosen}
                compatibility = check_compatibility(mapping)
                if not compatibility.compatible:
                    diagnostics["rejection_counts"]["compatibility_failure"] += 1
                    for check in compatibility.checks:
                        if check.status == "fail" or (check.status == "unknown" and check.critical):
                            diagnostics["compatibility_failures"][check.rule] += 1
                    continue
                if not _meets_workload(mapping, minimums):
                    diagnostics["rejection_counts"]["workload_minimum"] += 1
                    continue
                costs = _business_cost(mapping)
                if request.max_cost is not None and costs["landed"] > request.max_cost:
                    diagnostics["rejection_counts"]["over_budget"] += 1
                    continue
                required_sell, selling, profit, margin = _selling_metrics(request, costs["landed"])
                if margin + 1e-9 < request.minimum_margin:
                    diagnostics["rejection_counts"]["margin_below_target"] += 1
                    continue
                scores = _scores(mapping, workload, costs["landed"], minimums["ram_gb"],
                                 minimums["storage_gb"])
                raw_candidates.append({"chosen": chosen, "parts": mapping, "costs": costs,
                                       "required_sell": required_sell, "selling": selling,
                                       "profit": profit, "margin": margin, "scores": scores,
                                       "compatibility": compatibility})
    if not raw_candidates:
        preference = " Check the preferred CPU/GPU filters." if request.preferred_cpu or request.preferred_gpu else ""
        raise ValueError(
            "No compatible build meets the budget, margin, workload, and component preferences."
            + preference
        )

    # De-duplicate exact part combinations, then score every valid build against
    # this request. The score balances performance/value, acquisition cost and
    # margin headroom; deterministic tie-breakers keep results stable.
    unique: dict[tuple[str, ...], dict] = {}
    for row in raw_candidates:
        key = tuple(item.listing.canonical_product_id or item.listing.sku for item in row["chosen"])
        unique[key] = row
    rows = list(unique.values())
    max_performance = max(row["scores"]["performance"] for row in rows) or 1
    max_value = max(row["scores"]["value"] for row in rows) or 1
    costs = [row["costs"]["landed"] for row in rows]
    cost_span = max(costs) - min(costs)
    margins = [row["margin"] for row in rows]
    margin_span = max(margins) - min(margins)
    maximum_cost = max(costs)
    minimum_margin = min(margins)
    for row in rows:
        performance_value = (0.6 * row["scores"]["performance"] / max_performance
                             + 0.4 * row["scores"]["value"] / max_value)
        cost_score = 1 if not cost_span else (maximum_cost - row["costs"]["landed"]) / cost_span
        margin_score = 1 if not margin_span else (row["margin"] - minimum_margin) / margin_span
        row["ranking"] = round((0.55 * performance_value + 0.25 * cost_score
                                + 0.20 * margin_score) * 100, 2)
    rows.sort(key=lambda row: (-row["ranking"], -row["scores"]["performance"],
                               -row["scores"]["value"], row["costs"]["landed"],
                               tuple(item.listing.sku for item in row["chosen"])))

    def core_signature(row: dict) -> tuple[str, ...]:
        important = {"CPU", "GPU", "RAM", "Storage"}
        return tuple(item.listing.canonical_product_id or item.listing.sku
                     for item in row["chosen"] if item.listing.category in important)

    winners = [
        ("Best Overall", rows[0]),
        ("Best Performance", max(rows, key=lambda row: (
            row["scores"]["performance"], row["scores"]["value"]))),
        ("Best Value", max(rows, key=lambda row: (
            row["scores"]["value"], row["scores"]["performance"]))),
        ("Best Margin", max(rows, key=lambda row: (
            row["margin"], -row["costs"]["landed"]))),
    ]
    selected_rows: list[dict] = []
    labels: dict[int, list[str]] = {}
    for label, row in winners:
        labels.setdefault(id(row), []).append(label)
        if all(row is not existing for existing in selected_rows):
            selected_rows.append(row)
    for row in rows:
        if len(selected_rows) >= candidate_limit:
            break
        if (core_signature(row) not in {core_signature(existing) for existing in selected_rows}
                and all(row is not existing for existing in selected_rows)):
            labels[id(row)] = ["Balanced Alternative"]
            selected_rows.append(row)
    for row in rows:
        if len(selected_rows) >= candidate_limit:
            break
        if all(row is not existing for existing in selected_rows):
            labels[id(row)] = ["Balanced Alternative"]
            selected_rows.append(row)
    selected = [(labels[id(row)], row) for row in selected_rows]

    candidates: list[BuildCandidate] = []
    resolution = request.target_resolution or ("1440p" if request.use_case == "1440p gaming" else None)
    context = " ".join(part for part in (resolution, workload.title()) if part)
    for rank, (badges, row) in enumerate(selected[:candidate_limit], 1):
        chosen, cost = row["chosen"], row["costs"]["landed"]
        compatibility = row["compatibility"]
        label = " · ".join(badges)
        candidates.append(BuildCandidate(
            rank=rank, name=f"{label} {context}".strip(), badges=badges, components=list(chosen),
            total_parts_cost=row["costs"]["parts"], shipping_cost=row["costs"]["shipping"],
            assembly_cost=row["costs"]["assembly"], packaging_cost=row["costs"]["packaging"],
            warranty_reserve=row["costs"]["warranty"], landed_cost=cost,
            target_sell_price=request.selling_price_target, required_sell_price=row["required_sell"],
            total_cost=cost, suggested_selling_price=row["selling"],
            estimated_profit=row["profit"], actual_margin=row["margin"],
            performance_score=row["scores"]["performance"], value_score=row["scores"]["value"],
            ranking_score=row["ranking"], scores=row["scores"], compatibility=compatibility,
            compatibility_checks=[check.details for check in compatibility.checks
                                  if check.status == "pass"],
            rationale=(f"{label} for {context.lower()}: performance {row['scores']['performance']:.2f}, "
                       f"value {row['scores']['value']:.2f}, landed cost ฿{cost:,}, and "
                       f"{row['margin']:.2f}% expected margin. Every component uses its cheapest "
                       "eligible live JIB or Advice offer."),
        ))
        diagnostics["ranking_inputs"].append({"name": candidates[-1].name,
                                               "landed_cost": cost, "margin_pct": row["margin"],
                                               "scores": row["scores"], "ranking_score": row["ranking"]})
    primary = candidates[0]
    recommendation = Recommendation(
        components=primary.components, total_cost=primary.total_cost,
        suggested_selling_price=primary.suggested_selling_price,
        estimated_profit=primary.estimated_profit, actual_margin=primary.actual_margin,
        compatibility_checks=primary.compatibility_checks, rationale=primary.rationale,
        candidates=candidates,
    )
    diagnostics["rejection_counts"] = dict(diagnostics["rejection_counts"])
    diagnostics["compatibility_failures"] = dict(diagnostics["compatibility_failures"])
    diagnostics["valid_combinations"] = len(rows)
    diagnostics["returned_candidates"] = len(candidates)
    return recommendation, diagnostics


def build(request: BuildRequest, components: list[Component], candidate_limit: int = 5) -> Recommendation:
    return search_builds(request, components, candidate_limit)[0]

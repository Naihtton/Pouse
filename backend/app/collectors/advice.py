"""Conservative HTTP collector for Advice's public server-rendered catalog."""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from ..domain import CATEGORIES, Listing
from ..compatibility import extract_compatibility
from ..product_identity import parse_product_identity
from . import known_catalog_item
from .cache import ListingTTLCache

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://www.advice.co.th"
CATEGORY_PRICE_LIST = f"{BASE_URL}/nescen-n001/product/pricelist"
CATEGORY_PATHS = {
    "CPU": "computer-hardware/00023",
    "GPU": "computer-hardware/01762",
    "Motherboard": "computer-hardware/00027",
    "RAM": "computer-hardware/01397",
    "Storage": "harddisk-storage/028679",
    "PSU": "computer-hardware/00896",
    "Case": "computer-hardware/00897",
}
CATEGORY_ALIASES = {
    "cpu": "CPU", "processor": "CPU", "cpu / processor": "CPU",
    "gpu": "GPU", "vga": "GPU", "graphic card": "GPU", "graphics card": "GPU",
    "motherboard": "Motherboard", "mainboard": "Motherboard",
    "ram": "RAM", "memory": "RAM", "memory / ram": "RAM",
    "ssd": "Storage", "storage": "Storage", "harddisk storage": "Storage",
    "psu": "PSU", "power supply": "PSU", "psu / power supply": "PSU",
    "case": "Case", "computer case": "Case",
}
OUT_OF_STOCK = ("OUT OF STOCK", "SOLD OUT", "สินค้าหมด", "ไม่พร้อมจำหน่าย")
IN_STOCK = ("IN STOCK", "พร้อมจำหน่าย", "มีสินค้า")
_CACHE = ListingTTLCache()


def map_category(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return CATEGORY_ALIASES.get(normalized)


def parse_price(value: str) -> int | None:
    """Parse a Thai-baht display price without accepting unrelated decimals."""
    match = re.search(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{2,})(?:\.\d{1,2})?", value)
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
    except ValueError:
        return None


def extract_product_id(url: str) -> str | None:
    match = re.search(r"/productdetail/(A\d+)(?:[/?#]|$)", url, re.IGNORECASE)
    return match.group(1).upper() if match else None


def parse_pagination(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    for node in soup.select(".pagination-wrap button, .pagination a, [aria-label*='page' i]"):
        text = node.get_text(" ", strip=True)
        if text.isdigit():
            pages.append(int(text))
    return max(pages, default=1)


def _node_price(node: Tag | None) -> int | None:
    if node is None:
        return None
    for attribute in ("data-sale-price", "data-price", "content"):
        value = node.get(attribute)
        if isinstance(value, str) and (parsed := parse_price(value)) is not None:
            return parsed
    return parse_price(node.get_text(" ", strip=True))


def _prices(row: Tag) -> tuple[int | None, int | None]:
    sale = row.select_one(
        ".sale-price, .price-sale, .special-price, [data-sale-price], "
        ".catalog-product-price strong, .legacy-price-value"
    )
    regular = row.select_one("del, s, .regular-price, .price-regular, .old-price")
    sale_price = _node_price(sale)
    regular_price = _node_price(regular)
    if sale_price is not None:
        return sale_price, regular_price

    price_container = row.select_one(".catalog-product-price, .product-price, [data-price]")
    current = _node_price(price_container)
    if current is not None and regular_price == current:
        # A lone crossed-out number is not a purchasable current price.
        current = None
    return current, regular_price


def _stock(row: Tag) -> tuple[bool | None, str]:
    explicit = row.get("data-stock")
    if isinstance(explicit, str):
        normalized = explicit.strip().lower()
        if normalized in {"1", "true", "in-stock", "instock"}:
            return True, "explicit"
        if normalized in {"0", "false", "out-of-stock", "outofstock"}:
            return False, "explicit"
        return None, "unknown"
    text = row.get_text(" ", strip=True).upper()
    if any(marker in text for marker in OUT_OF_STOCK):
        return False, "explicit"
    if any(marker in text for marker in IN_STOCK):
        return True, "explicit"
    # Advice's public price-list only renders active branch catalog items. It
    # has no per-row delivery stock field, so this means catalog availability.
    return True, "active_catalog_listing"


class AdviceCollector:
    retailer = "Advice"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.last_error: str | None = None
        self.coverage: dict[str, dict] = {}

    @staticmethod
    def parse_results(html: str, requested_category: str) -> list[Listing]:
        category = map_category(requested_category)
        if category not in CATEGORIES:
            raise ValueError(f"Unsupported Advice category: {requested_category}")
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("tr")
        listings: list[Listing] = []
        collected_at = datetime.now(timezone.utc)
        for row in rows:
            link = row.select_one('a[href*="/product/productdetail/"]')
            if link is None:
                continue
            try:
                title = link.get_text(" ", strip=True)
                url = urljoin(BASE_URL, link.get("href", ""))
                product_id = extract_product_id(url)
                if not title or not product_id or not url.startswith(
                    f"{BASE_URL}/nescen-n001/product/productdetail/"
                ):
                    LOGGER.warning("Skipping malformed Advice product row in %s", category)
                    continue
                price, regular_price = _prices(row)
                if price is None:
                    LOGGER.warning("Skipping Advice product without a current price: %s", title)
                    continue
                spec_node = row.select_one(".catalog-product-spec")
                if spec_node is None and row.select_one(".legacy-price-value"):
                    cells = row.select("td")
                    spec_node = cells[1] if len(cells) > 1 else None
                specification = spec_node.get_text(" ", strip=True) if spec_node else ""
                stock, stock_basis = _stock(row)
                identity_text = f"{title} {specification}".strip()
                identity = parse_product_identity(category, identity_text)
                known_item = known_catalog_item(category, identity_text)
                compatibility_specs = known_item[1] if known_item else {}
                specs: dict[str, str | int] = dict(compatibility_specs)
                specs.update({key: value for key, value in
                              extract_compatibility(category, title, specification, identity).items()
                              if key not in specs})
                if specification:
                    specs["retailer_specification"] = specification
                specs.update({
                    "retailer_product_id": product_id,
                    "stock_basis": stock_basis,
                    "fulfillment": "store_pickup",
                })
                listings.append(Listing(
                    sku=f"ADVICE-{product_id}", name=title, title=title,
                    category=category, retailer=AdviceCollector.retailer,
                    source=AdviceCollector.retailer, brand=identity.manufacturer,
                    model=identity.model_number or identity.canonical_name,
                    price=price, regular_price=regular_price, shipping=0,
                    in_stock=stock, url=url, collected_at=collected_at,
                    specs=specs, product_identity=identity, data_mode="live",
                ))
            except (KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("Skipping malformed Advice product row: %s", exc)
        if not listings:
            LOGGER.warning("Advice returned no recognized product rows for %s", category)
        return listings

    async def collect(self, category: str | None = None) -> list[Listing]:
        self.last_error = None
        self.coverage = {}
        categories = [map_category(category)] if category else list(CATEGORY_PATHS)
        if any(value not in CATEGORIES for value in categories):
            raise ValueError(f"Unsupported Advice category: {category}")
        cache_key = ",".join(categories)
        ttl = 0 if self.transport else float(os.getenv(
            "ADVICE_CACHE_TTL_SECONDS", os.getenv("COLLECTOR_CACHE_TTL_SECONDS", "900")
        ))
        cached = _CACHE.get_with_metadata(cache_key, ttl)
        if cached is not None:
            listings, self.coverage = cached
            return listings

        listings: list[Listing] = []
        headers = {"User-Agent": "Pouse PC sourcing prototype"}
        async with httpx.AsyncClient(
            headers=headers, timeout=15, follow_redirects=True, transport=self.transport
        ) as client:
            for index, name in enumerate(categories):
                if index:
                    await asyncio.sleep(0.3)
                try:
                    section, menu_id = CATEGORY_PATHS[name].split("/")
                    response = None
                    retries = max(0, min(3, int(os.getenv("ADVICE_REQUEST_RETRIES", "2"))))
                    for attempt in range(retries + 1):
                        try:
                            response = await client.get(
                                CATEGORY_PRICE_LIST, params={"cate": section, "sub": menu_id}
                            )
                            response.raise_for_status()
                            break
                        except httpx.HTTPError:
                            if attempt >= retries:
                                raise
                            await asyncio.sleep(0 if self.transport else 0.5 * (2 ** attempt))
                    assert response is not None
                    parsed = self.parse_results(response.text, name)
                    soup = BeautifulSoup(response.text, "html.parser")
                    reported_ids = {
                        product_id for link in soup.select('a[href*="/product/productdetail/"]')
                        if (product_id := extract_product_id(str(link.get("href", ""))))
                    }
                    listings.extend(parsed)
                    self.coverage[name] = {
                        "source": self.retailer,
                        "category": "SSD" if name == "Storage" else name,
                        "collected": len({item.sku for item in parsed}),
                        "reported_total": len(reported_ids),
                        "pages_fetched": 1,
                        "page_size": len(reported_ids),
                        "truncated": False,
                        "status": "success" if parsed else "empty",
                    }
                except (httpx.HTTPError, ValueError):
                    self.coverage[name] = {
                        "source": self.retailer,
                        "category": "SSD" if name == "Storage" else name,
                        "collected": 0, "reported_total": None, "pages_fetched": 0,
                        "page_size": None, "truncated": False, "status": "error",
                    }
                    LOGGER.warning("Advice collection failed for category %s", name, exc_info=True)
        failed = [name for name, item in self.coverage.items() if item["status"] == "error"]
        if failed:
            self.last_error = f"Advice collection failed for: {', '.join(failed)}"
        cheapest_by_sku: dict[str, Listing] = {}
        for listing in listings:
            current = cheapest_by_sku.get(listing.sku)
            if current is None or listing.price < current.price:
                cheapest_by_sku[listing.sku] = listing
        result = list(cheapest_by_sku.values())
        for name in categories:
            if name in self.coverage:
                self.coverage[name]["collected"] = sum(
                    item.category == name for item in result
                )
        _CACHE.set(cache_key, result, self.coverage)
        return result


__all__ = ["AdviceCollector", "CATEGORY_PATHS", "extract_product_id", "map_category",
           "parse_pagination", "parse_price"]

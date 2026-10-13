"""Conservative HTTP collector for JIB's public product category pages."""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..domain import CATEGORIES, Listing
from ..compatibility import extract_compatibility
from ..product_identity import parse_product_identity
from . import known_catalog_item
from .cache import ListingTTLCache

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://www.jib.co.th"
CATEGORY_PATHS = {
    "CPU": ("2/43",),
    "GPU": ("2/51",),
    "Motherboard": ("2/46",),
    # JIB's /2/53 scope is desktop memory. Notebook and NAS/server RAM are
    # separate categories and intentionally excluded for parity with Advice.
    "RAM": ("2/53",),
    # Internal Storage means SSD. JIB separates internal 2.5-inch and M.2 SSDs.
    "Storage": ("3/1028", "3/1471"),
    "PSU": ("3/185",),
    "Case": ("3/184",),
}
CATEGORY_PATTERNS = (
    ("CPU", r"\bCPU\b|\bPROCESSOR\b|ซีพียู"),
    ("GPU", r"\bVGA\b|\bGRAPHICS? CARD\b|\bGEFORCE\b|\bRADEON\b|การ์ดแสดงผล"),
    ("Motherboard", r"\bMAINBOARD\b|\bMOTHERBOARD\b|เมนบอร์ด"),
    ("RAM", r"\bRAM\b|\bMEMORY\b|หน่วยความจำ"),
    ("Storage", r"\bSSD\b|\bSOLID STATE\b|\bNVME\b|\bM\.2\b"),
    ("PSU", r"\bPOWER SUPPLY\b|\bPSU\b|อุปกรณ์จ่ายไฟ"),
    ("Case", r"\bCOMPUTER CASE\b|\bPC CASE\b|\bCASE\b|เคสคอม"),
)
CATEGORY_ALIASES = {
    "cpu": "CPU", "gpu": "GPU", "vga": "GPU", "graphics card": "GPU",
    "motherboard": "Motherboard", "mainboard": "Motherboard", "ram": "RAM",
    "memory": "RAM", "ssd": "Storage", "storage": "Storage", "psu": "PSU",
    "power supply": "PSU", "case": "Case",
}
BRANDS = {
    "GIGABYTE": "Gigabyte", "ASROCK": "ASRock", "SAPPHIRE": "Sapphire",
    "POWERCOLOR": "PowerColor", "COOLER MASTER": "Cooler Master",
    "WESTERN DIGITAL": "Western Digital", "THERMALTAKE": "Thermaltake",
    "FRACTAL DESIGN": "Fractal Design", "G.SKILL": "G.Skill", "ADATA": "ADATA",
    "KINGSTON": "Kingston", "CORSAIR": "Corsair", "SAMSUNG": "Samsung",
    "DEEPCOOL": "DeepCool", "ANTEC": "Antec", "SEAGATE": "Seagate",
    "LEXAR": "Lexar", "TEAMGROUP": "TeamGroup", "CRUCIAL": "Crucial",
    "PATRIOT": "Patriot", "TRANSCEND": "Transcend", "HIKSEMI": "Hiksemi",
    "INNO3D": "Inno3D", "ZOTAC": "Zotac", "GALAX": "GALAX", "PNY": "PNY",
    "XFX": "XFX", "PALIT": "Palit", "COLORFUL": "Colorful", "MSI": "MSI",
    "ASUS": "ASUS", "AMD": "AMD", "INTEL": "Intel", "NVIDIA": "NVIDIA",
    "NZXT": "NZXT", "WD": "WD", "FSP": "FSP", "AEROCOOL": "AeroCool",
    "BIOSTAR": "Biostar", "LIAN LI": "Lian Li", "BE QUIET!": "be quiet!",
}
CATEGORY_PREFIX = re.compile(
    r"^(?:CPU|VGA|GRAPHICS? CARD|MAINBOARD|MOTHERBOARD|RAM|MEMORY|SSD|"
    r"POWER SUPPLY|PSU|COMPUTER CASE|PC CASE|CASE)\s*(?:\([^)]*\))?\s*[-:]*\s*",
    re.IGNORECASE,
)
OUT_OF_STOCK = ("สินค้าหมดชั่วคราว", "สินค้าหมด", "OUT OF STOCK", "UNAVAILABLE")
IN_STOCK = ("ซื้อเลย", "ADD TO CART", "BUY NOW")
FREE_SHIPPING = ("ส่งฟรี", "FREE SHIPPING")
_CACHE = ListingTTLCache()


def parse_coverage(html: str) -> tuple[int | None, int, int | None]:
    """Return JIB's reported item total, page count, and page size."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    total_match = re.search(r"ทั้งหมด\s*([\d,]+)\s*รายการ", text)
    pages_match = re.search(r"จำนวน\s*([\d,]+)\s*หน้า", text)
    size_match = re.search(r"หน้าละ\s*([\d,]+)\s*รายการ", text)
    total = int(total_match.group(1).replace(",", "")) if total_match else None
    pages = int(pages_match.group(1).replace(",", "")) if pages_match else 1
    size = int(size_match.group(1).replace(",", "")) if size_match else None
    return total, max(1, pages), size


def map_category(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    if normalized in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[normalized]
    for category, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, value, re.IGNORECASE):
            return category
    return None


def parse_price(value: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?", value)
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
    except ValueError:
        return None


def normalize_brand_model(title: str) -> tuple[str | None, str]:
    cleaned = re.sub(r"\s+", " ", title).strip()
    brand = None
    brand_alias = None
    for alias, display_name in sorted(BRANDS.items(), key=lambda pair: len(pair[0]), reverse=True):
        match = re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", cleaned, re.IGNORECASE)
        if match:
            brand, brand_alias = display_name, alias
            break

    without_category = CATEGORY_PREFIX.sub("", cleaned).strip()
    if brand_alias:
        brand_match = re.search(rf"(?<![A-Z0-9]){re.escape(brand_alias)}(?![A-Z0-9])", without_category, re.IGNORECASE)
        if brand_match:
            without_category = without_category[brand_match.end():].strip(" -:")
    model_match = re.search(r"\b(?:RYZEN|CORE\s+ULTRA|CORE\s+I[3579]|RTX|GTX|RX)\b", without_category, re.IGNORECASE)
    if model_match:
        without_category = without_category[model_match.start():]
    model = re.sub(r"\s+", " ", without_category).strip(" -:") or cleaned
    model = model.title()
    for source, display in (("Rtx", "RTX"), ("Gtx", "GTX"), ("Rx", "RX"),
                            ("Ddr4", "DDR4"), ("Ddr5", "DDR5"), ("Nvme", "NVMe"),
                            ("Oc", "OC"), ("Am4", "AM4"),
                            ("Am5", "AM5"), ("Gddr", "GDDR"), ("Sff", "SFF"),
                            ("Pcie", "PCIe"), ("Atx", "ATX"), ("Matx", "mATX"),
                            ("Ghz", "GHz"), ("Mhz", "MHz")):
        model = re.sub(rf"\b{source}\b", display, model)
    model = re.sub(r"\bXt\b", "XT", model)
    model = re.sub(r"\b(\d{1,2})Gb\b", r"\1GB", model, flags=re.IGNORECASE)
    model = re.sub(r"(?<![A-Za-z])(\d{1,2})G(?![A-Za-z0-9])", r"\1GB", model, flags=re.IGNORECASE)
    model = re.sub(r"\bGddr(?=\d)", "GDDR", model, flags=re.IGNORECASE)
    model = re.sub(r"\bTb\b", "TB", model, flags=re.IGNORECASE)
    return brand, model


def _product_id(url: str) -> str:
    match = re.search(r"/readProduct/(\d+)/", url)
    if match:
        return f"JIB-{match.group(1)}"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", url).strip("-")[-40:]
    return f"JIB-{slug}"


class JibCollector:
    retailer = "JIB"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.last_error: str | None = None
        self.coverage: dict[str, dict] = {}

    @staticmethod
    def parse_results(html: str, requested_category: str) -> list[Listing]:
        requested = map_category(requested_category)
        if requested not in CATEGORIES:
            raise ValueError(f"Unsupported JIB category: {requested_category}")
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".divboxpro")
        if not cards:
            LOGGER.warning("JIB returned no recognized product cards for %s", requested)
        listings = []
        collected_at = datetime.now(timezone.utc)
        for card in cards:
            try:
                title_node = card.select_one(".promo_name")
                price_node = card.select_one(".price_total")
                product_link = card.select_one('a[href*="/web/product/readProduct/"]')
                if not title_node or not product_link:
                    LOGGER.warning("Skipping malformed JIB product card in %s results", requested)
                    continue
                title = title_node.get_text(" ", strip=True)
                # Category URLs are exact retailer scopes. Classifying from a
                # title alone drops valid products whose title omits "RAM" or
                # "CASE", so the requested category is authoritative here.
                category = requested
                if not price_node:
                    LOGGER.warning("Skipping JIB product without price: %s", title)
                    continue
                price = parse_price(price_node.get_text(" ", strip=True))
                if price is None:
                    LOGGER.warning("Skipping JIB product with unparseable price: %s", title)
                    continue
                url = urljoin(BASE_URL, product_link.get("href", ""))
                if not url.startswith(f"{BASE_URL}/web/product/readProduct/"):
                    LOGGER.warning("Skipping JIB product with invalid URL: %s", title)
                    continue
                card_text = card.get_text(" ", strip=True)
                upper_text = card_text.upper()
                stock = False if any(text.upper() in upper_text for text in OUT_OF_STOCK) else (
                    True if any(text.upper() in upper_text for text in IN_STOCK) else None
                )
                shipping = 0 if any(text.upper() in upper_text for text in FREE_SHIPPING) else None
                brand, model = normalize_brand_model(title)
                identity = parse_product_identity(category, title)
                known_item = known_catalog_item(category, title)
                sku = _product_id(url)
                compatibility_specs = known_item[1] if known_item else {}
                specs = dict(compatibility_specs)
                specs.update({key: value for key, value in
                              extract_compatibility(category, title, "", identity).items()
                              if key not in specs})
                if brand:
                    specs["brand"] = brand
                specs["model"] = model
                specs["product_url"] = url
                specs["shipping_known"] = int(shipping is not None)
                listings.append(Listing(
                    sku=sku, name=title, title=title, category=category,
                    retailer=JibCollector.retailer, source=JibCollector.retailer,
                    brand=brand, model=model, price=price, shipping=shipping,
                    in_stock=stock, url=url, collected_at=collected_at, specs=specs,
                    product_identity=identity, data_mode="live",
                ))
            except (KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("Skipping malformed JIB product card: %s", exc)
        return listings

    async def collect(self, category: str | None = None) -> list[Listing]:
        self.last_error = None
        self.coverage = {}
        categories = [map_category(category)] if category else list(CATEGORY_PATHS)
        if any(value not in CATEGORIES for value in categories):
            raise ValueError(f"Unsupported JIB category: {category}")
        cache_key = ",".join(categories)
        ttl = 0 if self.transport else float(os.getenv(
            "JIB_CACHE_TTL_SECONDS", os.getenv("COLLECTOR_CACHE_TTL_SECONDS", "900")
        ))
        cached = _CACHE.get_with_metadata(cache_key, ttl)
        if cached is not None:
            listings, self.coverage = cached
            return listings
        headers = {"User-Agent": "Pouse PC sourcing prototype"}
        max_pages = max(1, min(10, int(os.getenv("JIB_MAX_PAGES_PER_CATEGORY", "10"))))
        retries = max(0, min(3, int(os.getenv("JIB_REQUEST_RETRIES", "2"))))
        listings = []
        async with httpx.AsyncClient(
            headers=headers, timeout=15, follow_redirects=True, transport=self.transport
        ) as client:
            request_number = 0
            for name in categories:
                category_listings: list[Listing] = []
                reported_total = 0
                totals_known = True
                pages_fetched = 0
                page_size: int | None = None
                truncated = False
                failed = False
                try:
                    for scope in CATEGORY_PATHS[name]:
                        first_total = None
                        scope_pages = 1
                        scope_page_size = None
                        for page_index in range(max_pages):
                            if page_index >= scope_pages:
                                break
                            if request_number:
                                await asyncio.sleep(0 if self.transport else 0.3)
                            offset = page_index * (scope_page_size or 100)
                            url = f"{BASE_URL}/web/product/product_list/{scope}/{offset}"
                            response = None
                            for attempt in range(retries + 1):
                                try:
                                    response = await client.get(url)
                                    response.raise_for_status()
                                    break
                                except httpx.HTTPError:
                                    if attempt >= retries:
                                        raise
                                    await asyncio.sleep(0 if self.transport else 0.5 * (2 ** attempt))
                            assert response is not None
                            request_number += 1
                            if page_index == 0:
                                first_total, scope_pages, scope_page_size = parse_coverage(response.text)
                                if first_total is None:
                                    totals_known = False
                                else:
                                    reported_total += first_total
                                if scope_page_size is not None:
                                    page_size = scope_page_size
                                if scope_pages > max_pages:
                                    truncated = True
                            category_listings.extend(self.parse_results(response.text, name))
                            pages_fetched += 1
                except (httpx.HTTPError, ValueError):
                    failed = True
                    LOGGER.warning("JIB collection failed for category %s", name, exc_info=True)
                listings.extend(category_listings)
                unique_count = len({item.sku for item in category_listings})
                self.coverage[name] = {
                    "source": self.retailer,
                    "category": "SSD" if name == "Storage" else name,
                    "collected": unique_count,
                    "reported_total": reported_total if totals_known else None,
                    "pages_fetched": pages_fetched,
                    "page_size": page_size,
                    "truncated": truncated,
                    "status": "error" if failed else ("success" if unique_count else "empty"),
                }
        failed_categories = [name for name, item in self.coverage.items() if item["status"] == "error"]
        if failed_categories:
            self.last_error = f"JIB collection failed for: {', '.join(failed_categories)}"
        cheapest_by_sku = {}
        for listing in listings:
            current = cheapest_by_sku.get(listing.sku)
            if current is None or listing.price < current.price:
                cheapest_by_sku[listing.sku] = listing
        result = list(cheapest_by_sku.values())
        for name in categories:
            if name in self.coverage:
                self.coverage[name]["collected"] = sum(item.category == name for item in result)
        _CACHE.set(cache_key, result, self.coverage)
        return result

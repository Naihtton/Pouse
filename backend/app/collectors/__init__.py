"""The two supported live retailer collectors."""
import re
from typing import Protocol

from ..domain import Listing


class Collector(Protocol):
    async def collect(self) -> list[Listing]: ...


# Curated compatibility metadata for products the Builder can validate confidently.
CATALOG = [
    ("r5600", "AMD Ryzen 5 5600 (stock cooler)", "CPU", 3400, {"socket": "AM4", "tier": 2, "watts": 65}),
    ("r5700x", "AMD Ryzen 7 5700G (stock cooler)", "CPU", 5400, {"socket": "AM4", "tier": 3, "watts": 65}),
    ("b550", "MSI B550M PRO-VDH", "Motherboard", 2600, {"socket": "AM4", "memory": "DDR4", "form": "mATX", "max_ram": 128}),
    ("rx6600", "Radeon RX 6600 8GB", "GPU", 6200, {"tier": 2, "watts": 132, "length": 200}),
    ("rx7600", "Radeon RX 7600 8GB", "GPU", 8700, {"tier": 3, "watts": 165, "length": 240}),
    ("rx7700", "Radeon RX 7700 XT 12GB", "GPU", 14200, {"tier": 4, "watts": 245, "length": 280}),
    ("ram16", "Kingston Fury 16GB (2×8GB) DDR4", "RAM", 1400, {"memory": "DDR4", "capacity": 16}),
    ("ram32", "Kingston Fury 32GB (2×16GB) DDR4", "RAM", 2400, {"memory": "DDR4", "capacity": 32}),
    ("ssd500", "WD Blue SN580 500GB NVMe", "Storage", 1300, {"capacity": 500, "interface": "M.2 NVMe"}),
    ("ssd1000", "WD Blue SN580 1TB NVMe", "Storage", 2200, {"capacity": 1000, "interface": "M.2 NVMe"}),
    ("psu550", "Corsair CX550 80+ Bronze", "PSU", 1700, {"watts": 550}),
    ("psu650", "Corsair CX650 80+ Bronze", "PSU", 2200, {"watts": 650}),
    ("case", "Airflow mATX case with fans", "Case", 1200, {"form": "mATX", "gpu_clearance": 320}),
]


def known_catalog_item(category: str, title: str) -> tuple[str, dict[str, str | int]] | None:
    """Attach existing Builder compatibility data only for confidently known products."""
    normalized = re.sub(r"\s+", " ", title).upper()
    patterns = {
        "r5600": r"\bAMD\s+(?:AM4\s+)?RYZEN\s+5\s+5600\b(?![A-Z0-9])",
        "r5700x": r"\bAMD\s+(?:AM4\s+)?RYZEN\s+7\s+5700G\b",
        "b550": r"\bMSI\s+B550M\s+PRO[- ]VDH\b(?!\s+WIFI)",
        "ram16": r"\bKINGSTON\s+FURY\s+BEAST\b.*\b16GB\s*\(\s*8GB\s*[X×]\s*2\s*\).*\bDDR4\b",
        "ram32": r"\bKINGSTON\s+FURY\s+BEAST\b.*\b32GB\s*\(\s*16GB\s*[X×]\s*2\s*\).*\bDDR4\b",
        "ssd500": r"\bWD\s+(?:BLUE\s+)?SN580\b.*\b500\.?\s*GB\b",
        "ssd1000": r"\bWD\s+(?:BLUE\s+)?SN580\b.*\b(?:1\.?\s*TB|1000\s*GB)\b",
        "psu550": r"\bCORSAIR\s+CX\s*550\b",
        "psu650": r"\bCORSAIR\s+CX\s*650\b",
    }
    for sku, pattern in patterns.items():
        catalog = next(item for item in CATALOG if item[0] == sku)
        if catalog[2] == category and re.search(pattern, normalized):
            return sku, catalog[4]
    return None


from .advice import AdviceCollector
from .jib import JibCollector


def default_collectors() -> list[Collector]:
    return [JibCollector(), AdviceCollector()]


__all__ = ["AdviceCollector", "CATALOG", "Collector", "JibCollector",
           "default_collectors", "known_catalog_item"]

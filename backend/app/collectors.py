"""Collectors return a common schema; replace mocks with API/feed collectors later."""
from typing import Protocol
from .domain import Listing


class Collector(Protocol):
    async def collect(self) -> list[Listing]: ...


# All prices and availability are synthetic, not retailer quotes.
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


class MockCollector:
    def __init__(self, retailer: str, offset: int, shipping: int):
        self.retailer, self.offset, self.shipping = retailer, offset, shipping

    async def collect(self) -> list[Listing]:
        return [Listing(sku=sku, name=name, category=category, retailer=self.retailer,
                        price=price + self.offset + (i % 3) * 40,
                        shipping=self.shipping, specs=specs)
                for i, (sku, name, category, price, specs) in enumerate(CATALOG)]


def default_collectors() -> list[Collector]:
    return [MockCollector("JIB", 100, 0), MockCollector("Advice", 0, 60),
            MockCollector("Shopee", -120, 90), MockCollector("Lazada", -80, 50)]

"""Conservative PC compatibility facts, kept separate from product identity."""
import math
import re

from .domain import ProductIdentity

GPU_PROFILES = {
    "GT 210": (1, 30), "GT 610": (1, 30), "GT 710": (1, 30),
    "GT 1030": (1, 30), "GTX 1660 SUPER": (2, 125), "RTX 3050": (2, 130),
    "RTX 5060": (3, 145), "RTX 5060 Ti": (3, 180), "RTX 5070": (4, 250),
    "RTX 5070 Ti": (4, 300), "RX 7600": (3, 165), "RX 7700 XT": (4, 245),
    "RX 9070 XT": (5, 304),
}

CPU_PROFILES = {
    "5500GT": ("AM4", 2, 65), "5600": ("AM4", 2, 65), "5600G": ("AM4", 2, 65),
    "5700G": ("AM4", 3, 65), "5700X": ("AM4", 3, 65),
}

CASE_CLEARANCE = {
    "AEROCOOL CS-110": 375,
    "GIGABYTE C102": 410,
    "ASUS A31 PLUS": 380,
}


def extract_compatibility(category: str, title: str, specification: str,
                          identity: ProductIdentity) -> dict[str, str | int]:
    text = f"{title} {specification}"
    specs: dict[str, str | int] = {}
    if category == "CPU" and identity.model_number in CPU_PROFILES:
        socket, tier, watts = CPU_PROFILES[identity.model_number]
        specs.update(socket=socket, tier=tier, watts=watts,
                     platform="AMD" if socket.startswith("AM") else "Intel")
    elif category == "Motherboard":
        socket = re.search(r"\b(?:SOCKET\s*)?(AM[45]|LGA\s*\d{4}|\d{4})\b", text, re.I)
        memory = re.search(r"\bDDR\s*([345])\b", text, re.I)
        form = re.search(r"\b(MICRO[- ]ATX|M-?ATX|MINI[- ]ITX|E-ATX|ATX)\b", text, re.I)
        if socket and memory and form:
            raw_socket = socket.group(1).upper().replace(" ", "")
            specs["socket"] = raw_socket if raw_socket.startswith(("AM", "LGA")) else f"LGA{raw_socket}"
            specs["platform"] = "AMD" if specs["socket"].startswith("AM") else "Intel"
            specs["memory"] = f"DDR{memory.group(1)}"
            raw_form = form.group(1).upper()
            specs["form"] = "mATX" if raw_form in {"MICRO-ATX", "MICRO ATX", "M-ATX", "MATX"} else raw_form
    elif category == "RAM" and identity.ram_type and identity.capacity_gb:
        specs.update(memory=identity.ram_type, capacity=identity.capacity_gb)
    elif category == "Storage" and identity.capacity_gb:
        specs["capacity"] = identity.capacity_gb
        if identity.interface:
            specs["interface"] = identity.interface
    elif category == "GPU" and identity.chipset in GPU_PROFILES:
        length = re.search(r"VGA\s+LENGTH\s*(\d+(?:\.\d+)?)\s*MM", text, re.I)
        if length:
            tier, watts = GPU_PROFILES[identity.chipset]
            specs.update(tier=tier, watts=watts, length=math.ceil(float(length.group(1))))
    elif category == "PSU" and identity.wattage:
        specs["watts"] = identity.wattage
    elif category == "Case" and identity.form_factor:
        specs["form"] = identity.form_factor
        clearance = next((value for model, value in CASE_CLEARANCE.items()
                          if model in text.upper()), None)
        if clearance:
            specs["gpu_clearance"] = clearance
    return specs

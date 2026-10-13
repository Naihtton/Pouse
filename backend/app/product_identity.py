"""Deterministic product identity parsing and cross-retailer matching."""
import re

from .domain import ProductIdentity, ProductMatchResult

CATEGORY_NAMES = {
    "cpu": "cpu", "processor": "cpu", "gpu": "gpu", "vga": "gpu",
    "graphics card": "gpu", "motherboard": "motherboard", "mainboard": "motherboard",
    "ram": "ram", "memory": "ram", "ssd": "ssd", "storage": "ssd",
    "psu": "psu", "power supply": "psu", "case": "case", "computer case": "case",
}

MANUFACTURERS = {
    "COOLER MASTER": "Cooler Master", "WESTERN DIGITAL": "Western Digital",
    "FRACTAL DESIGN": "Fractal Design", "BE QUIET!": "be quiet!",
    "POWER COLOR": "PowerColor", "POWERCOLOR": "PowerColor",
    "TEAM GROUP": "TeamGroup", "TEAMGROUP": "TeamGroup", "LIAN LI": "Lian Li",
    "GIGABYTE": "Gigabyte", "ASROCK": "ASRock", "SAPPHIRE": "Sapphire",
    "THERMALTAKE": "Thermaltake", "G.SKILL": "G.Skill", "KINGSTON": "Kingston",
    "CORSAIR": "Corsair", "SAMSUNG": "Samsung", "DEEPCOOL": "DeepCool",
    "SEAGATE": "Seagate", "CRUCIAL": "Crucial", "TRANSCEND": "Transcend",
    "HIKSEMI": "Hiksemi", "INNO3D": "Inno3D", "COLORFUL": "Colorful",
    "LEADTEK": "Leadtek", "AEROCOOL": "AeroCool", "BIOSTAR": "Biostar",
    "PATRIOT": "Patriot", "ANTEC": "Antec", "LEXAR": "Lexar", "ZOTAC": "Zotac",
    "BLACKBERRY": "Blackberry", "APACER": "Apacer", "PNY": "PNY",
    "LONGWELL": "Longwell", "MONTECH": "Montech", "NUBWO": "Nubwo",
    "SANDISK": "SanDisk", "HYTE": "HYTE",
    "GALAX": "GALAX", "PALIT": "Palit", "ASUS": "ASUS", "MSI": "MSI",
    "ADATA": "ADATA", "NZXT": "NZXT", "FSP": "FSP", "XFX": "XFX",
    "NVIDIA": "NVIDIA", "INTEL": "Intel", "AMD": "AMD", "WD": "WD",
}

GPU_PARTNERS = {
    name for alias, name in MANUFACTURERS.items()
    if alias not in {"NVIDIA", "AMD", "INTEL"}
}

GPU_VARIANTS = (
    "ROG ASTRAL", "ROG STRIX", "TUF GAMING", "AORUS MASTER", "STEEL LEGEND",
    "GAMING OC", "WINDFORCE OC", "TWIN EDGE OC", "VENTUS 3X OC", "VENTUS 2X OC",
    "SHADOW 2X PLUS", "VENTUS 2X PLUS", "INFINITY 2", "INFINITY2",
    "VENTUS 2X E OC", "SHADOW 3X OC", "SHADOW 2X OC",
    "NITRO GAMING PHANTOMLINK POLAR OC",
    "NITRO GAMING PHANTOMLINK OC", "LOW PROFILE", "LOWPROFILE", "EX DVI",
    "1-CLICK OC", "PRIME", "PROART", "DUAL", "PULSE", "NITRO+", "TAICHI",
    "REAPER", "HELLHOUND", "RED DEVIL", "PHANTOM GAMING", "CHALLENGER",
    "EAGLE OC", "INSPIRE 3X OC", "AERO OC", "GAMING X TRIO", "GAMING TRIO",
    "ICHILL X3", "TWIN X2 OC", "TWIN X2", "X3 OC", "TWIN EDGE",
    "WINDFORCE", "EAGLE", "PURE",
    "GAMING", "T1",
)

CATEGORY_PREFIX = re.compile(
    r"^(?:CPU|PROCESSOR|VGA|GRAPHICS? CARD|MAINBOARD|MOTHERBOARD|RAM|MEMORY|SSD|"
    r"POWER SUPPLY|PSU|COMPUTER CASE|PC CASE|CASE)\s*(?:\([^)]*\))?\s*[-:]*\s*",
    re.IGNORECASE,
)


def normalize_category(category: str) -> str:
    value = re.sub(r"\s+", " ", category.strip().lower())
    if value not in CATEGORY_NAMES:
        raise ValueError(f"Unsupported product category: {category}")
    return CATEGORY_NAMES[value]


def _slug(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def group_identity_key(identity: ProductIdentity) -> str:
    """Stable canonical candidate key, separate from retailer-derived model codes."""
    if identity.category == "gpu" and identity.manufacturer and identity.chipset:
        return ":".join(("gpu", _slug(identity.manufacturer), _slug(identity.chipset),
                         _slug(identity.variant) or "base", str(identity.vram_gb or 0)))
    return identity.identity_key


def _display_code(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" ()[]")).upper()


def _manufacturer(title: str, category: str) -> str | None:
    matches = []
    for alias, display in MANUFACTURERS.items():
        match = re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", title, re.IGNORECASE)
        if match:
            matches.append((match.start(), -len(alias), display))
    if not matches:
        return None
    matches.sort()
    if category == "gpu":
        partner = next((item[2] for item in matches if item[2] in GPU_PARTNERS), None)
        if partner:
            return partner
    return matches[0][2]


def _parenthetical_model_code(title: str) -> str | None:
    for value in reversed(re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", title)):
        value = next(part for part in value if part)
        candidate = _display_code(value)
        if len(candidate) > 100:
            continue
        looks_like_code = re.search(r"[-/]", candidate) or re.search(r"[A-Z]+\d{3,}[A-Z]*", candidate)
        if (re.search(r"\d", candidate) and looks_like_code
                and not re.search(r"SOCKET|GDDR|\d+\s*(?:GB|TB)|\d+\s*[X×]\s*\d+", candidate)
                and not re.fullmatch(r"(?:OC/)?D[3-7]", candidate)):
            return candidate
    return None


def _embedded_gpu_model_code(title: str) -> str | None:
    patterns = (
        r"\b(?:TUF|DUAL|PRIME|PROART|ROG|GV|ZT|NE|VCG)-[A-Z0-9]+(?:-[A-Z0-9]+)+\b",
        r"\b\d+GD[3-7][A-Z0-9]*(?:-[A-Z0-9]+)+\b",
    )
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match and re.search(r"(?:RTX|RX|N\d{4}|\d{3,4}|\d+GD[3-7])", match.group(0), re.IGNORECASE):
            return _display_code(match.group(0))
    return None


def _gpu_chipset(title: str) -> str | None:
    match = re.search(
        r"\b(RTX\s+(?:PRO\s+)?[A-Z]?\s*\d{3,4}(?:\s*(?:TI|SUPER))?|"
        r"GTX\s+\d{3,4}(?:\s*TI)?|GT\s+\d{3,4}|RX\s+\d{4}(?:\s*(?:XT|XTX))?|"
        r"ARC\s+[AB]\d{3})\b", title, re.IGNORECASE,
    )
    if not match:
        # Retailer model codes commonly omit the space after RTX/RX.
        match = re.search(r"\b(RTX|GTX|GT|RX)(\d{3,4})(TI|XT|XTX|SUPER)?\b", title, re.IGNORECASE)
        if not match:
            return None
        raw = " ".join(part for part in match.groups() if part)
    else:
        raw = match.group(1)
    raw = re.sub(r"\s+", " ", raw.upper()).strip()
    return re.sub(r"\bTI\b", "Ti", raw)


def _gpu_variant(title: str, chipset: str | None) -> str | None:
    upper = title.upper()
    primary = next((variant for variant in GPU_VARIANTS if re.search(
        rf"(?<![A-Z0-9]){re.escape(variant)}(?![A-Z0-9])", upper
    )), None)
    if primary is None and "AORUS" in upper and "MASTER" in upper:
        primary = "AORUS MASTER"
    modifiers = []
    for marker, display in (("EVO", "EVO"), ("WHITE", "White"), ("BTF", "BTF"),
                            ("SFF", "SFF"), ("HATSUNE MIKU", "Hatsune Miku")):
        if marker in upper and (not primary or marker not in primary):
            modifiers.append(display)
    if ("OC EDITION" in upper or re.search(r"\(\s*OC(?:/D[3-7])?\s*\)", upper)) \
            and (not primary or "OC" not in primary):
        modifiers.append("OC")
    if not primary and not modifiers:
        return None
    words = primary.title() if primary else ""
    replacements = {"Rog": "ROG", "Tuf": "TUF", "Oc": "OC", "X3": "X3",
                    "Nitro+": "NITRO+", "Proart": "ProArt",
                    "Lowprofile": "Low Profile", "Infinity2": "Infinity 2"}
    for source, target in replacements.items():
        words = re.sub(rf"\b{re.escape(source)}\b", target, words)
    return " ".join(part for part in (words, *modifiers) if part)


def _gpu_identity(category: str, title: str, manufacturer: str | None) -> ProductIdentity:
    upper = title.upper()
    chipset = _gpu_chipset(title)
    if "NVIDIA" in upper or "GEFORCE" in upper or (chipset and chipset.startswith(("RTX", "GTX", "GT "))):
        gpu_vendor = "NVIDIA"
    elif "AMD" in upper or "RADEON" in upper or (chipset and chipset.startswith("RX")):
        gpu_vendor = "AMD"
    elif "INTEL" in upper or (chipset and chipset.startswith("ARC")):
        gpu_vendor = "Intel"
    else:
        gpu_vendor = None
    model_number = _parenthetical_model_code(title) or _embedded_gpu_model_code(title)
    variant = _gpu_variant(title, chipset)
    memory_match = re.search(r"\b(G?DDR\s*[3-7]|HBM\s*\d*)\b", upper)
    memory_type = re.sub(r"\s+", "", memory_match.group(1)) if memory_match else None
    token_match = re.search(r"\(\s*(?:OC/)?(D[3-7])\s*\)", upper)
    memory_token = token_match.group(1) if token_match else None
    if memory_type is None and memory_token in {"D6", "D7"}:
        memory_type = f"GDDR{memory_token[-1]}"
    capacities = [int(value) for value in re.findall(r"\b(\d{1,3})\s*G(?:B)?\b", upper)]
    capacities.extend(int(value) for value in re.findall(r"(?:^|[-O])(\d{1,2})G(?:D|\b)", upper))
    vram_gb = max((value for value in capacities if value <= 96), default=None)

    parts = [manufacturer, chipset, variant]
    if vram_gb is not None:
        parts.append(f"{vram_gb}GB")
    if memory_type:
        parts.append(memory_type)
    canonical = " ".join(part for part in parts if part) or re.sub(r"\s+", " ", title).strip()
    if manufacturer and model_number:
        key = f"{_slug(manufacturer)}:{_slug(model_number)}"
    else:
        key = ":".join((category, _slug(manufacturer) or "unknown", _slug(chipset) or "unknown",
                        _slug(variant) or "base", str(vram_gb or 0)))
    return ProductIdentity(
        category=category, manufacturer=manufacturer, model_number=model_number, variant=variant,
        gpu_vendor=gpu_vendor, chipset=chipset, vram_gb=vram_gb, memory_type=memory_type,
        memory_token=memory_token,
        canonical_name=canonical, identity_key=key,
    )


def _cpu_identity(category: str, title: str, manufacturer: str | None) -> ProductIdentity:
    family_match = re.search(r"\b(RYZEN\s+[3579]|CORE\s+ULTRA\s+[3579]|CORE\s+I[3579])\b", title, re.IGNORECASE)
    product_family = family_match.group(1).title() if family_match else None
    if product_family:
        product_family = re.sub(r"\bI([3579])\b", lambda m: f"i{m.group(1)}", product_family)
    model_number = None
    if family_match:
        tail = title[family_match.end():]
        model_match = re.search(r"\b(\d{3,5}[A-Z]{0,3})\b", tail, re.IGNORECASE)
        model_number = _display_code(model_match.group(1)) if model_match else None
        if model_match and re.match(r"\s+PLUS\b", tail[model_match.end():], re.I):
            model_number += " PLUS"
    socket_match = re.search(r"\b(?:SOCKET\s*)?(AM[45]|LGA\s*\d{4})\b", title, re.IGNORECASE)
    cpu_socket = re.sub(r"\s+", "", socket_match.group(1).upper()) if socket_match else None
    suffix_match = re.search(r"\d{3,5}([A-Z]{1,3})\b", model_number or "", re.I)
    cpu_suffix = suffix_match.group(1).upper() if suffix_match else None
    packaging = "tray" if re.search(r"\b(?:TRAY|OEM)\b", title, re.I) else (
        "boxed" if re.search(r"\b(?:BOX(?:ED)?|RETAIL BOX)\b", title, re.I) else None)
    bundle = True if re.search(r"\b(?:BUNDLE|COMBO)\b", title, re.I) else None
    canonical = " ".join(part for part in (manufacturer, product_family, model_number) if part)
    canonical = canonical or re.sub(r"\s+", " ", CATEGORY_PREFIX.sub("", title)).strip()
    key = (f"{_slug(manufacturer)}:{_slug(model_number)}" if manufacturer and model_number else
           ":".join((category, _slug(manufacturer) or "unknown", _slug(product_family) or "unknown",
                     _slug(canonical))))
    return ProductIdentity(category=category, manufacturer=manufacturer, product_family=product_family,
                           model_number=model_number, cpu_socket=cpu_socket, cpu_suffix=cpu_suffix,
                           packaging=packaging, bundle=bundle,
                           canonical_name=canonical, identity_key=key)


def _ram_identity(category: str, title: str, manufacturer: str | None) -> ProductIdentity:
    upper = title.upper()
    ram_match = re.search(r"\bDDR\s*([345])\b", upper)
    ram_type = f"DDR{ram_match.group(1)}" if ram_match else None
    # Accept both 2x16GB and 16GBx2 forms. The total capacity is derived from
    # the kit expression so a retailer's prose cannot silently change it.
    kit = re.search(r"\b(\d+)\s*[X×]\s*(\d+)\s*GB\b", upper)
    reverse_kit = re.search(r"\b(\d+)\s*GB\s*[X×]\s*(\d+)\b", upper)
    kit_count = per_stick = total = None
    if kit:
        kit_count, per_stick = int(kit.group(1)), int(kit.group(2))
        total = kit_count * per_stick
    elif reverse_kit:
        per_stick, kit_count = int(reverse_kit.group(1)), int(reverse_kit.group(2))
        total = kit_count * per_stick
    else:
        cap = re.search(r"\b(\d{1,3})\s*GB\b", upper)
        total = int(cap.group(1)) if cap else None
    speed_patterns = (
        r"DDR\s*[345]\s*[-( ]\s*(\d{4,5})\s*\)?",
        r"\b(?:BUS|SPEED)\s*[: -]?\s*(\d{4,5})\b",
        r"\b(\d{4,5})\s*MHZ\b",
    )
    speed_mhz = next((int(m.group(1)) for p in speed_patterns if (m := re.search(p, upper))), None)
    if re.search(r"\bNON[- ]?ECC\b", upper):
        ecc = False
    elif re.search(r"\bECC\b", upper):
        ecc = True
    else:
        ecc = None
    model_number = _parenthetical_model_code(title)
    if model_number and re.fullmatch(r"(?:\d+GB)?[X×]\d+(?:GB)?", model_number, re.I):
        model_number = None
    parts = [manufacturer, model_number, ram_type]
    if total is not None: parts.append(f"{total}GB")
    if kit_count and per_stick: parts.append(f"{kit_count}x{per_stick}GB")
    if speed_mhz: parts.append(str(speed_mhz))
    canonical = " ".join(x for x in parts if x) or re.sub(r"\s+", " ", title).strip()
    key = ":".join((category, _slug(manufacturer) or "unknown", _slug(model_number) or "nomodel",
                    _slug(ram_type) or "unknown", str(total or 0), str(kit_count or 0),
                    str(per_stick or 0), str(speed_mhz or 0), "ecc" if ecc else "nonecc" if ecc is False else "eccunknown"))
    return ProductIdentity(category=category, manufacturer=manufacturer, model_number=model_number,
        ram_type=ram_type, capacity_gb=total, kit_count=kit_count,
        capacity_per_stick_gb=per_stick, speed_mhz=speed_mhz, ecc=ecc,
        canonical_name=canonical, identity_key=key)


def _motherboard_identity(category: str, title: str, manufacturer: str | None) -> ProductIdentity:
    upper = title.upper()
    # Board names nearly always begin with the chipset token. Keep suffixes
    # such as HVS/HDV and revision markers because they identify distinct SKUs.
    model_match = re.search(r"\b([ABHXZ]\d{3}[A-Z]*(?:-[A-Z0-9]+)+(?:\s+(?:V\d+|R\d+(?:\.\d+)?))?)\b", upper)
    model_number = _display_code(model_match.group(1)) if model_match else None
    if model_number is None:
        cleaned = re.sub(r"\s+", " ", CATEGORY_PREFIX.sub("", title)).strip(" -")
        if manufacturer:
            cleaned = re.sub(re.escape(manufacturer), "", cleaned, count=1, flags=re.I).strip(" -")
        candidate = re.split(r"\s+-\s+(?:AMD|INTEL)\s+SOCKET", cleaned, maxsplit=1, flags=re.I)[0]
        model_number = _display_code(candidate) if re.search(r"[ABHXZ]\d{3}", candidate, re.I) else None
    chipset_match = re.search(r"\b([ABHXZ]\d{3})[A-Z]*\b", upper)
    chipset = chipset_match.group(1) if chipset_match else None
    socket_match = re.search(r"\b(AM[45]|LGA\s*\d{4})\b", upper)
    cpu_socket = re.sub(r"\s+", "", socket_match.group(1)) if socket_match else None
    ram_match = re.search(r"\bDDR\s*([345])\b", upper)
    ram_type = f"DDR{ram_match.group(1)}" if ram_match else None
    form_match = re.search(r"\b(E-ATX|MICRO[- ]ATX|M-?ATX|MINI[- ]ITX|ITX|ATX)\b", upper)
    form_factor = None
    if form_match:
        raw = form_match.group(1)
        form_factor = "mATX" if raw in {"MATX", "M-ATX", "MICRO-ATX", "MICRO ATX"} else raw
    canonical = " ".join(x for x in (manufacturer, model_number) if x) or re.sub(r"\s+", " ", title).strip()
    key = ":".join((category, _slug(manufacturer) or "unknown", _slug(model_number) or "unknown",
                    _slug(ram_type), _slug(cpu_socket)))
    return ProductIdentity(category=category, manufacturer=manufacturer, model_number=model_number,
        chipset=chipset, cpu_socket=cpu_socket, ram_type=ram_type, form_factor=form_factor,
        canonical_name=canonical, identity_key=key)


def _case_identity(category: str, title: str, manufacturer: str | None) -> ProductIdentity:
    """Parse sellable case family, feature variant, and color separately."""
    upper = re.sub(r"\s+", " ", title.upper())
    cleaned = CATEGORY_PREFIX.sub("", upper)
    cleaned = re.sub(r"^(?:E-?ATX|M-?ATX|ATX|ITX)\s+CASE\s*(?:\(NP\))?\s*", "", cleaned)
    if manufacturer:
        cleaned = re.sub(re.escape(manufacturer.upper()), "", cleaned, count=1).strip(" -")

    parenthetical = _parenthetical_model_code(title)
    model_number = parenthetical
    if not model_number:
        tokens = re.findall(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", cleaned)
        ignored = {"CASE", "ARGB", "RGB", "ATX", "MATX", "ITX", "EATX", "USB"}
        for index, token in enumerate(tokens):
            if token in ignored:
                continue
            if re.search(r"[A-Z]", token) and re.search(r"\d", token):
                model_number = token
                break
            if token.isdigit() and index and tokens[index - 1] not in ignored:
                model_number = f"{tokens[index - 1]} {token}"
                break
    if model_number and "UPGRADE KIT" in upper:
        model_number = f"{model_number} UPGRADE KIT"

    variant_markers = (
        (r"\bTOUCH\s+INFINITE\b", "Touch Infinite"),
        (r"\bTEMPERED\s+GLASS\b|\bTG\b", "Tempered Glass"),
        (r"\bGLASS\b", "Glass"),
        (r"\bMESH\b", "Mesh"), (r"\bARGB\b", "ARGB"), (r"\bRGB\b", "RGB"),
        (r"\bRBG\b", "RGB"),
        (r"\bPRO\b", "Pro"), (r"\bPLUS\b", "Plus"), (r"\bMAX\b", "Max"),
    )
    variants = [display for pattern, display in variant_markers if re.search(pattern, upper)]
    variant = " ".join(variants) or None

    color = None
    color_markers = (
        "TARO MILK PURPLE", "BLUEBERRY MILK BLUE", "BLACK/BLACK", "BLACK/RED",
        "BLACK/WHITE", "SNOW WHITE", "ROSE GOLD", "PANDA", "PURPLE", "ORANGE", "YELLOW",
        "GREEN", "PINK", "BLUE", "RED", "WHITE", "BLACK",
    )
    normalized_color_text = upper.replace(" - ", " ")
    for marker in color_markers:
        if marker in normalized_color_text:
            color = marker.title().replace("/", "/")
            break

    form_match = re.search(r"\b(E-ATX|MICRO[- ]ATX|M-?ATX|MINI[- ]ITX|ITX|ATX)\b", upper)
    form_factor = None
    if form_match:
        raw = form_match.group(1)
        form_factor = "mATX" if raw in {"MATX", "M-ATX", "MICRO-ATX", "MICRO ATX"} else raw
    canonical = " ".join(part for part in (manufacturer, model_number, variant, color) if part)
    canonical = canonical or re.sub(r"\s+", " ", title).strip()
    key = ":".join((category, _slug(manufacturer) or "unknown",
                    _slug(model_number) or "unknown", _slug(variant), _slug(color)))
    return ProductIdentity(category=category, manufacturer=manufacturer,
                           model_number=model_number, variant=variant, color=color,
                           form_factor=form_factor, canonical_name=canonical,
                           identity_key=key)


def _generic_identity(category: str, title: str, manufacturer: str | None) -> ProductIdentity:
    cleaned = re.sub(r"\s+", " ", CATEGORY_PREFIX.sub("", title)).strip(" -")
    without_brand = cleaned
    if manufacturer:
        without_brand = re.sub(re.escape(manufacturer), "", without_brand, count=1, flags=re.IGNORECASE).strip(" -")
    parenthetical = _parenthetical_model_code(title)
    model_number = parenthetical
    if not model_number:
        if category == "motherboard":
            candidate = re.split(r"\s+-\s+(?:AMD|INTEL)\s+SOCKET", without_brand, maxsplit=1,
                                 flags=re.IGNORECASE)[0]
            model_number = _display_code(candidate) if re.search(r"\d", candidate) else None
        else:
            candidates = re.findall(r"\b(?=[A-Z0-9-]*\d)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\b",
                                    without_brand, re.IGNORECASE)
            ignored = {"DDR3", "DDR4", "DDR5", "GDDR6", "GDDR7", "NVME", "PCIE"}
            model_number = next((_display_code(value) for value in candidates
                                 if value.upper() not in ignored and not re.fullmatch(r"\d+(?:GB|TB)", value, re.I)), None)
    ram_match = re.search(r"\bDDR\s*([345])\b", title, re.IGNORECASE)
    ram_type = f"DDR{ram_match.group(1)}" if ram_match else None
    capacity_gb = None
    capacity_match = re.search(r"\b(\d+)\s*(TB|GB|G)\b", title, re.IGNORECASE)
    if capacity_match:
        capacity_gb = int(capacity_match.group(1)) * (1000 if capacity_match.group(2).upper() == "TB" else 1)
    watt_match = re.search(r"\b(\d{3,4})\s*W(?:ATT)?\b", title, re.IGNORECASE)
    wattage = int(watt_match.group(1)) if watt_match else None
    kit_match = re.search(r"(?:\(|\b)(\d+)\s*[X×]\s*(\d+)\s*GB", title, re.IGNORECASE)
    kit_count = int(kit_match.group(1)) if kit_match else None
    speed_match = re.search(r"\b(?:DDR[345][ -]?)?(\d{4,5})\s*(?:MHZ)?\b", title, re.IGNORECASE)
    speed_mhz = int(speed_match.group(1)) if category == "ram" and speed_match else None
    interface = None
    if category == "ssd":
        if re.search(r"\bNVME\b", title, re.IGNORECASE):
            interface = "NVMe"
        elif re.search(r"\bSATA\b", title, re.IGNORECASE):
            interface = "SATA"
    rating_match = re.search(r"80\s*(?:PLUS|\+)\s*(BRONZE|SILVER|GOLD|PLATINUM|TITANIUM)", title, re.I)
    efficiency_rating = rating_match.group(1).title() if rating_match else None
    chipset = None
    if category == "motherboard":
        chipset_match = re.search(r"\b([ABHXZ]\d{3}[A-Z]?)\b", title, re.I)
        chipset = chipset_match.group(1).upper() if chipset_match else None
    form_match = re.search(r"\b(E-ATX|MICRO[- ]ATX|M-?ATX|MINI[- ]ITX|ITX|ATX)\b", title, re.IGNORECASE)
    form_factor = None
    if form_match:
        raw_form = form_match.group(1).upper()
        form_factor = "mATX" if raw_form in {"MATX", "M-ATX", "MICRO-ATX", "MICRO ATX"} else raw_form
    canonical = " ".join(part for part in (manufacturer, without_brand) if part) or cleaned
    key_parts = (category, _slug(manufacturer) or "unknown", _slug(model_number) or _slug(without_brand),
                 str(capacity_gb or wattage or 0), _slug(ram_type or form_factor))
    identity_key = (f"{_slug(manufacturer)}:{_slug(model_number)}"
                    if manufacturer and model_number else ":".join(key_parts))
    return ProductIdentity(category=category, manufacturer=manufacturer, model_number=model_number,
                           chipset=chipset, ram_type=ram_type, capacity_gb=capacity_gb,
                           kit_count=kit_count, speed_mhz=speed_mhz, interface=interface,
                           wattage=wattage, efficiency_rating=efficiency_rating,
                           form_factor=form_factor, canonical_name=canonical,
                           identity_key=identity_key)


def parse_product_identity(category: str, title: str) -> ProductIdentity:
    normalized_category = normalize_category(category)
    cleaned = re.sub(r"\s+", " ", title).strip()
    manufacturer = _manufacturer(CATEGORY_PREFIX.sub("", cleaned), normalized_category)
    if normalized_category == "gpu":
        return _gpu_identity(normalized_category, cleaned, manufacturer)
    if normalized_category == "cpu":
        return _cpu_identity(normalized_category, cleaned, manufacturer)
    if normalized_category == "ram":
        return _ram_identity(normalized_category, cleaned, manufacturer)
    if normalized_category == "motherboard":
        return _motherboard_identity(normalized_category, cleaned, manufacturer)
    if normalized_category == "case":
        return _case_identity(normalized_category, cleaned, manufacturer)
    return _generic_identity(normalized_category, cleaned, manufacturer)


def _same(left, right) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return _slug(left) == _slug(right)
    return left == right


def candidate_key(identity: ProductIdentity) -> str:
    """Broad blocking key used before detailed cross-store comparison."""
    maker = _slug(identity.manufacturer) or "unknown"
    if identity.category == "cpu":
        base = re.match(r"\d+", identity.model_number or "")
        return f"cpu:{maker}:{base.group(0) if base else _slug(identity.model_number)}"
    if identity.category == "gpu":
        return f"gpu:{maker}:{_slug(identity.chipset)}:{identity.vram_gb or 0}"
    if identity.category == "motherboard":
        return f"motherboard:{maker}:{_slug(identity.model_number) or _slug(identity.chipset)}"
    if identity.category == "ram":
        return f"ram:{maker}:{_slug(identity.ram_type)}:{identity.capacity_gb or 0}"
    if identity.category == "ssd":
        return f"ssd:{maker}:{_slug(identity.model_number)}:{identity.capacity_gb or 0}"
    if identity.category == "psu":
        return f"psu:{maker}:{identity.wattage or 0}"
    return f"{identity.category}:{maker}:{_slug(identity.model_number)}"


def decision_reason(match: ProductMatchResult) -> str:
    """Human-readable, deterministic explanation for review tooling."""
    if match.conflicts:
        return "; ".join(match.conflicts)
    if match.matched:
        return "high-confidence match on " + ", ".join(match.matching_fields)
    missing = {
        "manufacturer": "missing manufacturer", "model_number": "missing model number",
        "variant": "missing variant", "capacity": "missing capacity",
        "total_capacity": "missing capacity", "vram": "missing VRAM",
        "memory_type": "one-sided missing memory type", "color": "one-sided missing color",
    }
    reasons = [missing[field] for field in match.unknown_fields if field in missing]
    if reasons:
        return "; ".join(dict.fromkeys(reasons))
    if match.possible_match:
        return "ambiguous model family"
    return "insufficient identity evidence"


def match_products(a: ProductIdentity, b: ProductIdentity) -> ProductMatchResult:
    matches: list[str] = []
    unknown: list[str] = []
    conflicts: list[str] = []
    conflict_labels = {
        "category": "different category", "manufacturer": "different manufacturer",
        "cpu_family": "different CPU family", "cpu_model": "different CPU model",
        "chipset": "different chipset", "vram": "different VRAM",
        "variant": "different variant", "model_number": "different model number",
        "ddr_generation": "different DDR generation",
        "total_capacity": "different total capacity", "speed": "different memory speed",
        "stick_count": "different stick count",
        "capacity_per_stick": "different capacity per stick", "ecc": "different ECC type",
        "board_model": "different motherboard model", "socket": "different socket",
        "capacity": "different capacity", "wattage": "different wattage",
    }

    def compare(field: str, label: str, critical: bool = False) -> str:
        left, right = getattr(a, field), getattr(b, field)
        if left is None or right is None:
            unknown.append(label)
            return "unknown"
        if _same(left, right):
            matches.append(label)
            return "match"
        if critical:
            conflicts.append(conflict_labels.get(label, f"different {label.replace('_', ' ')}"))
        return "conflict"

    def finish(score: float) -> ProductMatchResult:
        score = round(max(0, min(score, 1)), 2)
        if conflicts:
            score = min(score, .1)
        reasons = conflicts[:] if conflicts else [f"same {field.replace('_', ' ')}" for field in matches]
        return ProductMatchResult(matched=not conflicts and score >= .95,
            possible_match=not conflicts and .75 <= score < .95,
            confidence=score, reasons=reasons, conflicts=conflicts,
            matching_fields=matches, unknown_fields=unknown)

    if a.category != b.category:
        conflicts.append(conflict_labels["category"])
        return finish(0)
    manufacturer = compare("manufacturer", "manufacturer", critical=True)
    if conflicts:
        return finish(.05)

    if a.category == "cpu":
        family = compare("product_family", "cpu_family", critical=True)
        model = compare("model_number", "cpu_model", critical=True)
        socket = compare("cpu_socket", "socket")
        if conflicts:
            return finish(.1)
        score = (.20 if manufacturer == "match" else 0) + (.10 if family == "match" else 0)
        score += .65 if model == "match" else 0
        score += .05 if socket == "match" else 0
        return finish(score)

    if a.category == "gpu":
        exact_model = bool(a.model_number and b.model_number and _same(a.model_number, b.model_number))
        chipset = compare("chipset", "chipset", critical=True)
        vram = compare("vram_gb", "vram", critical=True)
        variant = compare("variant", "variant", critical=not exact_model)
        model = compare("model_number", "model_number", critical=True)
        memory = compare("memory_type", "memory_type")
        if conflicts:
            return finish(.1)
        score = (.20 if manufacturer == "match" else 0) + (.40 if chipset == "match" else 0)
        score += .25 if vram == "match" else 0
        score += .10 if variant == "match" else 0
        score += .15 if model == "match" else 0
        score += .05 if memory == "match" else 0
        return finish(score)

    if a.category == "ram":
        ddr = compare("ram_type", "ddr_generation", critical=True)
        capacity = compare("capacity_gb", "total_capacity", critical=True)
        speed = compare("speed_mhz", "speed", critical=True)
        sticks = compare("kit_count", "stick_count", critical=True)
        per_stick = compare("capacity_per_stick_gb", "capacity_per_stick", critical=True)
        model = compare("model_number", "model_number", critical=True)
        compare("ecc", "ecc", critical=True)
        if conflicts:
            return finish(.1)
        score = (.20 if manufacturer == "match" else 0) + (.20 if ddr == "match" else 0)
        score += .25 if capacity == "match" else 0
        score += .20 if speed == "match" else 0
        score += .10 if sticks == "match" else 0
        score += .05 if per_stick == "match" else 0
        score += .10 if model == "match" else 0
        return finish(score)

    if a.category == "motherboard":
        model = compare("model_number", "board_model", critical=True)
        chipset = compare("chipset", "chipset", critical=True)
        socket = compare("cpu_socket", "socket", critical=True)
        ddr = compare("ram_type", "ddr_generation", critical=True)
        form = compare("form_factor", "form_factor")
        if conflicts:
            return finish(.1)
        score = (.20 if manufacturer == "match" else 0) + (.70 if model == "match" else 0)
        score += .05 if chipset == "match" else 0
        score += .025 if socket == "match" else 0
        score += .025 if ddr == "match" else 0
        score += .02 if form == "match" else 0
        return finish(score)

    if a.category == "ssd":
        model = compare("model_number", "model_number", critical=True)
        capacity = compare("capacity_gb", "capacity", critical=True)
        interface = compare("interface", "interface")
        if conflicts:
            return finish(.1)
        score = (.20 if manufacturer == "match" else 0) + (.55 if model == "match" else 0)
        score += .25 if capacity == "match" else 0
        score += .03 if interface == "match" else 0
        return finish(score)

    if a.category == "psu":
        model = compare("model_number", "model_number", critical=True)
        wattage = compare("wattage", "wattage", critical=True)
        rating = compare("efficiency_rating", "efficiency_rating")
        if conflicts:
            return finish(.1)
        score = (.20 if manufacturer == "match" else 0) + (.76 if model == "match" else 0)
        score += .20 if wattage == "match" else 0
        score += .04 if rating == "match" else 0
        return finish(score)

    if a.category == "case":
        model = compare("model_number", "model_number", critical=True)
        variant = compare("variant", "variant", critical=True)
        color = compare("color", "color", critical=True)
        if conflicts:
            return finish(.1)
        one_sided_detail = variant == "unknown" and bool(a.variant or b.variant)
        one_sided_color = color == "unknown" and bool(a.color or b.color)
        score = (.20 if manufacturer == "match" else 0) + (.76 if model == "match" else 0)
        if one_sided_detail or one_sided_color:
            score = min(score, .90)
        return finish(score)

    model = compare("model_number", "model_number", critical=True)
    return finish((.20 if manufacturer == "match" else 0) + (.76 if model == "match" else 0))

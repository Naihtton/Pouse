from app.builder import normalize
from app.domain import Listing
from app.product_identity import match_products, parse_product_identity


def identity(title: str, category: str = "gpu"):
    return parse_product_identity(category, title)


def test_gpu_identity_extracts_structured_fields_without_vram_duplication():
    parsed = identity(
        "GIGABYTE GEFORCE RTX 5070 TI GAMING OC 16G - 16GB GDDR7"
    )
    assert parsed.manufacturer == "Gigabyte"
    assert parsed.gpu_vendor == "NVIDIA"
    assert parsed.chipset == "RTX 5070 Ti"
    assert parsed.variant == "Gaming OC"
    assert parsed.vram_gb == 16
    assert parsed.memory_type == "GDDR7"
    assert parsed.model_number is None
    assert parsed.canonical_name == "Gigabyte RTX 5070 Ti Gaming OC 16GB GDDR7"
    assert parsed.identity_key == "gpu:gigabyte:rtx5070ti:gamingoc:16"
    assert parsed.canonical_name.count("16GB") == 1


def test_gpu_identity_preserves_exact_retailer_model_code():
    parsed = identity(
        "ASUS TUF GAMING GEFORCE RTX 5070 TI 16GB GDDR7 OC EDITION "
        "(TUF-RTX5070TI-O16G-GAMING)"
    )
    assert parsed.manufacturer == "ASUS"
    assert parsed.model_number == "TUF-RTX5070TI-O16G-GAMING"
    assert parsed.variant == "TUF Gaming OC"
    assert parsed.identity_key == "asus:tufrtx5070tio16ggaming"


def test_gpu_manufacturer_is_separate_from_platform_vendor():
    leadtek = identity("LEADTEK NVIDIA RTX A400 4GB GDDR6")
    assert (leadtek.manufacturer, leadtek.gpu_vendor, leadtek.chipset) == (
        "Leadtek", "NVIDIA", "RTX A400"
    )
    asrock = identity("ASROCK AMD RADEON RX 9060 XT 16GB GDDR6 (RX9060XT SL 16G)")
    assert (asrock.manufacturer, asrock.gpu_vendor, asrock.chipset) == (
        "ASRock", "AMD", "RX 9060 XT"
    )


def test_gpu_model_code_can_supply_chipset_and_vram():
    parsed = identity("ASUS DUAL-RTX5070-O12G")
    assert parsed.model_number == "DUAL-RTX5070-O12G"
    assert parsed.chipset == "RTX 5070"
    assert parsed.variant == "Dual"
    assert parsed.vram_gb == 12

    compact = identity("MSI GT 710 2GB 2GD3H-LP")
    assert compact.model_number == "2GD3H-LP"


def test_gpu_matching_exact_model_number_across_different_titles():
    jib = identity(
        "ASUS TUF GAMING GEFORCE RTX 5070 TI 16GB GDDR7 OC EDITION "
        "(TUF-RTX5070TI-O16G-GAMING)"
    )
    advice = identity("ASUS TUF-RTX5070TI-O16G-GAMING")
    result = match_products(jib, advice)
    assert result.matched is True
    assert result.confidence >= 0.95
    assert "same model number" in result.reasons


def test_gpu_matching_rejects_different_variant_vram_and_manufacturer():
    dual = identity("ASUS DUAL GEFORCE RTX 5070 12GB GDDR7")
    prime = identity("ASUS PRIME GEFORCE RTX 5070 12GB GDDR7")
    assert match_products(dual, prime).matched is False

    eight = identity("ASUS DUAL GEFORCE RTX 5060 TI 8GB GDDR7")
    sixteen = identity("ASUS DUAL GEFORCE RTX 5060 TI 16GB GDDR7")
    assert match_products(eight, sixteen).reasons == ["different VRAM"]

    gigabyte = identity("GIGABYTE GEFORCE RTX 5070 DUAL 12GB GDDR7")
    assert match_products(dual, gigabyte).reasons == ["different manufacturer"]


def test_gpu_memory_tokens_are_only_expanded_when_deterministic():
    assert identity("RTX5060/8GB GIGABYTE WINDFORCE (OC/D7)").memory_type == "GDDR7"
    ambiguous = identity("GT1030/2GB GIGABYTE LOWPROFILE (D4)")
    assert ambiguous.memory_type is None
    assert ambiguous.memory_token == "D4"
    justified = identity("GT1030/2GB GIGABYTE LOWPROFILE (D4) 2GB DDR4")
    assert justified.memory_type == "DDR4"


def test_variant_formatting_normalizes_consistently():
    compact = identity("GIGABYTE GT 1030 LOWPROFILE 2GB DDR4")
    spaced = identity("GIGABYTE GT 1030 Low Profile 2GB DDR4")
    assert compact.variant == spaced.variant == "Low Profile"
    advice = identity("RTX5060/8GB GIGABYTE WINDFORCE (OC/D7)")
    jib = identity("GIGABYTE GEFORCE RTX 5060 WINDFORCE OC 8GB GDDR7")
    assert advice.variant == jib.variant == "Windforce OC"


def test_cpu_identity_for_amd_and_intel():
    amd = identity("AMD RYZEN 5 9600X", "cpu")
    assert (amd.manufacturer, amd.product_family, amd.model_number) == (
        "AMD", "Ryzen 5", "9600X"
    )
    assert amd.canonical_name == "AMD Ryzen 5 9600X"
    assert amd.identity_key == "amd:9600x"

    intel = identity("INTEL CORE ULTRA 7 265K", "cpu")
    assert (intel.manufacturer, intel.product_family, intel.model_number) == (
        "Intel", "Core Ultra 7", "265K"
    )
    assert intel.canonical_name == "Intel Core Ultra 7 265K"


def test_lightweight_other_category_fields():
    board = identity("MSI B550M PRO-VDH WIFI - AMD SOCKET AM4 DDR4 MICRO-ATX", "motherboard")
    assert board.model_number == "B550M PRO-VDH WIFI"
    assert board.ram_type == "DDR4"
    assert board.form_factor == "mATX"

    ram = identity("KINGSTON FURY BEAST 32GB (2X16GB) DDR4", "ram")
    assert ram.capacity_gb == 32
    assert ram.ram_type == "DDR4"

    ssd = identity("WD BLUE SN580 1TB NVME", "ssd")
    assert ssd.model_number == "SN580"
    assert ssd.capacity_gb == 1000

    psu = identity("CORSAIR CX650 650W 80 PLUS BRONZE", "psu")
    assert psu.model_number == "CX650"
    assert psu.wattage == 650

    case = identity("COOLER MASTER MASTERBOX Q300L MICRO-ATX CASE", "case")
    assert case.manufacturer == "Cooler Master"
    assert case.form_factor == "mATX"


def test_analyst_groups_by_identity_instead_of_retailer_sku():
    rows = [
        Listing(sku="JIB-1", name="ASUS DUAL-RTX5070-O12G", title="ASUS DUAL-RTX5070-O12G",
                category="GPU", retailer="JIB", price=23000, shipping=0, specs={}),
        Listing(sku="ADV-99", name="ASUS DUAL-RTX5070-O12G", title="ASUS DUAL-RTX5070-O12G",
                category="GPU", retailer="Advice", price=23500, shipping=0, specs={}),
    ]
    components = normalize(rows)
    assert len(components) == 1
    assert len(components[0].alternatives) == 2
    assert components[0].listing.product_identity.identity_key == "asus:dualrtx5070o12g"


def test_ram_hard_conflicts_prevent_false_merges():
    eight = identity("BLACKBERRY RAM DDR4 2666 8GB", "ram")
    sixteen = identity("BLACKBERRY RAM DDR4 2666 16GB", "ram")
    faster = identity("BLACKBERRY RAM DDR4 3200 8GB", "ram")
    one = identity("BLACKBERRY RAM DDR4 2666 1x8GB", "ram")
    two = identity("BLACKBERRY RAM DDR4 2666 2x4GB", "ram")
    assert "different total capacity" in match_products(eight, sixteen).conflicts
    assert "different memory speed" in match_products(eight, faster).conflicts
    assert "different stick count" in match_products(one, two).conflicts


def test_cpu_exact_model_conflicts_and_next_is_ignored():
    next_offer = identity("AMD Ryzen 5 5500GT (Next)", "cpu")
    same = identity("AMD Ryzen 5 5500GT", "cpu")
    assert match_products(next_offer, same).matched
    assert "different CPU model" in match_products(same, identity("AMD Ryzen 5 5500", "cpu")).conflicts
    assert "different CPU model" in match_products(same, identity("AMD Ryzen 5 5600GT", "cpu")).conflicts
    boxed = identity("AMD Ryzen 5 5500GT (Box-Next)", "cpu")
    assert boxed.packaging == "boxed"
    assert match_products(boxed, same).matched


def test_realistic_cross_retailer_title_variations():
    cpu = match_products(
        identity("AMD RYZEN 5 5600 3.5 GHZ SOCKET AM4", "cpu"),
        identity("CPU AMD RYZEN 5 5600 AM4", "cpu"),
    )
    assert cpu.matched and "cpu_model" in cpu.matching_fields

    board = match_products(
        identity("ASROCK A520M-HVS - AMD SOCKET AM4 DDR4 MICRO-ATX", "motherboard"),
        identity("ASROCK A520M-HVS", "motherboard"),
    )
    assert board.matched and {"socket", "ddr_generation"}.issubset(board.unknown_fields)

    gpu = match_products(
        identity("GIGABYTE RTX 5060 WINDFORCE OC 8GB GDDR7"),
        identity("RTX5060/8GB GIGABYTE WINDFORCE (OC/D7)"),
    )
    assert gpu.matched

    ram = match_products(
        identity("BLACKBERRY DDR4 3200 32GB 16GBx2", "ram"),
        identity("DDR4(3200) 32GB(16GBx2) BLACKBERRY", "ram"),
    )
    assert ram.matched

    ssd = match_products(
        identity("HIKSEMI CITY E3000 512GB NVME", "ssd"),
        identity("HIKSEMI E3000 512G", "ssd"),
    )
    assert ssd.matched


def test_motherboard_exact_model_conflicts():
    hvs = identity("ASRock A520M-HVS DDR4", "motherboard")
    hdv = identity("ASRock A520M-HDV DDR4", "motherboard")
    r2 = identity("ASRock A520M-HVS R2.0 DDR4", "motherboard")
    assert "different motherboard model" in match_products(hvs, hdv).conflicts
    assert "different motherboard model" in match_products(hvs, r2).conflicts


def test_gpu_ti_is_a_hard_chipset_conflict():
    base = identity("ASUS DUAL RTX 5060 8GB GDDR7")
    ti = identity("ASUS DUAL RTX 5060 Ti 8GB GDDR7")
    assert "different chipset" in match_products(base, ti).conflicts


def test_case_parser_separates_family_variant_color_and_accessory():
    panda = identity("HYTE Y70 TOUCH INFINITE - PANDA (E-ATX)", "case")
    black = identity("E-ATX CASE (NP) HYTE Y70 TOUCH INFINITE BLACK", "case")
    accessory = identity("HYTE Y70 14.9 LCD TOUCH UPGRADE KIT WHITE", "case")
    assert panda.manufacturer == "HYTE"
    assert panda.model_number == black.model_number == "Y70"
    assert panda.variant == black.variant == "Touch Infinite"
    assert panda.color == "Panda" and black.color == "Black"
    assert "different color" in match_products(panda, black).conflicts
    assert accessory.model_number == "Y70 UPGRADE KIT"
    glass = identity("MONTECH X3 GLASS BLACK", "case")
    mesh = identity("MONTECH X3 MESH BLACK", "case")
    assert "different variant" in match_products(glass, mesh).conflicts


def test_sandisk_exact_ssd_model_is_high_confidence():
    jib = identity("1 TB SSD SANDISK OPTIMUS 5100 SDSP51100TAN-000E0 NVME", "ssd")
    advice = identity("SANDISK OPTIMUS 5100 1TB (SDSP51100TAN-000E0) NVME", "ssd")
    assert match_products(jib, advice).matched

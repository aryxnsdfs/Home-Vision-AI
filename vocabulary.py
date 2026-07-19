"""
vocabulary.py — Canonical architectural vocabulary with synonym mappings.

Each dictionary maps a canonical term to a list of known synonyms,
misspellings, colloquial phrases, and Hinglish alternatives.

Used by matcher.py for the 3-layer matching pipeline:
    Layer 1: Exact match against synonyms
    Layer 2: Fuzzy match (thefuzz, score > 75)
    Layer 3: Semantic similarity (spacy en_core_web_md, > 0.65)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ROOMS — canonical room types → synonyms
# ---------------------------------------------------------------------------

ROOMS: dict[str, list[str]] = {
    "living room": [
        "living", "living room", "living area", "lounge", "sitting room",
        "drawing room", "hall", "baithak", "family room", "great room",
        "front room", "reception room", "parlour", "parlor",
    ],
    "kitchen": [
        "kitchen", "kitchn", "kitchenette", "cooking area", "rasoi",
        "modular kitchen", "open kitchen", "pantry kitchen", "wet kitchen",
        "dry kitchen", "kitchen space", "cook room",
    ],
    "master bedroom": [
        "master bedroom", "master bed", "master suite", "main bedroom",
        "primary bedroom", "primary suite", "master bed room",
    ],
    "bedroom": [
        "bedroom", "bed room", "bed", "room", "sleeping room",
        "guest bedroom", "kids bedroom", "children bedroom", "child room",
        "guest room", "spare room", "kamra",
    ],
    "bathroom": [
        "bathroom", "bath room", "bath", "toilet", "restroom", "washroom",
        "lavatory", "loo", "wc", "water closet", "attached bath",
        "common bathroom", "common toilet", "guest toilet",
    ],
    "balcony": [
        "balcony", "balkony", "balcny", "balconee", "terrace balcony",
        "verandah", "veranda", "porch", "deck", "patio", "loggia",
        "dry balcony", "wet balcony", "sit-out",
    ],
    "garage": [
        "garage", "parking", "car parking", "covered parking", "car porch",
        "carport", "car port", "parking space", "vehicle parking",
        "park my car", "car shed", "garaaj",
    ],
    "dining room": [
        "dining room", "dining", "dining area", "dining space",
        "dining hall", "eating area", "breakfast nook", "dinning room",
        "dinning", "khana khane ki jagah",
    ],
    "study room": [
        "study room", "study", "office", "home office", "work room",
        "library", "reading room", "workspace", "den", "studio",
        "computer room", "work from home room",
    ],
    "gym": [
        "gym", "home gym", "fitness room", "fitness center", "fitness centre",
        "workout room", "exercise room", "training room", "health club",
    ],
    "pooja room": [
        "pooja room", "puja room", "prayer room", "mandir", "temple room",
        "pooja", "puja", "worship room", "meditation room", "shrine",
        "pooja ghar", "mandir room",
    ],
    "store room": [
        "store room", "storage", "storage room", "storeroom", "godown",
        "utility room", "utility", "utility area", "service room",
        "store", "bhandar",
    ],
    "foyer": [
        "foyer", "entry", "entrance", "entryway", "vestibule",
        "lobby", "hallway", "corridor", "passage", "anteroom",
        "entrance hall", "mudroom", "deodi",
    ],
    "servant quarter": [
        "servant quarter", "servant room", "maid room", "staff quarter",
        "helper room", "domestic help room", "outhouse", "quarters",
        "naukar ka kamra", "service quarter",
    ],
    "terrace": [
        "terrace", "roof terrace", "rooftop", "chatt", "chhatt",
        "roof garden", "open terrace", "terrace garden",
    ],
    "courtyard": [
        "courtyard", "aangan", "angan", "open court", "inner court",
        "atrium", "central court", "open space",
    ],
    "laundry": [
        "laundry", "laundry room", "washing area", "utility laundry",
        "dhulai", "wash room", "laundry space",
    ],
    "hallway": [
        "hallway", "hall", "corridor", "passage", "passageway",
        "lobby", "landing", "gallery",
    ],
    "staircase": [
        "staircase", "stairs", "stairway", "stair", "seedi",
        "steps", "stairwell",
    ],
}


# ---------------------------------------------------------------------------
# STYLES — design/aesthetic style → synonyms
# ---------------------------------------------------------------------------

STYLES: dict[str, list[str]] = {
    "modern": [
        "modern", "contemporary", "minimal", "minimalist", "sleek",
        "clean lines", "current", "trendy", "aadhunik",
    ],
    "traditional": [
        "traditional", "classic", "heritage", "colonial", "vintage",
        "old style", "purana style", "desi", "paramparik",
    ],
    "minimalist": [
        "minimalist", "minimal", "simple", "clean", "understated",
        "less is more", "sparse", "uncluttered",
    ],
    "luxury": [
        "luxury", "luxurious", "premium", "high end", "upscale",
        "opulent", "lavish", "grand", "palatial", "mewadi",
        "royal", "elite",
    ],
    "industrial": [
        "industrial", "loft", "warehouse", "raw", "urban",
        "factory style", "exposed", "brutalist", "brutal",
        "concrete style", "rough",
    ],
    "coastal": [
        "coastal", "beach", "seaside", "nautical", "maritime",
        "ocean", "sea", "samudri", "beach house",
    ],
    "farmhouse": [
        "farmhouse", "rustic", "country", "rural", "cottage",
        "gaon", "village", "rustic charm",
    ],
    "vastu compliant": [
        "vastu", "vastu compliant", "vaastu", "vastu shastra",
        "vastu friendly", "vastu approved", "vaastu shastra",
    ],
    "open concept": [
        "open concept", "open plan", "open layout", "open floor plan",
        "no walls", "connected spaces", "flow-through",
    ],
    "compact": [
        "compact", "space saving", "space efficient", "small",
        "cozy", "snug", "tight", "chhota",
    ],
    "eco friendly": [
        "eco friendly", "green", "sustainable", "eco", "environment friendly",
        "energy efficient", "solar", "rainwater harvesting",
    ],
    "smart home": [
        "smart home", "smart", "automated", "iot", "connected",
        "home automation", "intelligent",
    ],
}


# ---------------------------------------------------------------------------
# MATERIALS — building materials → synonyms
# ---------------------------------------------------------------------------

MATERIALS: dict[str, list[str]] = {
    # Flooring
    "italian marble": [
        "italian marble", "marble", "imported marble", "statuario",
        "calacatta", "carrara", "white marble",
    ],
    "indian marble": [
        "indian marble", "makrana marble", "makrana", "rajasthani marble",
        "desi marble",
    ],
    "vitrified tiles": [
        "vitrified tiles", "vitrified", "ceramic tiles", "porcelain tiles",
        "glazed tiles", "floor tiles", "tiles",
    ],
    "kota stone": [
        "kota stone", "kota", "limestone", "natural stone flooring",
    ],
    "granite": [
        "granite", "granite flooring", "black granite", "granite stone",
    ],
    "wooden laminate": [
        "wooden laminate", "laminate", "wood floor", "wooden floor",
        "hardwood", "engineered wood", "vinyl", "lvt",
        "wooden flooring", "parquet",
    ],
    "terrazzo": [
        "terrazzo", "mosaic", "mosaic flooring", "chip flooring",
    ],
    # Wall materials
    "aac blocks": [
        "aac blocks", "aac", "autoclaved aerated concrete",
        "lightweight blocks", "siporex", "aerated blocks",
    ],
    "red clay bricks": [
        "red clay bricks", "red bricks", "clay bricks", "burnt bricks",
        "eent", "traditional bricks", "desi bricks",
    ],
    "fly ash bricks": [
        "fly ash bricks", "fly ash", "cement bricks", "machine bricks",
    ],
    "hollow concrete blocks": [
        "hollow concrete blocks", "hollow blocks", "concrete blocks",
        "cinder blocks", "block masonry",
    ],
    "stone masonry": [
        "stone masonry", "stone wall", "natural stone", "rubble masonry",
        "ashlar", "stone", "patthar",
    ],
    # Wall finishes
    "distemper": [
        "distemper", "whitewash", "lime wash", "chuna", "safedi",
    ],
    "acrylic paint": [
        "acrylic paint", "acrylic", "emulsion", "plastic paint",
        "tractor emulsion", "premium paint", "wall paint",
    ],
    "texture paint": [
        "texture paint", "texture", "textured wall", "designer paint",
        "wall texture",
    ],
    "exposed brick": [
        "exposed brick", "brick cladding", "exposed brick cladding",
        "brick finish", "naked brick",
    ],
    "wallpaper": [
        "wallpaper", "wallpapers", "wall covering", "wall paper",
    ],
    # Structural
    "rcc": [
        "rcc", "reinforced concrete", "reinforced cement concrete",
        "concrete frame", "rcc frame",
    ],
    "tmt steel": [
        "tmt steel", "tmt", "tmt bars", "reinforcement steel",
        "fe500", "fe550", "fe550d", "rebar", "sariya",
    ],
    # Woodwork
    "teak wood": [
        "teak wood", "teak", "saagwan", "sagwan", "hardwood",
        "timber", "oak", "sal wood", "sheesham",
    ],
    "flush doors": [
        "flush doors", "flush door", "plywood door", "commercial door",
    ],
    "upvc windows": [
        "upvc windows", "upvc", "pvc windows", "plastic windows",
        "upvc doors and windows",
    ],
    "aluminium windows": [
        "aluminium windows", "aluminum windows", "aluminium sliding",
        "aluminum sliding", "sliding windows", "metal windows",
    ],
    # Kitchen counters
    "quartz": [
        "quartz", "quartz countertop", "engineered stone",
        "quartz slab",
    ],
    "corian": [
        "corian", "solid surface", "acrylic countertop",
    ],
    "black granite": [
        "black granite", "granite counter", "granite countertop",
        "granite top", "kitchen granite",
    ],
    # Roofing
    "flat rcc slab": [
        "flat rcc slab", "flat roof", "rcc slab", "flat slab",
        "concrete roof",
    ],
    "sloped roof": [
        "sloped roof", "pitched roof", "sloped", "pitched",
        "gable roof", "hip roof", "inclined roof",
    ],
    "mangalore tiles": [
        "mangalore tiles", "clay tiles", "roof tiles", "country tiles",
        "kavelu",
    ],
    "metal sheet": [
        "metal sheet", "metal roof", "sheet roofing", "tin roof",
        "zinc sheet", "gi sheet",
    ],
}


# ---------------------------------------------------------------------------
# SIZE_MODIFIERS — size/scale descriptors → synonyms
# ---------------------------------------------------------------------------

SIZE_MODIFIERS: dict[str, list[str]] = {
    "large": [
        "large", "big", "spacious", "bada", "broad", "wide",
        "ample", "generous", "roomy", "expansive",
    ],
    "medium": [
        "medium", "moderate", "average", "standard", "regular",
        "normal", "mid-size", "theek thaak",
    ],
    "small": [
        "small", "compact", "tiny", "chhota", "little",
        "cozy", "snug", "petite", "narrow",
    ],
    "extra large": [
        "extra large", "very large", "huge", "massive", "enormous",
        "oversized", "bahut bada", "xl",
    ],
    "double height": [
        "double height", "double-height", "tall ceiling", "high ceiling",
        "cathedral ceiling", "vaulted", "lofty",
    ],
}


# ---------------------------------------------------------------------------
# INTENT_ACTIONS — user modification intents → synonyms
# ---------------------------------------------------------------------------

INTENT_ACTIONS: dict[str, list[str]] = {
    "add": [
        "add", "include", "put", "insert", "create", "make",
        "build", "place", "attach", "want", "need", "chahiye",
        "lagao", "daal do", "banao", "rakh do",
    ],
    "remove": [
        "remove", "delete", "take out", "eliminate", "drop",
        "get rid of", "hata do", "nikaal do", "no need",
    ],
    "resize": [
        "resize", "increase", "decrease", "expand", "shrink",
        "enlarge", "reduce", "make bigger", "make smaller",
        "bada karo", "chhota karo", "extend", "stretch",
    ],
    "move": [
        "move", "shift", "relocate", "reposition", "place beside",
        "place next to", "swap", "exchange", "hatao", "shift karo",
    ],
    "modify": [
        "modify", "change", "update", "alter", "adjust",
        "convert", "transform", "switch", "badlo",
    ],
}


# ---------------------------------------------------------------------------
# TYPOLOGY — building typology → synonyms
# ---------------------------------------------------------------------------

TYPOLOGY: dict[str, list[str]] = {
    "1bhk": [
        "1bhk", "1 bhk", "one bhk", "single bedroom",
        "one bedroom", "studio apartment",
    ],
    "2bhk": [
        "2bhk", "2 bhk", "two bhk", "two bedroom",
        "double bedroom",
    ],
    "3bhk": [
        "3bhk", "3 bhk", "three bhk", "three bedroom",
        "triple bedroom",
    ],
    "4bhk": [
        "4bhk", "4 bhk", "four bhk", "four bedroom",
    ],
    "5bhk": [
        "5bhk", "5 bhk", "five bhk", "five bedroom",
        "5bhk+",
    ],
    "duplex": [
        "duplex", "duplex house", "two floor", "two story",
        "double story", "do manzila",
    ],
    "villa": [
        "villa", "bungalow", "kothi", "independent house",
        "bangla", "haveli",
    ],
    "row house": [
        "row house", "rowhouse", "townhouse", "town house",
        "terraced house",
    ],
    "farmhouse": [
        "farmhouse", "farm house", "country house",
        "weekend house", "farm estate",
    ],
    "penthouse": [
        "penthouse", "pent house", "top floor",
        "sky villa",
    ],
}


# ---------------------------------------------------------------------------
# COLORS - colors for styling
# ---------------------------------------------------------------------------

COLORS: dict[str, list[str]] = {
    "black": ["black", "dark", "kala"],
    "white": ["white", "safed", "light"],
    "red": ["red", "laal", "crimson"],
    "blue": ["blue", "neela", "navy", "cyan"],
    "green": ["green", "hara", "emerald"],
    "gray": ["gray", "grey", "silver", "slate"],
    "brown": ["brown", "wood", "chocolate"],
}


# ---------------------------------------------------------------------------
# ALL_VOCABULARIES — convenience aggregation
# ---------------------------------------------------------------------------

ALL_VOCABULARIES: dict[str, dict[str, list[str]]] = {
    "rooms": ROOMS,
    "styles": STYLES,
    "materials": MATERIALS,
    "size_modifiers": SIZE_MODIFIERS,
    "intent_actions": INTENT_ACTIONS,
    "typology": TYPOLOGY,
    "colors": COLORS,
}

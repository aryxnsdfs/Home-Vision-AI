from typing import Dict, List, Any

# Parametric Templates
# Each template defines the primary layout of core rooms.
# Coordinates (x, z) and dimensions (w, l) are multipliers from 0.0 to 1.0.

TEMPLATE_1BHK_CORE = {
    "living_room":    {"x": 0.0, "z": 0.0, "w": 1.0, "l": 0.4},
    "kitchen":        {"x": 0.0, "z": 0.4, "w": 0.4, "l": 0.3},
    "corridor":       {"x": 0.4, "z": 0.4, "w": 0.2, "l": 0.6},
    "master_bedroom": {"x": 0.6, "z": 0.4, "w": 0.4, "l": 0.6}
}

TEMPLATE_2BHK_CORE = {
    "living_room":    {"x": 0.0, "z": 0.0, "w": 1.0, "l": 0.35},
    "kitchen":        {"x": 0.0, "z": 0.35, "w": 0.4, "l": 0.3},
    "corridor":       {"x": 0.4, "z": 0.35, "w": 0.2, "l": 0.65},
    "master_bedroom": {"x": 0.0, "z": 0.65, "w": 0.4, "l": 0.35},
    "bedroom":        {"x": 0.6, "z": 0.35, "w": 0.4, "l": 0.65}
}

TEMPLATE_3BHK_CORE = {
    "living_room":    {"x": 0.0, "z": 0.0, "w": 1.0, "l": 0.3},
    "kitchen":        {"x": 0.0, "z": 0.3, "w": 0.3, "l": 0.3},
    "corridor":       {"x": 0.3, "z": 0.3, "w": 0.2, "l": 0.7},
    "bedroom":        {"x": 0.5, "z": 0.3, "w": 0.5, "l": 0.3},
    "master_bedroom": {"x": 0.0, "z": 0.6, "w": 0.3, "l": 0.4},
    "bedroom_2":      {"x": 0.5, "z": 0.6, "w": 0.5, "l": 0.4}
}

TEMPLATES = {
    1: TEMPLATE_1BHK_CORE,
    2: TEMPLATE_2BHK_CORE,
    3: TEMPLATE_3BHK_CORE,
    0: TEMPLATE_1BHK_CORE  # Fallback for studios
}

def get_template_for_bhk(bhk: int) -> Dict[str, Any]:
    return TEMPLATES.get(bhk, TEMPLATE_3BHK_CORE)

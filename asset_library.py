"""Deterministic low-poly asset and space placement library.

The layout solver owns room rectangles and connectivity.  This module owns
only semantic classification and decoration placement, so furniture and
outdoor features cannot accidentally consume indoor room space.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Tuple


OUTDOOR_TYPES = {
    "balcony", "garden", "landscape", "parking", "garage",
    "portico", "veranda", "patio", "flat_terrace", "terrace", "roof_garden",
    # Water and open-air leisure features are site elements, not rooms. Left
    # indoors they entered the corridor graph and had to satisfy shared-wall
    # and door rules, which made whole layouts infeasible — asking for a
    # swimming pool could cost the user the entire house.
    "swimming_pool", "pool", "plunge_pool", "lap_pool", "jacuzzi",
    "deck", "sit_out", "sitout", "lawn", "courtyard_garden", "terrace_garden",
    "kitchen_garden", "play_area", "childrens_play_area", "driveway", "carport",
}
ROOFTOP_TYPES = {"terrace", "flat_terrace", "roof_garden", "rooftop_garden"}
BASEMENT_TYPES = {"basement", "cellar", "lower_ground", "lower_ground_floor"}

def canonical_type(value: Any) -> str:
    """Normalize only formatting; never replace a user-provided room name.

    Semantic classification belongs to Gemini's room program.  Keeping this
    lossless means a user can request any custom space and see that same space
    flow through layout, assets, and labels without a fixed alias dictionary.
    """
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text


def is_outdoor_type(value: Any) -> bool:
    return canonical_type(value) in OUTDOOR_TYPES


def is_rooftop_type(value: Any) -> bool:
    return canonical_type(value) in ROOFTOP_TYPES


def is_basement_type(value: Any) -> bool:
    return canonical_type(value) in BASEMENT_TYPES


def split_outdoor_specs(specs: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (indoor_specs, outdoor_specs) without mutating the input."""
    indoor, outdoor = [], []
    for spec in specs or []:
        item = dict(spec)
        item["type"] = canonical_type(item.get("type"))
        # Gemini may classify an unfamiliar user-defined space as outdoor.
        # Honor that semantic signal instead of relying on a fixed room list.
        is_outdoor = bool(item.get("is_outdoor")) or str(item.get("placement", "")).lower() == "outdoor"
        (outdoor if is_outdoor or is_outdoor_type(item["type"]) else indoor).append(item)
    return indoor, outdoor


def requested_outdoor_specs(prompt: str) -> List[Dict[str, Any]]:
    """Recover explicit site/roof requests that a vocabulary matcher may miss."""
    text = (prompt or "").lower()
    found: List[Dict[str, Any]] = []
    patterns = (
        ("parking", r"\b(?:car\s+)?parking\b|\bgarage\b|\bcarport\b"),
        ("garden", r"\bgarden\b|\blandscap(?:e|ing)\b|\btrees?\b|\bplants?\b"),
        ("terrace", r"\bterrace\b|\brooftop\b|\broof\s+garden\b"),
        ("balcony", r"\bbalcony\b|\bveranda\b|\bverandah\b|\bpatio\b|\bportico\b"),
    )
    for type_name, pattern in patterns:
        if re.search(pattern, text):
            found.append({"type": type_name, "confidence": 100, "requested_by_prompt": True})
    if re.search(r"\bwater\s+tank\b|\bwater\s+pump\b|\bsolar\s+panel\b|\brooftop\s+utilit", text) and not any(item["type"] == "terrace" for item in found):
        found.append({"type": "terrace", "confidence": 100, "requested_by_prompt": True, "utility_only": True})
    return found


def requested_custom_specs(prompt: str) -> List[Dict[str, Any]]:
    """Recognize high-value custom rooms before the generic matcher drops them."""
    text = (prompt or "").lower()
    found: List[Dict[str, Any]] = []
    patterns = (
        ("gym", r"\bgym\b|\bhome\s+gym\b|\bfitness\s+(?:room|center|centre)\b|\bworkout\s+room\b|\bexercise\s+room\b|\btraining\s+room\b"),
        ("wedding_hall", r"\bwedding\s+hall\b|\bbanquet\s+hall\b|\bmarriage\s+hall\b"),
        ("home_theater", r"\bhome\s+theat(?:er|re)\b|\bmedia\s+room\b|\bhome\s+cinema\b"),
    )
    for type_name, pattern in patterns:
        if re.search(pattern, text):
            found.append({"type": type_name, "confidence": 100, "requested_by_prompt": True})
    return found


# Asset records are intentionally renderer-neutral.  The React low-poly
# renderer maps `type` to optimized primitive meshes and uses these dimensions
# for consistent scale and collision-free placement.
ROOM_ASSETS: Dict[str, List[Dict[str, Any]]] = {
    "living_room": [
        {"type": "sofa", "width": 6.0, "length": 2.6, "x": 3.0, "z": 2.0},
        {"type": "tv_unit", "width": 4.5, "length": 0.7, "x": 3.0, "z": 8.0},
        {"type": "coffee_table", "width": 3.0, "length": 1.8, "x": 3.0, "z": 4.9},
        {"type": "side_table", "width": 1.2, "length": 1.2, "x": 7.0, "z": 2.0},
        {"type": "floor_lamp", "width": 0.7, "length": 0.7, "x": 8.0, "z": 7.2},
    ],
    "bedroom": [
        {"type": "bed", "width": 5.4, "length": 6.6, "x": 3.4, "z": 4.0},
        {"type": "wardrobe", "width": 4.0, "length": 1.5, "x": 8.0, "z": 1.2},
        {"type": "nightstand", "width": 1.2, "length": 1.2, "x": 0.7, "z": 3.0},
        {"type": "nightstand", "width": 1.2, "length": 1.2, "x": 6.1, "z": 3.0},
        {"type": "dresser", "width": 3.0, "length": 1.2, "x": 8.0, "z": 5.6},
    ],
    "master_bedroom": [
        {"type": "bed", "width": 6.0, "length": 6.8, "x": 3.7, "z": 4.0},
        {"type": "wardrobe", "width": 5.0, "length": 1.5, "x": 8.5, "z": 1.2},
        {"type": "nightstand", "width": 1.2, "length": 1.2, "x": 0.8, "z": 3.0},
        {"type": "nightstand", "width": 1.2, "length": 1.2, "x": 6.8, "z": 3.0},
        {"type": "dresser", "width": 3.5, "length": 1.2, "x": 8.5, "z": 5.8},
    ],
    "kitchen": [
        {"type": "countertop", "width": 6.0, "length": 2.0, "x": 3.2, "z": 1.2},
        {"type": "fridge", "width": 2.2, "length": 2.2, "x": 1.4, "z": 4.2},
        {"type": "stove", "width": 2.2, "length": 2.0, "x": 5.6, "z": 1.2},
        {"type": "sink", "width": 1.6, "length": 1.5, "x": 7.2, "z": 1.2},
        {"type": "island", "width": 4.0, "length": 1.8, "x": 4.0, "z": 5.0},
    ],
    "dining_room": [
        {"type": "dining_table", "width": 5.0, "length": 3.0, "x": 5.0, "z": 5.0},
        {"type": "dining_chair", "width": 1.0, "length": 1.0, "x": 2.2, "z": 5.0},
        {"type": "dining_chair", "width": 1.0, "length": 1.0, "x": 7.8, "z": 5.0},
        {"type": "dining_chair", "width": 1.0, "length": 1.0, "x": 5.0, "z": 3.0},
        {"type": "dining_chair", "width": 1.0, "length": 1.0, "x": 5.0, "z": 7.0},
        {"type": "sideboard", "width": 4.0, "length": 1.0, "x": 5.0, "z": 1.0},
    ],
    "bathroom": [
        {"type": "toilet", "width": 1.8, "length": 2.4, "x": 1.6, "z": 2.0},
        {"type": "basin", "width": 2.0, "length": 1.5, "x": 4.5, "z": 1.2},
        {"type": "shower", "width": 3.0, "length": 3.0, "x": 4.2, "z": 4.5},
        {"type": "mirror", "width": 2.0, "length": 0.2, "x": 4.5, "z": 0.9},
    ],
    "powder_room": [
        {"type": "toilet", "width": 1.8, "length": 2.4, "x": 1.7, "z": 2.0},
        {"type": "basin", "width": 1.8, "length": 1.4, "x": 4.4, "z": 1.3},
        {"type": "mirror", "width": 1.8, "length": 0.2, "x": 4.4, "z": 1.0},
    ],
    "study_room": [
        {"type": "desk", "width": 4.0, "length": 1.8, "x": 4.0, "z": 1.5},
        {"type": "office_chair", "width": 1.4, "length": 1.4, "x": 4.0, "z": 3.1},
        {"type": "bookcase", "width": 3.8, "length": 1.0, "x": 7.5, "z": 6.0},
        {"type": "desk_lamp", "width": 0.6, "length": 0.6, "x": 5.0, "z": 1.5},
    ],
    "gym": [
        {"type": "treadmill", "width": 3.0, "length": 6.0, "height": 1.2, "x": 2.2, "z": 3.0},
        {"type": "exercise_bike", "width": 2.2, "length": 3.0, "height": 1.5, "x": 6.0, "z": 2.5},
        {"type": "weight_bench", "width": 2.2, "length": 5.0, "height": 1.1, "x": 3.0, "z": 7.0},
        {"type": "dumbbell_rack", "width": 4.0, "length": 1.0, "height": 1.5, "x": 7.5, "z": 7.0},
        {"type": "yoga_mat", "width": 2.2, "length": 5.5, "height": 0.08, "x": 8.0, "z": 3.5},
        {"type": "wall_mirror", "width": 5.0, "length": 0.2, "height": 2.3, "x": 4.5, "z": 0.8},
    ],
    "home_theater": [
        {"type": "screen", "width": 6.0, "length": 0.3, "x": 5.0, "z": 1.0},
        {"type": "theater_seating", "width": 6.0, "length": 2.2, "x": 5.0, "z": 5.0},
        {"type": "theater_seating", "width": 6.0, "length": 2.2, "x": 5.0, "z": 8.0},
        {"type": "av_console", "width": 4.0, "length": 1.0, "x": 5.0, "z": 2.2},
    ],
    "foyer": [
        {"type": "console", "width": 4.0, "length": 1.0, "x": 4.0, "z": 1.0},
        {"type": "mirror", "width": 2.5, "length": 0.2, "x": 4.0, "z": 1.3},
        {"type": "bench", "width": 3.0, "length": 1.0, "x": 4.0, "z": 4.0},
    ],
    "corridor": [{"type": "console", "width": 3.0, "length": 0.8, "x": 2.0, "z": 2.0}, {"type": "wall_light", "width": 0.4, "length": 0.4, "x": 2.0, "z": 4.0}],
    "staircase": [{"type": "stair_steps", "width": 3.0, "length": 7.0, "x": 2.0, "z": 4.0}, {"type": "handrail", "width": 0.3, "length": 6.0, "x": 3.5, "z": 4.0}],
    "pooja_room": [{"type": "altar", "width": 3.0, "length": 1.2, "x": 3.0, "z": 1.0}, {"type": "diya_stand", "width": 0.6, "length": 0.6, "x": 1.2, "z": 1.2}],
    "laundry": [{"type": "washer", "width": 2.2, "length": 2.2, "x": 1.6, "z": 1.6}, {"type": "utility_sink", "width": 2.0, "length": 1.4, "x": 4.5, "z": 1.4}, {"type": "shelf", "width": 3.0, "length": 0.8, "x": 4.0, "z": 4.5}],
    "store_room": [{"type": "shelf", "width": 3.5, "length": 0.8, "x": 2.0, "z": 1.0}, {"type": "shelf", "width": 3.5, "length": 0.8, "x": 6.0, "z": 1.0}],
    "utility": [{"type": "washer", "width": 2.2, "length": 2.2, "x": 1.6, "z": 1.6}, {"type": "utility_sink", "width": 2.0, "length": 1.4, "x": 4.5, "z": 1.4}],
    "wedding_hall": [{"type": "stage", "width": 10.0, "length": 2.5, "x": 6.0, "z": 1.8}, {"type": "guest_seating", "width": 4.0, "length": 2.0, "x": 3.0, "z": 6.0}, {"type": "guest_seating", "width": 4.0, "length": 2.0, "x": 9.0, "z": 6.0}, {"type": "flower_decor", "width": 1.0, "length": 1.0, "x": 1.0, "z": 1.4}, {"type": "flower_decor", "width": 1.0, "length": 1.0, "x": 11.0, "z": 1.4}, {"type": "chandelier", "width": 1.2, "length": 1.2, "x": 6.0, "z": 5.0}],
}


def _clamp_asset(item: Dict[str, Any], width: float, length: float) -> Dict[str, Any] | None:
    w = min(float(item.get("width", 1.0)), max(0.8, width - 0.8))
    l = min(float(item.get("length", 1.0)), max(0.8, length - 0.8))
    if width < 3.0 or length < 3.0:
        return None
    x = max(w / 2 + 0.4, min(float(item.get("x", width / 2)), width - w / 2 - 0.4))
    z = max(l / 2 + 0.4, min(float(item.get("z", length / 2)), length - l / 2 - 0.4))
    return {**item, "x": round(x, 2), "z": round(z, 2), "width": round(w, 2), "length": round(l, 2), "height": float(item.get("height", 0.8))}


def furniture_for_room(room_type: str, width: float, length: float, prompt: str = "") -> List[Dict[str, Any]]:
    rt = canonical_type(room_type)
    max_assets = furniture_capacity(rt, width, length)
    if max_assets == 0:
        return []
    items = ROOM_ASSETS.get(rt)
    if items is None and "bedroom" in rt:
        items = ROOM_ASSETS["master_bedroom"] if rt == "master_bedroom" else ROOM_ASSETS["bedroom"]
    if items is None and "bath" in rt:
        items = ROOM_ASSETS["bathroom"]
    if items is None:
        # Open-ended room support: every unknown user-created space still
        # receives a usable, context-neutral furnishing kit.  Specialized
        # catalogs above are optimizations, never prerequisites for generation.
        items = [
            {"type": "work_table", "width": 4.0, "length": 2.0, "x": width * 0.5, "z": length * 0.22},
            {"type": "chair", "width": 1.2, "length": 1.2, "x": width * 0.5, "z": length * 0.42},
            {"type": "storage_unit", "width": 3.0, "length": 1.0, "x": width * 0.2, "z": length * 0.78},
            {"type": "area_rug", "width": min(6.0, width * 0.55), "length": min(5.0, length * 0.42), "x": width * 0.58, "z": length * 0.65},
            {"type": "floor_lamp", "width": 0.7, "length": 0.7, "x": width * 0.86, "z": length * 0.82},
        ]
    return fit_furniture_assets([item for item in items], float(width), float(length), max_assets=max_assets)


def furniture_capacity(room_type: Any, width: float, length: float) -> int:
    """Return a minimal visual asset budget for an arbitrary named space."""
    room = canonical_type(room_type)
    if re.search(r"(?:corridor|hallway|passage|stair|lobby|foyer|courtyard|angan|parking|garden|terrace|balcony|utility|store)", room):
        return 0
    if float(width) * float(length) < 90 or re.search(r"(?:bath|toilet|powder|wash|pooja)", room):
        return 1
    return 2



def fit_furniture_assets(
    items: Iterable[Dict[str, Any]], width: float, length: float, max_assets: int = 2,
) -> List[Dict[str, Any]]:
    """Fit arbitrary Gemini assets inside a room without collisions.

    Positions from Gemini are treated as preferences, never as geometry truth.
    This pass rescales oversized objects and packs them against candidate
    points while keeping a clear margin around the room perimeter.
    """
    if width < 3.0 or length < 3.0 or max_assets <= 0:
        return []
    margin = 0.55
    placed: List[Dict[str, Any]] = []

    def overlaps(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        return not (
            a["x"] + a["width"] / 2 + 0.25 <= b["x"] - b["width"] / 2
            or a["x"] - a["width"] / 2 >= b["x"] + b["width"] / 2 + 0.25
            or a["z"] + a["length"] / 2 + 0.25 <= b["z"] - b["length"] / 2
            or a["z"] - a["length"] / 2 >= b["z"] + b["length"] / 2 + 0.25
        )

    # Minimal by design: Gemini may suggest a complete set, but the viewport
    # should show only the most useful pieces, not a crowded showroom.
    # Keep rooms readable in the 3-D view.  The first two catalog entries are
    # the room's primary pieces (for example bed + wardrobe or sofa + TV).
    # Secondary decoration must never make a room look crowded.
    for raw in list(items or [])[:max_assets]:
        item = dict(raw)
        item["width"] = min(max(0.6, float(item.get("width", 1.0))), max(0.6, width - 2 * margin))
        item["length"] = min(max(0.6, float(item.get("length", 1.0))), max(0.6, length - 2 * margin))
        item["height"] = max(0.05, float(item.get("height", 0.8)))
        preferred = (
            min(max(float(item.get("x", width / 2)), margin + item["width"] / 2), width - margin - item["width"] / 2),
            min(max(float(item.get("z", length / 2)), margin + item["length"] / 2), length - margin - item["length"] / 2),
        )
        candidates = []
        # Include wall-aligned slots so two large essentials can share a room
        # without forcing one to disappear just because Gemini preferred the
        # center for both.
        edge_x = [margin + item["width"] / 2, width - margin - item["width"] / 2]
        edge_z = [margin + item["length"] / 2, length - margin - item["length"] / 2]
        edge_candidates = [(x, z) for x in edge_x for z in edge_z]
        candidates.extend(edge_candidates if item["width"] > width * 0.35 or item["length"] > length * 0.35 else [preferred])
        candidates.append(preferred)
        step = 0.75
        for ix in range(1, max(2, int(width / step))):
            for iz in range(1, max(2, int(length / step))):
                candidates.append((margin + ix * step, margin + iz * step))
        chosen = None
        for x, z in sorted(candidates, key=lambda p: (p[0] - preferred[0]) ** 2 + (p[1] - preferred[1]) ** 2):
            x = min(max(float(x), margin + item["width"] / 2), width - margin - item["width"] / 2)
            z = min(max(float(z), margin + item["length"] / 2), length - margin - item["length"] / 2)
            candidate = {**item, "x": x, "z": z}
            if all(not overlaps(candidate, other) for other in placed):
                chosen = candidate
                break
        if chosen is None:
            # Keep the asset recognizable but reduce and relocate it until it
            # fits. Never silently lose an essential because the AI preferred
            # the same center point as another asset.
            for factor in (0.65, 0.5, 0.35):
                resized = {**item, "width": item["width"] * factor, "length": item["length"] * factor}
                for x, z in candidates:
                    x = min(max(float(x), margin + resized["width"] / 2), width - margin - resized["width"] / 2)
                    z = min(max(float(z), margin + resized["length"] / 2), length - margin - resized["length"] / 2)
                    candidate = {**resized, "x": x, "z": z}
                    if all(not overlaps(candidate, other) for other in placed):
                        chosen = candidate
                        break
                if chosen is not None:
                    break
            if chosen is None:
                continue
        chosen["x"] = round(float(chosen["x"]), 2)
        chosen["z"] = round(float(chosen["z"]), 2)
        chosen["width"] = round(float(chosen["width"]), 2)
        chosen["length"] = round(float(chosen["length"]), 2)
        placed.append(chosen)
    return placed


def _outdoor_assets(kind: str, width: float, length: float, prompt: str = "") -> List[Dict[str, Any]]:
    kind = canonical_type(kind)
    if kind in {"parking", "garage"}:
        assets = [{"type": "parking_bay", "width": 9.0, "length": 18.0, "x": width * 0.28, "z": length / 2},
                  {"type": "parking_bay", "width": 9.0, "length": 18.0, "x": width * 0.72, "z": length / 2},
                  {"type": "car", "width": 6.0, "length": 12.0, "x": width * 0.28, "z": length / 2},
                  {"type": "pathway", "width": 4.0, "length": length, "x": width / 2, "z": length / 2},
                  {"type": "outdoor_light", "width": 0.5, "length": 0.5, "x": 1.0, "z": 1.0},
                  {"type": "outdoor_light", "width": 0.5, "length": 0.5, "x": width - 1.0, "z": length - 1.0}]
    elif kind in {"terrace", "flat_terrace", "roof_garden"}:
        assets = [{"type": "railing", "width": width, "length": 0.25, "x": width / 2, "z": 0.25},
                  {"type": "planter", "width": 3.0, "length": 1.2, "x": 2.0, "z": length - 1.0},
                  {"type": "roof_seating", "width": 3.5, "length": 2.0, "x": width * 0.55, "z": length * 0.55},
                  {"type": "water_tank", "width": 4.0, "length": 4.0, "x": width - 3.0, "z": 3.0},
                  {"type": "water_pump", "width": 1.2, "length": 1.2, "x": width - 6.0, "z": 3.0},
                  {"type": "solar_panel", "width": 5.0, "length": 2.0, "x": 3.0, "z": 3.0},
                  {"type": "outdoor_light", "width": 0.5, "length": 0.5, "x": width / 2, "z": length - 1.0}]
    else:
        assets = [{"type": "tree", "width": 2.5, "length": 2.5, "x": width * 0.2, "z": length * 0.25},
                  {"type": "tree", "width": 2.5, "length": 2.5, "x": width * 0.8, "z": length * 0.3},
                  {"type": "shrub", "width": 1.2, "length": 1.2, "x": width * 0.3, "z": length * 0.7},
                  {"type": "pathway", "width": 3.0, "length": length * 0.8, "x": width / 2, "z": length / 2},
                  {"type": "bench", "width": 3.0, "length": 1.0, "x": width * 0.72, "z": length * 0.72},
                  {"type": "outdoor_light", "width": 0.5, "length": 0.5, "x": 1.0, "z": length / 2},
                  {"type": "outdoor_light", "width": 0.5, "length": 0.5, "x": width - 1.0, "z": length / 2}]
    return [asset for item in assets if (asset := _clamp_asset(item, width, length))]


def build_outdoor_areas(specs: Iterable[Dict[str, Any]], plot_width: float, plot_length: float,
                        building_nodes: Iterable[Any], floors: int = 1, prompt: str = "") -> List[Dict[str, Any]]:
    """Place requested outdoor areas beside the building, never in its AABB."""
    specs = list(specs or [])
    if not specs:
        return []
    nodes = list(building_nodes or [])
    if nodes:
        min_x = min(n.rect.x for n in nodes)
        max_x = max(n.rect.x + n.rect.width for n in nodes)
        min_z = min(n.rect.z for n in nodes)
        max_z = max(n.rect.z + n.rect.length for n in nodes)
    else:
        min_x, min_z, max_x, max_z = 3.0, 3.0, plot_width - 3.0, plot_length - 3.0
    result = []
    for index, spec in enumerate(specs):
        kind = canonical_type(spec.get("type"))
        rooftop = is_rooftop_type(kind)
        if rooftop:
            width, length = max(8.0, max_x - min_x), max(8.0, max_z - min_z)
            x, z, floor_index = min_x, min_z, max(0, int(floors) - 1)
        else:
            if kind in {"parking", "garage"}:
                width, length = 20.0, 22.0
            elif kind in {"balcony", "portico", "veranda", "patio"}:
                width, length = 12.0, 8.0
            else:
                width, length = 18.0, 14.0
            gap = 2.0
            # Prefer the side with the most available plot clearance.
            candidates = [
                (max_x + gap, max(min_z, 2.0), "east", max(0.0, plot_width - max_x - gap - width)),
                (min_x - gap - width, max(min_z, 2.0), "west", max(0.0, min_x - gap - width)),
                (max(min_x, 2.0), max_z + gap, "south", max(0.0, plot_length - max_z - gap - length)),
                (max(min_x, 2.0), min_z - gap - length, "north", max(0.0, min_z - gap - length)),
            ]
            valid = [c for c in candidates if c[0] >= 0.5 and c[1] >= 0.5 and c[0] + width <= plot_width - 0.5 and c[1] + length <= plot_length - 0.5]
            if not valid:
                # Never emit geometry outside the user plot.  Reduce the
                # requested site area to the largest in-plot footprint.
                width = min(width, max(4.0, plot_width - 1.0))
                length = min(length, max(4.0, plot_length - 1.0))
                candidates = [
                    (0.5, 0.5, "west", plot_width * plot_length),
                    (max(0.5, plot_width - width - 0.5), 0.5, "east", plot_width * plot_length),
                    (0.5, max(0.5, plot_length - length - 0.5), "north", plot_width * plot_length),
                ]
                valid = [c for c in candidates if c[0] + width <= plot_width - 0.5 and c[1] + length <= plot_length - 0.5]
            x, z, side, _ = max(valid, key=lambda c: c[3])
            floor_index = 0
        result.append({
            "id": f"outdoor-{kind}-{index + 1}", "type": kind,
            "name": kind.replace("_", " ").title(), "x": round(x, 2), "z": round(z, 2),
            "width": round(width, 2), "length": round(length, 2), "floorIndex": floor_index,
            "is_outdoor": True, "is_rooftop": rooftop,
            "assets": _outdoor_assets(kind, width, length, prompt),
            "access": {"from": {"x": round((min_x + max_x) / 2, 2), "z": round(max_z, 2)},
                       "to": {"x": round(x + width / 2, 2), "z": round(z, 2)}},
        })
    return result

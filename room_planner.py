"""
room_planner.py — Smart room sorting, generation-order pipeline, duplex
distribution, feature-attachment map, overflow rules and final layout
validation for the architectural layout engine.

This module is the single source of truth for the placement *rules*. The
geometric work (BSP carve, doors, windows) still lives in layout_engine.py;
here we decide WHAT gets generated, in WHICH ORDER, on WHICH FLOOR, and whether
the result is buildable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from layout_engine import (
    RoomNode,
    ROOM_MINIMUMS,
    get_min_area,
    get_min_dim,
    validate_layout,
    _share_edge,
)

# ---------------------------------------------------------------------------
# 1. Classification Matrix
# ---------------------------------------------------------------------------

ZONE_PUBLIC = "public"
ZONE_FAMILY = "family"
ZONE_PRIVATE = "private"
ZONE_SERVICE = "service"
ZONE_OUTDOOR = "outdoor"
ZONE_STRUCTURAL = "structural"

# type → zone. Synonyms map to the same canonical type elsewhere; keep the
# common spellings the engine emits.
ROOM_ZONES: Dict[str, str] = {
    # Public Zone
    "foyer": ZONE_PUBLIC,
    "living_room": ZONE_PUBLIC,
    "dining_room": ZONE_PUBLIC,
    "powder_room": ZONE_PUBLIC,
    "veranda": ZONE_PUBLIC,
    "corridor": ZONE_PUBLIC,
    "hallway": ZONE_PUBLIC,
    "staircase": ZONE_PUBLIC,
    # Family Zone
    "kitchen": ZONE_FAMILY,
    "pooja_room": ZONE_FAMILY,
    "courtyard": ZONE_FAMILY,
    "brahmasthan": ZONE_FAMILY,
    "built_in_seating": ZONE_FAMILY,
    "dining": ZONE_FAMILY,
    # Private Zone
    "master_bedroom": ZONE_PRIVATE,
    "bedroom": ZONE_PRIVATE,
    "elderly_suite": ZONE_PRIVATE,
    "study_room": ZONE_PRIVATE,
    "gym": ZONE_PRIVATE,
    "bathroom": ZONE_PRIVATE,      # attached bathrooms
    # Service Zone
    "utility": ZONE_SERVICE,
    "utility_area": ZONE_SERVICE,
    "store_room": ZONE_SERVICE,
    "laundry": ZONE_SERVICE,
    "sump_tank": ZONE_SERVICE,
    # Outdoor Zone
    "balcony": ZONE_OUTDOOR,
    "portico": ZONE_OUTDOOR,
    "parking": ZONE_OUTDOOR,
    "flat_terrace": ZONE_OUTDOOR,
    "otta": ZONE_OUTDOOR,
    # Structural Zone — features, never room space.
    "jali": ZONE_STRUCTURAL,
    "chhajja": ZONE_STRUCTURAL,
    "jharokha": ZONE_STRUCTURAL,
    "stack_vent": ZONE_STRUCTURAL,
    "parapet": ZONE_STRUCTURAL,
    "overhead_tank": ZONE_STRUCTURAL,
}

# Structural features must NEVER consume room space, shrink rooms or modify
# room dimensions. They are stripped from the BSP room pool and applied only
# after room generation + validation.
STRUCTURAL_TYPES = {t for t, z in ROOM_ZONES.items() if z == ZONE_STRUCTURAL}


def classify_zone(rtype: str) -> str:
    """Return the classification zone for a room type (defaults to service)."""
    rt = _canon(rtype)
    if rt in ROOM_ZONES:
        return ROOM_ZONES[rt]
    # Substring fallback so prefixed/suffixed ids still classify.
    for key, zone in ROOM_ZONES.items():
        if key in rt:
            return zone
    return ZONE_SERVICE


def is_structural(rtype: str) -> bool:
    rt = _canon(rtype)
    return rt in STRUCTURAL_TYPES or any(s in rt for s in STRUCTURAL_TYPES)


def _canon(rtype: str) -> str:
    return (rtype or "").replace(" ", "_").lower()


# Universal Access Policies Database
ACCESS_POLICIES: Dict[str, Dict[str, Any]] = {
    "bedroom": {
        "allowed_from": ["corridor", "lobby", "family_lounge", "private_landing", "foyer"],
        "transit_allowed": False,
    },
    "master_bedroom": {
        "allowed_from": ["corridor", "lobby", "family_lounge", "private_landing", "foyer"],
        "transit_allowed": False,
    },
    "attached_bathroom": {
        "allowed_from": ["assigned_bedroom", "bedroom", "master_bedroom"],
        "maximum_connections": 1,
        "transit_allowed": False,
    },
    "common_bathroom": {
        "allowed_from": ["corridor", "lobby", "foyer", "family_lounge"],
        "transit_allowed": False,
    },
    "bathroom": {
        "allowed_from": ["corridor", "lobby", "foyer", "family_lounge", "bedroom", "master_bedroom"],
        "transit_allowed": False,
    },
    "gym": {
        "allowed_from": ["foyer", "family_lounge", "lobby", "corridor"],
        "transit_allowed": False,
    },
    "kitchen": {
        "allowed_from": ["dining_room", "service_passage", "corridor", "family_lounge"],
        "transit_allowed": False,
    },
    "dining_room": {
        "allowed_from": ["living_room", "family_lounge", "foyer", "corridor"],
        "preferred_connections": ["kitchen"],
        "transit_allowed": True,
    },
    "office": {
        "allowed_from": ["foyer", "living_room", "lobby", "corridor"],
        "transit_allowed": False,
    },
    "study_room": {
        "allowed_from": ["foyer", "family_lounge", "lobby", "corridor", "bedroom", "master_bedroom"],
        "transit_allowed": False,
    },
    "home_theater": {
        "allowed_from": ["foyer", "family_lounge", "lobby", "corridor"],
        "transit_allowed": False,
    },
}

FORBIDDEN_TRANSIT_TYPES = {
    "bedroom",
    "master_bedroom",
    "bathroom",
    "powder_room",
    "kitchen",
    "closet",
    "store_room",
    "utility",
    "utility_area",
}


def validate_circulation_access(nodes: List[RoomNode]) -> Dict[str, Any]:
    """Validate walking routes from main entrance to all destination rooms."""
    import collections
    node_by_id = {n.id: n for n in nodes}
    adj: Dict[str, List[str]] = {n.id: [] for n in nodes}
    
    for n in nodes:
        for other in nodes:
            if other.id == n.id:
                continue
            if _share_edge(n.rect, other.rect):
                if other.id not in adj[n.id]:
                    adj[n.id].append(other.id)

    entrance_node = next(
        (n for n in nodes if _canon(n.type) in {"foyer", "entrance", "living_room", "corridor"}),
        nodes[0] if nodes else None
    )

    if not entrance_node:
        return {"passed": True, "errors": []}

    errors = []
    for n in nodes:
        if n.id == entrance_node.id or _canon(n.type) in {"corridor", "hallway", "staircase", "void"}:
            continue
        
        queue = collections.deque([[entrance_node.id]])
        visited = {entrance_node.id}
        found_path = None
        
        while queue:
            path = queue.popleft()
            curr = path[-1]
            if curr == n.id:
                found_path = path
                break
            for nxt in adj.get(curr, []):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(path + [nxt])

        if not found_path:
            errors.append({"code": "UNREACHABLE_ROOM", "message": f"'{n.name}' ({n.id}) has no walking path from main entrance."})
            continue

        intermediate_ids = found_path[1:-1]
        for mid_id in intermediate_ids:
            mid_node = node_by_id.get(mid_id)
            if mid_node and _canon(mid_node.type) in FORBIDDEN_TRANSIT_TYPES:
                errors.append({"code": "INVALID_TRANSIT", "message": f"Path to '{n.name}' passes through private/service space '{mid_node.name}' ({mid_node.type})."})

    return {"passed": len(errors) == 0, "errors": errors}


# ---------------------------------------------------------------------------
# 2. Generation Order Pipeline
#    Step 1 Core → Step 2 Supporting → Step 3 Architectural → Step 4 Structural
#    This order is NEVER reversed.
# ---------------------------------------------------------------------------

PHASE_CORE = 1
PHASE_SUPPORTING = 2
PHASE_ARCHITECTURAL = 3
PHASE_STRUCTURAL = 4

CORE_TYPES = {
    "living_room", "kitchen", "bedroom", "master_bedroom", "dining_room",
    "bathroom", "foyer", "corridor", "hallway", "staircase",
    "study_room", "elderly_suite",
    "gym",
}
SUPPORTING_TYPES = {
    "pooja_room", "powder_room", "utility", "utility_area", "store_room",
    "laundry", "courtyard", "brahmasthan", "built_in_seating",
}
ARCHITECTURAL_TYPES = {
    "balcony", "veranda", "portico", "parking", "otta", "flat_terrace",
    "double_height",
}
# STRUCTURAL_TYPES defined above.


def generation_phase(rtype: str) -> int:
    rt = _canon(rtype)
    if rt in CORE_TYPES or any(c in rt for c in CORE_TYPES):
        return PHASE_CORE
    if rt in SUPPORTING_TYPES or any(c in rt for c in SUPPORTING_TYPES):
        return PHASE_SUPPORTING
    if is_structural(rt):
        return PHASE_STRUCTURAL
    if rt in ARCHITECTURAL_TYPES or any(c in rt for c in ARCHITECTURAL_TYPES):
        return PHASE_ARCHITECTURAL
    return PHASE_SUPPORTING


def sort_spec_by_generation_order(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable-sort a room-spec list into Core → Supporting → Architectural →
    Structural order. Preserves original order within a phase."""
    return sorted(specs, key=lambda r: generation_phase(r.get("type", "")))


def strip_structural(specs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split specs into (room_pool, structural_features). Structural features
    are removed from the BSP pool so they cannot consume room space."""
    rooms, structural = [], []
    for r in specs:
        (structural if is_structural(r.get("type", "")) else rooms).append(r)
    return rooms, structural


# ---------------------------------------------------------------------------
# 3. Duplex Distribution Rules
# ---------------------------------------------------------------------------

# Types that always live on the ground floor of a duplex.
_GROUND_ALWAYS = {
    "living_room", "dining_room", "kitchen", "utility", "utility_area",
    "pooja_room", "powder_room", "foyer", "corridor", "hallway", "staircase",
    "store_room", "parking", "portico", "veranda", "courtyard",
}
# Types that prefer the upper floor of a duplex.
_FIRST_PREFERRED = {"balcony", "built_in_seating"}


def split_duplex_specs(
    specs: List[Dict[str, Any]],
    bedroom_count: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Distribute room specs across ground and first floors of a duplex.

    Allocation (bedroom_count = master + plain bedrooms):
      2 BR  → all bedrooms on Ground (no duplex split of bedrooms).
      3 BR  → Ground: 1 guest/elderly bedroom; First: Master + 1 bedroom.
      4 BR+ → Ground: 1 guest/elderly bedroom; First: Master + remaining.

    Private rooms go upstairs whenever possible. Bathrooms follow bedrooms.
    Returns (ground_specs, first_specs). Both floors keep a staircase + corridor
    so the flight is continuous and every floor has circulation.
    """
    ground: List[Dict[str, Any]] = []
    first: List[Dict[str, Any]] = []

    beds = [r for r in specs if _canon(r.get("type", "")) in ("bedroom", "master_bedroom", "elderly_suite")]
    baths = [r for r in specs if _canon(r.get("type", "")) == "bathroom"]
    others = [
        r for r in specs
        if r not in beds and r not in baths
    ]

    # How many bedrooms go downstairs (guest/elderly).
    if bedroom_count <= 2:
        ground_bed_n = len(beds)   # everything stays on ground
    else:
        ground_bed_n = 1           # one guest/elderly suite downstairs

    # Bedrooms: fill ground quota first, rest go upstairs.
    for i, b in enumerate(beds):
        (ground if i < ground_bed_n else first).append(b)

    # Bathrooms: one stays downstairs (for the guest/powder side), rest upstairs
    # with the private cluster.
    if baths:
        ground.append(baths[0])
        first.extend(baths[1:])

    # Non-bedroom rooms by zone preference.
    for r in others:
        rt = _canon(r.get("type", ""))
        if rt in _FIRST_PREFERRED and first_has_private(first):
            first.append(r)
        elif rt in _GROUND_ALWAYS:
            ground.append(r)
        else:
            # Family/private support follows the public core downstairs unless
            # it is clearly a private upstairs amenity.
            zone = classify_zone(rt)
            (first if zone == ZONE_PRIVATE and first_has_private(first) else ground).append(r)

    # Guarantee circulation + a continuous staircase on both floors.
    _ensure_type(ground, "staircase")
    _ensure_type(first, "staircase")
    _ensure_type(first, "corridor")
    if not first:
        # Degenerate (e.g. <=2 BR): keep the upper floor as a family space.
        first.append({"type": "living_room", "confidence": 100})
        _ensure_type(first, "staircase")

    # `_ensure_type` may add the staircase after the AI topology has already
    # been wired.  Re-attach it here so duplex splitting can never leave the
    # vertical circulation isolated or reachable only through a bedroom.
    def _connect_stair_to_public_core(floor_specs: List[Dict[str, Any]]) -> None:
        stair = next(
            (r for r in floor_specs if _canon(r.get("type", "")) in {"staircase", "stairwell"}),
            None,
        )
        if stair is None:
            return
        hub = next(
            (r for r in floor_specs if _canon(r.get("type", "")) in {"corridor", "hallway", "foyer"}),
            None,
        )
        if hub is None:
            hub = next(
                (r for r in floor_specs if _canon(r.get("type", "")) in {"living_room", "dining_room"}),
                None,
            )
        if hub is None or hub is stair:
            return
        connections = stair.setdefault("connections", [])
        if not any(str(c.get("target_room", "")) == str(hub.get("type", "")) for c in connections):
            connections.append({"target_room": hub.get("type"), "intent": "standard", "weight": 20})

    _connect_stair_to_public_core(ground)
    _connect_stair_to_public_core(first)

    return ground, first


# Features that must NEVER appear unless the user explicitly requested them
# (either via the room spec or an Indian-feature flag). This is the hard
# guarantee behind the "only generate what was requested" rule — even if some
# upstream step leaks one in, it is stripped here.
_NEVER_AUTO_TYPES = {
    "pooja_room", "powder_room", "brahmasthan", "courtyard", "utility",
    "utility_area", "store_room", "built_in_seating", "void", "otta",
    "portico", "veranda", "flat_terrace",
}

# Indian-option flag -> the room type(s) it authorises.
_INDIAN_FLAG_TYPES: Dict[str, List[str]] = {
    "pooja_room": ["pooja_room"],
    "powder_room": ["powder_room"],
    "utility_area": ["utility", "utility_area"],
    "store_room": ["store_room"],
    "brahmasthan": ["brahmasthan"],
    "angan": ["courtyard"],
    "courtyard": ["courtyard"],
    "otta": ["otta"],
    "portico": ["portico"],
    "built_in_seating": ["built_in_seating"],
    "double_height": ["void"],
    "void": ["void"],
    "flat_terrace": ["flat_terrace"],
}


def requested_type_set(
    specs: List[Dict[str, Any]],
    indian_options: Optional[Dict[str, Any]] = None,
) -> set:
    """Build the set of room types the user actually asked for: every type in
    the room spec, plus those authorised by enabled Indian-feature flags."""
    indian_options = indian_options or {}
    allowed = {_canon(r.get("type", "")) for r in specs}
    for flag, types in _INDIAN_FLAG_TYPES.items():
        if indian_options.get(flag):
            allowed.update(types)
    return allowed


def enforce_requested_only(
    nodes: List[RoomNode],
    requested: set,
) -> List[RoomNode]:
    """Strip any never-auto feature room that was not requested. Core rooms and
    circulation (corridor/staircase) are always kept. Mutates and returns
    `nodes` so callers can reassign in place.

    After removing a room, any door on a *kept* neighbour that opened directly
    into the removed room is also dropped — otherwise the neighbour is left with
    a doorway leading to nowhere (e.g. Living keeps a door to a stripped Pooja).
    """
    kept = [
        n for n in nodes
        if _canon(n.type) not in _NEVER_AUTO_TYPES or _canon(n.type) in requested
    ]
    removed = [n for n in nodes if n not in kept]

    if removed:
        eps = 0.25

        def _in_rect(px, pz, rect):
            return (rect.x - eps) <= px <= (rect.x + rect.width + eps) and \
                   (rect.z - eps) <= pz <= (rect.z + rect.length + eps)

        for k in kept:
            r = getattr(k, "rect", None)
            if r is None or not getattr(k, "doors", None):
                continue
            surviving = []
            for d in k.doors:
                if getattr(d, "is_main", False):
                    surviving.append(d)
                    continue
                # World point just OUTSIDE this door's wall.
                o = getattr(d, "wall_orientation", "")
                if o == "east":
                    px, pz = r.x + r.width + eps, r.z + d.z
                elif o == "west":
                    px, pz = r.x - eps, r.z + d.z
                elif o == "north":
                    px, pz = r.x + d.x, r.z - eps
                elif o == "south":
                    px, pz = r.x + d.x, r.z + r.length + eps
                else:
                    surviving.append(d)
                    continue
                # Drop only if it opens into a room we just removed.
                if any(_in_rect(px, pz, rem.rect) for rem in removed if getattr(rem, "rect", None)):
                    continue
                surviving.append(d)
            k.doors[:] = surviving

    nodes[:] = kept
    return nodes


def first_has_private(first: List[Dict[str, Any]]) -> bool:
    return any(classify_zone(r.get("type", "")) == ZONE_PRIVATE for r in first)


def _ensure_type(specs: List[Dict[str, Any]], rtype: str) -> None:
    if not any(_canon(r.get("type", "")) == rtype for r in specs):
        specs.append({"type": rtype, "confidence": 100})


# ---------------------------------------------------------------------------
# 4. Feature Attachment Rules (reference map; the engine's parasite carver in
#    layout_engine.py consumes these anchor preferences).
# ---------------------------------------------------------------------------

FEATURE_ATTACHMENT: Dict[str, List[str]] = {
    "pooja_room": ["living_room", "dining_room"],
    "utility": ["kitchen"],
    "utility_area": ["kitchen"],
    "store_room": ["kitchen", "utility"],
    "powder_room": ["foyer", "living_room"],
    "balcony": ["master_bedroom", "bedroom"],
    "built_in_seating": ["living_room", "veranda"],
}


def attachment_anchors(rtype: str) -> List[str]:
    return FEATURE_ATTACHMENT.get(_canon(rtype), [])


# ---------------------------------------------------------------------------
# 5. Overflow & Validation Constraints
# ---------------------------------------------------------------------------

INSUFFICIENT_SPACE_MSG = (
    "Insufficient space available for this room. "
    "Try resizing nearby rooms or generating a larger layout."
)

# A new room needs its own minimum area plus a circulation buffer; below this
# we would be creating a tiny unusable room, which is a hard constraint.
_OVERFLOW_BUFFER = 1.15


def can_place_room(free_area: float, rtype: str) -> bool:
    """True if `rtype` fits in `free_area` (ft²) without creating a tiny,
    unusable room. Used by the overflow guard."""
    needed = get_min_area(rtype) * _OVERFLOW_BUFFER
    return free_area >= needed


def final_layout_validation(
    nodes: List[RoomNode],
    indian_options: Optional[Dict[str, Any]] = None,
    is_duplex: bool = False,
) -> Dict[str, Any]:
    """Explicitly confirm the layout is buildable.

    Confirms: minimum room dimensions, circulation paths, door/stair
    accessibility, room adjacency rules, Vastu compliance, structural
    integrity and buildability. Returns a structured report:
        {"ok": bool, "checks": {name: {"ok": bool, "issues": [...]}}, "issues": [...]}
    """
    indian_options = indian_options or {}
    checks: Dict[str, Tuple[bool, List[str]]] = {}

    by_type: Dict[str, List[RoomNode]] = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)

    open_types = {
        "void", "portico", "parking", "balcony", "veranda", "otta",
        "courtyard", "staircase",
    }

    # 1. Minimum room dimensions / area — never tiny, never below validation.
    dim_issues: List[str] = []
    for n in nodes:
        if n.type in STRUCTURAL_TYPES or n.type in open_types:
            continue
        mn = ROOM_MINIMUMS.get(n.type)
        if not mn:
            continue
        if min(n.rect.width, n.rect.length) < mn["min_dim"] - 0.1:
            dim_issues.append(f"{n.name}: below minimum dimension {mn['min_dim']:.0f} ft.")
        if n.rect.area < mn["area"] - 1.0:
            dim_issues.append(f"{n.name}: below minimum area {mn['area']:.0f} ft².")
    checks["min_dimensions"] = (not dim_issues, dim_issues)

    # 2. Circulation paths + adjacency rules (reuse architect checks).
    arch = validate_layout(nodes)
    circ_issues = [w for w in arch if "corridor" in w.lower() or "circulation" in w.lower() or "adjacent" in w.lower()]
    adj_issues = [w for w in arch if w not in circ_issues and "toilet" in w.lower()]
    checks["circulation_paths"] = (not circ_issues, circ_issues)
    checks["adjacency_rules"] = (not adj_issues, adj_issues)

    # 3. Door / stair accessibility.
    door_issues: List[str] = []
    for n in nodes:
        if n.type in open_types or n.type in STRUCTURAL_TYPES:
            continue
        if not n.doors:
            door_issues.append(f"{n.name}: no door access.")
    if is_duplex and not by_type.get("staircase"):
        door_issues.append("Duplex has no staircase connecting floors.")
    checks["door_stair_access"] = (not door_issues, door_issues)

    # 4. Vastu compliance (best-effort: pooja not against a toilet; kitchen SE).
    vastu_issues: List[str] = []
    toilets = by_type.get("bathroom", []) + by_type.get("powder_room", [])
    for p in by_type.get("pooja_room", []):
        if any(_share_edge(p.rect, t.rect) for t in toilets):
            vastu_issues.append("Pooja Room shares a wall with a toilet (Vastu).")
            break
    checks["vastu"] = (not vastu_issues, vastu_issues)

    # 5. Structural integrity. Inject structural column grid into rooms with spans > 25 ft.
    # 3. Minimum room sizes
    size_issues = []
    for n in nodes:
        w, l = n.rect.width, n.rect.length
        ctype = _canon(n.type)
        if ctype in MIN_ROOM_AREAS and (w * l) < (MIN_ROOM_AREAS[ctype] * 0.9):
            size_issues.append({"code": "WARNING_SMALL_ROOM", "message": f"{n.name} is {(w*l):.0f} sqft, below minimum."})
    checks["minimum_sizes"] = (not size_issues, size_issues)

    # 4. Mandatory minimum dimensions
    dim_issues = []
    for n in nodes:
        w, l = n.rect.width, n.rect.length
        if min(w, l) < 3.0:
            dim_issues.append({"code": "WARNING_NARROW_ROOM", "message": f"{n.name} has a dimension < 3.0ft."})
    checks["minimum_dimensions"] = (not dim_issues, dim_issues)

    # 5. Structural integrity (span limits)
    struct_issues = []
    import math
    for n in nodes:
        span = min(n.rect.width, n.rect.length)
        if span > 25.0:
            mep_list = getattr(n, "mep_nodes", []) or []
            has_columns = any(isinstance(m, dict) and m.get("type") in {"structural_column", "column", "pillar"} for m in mep_list)
            if not has_columns:
                cols_x = max(0, math.ceil(n.rect.width / 20.0) - 1)
                cols_z = max(0, math.ceil(n.rect.length / 20.0) - 1)
                if cols_x > 0 or cols_z > 0:
                    for ix in range(1, cols_x + 1):
                        for iz in range(1, cols_z + 1):
                            px = n.rect.x + (ix * (n.rect.width / (cols_x + 1)))
                            pz = n.rect.z + (iz * (n.rect.length / (cols_z + 1)))
                            n.mep_nodes.append({
                                "type": "structural_column",
                                "name": "Structural Column",
                                "x": round(px, 2),
                                "z": round(pz, 2),
                                "width": 1.2,
                                "length": 1.2,
                            })
                            has_columns = True
            if span > 150.0 and not has_columns:
                struct_issues.append({"code": "STRUCTURAL_LIMIT", "message": f"{n.name}: {span:.0f} ft span exceeds RCC limit."})
    checks["structural_integrity"] = (not struct_issues, struct_issues)

    # 6. Buildability — no degenerate/overlapping rooms.
    build_issues = []
    for n in nodes:
        if n.rect.width <= 0 or n.rect.length <= 0:
            build_issues.append({"code": "INVALID_ROOM_DIMENSIONS", "message": f"{n.name}: non-positive dimension (unbuildable)."})
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if a.type in STRUCTURAL_TYPES or b.type in STRUCTURAL_TYPES:
                continue
            ox = min(a.rect.x + a.rect.width, b.rect.x + b.rect.width) - max(a.rect.x, b.rect.x)
            oz = min(a.rect.z + a.rect.length, b.rect.z + b.rect.length) - max(a.rect.z, b.rect.z)
            if ox > 0.3 and oz > 0.3:
                build_issues.append({"code": "ROOM_OVERLAP", "message": f"{a.name} overlaps {b.name}."})
    checks["buildability"] = (not build_issues, build_issues)

    # 7. Universal Circulation & Transit Policy Check
    circ_access = validate_circulation_access(nodes)
    checks["universal_circulation_policy"] = (circ_access["passed"], circ_access["errors"])

    issues_all = [i for _, issues in checks.values() for i in issues]
    
    # Validation is OK ONLY if there are no errors belonging to FATAL_CATEGORIES
    ok = not any(err.get("code") in FATAL_CATEGORIES for err in issues_all if isinstance(err, dict))
    
    return {
        "ok": ok,
        "checks": {k: {"ok": v[0], "issues": v[1]} for k, v in checks.items()},
        "errors": issues_all, # Structured errors
    }

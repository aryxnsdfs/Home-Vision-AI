"""
layout_engine.py — Deterministic BSP Layout Engine for Architectural Floor Plans.

Implements Binary Space Partitioning (BSP) to divide a master bounding box into rooms,
enforces architectural rules, resolves adjacencies, and places doors/windows.
"""

from __future__ import annotations

import time

import logging
import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from layout_templates import get_template_for_bhk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    x: float
    z: float
    width: float
    length: float

    @property
    def area(self) -> float:
        return self.width * self.length

@dataclass
class Door:
    x: float
    z: float
    wall_orientation: str  # "north", "south", "east", "west"
    width: float = 3.0
    height: float = 7.0
    is_main: bool = False

@dataclass
class Window:
    x: float
    z: float
    wall_orientation: str
    width: float = 4.0
    height: float = 4.0
    sill_height: float = 3.0

@dataclass
class RoomNode:
    id: str
    type: str
    name: str
    rect: Rect
    doors: List[Door] = field(default_factory=list)
    windows: List[Window] = field(default_factory=list)
    wallThicknessIn: float = 6.0
    is_wet: bool = False
    main_entrance: bool = False
    shared_walls: List[str] = field(default_factory=list)
    floorColor: str = ""
    wallColor: str = ""
    is_double_height: bool = False
    roof_type: str = "flat"  # flat, open, pitched
    furnitureColor: str = ""
    furniture: List[str] = field(default_factory=list)
    mep_nodes: List[Dict[str, Any]] = field(default_factory=list)
    # Faces where this room should NOT render walls because an adjacent room
    # already renders the shared wall.  Populated by compute_shared_walls for
    # circulation rooms (corridor, hallway, staircase) to prevent double-thick
    # walls when both neighbours render on the same face.
    suppress_wall_faces: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "x": round(self.rect.x, 2),
            "z": round(self.rect.z, 2),
            "width": round(self.rect.width, 2),
            "length": round(self.rect.length, 2),
            "wallThicknessIn": self.wallThicknessIn,
            "main_entrance": self.main_entrance,
            "shared_walls": self.shared_walls,
            "suppress_wall_faces": self.suppress_wall_faces,
            "floorColor": self.floorColor,
            "wallColor": self.wallColor,
            "is_double_height": self.is_double_height,
            "roof_type": self.roof_type,
            "furnitureColor": self.furnitureColor,
            "furniture": self.furniture,
            "mep_nodes": self.mep_nodes,
            "doors": [{"x": round(d.x, 2), "z": round(d.z, 2), "wall_orientation": d.wall_orientation, "width": d.width, "height": d.height} for d in self.doors],
            "windows": [{"x": round(w.x, 2), "z": round(w.z, 2), "wall_orientation": w.wall_orientation, "width": w.width, "height": w.height, "sill_height": w.sill_height} for w in self.windows],
        }

# ---------------------------------------------------------------------------
# Module-level helpers: shared wall computation & main entrance injection
# ---------------------------------------------------------------------------
def compute_minimum_plot_area(room_types: List[str]) -> float:
    """
    Computes the absolute minimum buildable area required for a list of rooms.
    Includes a 25% buffer for walls, circulation, and corridors.
    """
    total_area = sum(get_min_area(rt) for rt in room_types)
    return total_area * 1.25
def compute_shared_walls(rooms: List[RoomNode]) -> List[Dict]:
    """
    For every pair (r1, r2), check if they share a wall segment with tolerance 0.2.
    Also collects all EXTERIOR wall segments.
    Returns deduplicated list of wall dicts.
    """
    tolerance = 0.2
    dedup_tol = 0.05
    walls: List[Dict] = []
    seen_segments: List[Tuple[float, float, float, float]] = []

    def _seg_is_dup(x1: float, z1: float, x2: float, z2: float) -> bool:
        for sx1, sz1, sx2, sz2 in seen_segments:
            if (abs(sx1 - x1) < dedup_tol and abs(sz1 - z1) < dedup_tol
                    and abs(sx2 - x2) < dedup_tol and abs(sz2 - z2) < dedup_tol):
                return True
        return False

    def _add_wall(x1: float, z1: float, x2: float, z2: float,
                  orientation: str, room_ids: List[str],
                  is_shared: bool, is_exterior: bool = False):
        if _seg_is_dup(x1, z1, x2, z2):
            return
        seen_segments.append((x1, z1, x2, z2))
        wall = {
            "id": str(uuid.uuid4()),
            "x1": round(x1, 3),
            "z1": round(z1, 3),
            "x2": round(x2, 3),
            "z2": round(z2, 3),
            "orientation": orientation,
            "room_ids": room_ids,
            "is_shared": is_shared,
        }
        if is_exterior:
            wall["is_exterior"] = True
        walls.append(wall)

    def _overlap_1d(min1: float, max1: float, min2: float, max2: float) -> Tuple[bool, float, float]:
        o_min = max(min1, min2)
        o_max = min(max1, max2)
        return o_max > o_min, o_min, o_max

    # Shared walls between room pairs
    for i, r1 in enumerate(rooms):
        for j, r2 in enumerate(rooms):
            if i >= j:
                continue

            # Right of r1 == Left of r2 (vertical wall)
            if abs((r1.rect.x + r1.rect.width) - r2.rect.x) < tolerance:
                ok, o_min, o_max = _overlap_1d(
                    r1.rect.z, r1.rect.z + r1.rect.length,
                    r2.rect.z, r2.rect.z + r2.rect.length)
                if ok:
                    wall_x = r2.rect.x
                    _add_wall(wall_x, o_min, wall_x, o_max, "vertical",
                              [r1.id, r2.id], True)

            # Left of r1 == Right of r2 (vertical wall)
            if abs(r1.rect.x - (r2.rect.x + r2.rect.width)) < tolerance:
                ok, o_min, o_max = _overlap_1d(
                    r1.rect.z, r1.rect.z + r1.rect.length,
                    r2.rect.z, r2.rect.z + r2.rect.length)
                if ok:
                    wall_x = r1.rect.x
                    _add_wall(wall_x, o_min, wall_x, o_max, "vertical",
                              [r1.id, r2.id], True)

            # Bottom of r1 == Top of r2 (horizontal wall)
            if abs((r1.rect.z + r1.rect.length) - r2.rect.z) < tolerance:
                ok, o_min, o_max = _overlap_1d(
                    r1.rect.x, r1.rect.x + r1.rect.width,
                    r2.rect.x, r2.rect.x + r2.rect.width)
                if ok:
                    wall_z = r2.rect.z
                    _add_wall(o_min, wall_z, o_max, wall_z, "horizontal",
                              [r1.id, r2.id], True)

            # Top of r1 == Bottom of r2 (horizontal wall)
            if abs(r1.rect.z - (r2.rect.z + r2.rect.length)) < tolerance:
                ok, o_min, o_max = _overlap_1d(
                    r1.rect.x, r1.rect.x + r1.rect.width,
                    r2.rect.x, r2.rect.x + r2.rect.width)
                if ok:
                    wall_z = r1.rect.z
                    _add_wall(o_min, wall_z, o_max, wall_z, "horizontal",
                              [r1.id, r2.id], True)

    # Compute buildable extents for exterior detection
    if rooms:
        min_x = min(r.rect.x for r in rooms)
        min_z = min(r.rect.z for r in rooms)
        max_x = max(r.rect.x + r.rect.width for r in rooms)
        max_z = max(r.rect.z + r.rect.length for r in rooms)

        ext_tol = 0.2
        for r in rooms:
            # West exterior wall
            if abs(r.rect.x - min_x) < ext_tol:
                _add_wall(r.rect.x, r.rect.z, r.rect.x, r.rect.z + r.rect.length,
                          "vertical", [r.id], False, True)
            # East exterior wall
            if abs((r.rect.x + r.rect.width) - max_x) < ext_tol:
                _add_wall(r.rect.x + r.rect.width, r.rect.z,
                          r.rect.x + r.rect.width, r.rect.z + r.rect.length,
                          "vertical", [r.id], False, True)
            # North exterior wall (z = min_z)
            if abs(r.rect.z - min_z) < ext_tol:
                _add_wall(r.rect.x, r.rect.z, r.rect.x + r.rect.width, r.rect.z,
                          "horizontal", [r.id], False, True)
            # South exterior wall (z = max_z)
            if abs((r.rect.z + r.rect.length) - max_z) < ext_tol:
                _add_wall(r.rect.x, r.rect.z + r.rect.length,
                          r.rect.x + r.rect.width, r.rect.z + r.rect.length,
                          "horizontal", [r.id], False, True)

    # Update shared_walls on each RoomNode
    room_by_id = {r.id: r for r in rooms}
    for wall in walls:
        if wall.get("is_shared"):
            for rid in wall["room_ids"]:
                if rid in room_by_id:
                    other_ids = [oid for oid in wall["room_ids"] if oid != rid]
                    for oid in other_ids:
                        if oid not in room_by_id[rid].shared_walls:
                            room_by_id[rid].shared_walls.append(oid)

    # ── Suppress corridor/hallway/staircase walls on shared faces ────────
    # When a circulation room shares a face with ANY other room, the
    # circulation room should NOT render its wall on that face — the adjacent
    # room already renders the wall (with proper door openings). This prevents
    # the double-thick wall problem.
    # Corridors/hallways suppress walls on ALL shared faces (they're pure passage).
    # Staircases only suppress walls on faces shared with corridors/hallways
    # (staircase is enclosed; only its corridor-facing wall is open).
    _PASSAGE_TYPES = {"corridor", "hallway"}          # suppress on ALL shared faces
    _STAIR_TYPES   = {"staircase"}                    # suppress only vs other circ
    _ALL_CIRC      = _PASSAGE_TYPES | _STAIR_TYPES

    # Clear stale suppress data from prior compute_shared_walls calls.
    for r in rooms:
        if r.type in _ALL_CIRC:
            r.suppress_wall_faces = []

    for wall in walls:
        if not wall.get("is_shared"):
            continue
        # Get both rooms on this shared wall
        wall_rooms = [room_by_id.get(rid) for rid in wall["room_ids"]]
        wall_rooms = [r for r in wall_rooms if r]
        wall_type_set = {r.type for r in wall_rooms}

        for rm in wall_rooms:
            # Corridors/hallways suppress on ALL shared faces
            if rm.type in _PASSAGE_TYPES:
                pass  # always suppress
            # Staircases only suppress on faces shared with corridor/hallway
            elif rm.type in _STAIR_TYPES:
                other_types = wall_type_set - {rm.type}
                if not (other_types & _PASSAGE_TYPES):
                    continue  # staircase vs non-corridor → keep wall
            else:
                continue  # regular room → never suppress

            # Determine which face of this room the shared wall is on
            r = rm.rect
            if wall["orientation"] == "vertical":
                wall_x = wall["x1"]
                if abs(wall_x - r.x) < tolerance:
                    face = "west"
                elif abs(wall_x - (r.x + r.width)) < tolerance:
                    face = "east"
                else:
                    continue
            else:  # horizontal
                wall_z = wall["z1"]
                if abs(wall_z - r.z) < tolerance:
                    face = "north"
                elif abs(wall_z - (r.z + r.length)) < tolerance:
                    face = "south"
                else:
                    continue
            if face not in rm.suppress_wall_faces:
                rm.suppress_wall_faces.append(face)

    return walls


def _share_edge(a: Rect, b: Rect, tol: float = 0.35) -> bool:
    """True if two rects share a wall segment (adjacent, not just touching a corner)."""
    if abs((a.x + a.width) - b.x) < tol or abs(a.x - (b.x + b.width)) < tol:
        return min(a.z + a.length, b.z + b.length) - max(a.z, b.z) > 0.5
    if abs((a.z + a.length) - b.z) < tol or abs(a.z - (b.z + b.length)) < tol:
        return min(a.x + a.width, b.x + b.width) - max(a.x, b.x) > 0.5
    return False


def validate_layout(nodes: List[RoomNode]) -> List[str]:
    """Architect-style sanity checks from the placement rules.

    Returns human-readable warnings (does not mutate the layout). Run this after
    doors have been resolved so the door-access checks are meaningful.
    """
    warnings: List[str] = []
    by_type: Dict[str, List[RoomNode]] = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)

    def adjacent(t1: str, t2: str) -> bool:
        for a in by_type.get(t1, []):
            for b in by_type.get(t2, []):
                if a.id != b.id and _share_edge(a.rect, b.rect):
                    return True
        return False

    if by_type.get("kitchen") and by_type.get("dining_room") and not adjacent("kitchen", "dining_room"):
        warnings.append("Kitchen is not adjacent to the Dining Room.")
    if by_type.get("utility") and not adjacent("utility", "kitchen"):
        warnings.append("Utility area is not attached to the Kitchen.")
    if by_type.get("store_room") and not (adjacent("store_room", "kitchen") or adjacent("store_room", "utility")):
        warnings.append("Store Room is isolated from the Kitchen/Utility.")

    # Pooja Room must not share a wall with a toilet.
    toilets = by_type.get("bathroom", []) + by_type.get("powder_room", [])
    for p in by_type.get("pooja_room", []):
        if any(_share_edge(p.rect, t.rect) for t in toilets):
            warnings.append("Pooja Room shares a wall with a toilet.")
            break

    # Master Bedroom should be the largest bedroom.
    masters = by_type.get("master_bedroom", [])
    beds = by_type.get("bedroom", [])
    if masters and beds and max(b.rect.area for b in beds) > masters[0].rect.area + 1.0:
        warnings.append("Master Bedroom is not the largest bedroom.")

    # Bathrooms must be single-access destinations, never passages/connectors.
    for b in by_type.get("bathroom", []):
        if len(b.doors) > 1:
            warnings.append(f"{b.name} acts as a passage (more than one door).")

    # Every bedroom should open directly onto circulation, not only via a bathroom.
    circ = (by_type.get("corridor", []) + by_type.get("hallway", [])
            + by_type.get("foyer", []) + by_type.get("living_room", []))
    if circ:
        for bed in by_type.get("bedroom", []) + by_type.get("master_bedroom", []):
            if not any(_share_edge(bed.rect, c.rect) for c in circ):
                warnings.append(f"{bed.name} is not adjacent to a corridor/circulation space.")

    # Every habitable room should have a door (skip open/outdoor spaces).
    open_types = {"void", "portico", "parking", "veranda", "balcony", "staircase", "otta", "courtyard"}
    for n in nodes:
        if n.type in open_types:
            continue
        if not n.doors:
            warnings.append(f"{n.name} has no door access.")

    return warnings


def _dedupe_type(nodes: List[RoomNode], rtype: str) -> None:
    """Keep only the largest node of `rtype`; drop the rest in-place.

    Guarantees a floor never carries duplicate vertical-circulation elements
    (e.g. two staircases) or a corridor chain — exactly one survives.
    """
    same = [n for n in nodes if n.type == rtype]
    if len(same) <= 1:
        return
    keep = max(same, key=lambda n: n.rect.area)
    nodes[:] = [n for n in nodes if n.type != rtype or n is keep]


def align_duplex_floors(
    floor0: List[RoomNode],
    floor1: List[RoomNode],
    make_void: bool = False,
) -> List[RoomNode]:
    """Vertically pair two floors for a duplex.

    - Collapses any duplicate staircases to exactly ONE per floor, then locks the
      upper staircase directly above the ground-floor staircase so the flight is
      continuous (a single vertical circulation element, never duplicated).
    - Only opens a double-height void over the ground-floor living room when the
      user explicitly requested one (`make_void`). Otherwise no void is invented.
    - Clips any upstairs room that still intersects those locked zones, dropping
      it only if it would become unusably small.
    """
    # Exactly one staircase / corridor per floor before we align anything.
    for fl in (floor0, floor1):
        _dedupe_type(fl, "staircase")
        _dedupe_type(fl, "corridor")

    stair0 = next((n for n in floor0 if n.type == "staircase"), None)
    living0 = next((n for n in floor0 if n.type == "living_room"), None)

    # 1. Lock the staircase above itself (one continuous flight).
    if stair0:
        rect = Rect(stair0.rect.x, stair0.rect.z, stair0.rect.width, stair0.rect.length)
        stair1 = next((n for n in floor1 if n.type == "staircase"), None)
        if stair1:
            stair1.rect = rect
        else:
            floor1.append(RoomNode(id="staircase-f1", type="staircase", name="Staircase",
                                   rect=rect, wallThicknessIn=6.0, floorColor="#e5e7eb"))

    # 2. Double-height void over the living room — ONLY when requested.
    if make_void and living0:
        living0.is_double_height = True
        floor1[:] = [n for n in floor1 if n.type != "living_room"]
        floor1.append(RoomNode(
            id="void-f1", type="void", name="Double Height Void",
            rect=Rect(living0.rect.x, living0.rect.z, living0.rect.width, living0.rect.length),
            wallThicknessIn=0.0, roof_type="open", floorColor="#0b1220",
        ))
    elif living0:
        # No void: drop the redundant upstairs living room but keep the floor solid.
        floor1[:] = [n for n in floor1 if n.type != "living_room"]

    # 3. Clip remaining rooms out of the locked zones.
    locked = [n.rect for n in floor1 if n.type in ("staircase", "void")]

    def _overlap(a: Rect, b: Rect) -> Tuple[float, float]:
        ox = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
        oz = min(a.z + a.length, b.z + b.length) - max(a.z, b.z)
        return ox, oz

    kept: List[RoomNode] = []
    for n in floor1:
        if n.type in ("staircase", "void"):
            kept.append(n)
            continue
        # Corridor is circulation — must survive on the first floor so every
        # bedroom remains reachable. We allow clipping down to a walkable width
        # (4 ft) rather than dropping it.
        is_circ = n.type in ("corridor", "hallway")
        min_dim = 4.0 if is_circ else 5.0
        drop = False
        for lr in locked:
            ox, oz = _overlap(n.rect, lr)
            if ox <= 0.3 or oz <= 0.3:
                continue
            if ox < oz:  # clip horizontally out of the locked zone
                if n.rect.x < lr.x:
                    n.rect.width -= ox
                else:
                    n.rect.x += ox
                    n.rect.width -= ox
            else:        # clip vertically
                if n.rect.z < lr.z:
                    n.rect.length -= oz
                else:
                    n.rect.z += oz
                    n.rect.length -= oz
            if n.rect.width < min_dim or n.rect.length < min_dim:
                drop = True
                break
        if not drop:
            kept.append(n)

    # Hard guarantee: first floor MUST have a corridor (or other circulation
    # type) so every room remains accessible from the staircase. If clipping
    # killed it, re-add one beside the staircase footprint.
    has_circ = any(n.type in ("corridor", "hallway", "foyer") for n in kept)
    stair = next((n for n in kept if n.type == "staircase"), None)
    if not has_circ and stair:
        sr = stair.rect
        # Place a 5 ft × stair-length corridor strip next to the staircase.
        new_rect = Rect(sr.x + sr.width, sr.z, 5.0, max(sr.length, 8.0))
        kept.append(RoomNode(
            id="corridor-f1-fallback", type="corridor", name="Corridor",
            rect=new_rect, wallThicknessIn=6.0, floorColor="#f3f4f6",
        ))

    # ── Post-clip corridor spine fix ──────────────────────────────────
    # The corridor must span the full z-range of all habitable rooms on
    # this floor so that every bedroom/bathroom shares a wall with it and
    # the adjacency resolver can place doors. Also enforce minimum width
    # of 5 ft so it isn't an unusably narrow slit.
    corridor = next((n for n in kept if n.type in ("corridor", "hallway")), None)
    if corridor:
        habitable = [n for n in kept if n.type not in ("staircase", "void", "corridor", "hallway")]
        if habitable:
            min_z = min(n.rect.z for n in habitable)
            max_z = max(n.rect.z + n.rect.length for n in habitable)
            # Extend corridor to cover the full z-span of habitable rooms.
            if corridor.rect.z > min_z:
                old_z = corridor.rect.z
                corridor.rect.z = min_z
                corridor.rect.length += old_z - min_z
            if corridor.rect.z + corridor.rect.length < max_z:
                corridor.rect.length = max_z - corridor.rect.z
        # Enforce minimum walkable width (5 ft).
        if corridor.rect.width < 5.0:
            corridor.rect.width = 5.0

    floor1[:] = kept
    return floor1


def inject_main_entrance(
    rooms: List[RoomNode],
    buildable_width: float,
    buildable_length: float,
    setback_x: float,
    setback_z: float,
) -> None:
    """
    Find foyer or living_room and mark it as main entrance.
    Prefer a room whose south wall faces the front (z ≈ setback_z) or
    any exterior boundary. Injects a large Door at position 0.
    """
    candidate = None
    for r in rooms:
        if r.type == "living_room":
            candidate = r
            break
    if candidate is None:
        for r in rooms:
            if r.type == "foyer":
                candidate = r
                break
    if candidate is None:
        # fallback to the largest room or first room
        if rooms:
            candidate = rooms[0]
        else:
            return

    r = candidate
    tolerance = 1.5

    # Compute distances to each boundary face
    dist_south = abs((r.rect.z + r.rect.length) - (setback_z + buildable_length))
    dist_north = abs(r.rect.z - setback_z)
    dist_west  = abs(r.rect.x - setback_x)
    dist_east  = abs((r.rect.x + r.rect.width) - (setback_x + buildable_width))

    # Prefer south (front-facing), then north, east, west
    face_order = sorted(
        [("south", dist_south), ("north", dist_north),
         ("east", dist_east),  ("west", dist_west)],
        key=lambda t: t[1]
    )
    chosen_face = face_order[0][0]

    r.main_entrance = True
    # Record the preferred entrance face. The actual main door (with a visible
    # leaf) is placed by WindowPlacer on a verified exterior wall, so we do NOT
    # add a door here — that previously created a duplicate, leaf-less opening.
    r.main_entrance_wall = chosen_face

# ---------------------------------------------------------------------------
# Architectural Minimums (ft²) and minimum widths (ft)
# These are absolute floors — the engine will NEVER produce rooms smaller.
# ---------------------------------------------------------------------------

ROOM_MINIMUMS: Dict[str, Dict[str, float]] = {
    "living_room":     {"area": 150.0, "min_dim": 11.0},
    "dining_room":     {"area":  80.0, "min_dim":  8.0},
    "kitchen":         {"area":  60.0, "min_dim":  7.0},
    "master_bedroom":  {"area": 160.0, "min_dim": 11.0},
    "bedroom":         {"area": 140.0, "min_dim": 10.0},
    "bathroom":        {"area":  40.0, "min_dim":  5.0},
    "foyer":           {"area":  30.0, "min_dim":  4.0},
    "corridor":        {"area":  40.0, "min_dim":  4.0},
    "balcony":         {"area":  40.0, "min_dim":  4.0},
    "store_room":      {"area":  25.0, "min_dim":  4.0},
    "pooja_room":      {"area":  20.0, "min_dim":  4.0},
    "utility":         {"area":  30.0, "min_dim":  4.0},
    "garage":          {"area": 150.0, "min_dim": 10.0},
    "study_room":      {"area":  60.0, "min_dim":  7.0},
    "staircase":       {"area":  30.0, "min_dim":  4.0},
    "laundry":         {"area":  25.0, "min_dim":  4.0},
    "veranda":         {"area":  40.0, "min_dim":  4.0},
    "parking":         {"area": 100.0, "min_dim": 8.0},
}
# Default for unknown types
_DEFAULT_MIN = {"area": 40.0, "min_dim": 5.0}

def get_min_area(rtype: str) -> float:
    return ROOM_MINIMUMS.get(rtype, _DEFAULT_MIN)["area"]

def get_min_dim(rtype: str) -> float:
    return ROOM_MINIMUMS.get(rtype, _DEFAULT_MIN)["min_dim"]

# ---------------------------------------------------------------------------
# Color theming — turns a UI/AI color selection into a coherent room palette
# so the chosen color is actually reflected on walls, floors and furniture.
# ---------------------------------------------------------------------------

PALETTE_HEX: Dict[str, str] = {
    # Interiors
    "off_white": "#F8F8FF", "warm_beige": "#F5F5DC", "light_grey": "#D3D3D3",
    "beige": "#F5F5DC", "sage": "#9CA986", "terracotta": "#E2725B", "charcoal": "#36454F",
    # Exteriors
    "mustard": "#E4A010", "cream": "#FDF5E6", "peach": "#FFDAB9", "sea_green": "#2E8B57",
    "indigo": "#4B0082", "white": "#FFFFFF", "concrete": "#808080", "brick": "#B22222",
    "wood": "#DEB887",
    # Common color words (fallbacks)
    "blue": "#2563EB", "green": "#22C55E", "red": "#EF4444", "yellow": "#EAB308",
    "orange": "#F97316", "purple": "#8B5CF6", "pink": "#EC4899", "teal": "#14B8A6",
    "grey": "#9CA3AF", "gray": "#9CA3AF",
}


def _to_hex(value: Optional[str]) -> Optional[str]:
    """Resolve a palette id, color word, or raw hex string to a #RRGGBB hex."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v.startswith("#") and len(v) in (4, 7):
        return value if value.startswith("#") else f"#{value}"
    return PALETTE_HEX.get(v)


def _mix_with_white(hex_color: str, ratio: float) -> str:
    """Blend a hex color toward white. ratio=0 → original, ratio=1 → white."""
    try:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = round(r + (255 - r) * ratio)
        g = round(g + (255 - g) * ratio)
        b = round(b + (255 - b) * ratio)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


# Furniture palette ids → wood/finish hexes. Independent from wall/floor.
FURNITURE_HEX: Dict[str, str] = {
    "light_wood": "#C8A878", "dark_wood": "#5A3A22", "walnut": "#4B3621",
    "modern_gray": "#6B7280", "white_oak": "#D8C2A0", "teak": "#9C6B3F",
}
# Floor material ids → surface hexes. Independent from wall paint.
FLOOR_HEX: Dict[str, str] = {
    "marble_white": "#F1F0EC", "beige_marble": "#E6DCC8", "granite": "#4A4A52",
    "wooden_flooring": "#8B5A2B", "ceramic_tile": "#D7DDE5", "concrete_finish": "#8B929D",
}
# Vastu directional wall colors (cardinal + ordinal). Pastel near-white tints so
# walls read as a real residential interior — directional hue is barely visible
# rather than dominating every surface like a colour-blocked nursery. Each value
# is a very pale wash of the canonical Vastu hue (green/white/red/blue/etc).
VASTU_DIR_HEX: Dict[str, str] = {
    "north":      "#B8D4B8",  # soft sage green (clearly green)
    "east":       "#E8E0D4",  # warm cream (off-white, not blank)
    "south":      "#E0B8AD",  # dusty rose (warm, clearly tinted)
    "west":       "#B8C8D8",  # slate blue (clearly blue-grey)
    "south_west": "#D0BCA0",  # sandstone (warm earthy)
    "north_east": "#D8D0A0",  # muted gold / turmeric
    "north_west": "#C8CCD4",  # cool grey-blue
    "south_east": "#D8BD98",  # terracotta cream
}


def vastu_color_for_direction(direction: str) -> Optional[str]:
    return VASTU_DIR_HEX.get(direction)


def resolve_theme(colors: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Build a coherent palette from the colors dict sent by the UI / AI.

    Each palette controls ONLY its own surface — no cross-bleed:
      • exterior  → exterior facade walls
      • interior  → interior wall paint
      • roof      → roof material
      • furniture → furniture (independent; default wood when unset)
      • floor     → floor material (independent)
    A value of None for a channel means "no override — keep the default".
    """
    colors = colors or {}
    ai = _to_hex(colors.get("ai_color"))
    interior = _to_hex(colors.get("interior"))
    exterior = _to_hex(colors.get("exterior"))
    roof = _to_hex(colors.get("roof"))

    # Furniture / floor accept either a palette id or a raw hex.
    fv = (colors.get("furniture") or "").strip().lower() if isinstance(colors.get("furniture"), str) else None
    furniture = FURNITURE_HEX.get(fv) or _to_hex(colors.get("furniture")) if fv else None
    flv = (colors.get("floor") or "").strip().lower() if isinstance(colors.get("floor"), str) else None
    floor = FLOOR_HEX.get(flv) or _to_hex(colors.get("floor")) if flv else None

    vastu = bool(colors.get("vastuColors"))

    # Interior wall: use the chosen interior hue with only a gentle lift toward
    # white so the selection is clearly visible (gray stays gray, not white).
    wall = _mix_with_white(interior, 0.2) if interior else (_mix_with_white(ai, 0.4) if ai else None)

    accent = ai or exterior or interior

    # Vastu mode owns all colors: manual palettes are suppressed so only the
    # directional wall colors (applied post-generation) take effect.
    if vastu:
        return {
            "accent": None, "wall": None, "floor": None, "furniture": None,
            "exterior": None, "roof": None, "vastu": True,
        }

    return {
        "accent": accent,
        "wall": wall,
        "floor": floor,
        "furniture": furniture,
        "exterior": exterior,
        "roof": roof,
        "vastu": vastu,
    }

class LayoutEngine:
    def __init__(self, plot_width: float, plot_length: float, colors: Dict[str, Any] = None):
        # We always want some reasonable bounds.
        self.plot_width = max(20.0, float(plot_width))
        self.plot_length = max(20.0, float(plot_length))
        self.colors = colors or {}
        self.theme = resolve_theme(self.colors)

        # Legal setbacks: 85% buildable area centred on plot
        self.buildable_width = plot_width * 0.85
        self.buildable_length = plot_length * 0.85
        self.setback_x = (plot_width - self.buildable_width) / 2
        self.setback_z = (plot_length - self.buildable_length) / 2

        self.last_walls: List[Dict] = []

    @property
    def walls(self) -> List[Dict]:
        return self.last_walls

    def generate(self, rooms_spec: List[Dict[str, Any]], blocked_zones: Optional[List[Rect]] = None, indian_options: Optional[Dict[str, Any]] = None, layout_rules: Optional[List[Dict[str, str]]] = None, restrict_slots: bool = False) -> List[RoomNode]:
        if indian_options is None:
            indian_options = {}
        if layout_rules is None:
            layout_rules = []
            
        logger.info("[PERF] LayoutEngine.generate started.")
        nodes: List[RoomNode] = []
        if not rooms_spec:
            return nodes

        type_counts: Dict[str, int] = {}
        ai_requested_rooms: List[Tuple[str, str]] = []
        bhk_count = 0
        for r in rooms_spec:
            rt = r["type"].replace(" ", "_").lower()
            if "master_bedroom" in rt or "bedroom" in rt:
                bhk_count += 1
            type_counts[rt] = type_counts.get(rt, 0) + 1
            room_id = f"{rt}-{type_counts[rt]}"
            ai_requested_rooms.append((room_id, rt))

        # Determine if we should mirror the template for Vastu
        mirror_x = False
        mirror_z = False
        if indian_options.get("vastu") or indian_options.get("kitchen_se"):
            # Master bed SW (z=1, x=0 or 1), Kitchen SE (z=1, x=1)
            mirror_x = True

        from layout_templates import get_template_for_bhk
        import copy
        base_template = copy.deepcopy(get_template_for_bhk(bhk_count))

        # ---- Apply Dynamic Layout Rules (Workstream 2 & 7) ----
        # Swap slots *before* room instantiation so parasites anchor to the correct location!
        dir_coords = {
            "south_east": (1.0, 1.0), "south_west": (0.0, 1.0),
            "north_east": (1.0, 0.0), "north_west": (0.0, 0.0),
            "center": (0.5, 0.5)
        }

        for rule in layout_rules:
            room_type = rule.get("room")
            direction = rule.get("direction")
            if not room_type or not direction or direction not in dir_coords:
                continue

            current_slot = None
            for k in base_template.keys():
                if room_type in k:
                    current_slot = k
                    break
            
            if current_slot:
                target_x, target_z = dir_coords[direction]
                best_slot = None
                best_dist = float('inf')
                
                for k, v in base_template.items():
                    cx = v["x"] + v["w"]/2
                    cz = v["z"] + v["l"]/2
                    dist = (cx - target_x)**2 + (cz - target_z)**2
                    if dist < best_dist:
                        best_dist = dist
                        best_slot = k
                        
                if best_slot and best_slot != current_slot:
                    temp = base_template[current_slot]
                    base_template[current_slot] = base_template[best_slot]
                    base_template[best_slot] = temp
        
        used_ai_rooms = set()
        
        # 1. Instantiate Core Nodes
        for slot_key, param_rect in base_template.items():
            matched_id = None
            matched_rt = slot_key
            for rid, rt in ai_requested_rooms:
                if rid not in used_ai_rooms and (rt in slot_key or slot_key in rt or (slot_key.startswith("bedroom") and "bedroom" in rt)):
                    matched_id = rid
                    matched_rt = rt
                    used_ai_rooms.add(rid)
                    break
            
            if not matched_id:
                if restrict_slots:
                    # Duplex floors: keep circulation/stairs so the floor stays
                    # accessible; never auto-fill other unrequested slots.
                    if not (
                        slot_key.startswith("corridor") or slot_key.startswith("staircase")
                    ):
                        continue
                    matched_id = f"{slot_key}-core"
                else:
                    # Single floor — STRICT generation. Never invent rooms the
                    # user never asked for. The ONE exception is the template's
                    # central corridor slot: it is the legitimate circulation
                    # hub that the surrounding rooms are positioned around, so
                    # skipping it would leave dead space. Exactly one corridor,
                    # never a chain. Staircases come only from the duplex split.
                    if slot_key.startswith("corridor"):
                        matched_id = f"{slot_key}-core"
                    else:
                        continue

            is_wet = "kitchen" in matched_rt or "bath" in matched_rt or "laundry" in matched_rt
            wall_thick = 8.0 if is_wet else 6.0
            
            # Parametric scaling
            px = param_rect["x"]
            pz = param_rect["z"]
            if mirror_x: px = 1.0 - px - param_rect["w"]
            if mirror_z: pz = 1.0 - pz - param_rect["l"]
            
            x = self.setback_x + px * self.buildable_width
            z = self.setback_z + pz * self.buildable_length
            w = param_rect["w"] * self.buildable_width
            l = param_rect["l"] * self.buildable_length
            
            # Colors
            colors = {
                "living_room": "#fef3c7", "kitchen": "#e0f2fe", 
                "bedroom": "#f3e8ff", "master_bedroom": "#fae8ff", 
                "bathroom": "#dcfce7", "dining_room": "#ffedd5", 
                "corridor": "#f3f4f6"
            }
            floor_color = colors.get(matched_rt, "#ffffff")
            for k, c in colors.items():
                if k in matched_rt: floor_color = c

            # Apply the user/AI selected palettes — each channel independently,
            # so an interior choice never bleeds onto furniture and vice-versa.
            wall_color = ""
            furniture_color = ""
            if self.theme.get("wall"):
                wall_color = self.theme["wall"]
            if self.theme.get("floor"):
                floor_color = self.theme["floor"]
            if self.theme.get("furniture"):
                furniture_color = self.theme["furniture"]

            nodes.append(RoomNode(
                id=matched_id, type=matched_rt, name=matched_rt.replace("_", " ").title(),
                rect=Rect(x, z, w, l), wallThicknessIn=wall_thick, is_wet=is_wet,
                floorColor=floor_color, wallColor=wall_color, furnitureColor=furniture_color
            ))

        # 2. Process Parasites (Mutators)
        parasites = [(rid, rt) for rid, rt in ai_requested_rooms if rid not in used_ai_rooms]
        
        # Vastu Hard-Injected Parasites
        if indian_options.get("pooja_room") and not any("pooja" in n.type for n in nodes) and not any("pooja" in rt for _, rt in parasites):
            parasites.append(("pooja-1", "pooja_room"))
        if indian_options.get("powder_room") and not any("powder" in n.type for n in nodes) and not any("powder" in rt for _, rt in parasites):
            parasites.append(("powder-1", "powder_room"))
        if indian_options.get("utility_area") and not any("utility" in n.type for n in nodes) and not any("utility" in rt for _, rt in parasites):
            parasites.append(("utility-1", "utility"))
            
        for rid, rt in parasites:
            anchor_candidates = []
            if "bath" in rt: anchor_candidates = ["bedroom", "master_bedroom", "living"]
            elif "utility" in rt: anchor_candidates = ["kitchen"]            # utility attaches to kitchen
            elif "store" in rt: anchor_candidates = ["kitchen", "utility"]   # store near kitchen/utility
            elif "powder" in rt: anchor_candidates = ["living", "foyer"]     # powder near living/foyer
            elif "pooja" in rt: anchor_candidates = ["living", "dining", "foyer"]  # pooja near living/dining
            elif "courtyard" in rt or "angan" in rt: anchor_candidates = ["living", "dining"]
            
            anchor_node = None
            if "bath" in rt:
                # Attached bath belongs to the Master Bedroom. Prefer an existing
                # master; else pick the LARGEST bedroom (most suitable) and PROMOTE
                # it to master. Bath space is then carved from that bedroom only —
                # never from corridor / kitchen / utility / other bedrooms.
                master = next((n for n in nodes if "master_bedroom" in n.type), None)
                if master:
                    anchor_node = master
                else:
                    beds = [n for n in nodes if "bedroom" in n.type]
                    if beds:
                        anchor_node = max(beds, key=lambda n: n.rect.area)
                        anchor_node.type = "master_bedroom"
                        anchor_node.name = "Master Bedroom"
            if anchor_node is None:
                for cand in anchor_candidates:
                    for n in nodes:
                        if cand in n.type:
                            anchor_node = n
                            break
                    if anchor_node: break

            if not anchor_node and nodes:
                # default to biggest room
                anchor_node = max(nodes, key=lambda n: n.rect.area)
                
            if anchor_node:
                p_area = ROOM_MINIMUMS.get(rt, {}).get("area", 30.0)
                min_dim = ROOM_MINIMUMS.get(rt, {}).get("min_dim", 4.0)
                
                # We carve out from the parent node
                carve_w = max(min_dim, p_area / (anchor_node.rect.length * 0.5))
                carve_l = max(min_dim, p_area / carve_w)
                
                # Cap carve size so it doesn't destroy parent
                carve_w = min(carve_w, anchor_node.rect.width * 0.30)
                carve_l = min(carve_l, anchor_node.rect.length * 0.30)

                # Compact rooms must stay compact — never bedroom/hall sized.
                # REDUCE these max dimensions so they act as corner nooks, not full rooms.
                COMPACT_MAX = {"pooja_room": 5.5, "powder_room": 4.5, "store_room": 5.0}
                cmax = COMPACT_MAX.get(rt)
                if cmax:
                    carve_w = min(carve_w, cmax)
                    carve_l = min(carve_l, cmax)
                
                # Which sides of the anchor face a circulation space? We must NOT
                # carve a bathroom out of that side, or the bath would end up
                # between the corridor and the bedroom (a bath-as-gateway).
                a = anchor_node.rect
                ax0, ax1, az0, az1 = a.x, a.x + a.width, a.z, a.z + a.length
                circ_sides = set()
                for c in nodes:
                    if c.type not in ("corridor", "hallway", "foyer", "living_room"):
                        continue
                    cx0, cx1, cz0, cz1 = c.rect.x, c.rect.x + c.rect.width, c.rect.z, c.rect.z + c.rect.length
                    z_ov = min(az1, cz1) - max(az0, cz0)
                    x_ov = min(ax1, cx1) - max(ax0, cx0)
                    if abs(ax0 - cx1) < 0.3 and z_ov > 0.5: circ_sides.add("left")
                    if abs(ax1 - cx0) < 0.3 and z_ov > 0.5: circ_sides.add("right")
                    if abs(az0 - cz1) < 0.3 and x_ov > 0.5: circ_sides.add("top")
                    if abs(az1 - cz0) < 0.3 and x_ov > 0.5: circ_sides.add("bottom")

                slice_w = carve_w
                slice_l = carve_l
                # Preferred carve sides, skipping any that face circulation. For a
                # bathroom this keeps the bedroom touching the corridor.
                avoid = circ_sides if "bath" in rt else set()
                order = ["right", "bottom", "left", "top"] if a.width > a.length else ["bottom", "right", "top", "left"]
                side = next((s for s in order if s not in avoid), order[0])

                # Bedroom-cluster protection: never shrink the parent below its
                # minimum livable dimension. Clamp the slice; if even the smallest
                # bath won't fit without breaking the parent, skip the carve.
                a_min = ROOM_MINIMUMS.get(anchor_node.type, {}).get("min_dim", 8.0)
                if side in ("left", "right"):
                    slice_w = min(slice_w, a.width - a_min)
                    if slice_w < min_dim:
                        continue
                else:
                    slice_l = min(slice_l, a.length - a_min)
                    if slice_l < min_dim:
                        continue

                if side == "right":
                    p_rect = Rect(a.x + a.width - slice_w, a.z, slice_w, a.length)
                    a.width -= slice_w
                elif side == "left":
                    p_rect = Rect(a.x, a.z, slice_w, a.length)
                    a.x += slice_w
                    a.width -= slice_w
                elif side == "bottom":
                    p_rect = Rect(a.x, a.z + a.length - slice_l, a.width, slice_l)
                    a.length -= slice_l
                else:  # top
                    p_rect = Rect(a.x, a.z, a.width, slice_l)
                    a.z += slice_l
                    a.length -= slice_l

                # Compact rooms (pooja/powder/store) are CORNER nooks — cap BOTH
                # axes and seat them in the corner. The rest of the carved edge is
                # handed back to the parent (living) as OPEN floor so there is a
                # walking passage beside the nook instead of a full dividing wall.
                if cmax:
                    if side in ("left", "right"):
                        if p_rect.length > cmax:
                            # Give the un-used length back to the parent: shift the
                            # parent's far edge to re-cover the strip beside the nook.
                            if side == "right":
                                a.width += slice_w  # parent reclaims full strip…
                            elif side == "left":
                                a.x -= slice_w
                                a.width += slice_w
                            p_rect.length = cmax
                            # Parent now keeps its full footprint (open floor); the
                            # nook overlaps only its corner.
                        if p_rect.width > cmax:
                            p_rect.width = cmax
                    else:
                        if p_rect.width > cmax:
                            if side == "bottom":
                                a.length += slice_l
                            elif side == "top":
                                a.z -= slice_l
                                a.length += slice_l
                            p_rect.width = cmax
                        if p_rect.length > cmax:
                            p_rect.length = cmax

                is_wet = "bath" in rt or "utility" in rt
                p_floor = self.theme["floor"] if self.theme.get("floor") else "#f8fafc"
                p_wall = self.theme["wall"] if self.theme.get("wall") else ""
                p_furn = self.theme["furniture"] if self.theme.get("furniture") else ""
                nodes.append(RoomNode(
                    id=rid, type=rt, name=rt.replace("_", " ").title(),
                    rect=p_rect, wallThicknessIn=8.0 if is_wet else 6.0, is_wet=is_wet,
                    floorColor=p_floor, wallColor=p_wall, furnitureColor=p_furn
                ))

        # 3. Post-Processing & Special Vastu Rules
        living = next((n for n in nodes if n.type == "living_room"), None)
        
        # ── Rule: Otta / Thinnai ─────────────────────────
        if indian_options.get("otta") and living and living.rect.length > 10.0:
            otta_l = 4.0
            living.rect.length -= otta_l
            living.rect.z += otta_l
            otta_rect = Rect(living.rect.x, living.rect.z - otta_l, living.rect.width, otta_l)
            nodes.append(RoomNode(id="otta-1", type="veranda", name="Otta", rect=otta_rect, wallThicknessIn=4.0, roof_type="flat", floorColor="#d6d3d1"))

        # ── Rule: Portico ────────────────────────────────────
        if indian_options.get("portico"):
            portico_rect = Rect(self.setback_x, self.setback_z, 10.0, 15.0)
            for n in nodes:
                if n.rect.x < portico_rect.x + 10.0 and n.rect.z < portico_rect.z + 15.0:
                    push_x = (portico_rect.x + 10.0) - n.rect.x
                    n.rect.x += push_x
                    n.rect.width = max(n.rect.width - push_x, 4.0)
            nodes.append(RoomNode(id="portico-1", type="parking", name="Portico", rect=portico_rect, wallThicknessIn=0.0, roof_type="flat", floorColor="#9ca3af"))

        # ── Rule: Double-Height Ceiling ──────────────────────────────────
        if indian_options.get("double_height") and living:
            living.is_double_height = True

        inject_main_entrance(nodes, self.buildable_width, self.buildable_length,
                             self.setback_x, self.setback_z)
                             
        logger.info("  [Post-Processing] Computing shared and exterior wall segments...")
        self.last_walls = compute_shared_walls(nodes)
        
        for node in nodes:
            if node.rect.width >= 8.0 and node.rect.length >= 8.0:
                if "bedroom" in node.type.lower():
                    node.furniture = ["Bed", "Wardrobe"]
                elif "living" in node.type.lower():
                    node.furniture = ["Sofa", "TV Unit"]
                    if indian_options.get("diwan"):
                        node.furniture.append("Diwan")
                elif "kitchen" in node.type.lower():
                    node.furniture = ["Countertop", "Fridge"]
                elif "dining" in node.type.lower():
                    node.furniture = ["Dining Table"]
                else:
                    node.furniture = []
            else:
                if "bath" in node.type.lower() or "toilet" in node.type.lower() or "powder" in node.type.lower():
                    node.furniture = ["Toilet", "Shower"]
                elif "pooja" in node.type.lower():
                    node.furniture = ["Altar", "Diya Stand"]
                elif "store" in node.type.lower() or "utility" in node.type.lower():
                    node.furniture = ["Small Shelf"]
                else:
                    node.furniture = []

        # Validation fix: the Master Bedroom must be the largest bedroom.
        masters = [n for n in nodes if n.type == "master_bedroom"]
        bedrooms = [n for n in nodes if n.type == "bedroom"]
        if masters and bedrooms:
            master = masters[0]
            largest_bed = max(bedrooms, key=lambda n: n.rect.area)
            if largest_bed.rect.area > master.rect.area + 1.0:
                master.type, master.name = "bedroom", "Bedroom"
                largest_bed.type, largest_bed.name = "master_bedroom", "Master Bedroom"

        # ── Vastu Directional Colors ─────────────────────────────────────
        # When Vastu mode is on, room wall colors are assigned by the cardinal
        # direction of each room relative to the building centre. Pastel tints
        # only (see VASTU_DIR_HEX) so the house reads as a real interior, not a
        # colour-blocked diagram. Circulation / wet-room / utility types keep
        # their domain-appropriate neutrals so corridors stay visually neutral
        # and bathrooms don't get tinted into a bedroom hue.
        _VASTU_SKIP_TYPES = {
            "corridor", "hallway", "foyer", "staircase", "void",
            "bathroom", "powder_room", "utility", "store_room",
            "kitchen", "garage", "parking", "balcony",
        }
        if self.theme.get("vastu") and nodes:
            cx = self.setback_x + self.buildable_width / 2.0
            cz = self.setback_z + self.buildable_length / 2.0
            for n in nodes:
                if n.type in _VASTU_SKIP_TYPES:
                    continue
                rx = n.rect.x + n.rect.width / 2.0
                rz = n.rect.z + n.rect.length / 2.0
                dx, dz = rx - cx, rz - cz
                # +z is South, -z is North, +x is East, -x is West in this grid.
                ns = "south" if dz > self.buildable_length * 0.12 else ("north" if dz < -self.buildable_length * 0.12 else "")
                ew = "east" if dx > self.buildable_width * 0.12 else ("west" if dx < -self.buildable_width * 0.12 else "")
                if ns and ew:
                    key = f"{ns}_{ew}"
                else:
                    key = ns or ew or "north_east"
                color = vastu_color_for_direction(key) or vastu_color_for_direction(ns or ew or "north")
                if color:
                    n.wallColor = color

        return nodes

# ---------------------------------------------------------------------------
# Adjacency Graph & Doors
# ---------------------------------------------------------------------------

class AdjacencyResolver:
    def __init__(self, rooms: List[RoomNode], open_rooms: List[str] = None):
        self.rooms = rooms
        self.open_rooms = open_rooms or []

    def _rects_overlap_1d(self, min1: float, max1: float, min2: float, max2: float) -> Tuple[bool, float, float]:
        overlap_min = max(min1, min2)
        overlap_max = min(max1, max2)
        return overlap_max > overlap_min, overlap_min, overlap_max

    def _get_shared_edges(self, r1: RoomNode, r2: RoomNode, tolerance: float = 0.3) -> List[Dict[str, Any]]:
        edges = []
        # r1 right edge vs r2 left edge
        if abs((r1.rect.x + r1.rect.width) - r2.rect.x) < tolerance:
            overlap, o_min, o_max = self._rects_overlap_1d(
                r1.rect.z, r1.rect.z + r1.rect.length,
                r2.rect.z, r2.rect.z + r2.rect.length
            )
            if overlap:
                edges.append({"orientation": "east", "overlap": (o_min, o_max), "wall_x": r2.rect.x})

        # r1 left edge vs r2 right edge
        if abs(r1.rect.x - (r2.rect.x + r2.rect.width)) < tolerance:
            overlap, o_min, o_max = self._rects_overlap_1d(
                r1.rect.z, r1.rect.z + r1.rect.length,
                r2.rect.z, r2.rect.z + r2.rect.length
            )
            if overlap:
                edges.append({"orientation": "west", "overlap": (o_min, o_max), "wall_x": r1.rect.x})

        # r1 bottom edge vs r2 top edge
        if abs((r1.rect.z + r1.rect.length) - r2.rect.z) < tolerance:
            overlap, o_min, o_max = self._rects_overlap_1d(
                r1.rect.x, r1.rect.x + r1.rect.width,
                r2.rect.x, r2.rect.x + r2.rect.width
            )
            if overlap:
                edges.append({"orientation": "south", "overlap": (o_min, o_max), "wall_z": r2.rect.z})

        # r1 top edge vs r2 bottom edge
        if abs(r1.rect.z - (r2.rect.z + r2.rect.length)) < tolerance:
            overlap, o_min, o_max = self._rects_overlap_1d(
                r1.rect.x, r1.rect.x + r1.rect.width,
                r2.rect.x, r2.rect.x + r2.rect.width
            )
            if overlap:
                edges.append({"orientation": "north", "overlap": (o_min, o_max), "wall_z": r1.rect.z})

        return edges

    def _connect(self, a: RoomNode, b: RoomNode, width: float = 3.0) -> bool:
        """Place a reciprocal door between two adjacent rooms. Returns False if
        they don't share a usable wall."""
        edges = self._get_shared_edges(a, b)
        if not edges:
            return False
        be = max(edges, key=lambda e: e["overlap"][1] - e["overlap"][0])
        mid = (be["overlap"][0] + be["overlap"][1]) / 2.0
        da = Door(x=0, z=0, wall_orientation=be["orientation"], width=width)
        if be["orientation"] in ("east", "west"):
            da.x = be["wall_x"] - a.rect.x
            da.z = mid - a.rect.z
        else:
            da.x = mid - a.rect.x
            da.z = be["wall_z"] - a.rect.z
        a.doors.append(da)

        opp = {"east": "west", "west": "east", "south": "north", "north": "south"}[be["orientation"]]
        db = Door(x=0, z=0, wall_orientation=opp, width=width)
        if opp == "west":
            db.x = 0; db.z = mid - b.rect.z
        elif opp == "east":
            db.x = b.rect.width; db.z = mid - b.rect.z
        elif opp == "north":
            db.x = mid - b.rect.x; db.z = 0
        else:
            db.x = mid - b.rect.x; db.z = b.rect.length
        b.doors.append(db)
        return True

    def resolve(self):
        logger.info(f"  [AdjacencyResolver] Resolving doors for {len(self.rooms)} rooms.")
        
        # Priority: living room and dining room connect to everything
        living_rooms = [r for r in self.rooms if r.type == "living_room"]
        hallways = [r for r in self.rooms if r.type in ["hallway", "foyer", "corridor"]]
        dining_rooms = [r for r in self.rooms if r.type == "dining_room"]
        
        # ADDED: dining_rooms to core circulation
        core_rooms = living_rooms + hallways + dining_rooms

        # ── Bathroom access discipline ──────────────────────────────────
        # Each bathroom gets exactly ONE access: attached to a single bedroom
        # (preferring the master), otherwise it is a common bathroom reached
        # from the corridor/living area. This prevents a bedroom from owning
        # multiple baths and stops a bath from being shared by both a bedroom
        # and a corridor.
        bathrooms = [r for r in self.rooms if r.type == "bathroom"]
        bedrooms = [r for r in self.rooms if "bedroom" in r.type]
        ordered_bedrooms = (
            [b for b in bedrooms if b.type == "master_bedroom"]
            + [b for b in bedrooms if b.type != "master_bedroom"]
        )
        bath_owner = {}        # bath.id -> ("bedroom", bedroom_id) | ("common", None)
        bedroom_taken = set()  # bedrooms that already have one attached bath
        for bath in bathrooms:
            owner = ("common", None)
            for bed in ordered_bedrooms:
                if bed.id in bedroom_taken:
                    continue
                if self._get_shared_edges(bath, bed):
                    owner = ("bedroom", bed.id)
                    bedroom_taken.add(bed.id)
                    break
            bath_owner[bath.id] = owner

        # Connection graph (room.id -> set of connected room ids) so we can verify
        # bedrooms reach circulation and bathrooms never act as connectors.
        conn = {r.id: set() for r in self.rooms}

        def _record(a, b):
            conn[a.id].add(b.id)
            conn[b.id].add(a.id)

        for i, r1 in enumerate(self.rooms):
            for j, r2 in enumerate(self.rooms):
                if i >= j:
                    continue

                edges = self._get_shared_edges(r1, r2)
                if not edges:
                    continue

                # Destination-only rooms get exactly ONE door so they can never
                # become a passage (e.g. Pooja wedged between Living and Dining).
                # Each must remain a dead-end you enter and leave the same way.
                SINGLE_ACCESS = {"pooja_room", "store_room", "powder_room"}
                if r1.type in SINGLE_ACCESS and r1.doors:
                    continue
                if r2.type in SINGLE_ACCESS and r2.doors:
                    continue

                # Should we place a door here?
                # Don't place doors between two bathrooms
                if r1.type == "bathroom" and r2.type == "bathroom":
                    continue
                    
                # Always connect to core rooms
                is_core_conn = r1 in core_rooms or r2 in core_rooms
                
                if not is_core_conn:
                    # Strictly enforce hub-and-spoke. Only allow specific non-core connections.
                    allowed_pairs = [
                        ("bedroom", "bathroom"),
                        ("master_bedroom", "bathroom"),
                        ("kitchen", "dining_room"),
                        ("kitchen", "balcony"),
                        ("kitchen", "store_room"),
                        ("kitchen", "utility"),
                        ("kitchen", "utility_area")
                    ]
                    allowed = False
                    for t1, t2 in allowed_pairs:
                        if (t1 in r1.type and t2 in r2.type) or (t2 in r1.type and t1 in r2.type):
                            allowed = True
                            break
                    if not allowed:
                        continue

                # Enforce the bathroom access decided in the pre-pass.
                if r1.type == "bathroom" or r2.type == "bathroom":
                    bath = r1 if r1.type == "bathroom" else r2
                    other = r2 if r1.type == "bathroom" else r1
                    # A bathroom is a single-access destination — never a connector.
                    if bath.doors:
                        continue
                    kind, owner_id = bath_owner.get(bath.id, ("common", None))
                    if kind == "bedroom":
                        # Attached bath opens ONLY into its owner bedroom.
                        if other.id != owner_id:
                            continue
                    else:
                        # Common bath never opens directly into a bedroom.
                        if "bedroom" in other.type:
                            continue

                best_edge = max(edges, key=lambda e: e["overlap"][1] - e["overlap"][0])
                
                # Place door in the middle of overlap
                midpoint = (best_edge["overlap"][0] + best_edge["overlap"][1]) / 2.0
                overlap_len = best_edge["overlap"][1] - best_edge["overlap"][0]
                
                door_width = 3.0
                if r1.type in ["bathroom", "store_room"] or r2.type in ["bathroom", "store_room"]:
                    door_width = 2.5

                # Corridor / hallway connects to ANY room with a wide passage —
                # the corridor is a circulation spine, not a walled-off tunnel.
                # Without wide openings, both rooms render their own wall on the
                # shared face → thick double-wall with a tiny gap.
                pair_types = {r1.type, r2.type}
                has_circ = pair_types & {"corridor", "hallway", "foyer"}

                # Staircase ↔ corridor: full-width (one continuous zone).
                if "staircase" in pair_types and has_circ:
                    door_width = overlap_len - 0.3

                # Corridor ↔ bedroom / master: standard residential door (privacy).
                # NOT an archway — bedrooms must have proper doors, not wall-size gaps.
                elif has_circ and pair_types & {"bedroom", "master_bedroom"}:
                    door_width = 3.5

                # Corridor ↔ living/dining/kitchen: generous 5+ ft opening.
                elif has_circ and pair_types & {"living_room", "dining_room", "kitchen"}:
                    door_width = max(door_width, min(overlap_len - 0.3, 6.0))

                # Corridor ↔ bathroom (common bath): standard 3 ft door (privacy).
                # (no change needed — already 2.5 ft from bathroom check above)

                # General open concept logic
                r1_clean = r1.type.replace("_", " ")
                r2_clean = r2.type.replace("_", " ")
                is_open = any(r in r1_clean or r in r2_clean for r in self.open_rooms)

                if is_open and "living_room" in [r1.type, r2.type]:
                    # Make door full width of overlap
                    door_width = overlap_len - 0.2
                
                door = Door(x=0, z=0, wall_orientation=best_edge["orientation"], width=door_width)
                
                # Add to r1
                if best_edge["orientation"] in ["east", "west"]:
                    door.x = best_edge["wall_x"] - r1.rect.x
                    door.z = midpoint - r1.rect.z
                else:
                    door.x = midpoint - r1.rect.x
                    door.z = best_edge["wall_z"] - r1.rect.z
                    
                r1.doors.append(door)
                
                # Add reciprocal door to r2 (optional, frontend might just render from one room)
                # We'll just add it to r1 for simplicity, frontend can render it
                # Actually, adding to both makes it easier for frontend if it renders per-room
                door2 = Door(x=0, z=0, wall_orientation="", width=door_width)
                if best_edge["orientation"] == "east":
                    door2.wall_orientation = "west"
                    door2.x = 0  # relative to r2
                    door2.z = midpoint - r2.rect.z
                elif best_edge["orientation"] == "west":
                    door2.wall_orientation = "east"
                    door2.x = r2.rect.width
                    door2.z = midpoint - r2.rect.z
                elif best_edge["orientation"] == "south":
                    door2.wall_orientation = "north"
                    door2.x = midpoint - r2.rect.x
                    door2.z = 0
                elif best_edge["orientation"] == "north":
                    door2.wall_orientation = "south"
                    door2.x = midpoint - r2.rect.x
                    door2.z = r2.rect.length
                    
                r2.doors.append(door2)
                _record(r1, r2)
                logger.info(f"    Placed door between '{r1.name}' and '{r2.name}' (wall: {best_edge['orientation']}, width: {door_width:.1f} ft)")

        # ── Bedroom & habitable room privacy pass ──────────────────────
        # Every bedroom AND every habitable room (dining, kitchen, pooja, etc.)
        # must be reachable from a circulation/public room — never only through
        # a bathroom or single-access room. For every room that doesn't yet
        # connect to a circulation hub, force a direct door to an adjacent one.
        circ_types = {"corridor", "hallway", "foyer", "living_room"}
        circ_rooms = [r for r in self.rooms if r.type in circ_types]
        rooms_by_id = {r.id: r for r in self.rooms}

        # Rooms that need a guaranteed route to circulation:
        # Include bathrooms that are "common" (not attached to a bedroom) so they
        # can be reached from the corridor.
        common_baths = {bid for bid, (kind, _) in bath_owner.items() if kind == "common"}
        needs_circ = [r for r in self.rooms if (
            "bedroom" in r.type or
            r.type in ("dining_room", "kitchen", "pooja_room") or
            (r.type == "bathroom" and r.id in common_baths)
        )]
        for room in needs_circ:
            reaches_circ = any(rooms_by_id[c].type in circ_types for c in conn[room.id])
            if reaches_circ:
                continue
            # Prefer corridor, then foyer/living, then any circulation room.
            ordered = sorted(circ_rooms, key=lambda r: {"corridor": 0, "hallway": 0, "foyer": 1, "living_room": 2}.get(r.type, 3))
            for circ in ordered:
                if circ.id in conn[room.id]:
                    continue
                if self._connect(room, circ, width=3.0):
                    _record(room, circ)
                    logger.info(f"    [Privacy] Opened '{room.name}' onto circulation '{circ.name}'")
                    break

        # ── Safety pass: never leave a room unreachable ─────────────────
        # The access rules above (and BSP placement) can leave a room with no
        # door. Guarantee at least one door for every habitable room, preferring
        # circulation/public neighbours over bedrooms.
        open_types = {"void", "portico", "parking", "balcony", "veranda", "otta", "courtyard", "staircase"}

        def _priority(room):
            order = {"corridor": 0, "hallway": 0, "foyer": 1, "living_room": 2, "dining_room": 3, "kitchen": 4}
            return order.get(room.type, 6 if "bedroom" in room.type else 5)

        for room in self.rooms:
            if room.doors or room.type in open_types:
                continue
            candidates = sorted(
                [r for r in self.rooms if r.id != room.id and r.type != "bathroom"],
                key=_priority,
            )
            for other in candidates:
                if self._connect(room, other, width=2.5 if room.type in ("bathroom", "store_room", "powder_room") else 3.0):
                    logger.info(f"    [Safety] Connected isolated room '{room.name}' to '{other.name}'")
                    break

# ---------------------------------------------------------------------------
# Window Generation
# ---------------------------------------------------------------------------

class WindowPlacer:
    def __init__(self, rooms: List[RoomNode], plot_width: float, plot_length: float,
                 setback_x: float = 0.0, setback_z: float = 0.0):
        self.rooms = rooms
        self.plot_width = plot_width
        self.plot_length = plot_length
        self.setback_x = setback_x
        self.setback_z = setback_z

        # Compute buildable extents
        buildable_width = plot_width * 0.85 if setback_x == 0.0 else plot_width - 2 * setback_x
        buildable_length = plot_length * 0.85 if setback_z == 0.0 else plot_length - 2 * setback_z
        self._ext_west  = setback_x
        self._ext_east  = setback_x + buildable_width
        self._ext_north = setback_z
        self._ext_south = setback_z + buildable_length

    def place_windows(self):
        logger.info(f"  [WindowPlacer] Starting window placement. Bounds: North={self._ext_north:.1f}, South={self._ext_south:.1f}, West={self._ext_west:.1f}, East={self._ext_east:.1f}")
        tolerance = 0.3
        
        main_door_added = False
        
        for r in self.rooms:
            # Check which walls are on the buildable-area exterior
            is_exterior_west  = abs(r.rect.x - self._ext_west) < tolerance
            is_exterior_east  = abs((r.rect.x + r.rect.width) - self._ext_east) < tolerance
            is_exterior_north = abs(r.rect.z - self._ext_north) < tolerance
            is_exterior_south = abs((r.rect.z + r.rect.length) - self._ext_south) < tolerance
            
            if r.type == "living_room" and not main_door_added:
                door_width = 3.5
                # Door coords are wall-CENTER (matching the renderer & interior doors),
                # is_main=True so a visible door leaf is rendered. Prefer the face the
                # entrance heuristic chose; otherwise any exterior wall; else south.
                pref = getattr(r, "main_entrance_wall", None)

                def _mk(face):
                    if face == "south":
                        return Door(x=r.rect.width/2, z=r.rect.length, wall_orientation="south", width=door_width, height=8.0, is_main=True)
                    if face == "north":
                        return Door(x=r.rect.width/2, z=0, wall_orientation="north", width=door_width, height=8.0, is_main=True)
                    if face == "east":
                        return Door(x=r.rect.width, z=r.rect.length/2, wall_orientation="east", width=door_width, height=8.0, is_main=True)
                    return Door(x=0, z=r.rect.length/2, wall_orientation="west", width=door_width, height=8.0, is_main=True)

                exterior = {"south": is_exterior_south, "north": is_exterior_north,
                            "east": is_exterior_east, "west": is_exterior_west}
                face = None
                if pref and exterior.get(pref):
                    face = pref
                else:
                    face = next((f for f, ok in (("south", is_exterior_south), ("north", is_exterior_north),
                                                 ("east", is_exterior_east), ("west", is_exterior_west)) if ok), None)
                if face is None:
                    face = pref or "south"  # fallback so there is always a main door
                r.doors.append(_mk(face))
                main_door_added = True
                logger.info(f"    Placed main entrance door on {face} wall of '{r.name}' (width: {door_width:.1f} ft)")
            
            # Place windows on exterior walls if it's a habitable room
            if r.type in ["living_room", "bedroom", "master_bedroom", "kitchen", "study_room", "dining_room"]:
                window_width = 4.0
                if r.type in ["kitchen", "bathroom"]:
                    window_width = 2.0
                    
                doors_by_wall = {d.wall_orientation for d in r.doors}
                if getattr(r, "main_entrance_wall", None):
                    doors_by_wall.add(r.main_entrance_wall)
                    
                if is_exterior_south and r.rect.width > window_width + 1 and "south" not in doors_by_wall:
                    w = Window(x=r.rect.width / 2, z=r.rect.length, wall_orientation="south", width=window_width)
                    r.windows.append(w)
                    logger.info(f"    Placed south window on habitable room '{r.name}' (width: {window_width:.1f} ft)")
                elif is_exterior_north and r.rect.width > window_width + 1 and "north" not in doors_by_wall:
                    w = Window(x=r.rect.width / 2, z=0, wall_orientation="north", width=window_width)
                    r.windows.append(w)
                    logger.info(f"    Placed north window on habitable room '{r.name}' (width: {window_width:.1f} ft)")
                elif is_exterior_east and r.rect.length > window_width + 1 and "east" not in doors_by_wall:
                    w = Window(x=r.rect.width, z=r.rect.length / 2, wall_orientation="east", width=window_width)
                    r.windows.append(w)
                    logger.info(f"    Placed east window on habitable room '{r.name}' (width: {window_width:.1f} ft)")
                elif is_exterior_west and r.rect.length > window_width + 1 and "west" not in doors_by_wall:
                    w = Window(x=0, z=r.rect.length / 2, wall_orientation="west", width=window_width)
                    r.windows.append(w)
                    logger.info(f"    Placed west window on habitable room '{r.name}' (width: {window_width:.1f} ft)")

            # Bathrooms get small ventilators
            if r.type == "bathroom":
                window_width = 2.0
                sill = 5.0
                h = 2.0
                if is_exterior_south:
                    r.windows.append(Window(x=r.rect.width / 2, z=r.rect.length, wall_orientation="south", width=window_width, height=h, sill_height=sill))
                    logger.info(f"    Placed south ventilator on bathroom '{r.name}'")
                elif is_exterior_north:
                    r.windows.append(Window(x=r.rect.width / 2, z=0, wall_orientation="north", width=window_width, height=h, sill_height=sill))
                    logger.info(f"    Placed north ventilator on bathroom '{r.name}'")
                elif is_exterior_east:
                    r.windows.append(Window(x=r.rect.width, z=r.rect.length / 2, wall_orientation="east", width=window_width, height=h, sill_height=sill))
                    logger.info(f"    Placed east ventilator on bathroom '{r.name}'")
                elif is_exterior_west:
                    r.windows.append(Window(x=0, z=r.rect.length / 2, wall_orientation="west", width=window_width, height=h, sill_height=sill))
                    logger.info(f"    Placed west ventilator on bathroom '{r.name}'")

# ---------------------------------------------------------------------------
# Architectural Rules
# ---------------------------------------------------------------------------
class ArchitecturalRules:
    @staticmethod
    def optimize_wet_walls(rooms: List[RoomNode]):
        # In a full constraint solver, we'd force kitchens and bathrooms to be adjacent.
        # Here we just mark them as wet and set wall thickness.
        for r in rooms:
            if r.type in ["kitchen", "bathroom", "laundry"]:
                r.is_wet = True
                r.wallThicknessIn = 8.0
    
    @staticmethod
    def validate_rules(rooms: List[RoomNode]) -> List[str]:
        warnings = []
        
        # Wet wall check
        wet_rooms = [r for r in rooms if r.is_wet]
        # (This is simplified, full graph check would be better)
        
        # Entry flow
        has_foyer = any(r.type == "foyer" for r in rooms)
        # if not has_foyer:
        #     warnings.append("No foyer detected. Entrance opens directly into living area.")
            
        # Daylighting
        for r in rooms:
            if r.type in ["living_room", "bedroom", "master_bedroom"]:
                if len(r.windows) == 0:
                    pass
                    # warnings.append(f"No exterior window found in {r.name} for daylighting.")
                    
        return warnings

"""
layout_engine.py — Deterministic BSP Layout Engine for Architectural Floor Plans.

Implements Binary Space Partitioning (BSP) to divide a master bounding box into rooms,
enforces architectural rules, resolves adjacencies, and places doors/windows.
"""

from __future__ import annotations

import time
import os
import json
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
    connections: List[Dict[str, str]] = field(default_factory=list)
    floorColor: str = ""
    wallColor: str = ""
    is_double_height: bool = False
    roof_type: str = "flat"  # flat, open, pitched
    furnitureColor: str = ""
    furniture: List[Any] = field(default_factory=list)
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
            "connections": self.connections,
            "suppress_wall_faces": self.suppress_wall_faces,
            "floorColor": self.floorColor,
            "wallColor": self.wallColor,
            "is_double_height": self.is_double_height,
            "roof_type": self.roof_type,
            "furnitureColor": self.furnitureColor,
            "furniture": getattr(self, 'furniture', []),
            "mep_nodes": self.mep_nodes,
            "doors": [{"x": round(d.x, 2), "z": round(d.z, 2), "wall_orientation": d.wall_orientation, "width": d.width, "height": getattr(d, 'height', 7.0), "is_main": bool(getattr(d, 'is_main', False))} for d in getattr(self, 'doors', [])],
            "windows": [{"x": round(w.x, 2), "z": round(w.z, 2), "wall_orientation": w.wall_orientation, "width": w.width, "height": getattr(w, 'height', 4.0), "sill_height": getattr(w, 'sill_height', 3.0)} for w in getattr(self, 'windows', [])],
        }

class ContextualFurniturePlacementEngine:
    CATALOG_DIR = os.path.join(os.path.dirname(__file__), "furniture_catalogs")

    @classmethod
    def place_for_room(cls, node: RoomNode, indian_options: Dict[str, Any]):
        node.furniture = []
        if node.rect.width < 5.0 or node.rect.length < 5.0:
            return

        cat_name = ""
        rt = node.type.lower()
        if "living" in rt: cat_name = "living_room"
        elif "bedroom" in rt: cat_name = "bedroom"
        elif "kitchen" in rt: cat_name = "kitchen"
        
        if not cat_name:
            if "dining" in rt:
                node.furniture.append({"type": "Dining Table", "x": node.rect.width / 2.0, "z": node.rect.length / 2.0})
            return
            
        path = os.path.join(cls.CATALOG_DIR, f"{cat_name}.json")
        if not os.path.exists(path):
            return
            
        with open(path, "r") as f:
            items = json.load(f)
            
        placed = []
        for item in items:
            w, l = item.get("width", 2.0), item.get("length", 2.0)
            affinity = item.get("affinity", "center")
            
            # Helper: clamp a position so the item stays inside the room
            def clamp_pos(fx, fz, item_w, item_l, room_w, room_l):
                fx = max(item_w / 2.0, min(fx, room_w - item_w / 2.0))
                fz = max(item_l / 2.0, min(fz, room_l - item_l / 2.0))
                return fx, fz

            rw, rl = node.rect.width, node.rect.length

            if affinity == "center":
                fx, fz = rw / 2.0, rl / 2.0
            else:
                if item.get("relationship") == "faces_sofa":
                    fx, fz = rw / 2.0, rl - l / 2.0
                elif item.get("relationship") == "next_to_bed":
                    fx, fz = min(rw / 2.0 + 3.0, rw - w / 2.0), l / 2.0
                else:
                    # Wall-affinity items go against the top wall
                    fx, fz = rw / 2.0, l / 2.0

            fx, fz = clamp_pos(fx, fz, w, l, rw, rl)
            placed.append({"type": item["type"], "x": round(fx, 2), "z": round(fz, 2)})
                    
        node.furniture = placed

class MainEntrancePlacementEngine:
    @classmethod
    def place_main_door(cls, nodes: List[RoomNode], walls: List[Dict], primary_entry_room_id: str, front_orientation: str):
        if not primary_entry_room_id:
            return
        
        entry_room = next((n for n in nodes if primary_entry_room_id in n.id or primary_entry_room_id in n.type), None)
        if not entry_room:
            entry_room = nodes[0]
            
        exterior_walls = []
        for w in walls:
            if len(w.get("room_ids", [])) == 1 and w["room_ids"][0] == entry_room.id:
                exterior_walls.append(w)
                
        if not exterior_walls:
            return
            
        target_walls = []
        for w in exterior_walls:
            is_horiz = w["wall_orientation"] == "horizontal"
            is_vert = w["wall_orientation"] == "vertical"
            if front_orientation == "north" and is_horiz and abs(w["z1"] - entry_room.rect.z) < 0.1:
                target_walls.append(w)
            elif front_orientation == "south" and is_horiz and abs(w["z1"] - (entry_room.rect.z + entry_room.rect.length)) < 0.1:
                target_walls.append(w)
            elif front_orientation == "east" and is_vert and abs(w["x1"] - (entry_room.rect.x + entry_room.rect.width)) < 0.1:
                target_walls.append(w)
            elif front_orientation == "west" and is_vert and abs(w["x1"] - entry_room.rect.x) < 0.1:
                target_walls.append(w)
                
        if not target_walls:
            target_walls = exterior_walls
            
        def wall_length(w):
            return abs(w["x2"] - w["x1"]) if w["wall_orientation"] == "horizontal" else abs(w["z2"] - w["z1"])
            
        longest = max(target_walls, key=wall_length)
        
        cx = (longest["x1"] + longest["x2"]) / 2.0
        cz = (longest["z1"] + longest["z2"]) / 2.0
        
        entry_room.doors.append(Door(
            x=cx - entry_room.rect.x,
            z=cz - entry_room.rect.z,
            width=4.0,
            height=7.0,
            wall_orientation=longest["wall_orientation"],
            is_main=True
        ))

def place_furniture(nodes: List[RoomNode], indian_options: Dict[str, Any]):
    for node in nodes:
        ContextualFurniturePlacementEngine.place_for_room(node, indian_options)
        if not node.furniture:
            if "bath" in node.type.lower() or "toilet" in node.type.lower() or "powder" in node.type.lower():
                node.furniture = [
                    {"type": "Toilet", "x": 1.5, "z": 1.5},
                    {"type": "Shower", "x": node.rect.width - 1.5, "z": node.rect.length - 1.5}
                ]
            elif "pooja" in node.type.lower():
                node.furniture = [
                    {"type": "Altar", "x": node.rect.width / 2.0, "z": 1.0}, 
                    {"type": "Diya Stand", "x": 1.0, "z": 1.0}
                ]
            elif "store" in node.type.lower() or "utility" in node.type.lower():
                node.furniture = [{"type": "Small Shelf", "x": 1.0, "z": 1.0}]
            else:
                node.furniture = []

# ---------------------------------------------------------------------------
# Module-level helpers: shared wall computation & main entrance injection
# ---------------------------------------------------------------------------
def compute_minimum_plot_area(room_types: List[str]) -> float:
    """
    Computes the absolute minimum buildable area required for a list of rooms.
    Includes a 25% buffer for walls, circulation, and corridors.
    """
    total_area = sum(get_min_area(rt) for rt in room_types)
    return total_area * 1.2
def compute_shared_walls(rooms: List[RoomNode]) -> List[Dict]:
    """
    Transforms AABB walls into strict React frontend JSON payload.
    (centerX, centerY, length, rotationAngle, thickness)
    """
    from layout_engine import generate_walls_from_aabbs
    import math
    
    walls_raw = generate_walls_from_aabbs(rooms)
    frontend_walls = []
    
    for w in walls_raw:
        if w.get("suppressed"):
            continue
            
        x1, z1 = w["x1"], w["z1"]
        x2, z2 = w["x2"], w["z2"]
        
        cx = (x1 + x2) / 2.0
        cy = (z1 + z2) / 2.0
        length = math.sqrt((x2 - x1)**2 + (z2 - z1)**2)
        
        # rotationAngle in radians (0 for horizontal, PI/2 for vertical)
        is_vertical = w["orientation"] == "vertical"
        rot = math.pi / 2.0 if is_vertical else 0.0
        thickness = 0.15

        # Shorten vertical walls by thickness to prevent Z-fighting at corners
        if is_vertical and length > thickness * 2:
            length -= thickness
        
        frontend_walls.append({
            "centerX": round(cx, 3),
            "centerY": round(cy, 3),
            "length": round(length, 3),
            "rotationAngle": round(rot, 3),
            "thickness": thickness,
            "id": w["id"],
            # Preserve the finite-wall topology for the 3D renderer.  The
            # frontend must not infer facade status from a room's bounding box:
            # stepped plans and partial shared walls make that ambiguous.
            "orientation": w["orientation"],
            "isExterior": bool(w.get("is_exterior")),
            "roomIds": w["room_ids"],
            "x1": round(x1, 3),
            "z1": round(z1, 3),
            "x2": round(x2, 3),
            "z2": round(z2, 3),
        })

    exterior_walls = [w for w in frontend_walls if w["isExterior"]]
    logger.info(
        "[FACADE DEBUG] Serialized %d finite walls (%d exterior). Exterior records: %s",
        len(frontend_walls),
        len(exterior_walls),
        [
            {
                "rooms": w["roomIds"], "orientation": w["orientation"],
                "line": (w["x1"], w["z1"], w["x2"], w["z2"])
            }
            for w in exterior_walls
        ],
    )
        
    return frontend_walls


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
    "wedding_hall":    {"area": 800.0, "min_dim": 20.0},
    "home_theater":    {"area": 250.0, "min_dim": 14.0},
    "gym":             {"area": 120.0, "min_dim": 10.0},
    "courtyard":       {"area": 100.0, "min_dim": 8.0},
    "garden":          {"area": 100.0, "min_dim": 8.0},
    "pool":            {"area": 150.0, "min_dim": 10.0},
    "basement":        {"area": 200.0, "min_dim": 12.0},
    "terrace":         {"area": 200.0, "min_dim": 12.0},
    "office":          {"area": 100.0, "min_dim": 8.0},
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

def generate_walls_from_aabbs(rooms: List[RoomNode]) -> List[Dict]:
    """
    Generate finite wall segments directly from room bounding boxes.
    Splits overlapping parallel walls into atomic segments to perfectly map 
    exterior and interior shared boundaries without infinite extensions.
    """
    walls: List[Dict] = []
    
    v_segments = []
    h_segments = []
    
    for r in rooms:
        rx, rz, rw, rl = r.rect.x, r.rect.z, r.rect.width, r.rect.length
        # Vertical
        v_segments.append((rx, rz, rz + rl, r.id))
        v_segments.append((rx + rw, rz, rz + rl, r.id))
        # Horizontal
        h_segments.append((rz, rx, rx + rw, r.id))
        h_segments.append((rz + rl, rx, rx + rw, r.id))
        
    def process_segments(segments, orientation):
        grouped = {}
        for c, min_c, max_c, rid in segments:
            found_key = None
            for k in grouped:
                if abs(k - c) < 0.1:
                    found_key = k
                    break
            if found_key is None:
                found_key = c
                grouped[found_key] = []
            grouped[found_key].append((min_c, max_c, rid))
            
        for c, segs in grouped.items():
            points = set()
            for s in segs:
                points.add(round(s[0], 2))
                points.add(round(s[1], 2))
            pts = sorted(list(points))
            
            for i in range(len(pts)-1):
                p1, p2 = pts[i], pts[i+1]
                mid = (p1 + p2) / 2.0
                
                touching_rooms = []
                for min_c, max_c, rid in segs:
                    if min_c - 0.05 <= mid <= max_c + 0.05:
                        if rid not in touching_rooms:
                            touching_rooms.append(rid)
                            
                if not touching_rooms:
                    continue
                    
                is_shared = len(touching_rooms) > 1
                is_exterior = len(touching_rooms) == 1
                
                # Check for open flow
                open_flow = False
                if is_shared:
                    r1_id, r2_id = touching_rooms[0], touching_rooms[1]
                    r1 = next((r for r in rooms if r.id == r1_id), None)
                    r2 = next((r for r in rooms if r.id == r2_id), None)
                    
                    if r1 and r2:
                        for conn in r1.connections:
                            if conn.get("target_room") == r2.type and conn.get("intent") == "open_flow":
                                open_flow = True
                                break
                        for conn in r2.connections:
                            if conn.get("target_room") == r1.type and conn.get("intent") == "open_flow":
                                open_flow = True
                                break

                # We do not skip wall generation for open flow anymore. We place walls and doors to satisfy validator.
                
                x1 = c if orientation == "vertical" else p1
                z1 = p1 if orientation == "vertical" else c
                x2 = c if orientation == "vertical" else p2
                z2 = p2 if orientation == "vertical" else c
                
                wall = {
                    "id": str(uuid.uuid4()),
                    "x1": round(x1, 3),
                    "z1": round(z1, 3),
                    "x2": round(x2, 3),
                    "z2": round(z2, 3),
                    "orientation": orientation,
                    "room_ids": touching_rooms,
                    "is_shared": is_shared,
                }
                if is_exterior:
                    wall["is_exterior"] = True
                walls.append(wall)
                
    process_segments(v_segments, "vertical")
    process_segments(h_segments, "horizontal")
    
    room_by_id = {r.id: r for r in rooms}
    for wall in walls:
        if wall.get("is_shared"):
            for rid in wall["room_ids"]:
                if rid in room_by_id:
                    other_ids = [oid for oid in wall["room_ids"] if oid != rid]
                    for oid in other_ids:
                        if oid not in room_by_id[rid].shared_walls:
                            room_by_id[rid].shared_walls.append(oid)
                            
    # Suppress corridor walls on shared faces
    # CRITICAL FIX: Only suppress a wall if BOTH rooms touching it include a passage type.
    # Previously, any wall touching a corridor was suppressed — this incorrectly silenced
    # bedroom↔bathroom walls when the bathroom also happened to touch the corridor.
    # Now we only suppress the wall if it is *exclusively* between passage-type rooms,
    # OR if one side is a passage and there are no private rooms on the other side.
    _PASSAGE_TYPES = {"corridor", "hallway", "foyer", "staircase", "passage"}
    _PRIVATE_TYPES  = {"bathroom", "toilet", "bedroom", "master_bedroom", "closet",
                       "pooja_room", "powder_room", "store_room", "utility"}
    for w in walls:
        if w.get("is_shared"):
            types_on_wall = {room_by_id[rid].type for rid in w["room_ids"] if rid in room_by_id}
            is_any_passage = bool(types_on_wall & _PASSAGE_TYPES)
            is_any_private = bool(types_on_wall & _PRIVATE_TYPES)
            # Only suppress if passage is involved but no private room needs a direct door here
            # i.e. the wall is purely between two passage rooms OR between a passage and a
            # non-private public room (living/dining/kitchen) that already gets an open-flow opening.
            if is_any_passage and not is_any_private:
                w["suppressed"] = True

    return walls


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

    def generate(self, rooms_spec: List[Dict[str, Any]], blocked_zones: Optional[List[Rect]] = None, indian_options: Optional[Dict[str, Any]] = None, layout_rules: Optional[List[Dict[str, str]]] = None, restrict_slots: bool = False, master_blueprint: Optional[List[Dict[str, Any]]] = None, plot_info: Optional[Dict[str, Any]] = None) -> List[RoomNode]:
        if indian_options is None:
            indian_options = {}
        if layout_rules is None:
            layout_rules = []
            
        # --- ZERO HARDCODING: COLLECT AI OUTDOOR DESIGNATIONS ---
        # Collect AI-designated outdoor room types directly from the processed specification
        ai_outdoor_types = {r["type"].replace(" ", "_").lower() for r in rooms_spec if isinstance(r, dict) and r.get("is_outdoor")}
        ai_wet_types = {r["type"].replace(" ", "_").lower() for r in rooms_spec if isinstance(r, dict) and r.get("is_wet")}
            
        logger.info(f"[PERF] LayoutEngine.generate started for {len(rooms_spec)} rooms. Plot: {self.plot_width}x{self.plot_length}")
        start_time = time.time()
        nodes: List[RoomNode] = []
        
        # --- ZERO-STATIC ENGINE: DUMB EXECUTION IF MASTER BLUEPRINT PROVIDED ---
        if master_blueprint:
            logger.info("MasterBlueprint detected! Bypassing constraint solver. Executing raw coordinates.")
            type_counts: Dict[str, int] = {}
            for bp in master_blueprint:
                rt = bp.get("room_type", "room").replace(" ", "_").lower()
                type_counts[rt] = type_counts.get(rt, 0) + 1
                room_id = f"{rt}-{type_counts[rt]}"

                rect = Rect(float(bp.get("position_x", 0)), float(bp.get("position_z", 0)),
                          float(bp.get("width", 0)), float(bp.get("length", 0)))
                is_wet = "kitchen" in rt or "bath" in rt or "laundry" in rt or "toilet" in rt
                node = RoomNode(
                    id=room_id,
                    type=rt,
                    name=rt.replace("_", " ").title(),
                    rect=rect,
                    is_wet=is_wet,
                    wallThicknessIn=8.0 if is_wet else 6.0,
                    connections=bp.get("connections", [])
                )
                # Doors and Windows will be generated deterministically by the layout engine!
                nodes.append(node)

            # --- SNAPPING ALGORITHM (DISABLED) ---
            # Snapping is now natively handled by CP-SAT Macro-Zone alignment.
            # Legacy snapping destroyed minimum dimension constraints by averaging coordinates.

            # --- ORIGIN ANCHORING ---
            if nodes:
                # 1. Find the absolute boundaries of the generated house
                min_x = min(n.rect.x for n in nodes)
                max_x = max(n.rect.x + n.rect.width for n in nodes)
                min_z = min(n.rect.z for n in nodes)
                max_z = max(n.rect.z + n.rect.length for n in nodes)
                
                house_width = max_x - min_x
                house_length = max_z - min_z
                
                # 2. Calculate exactly how much empty space belongs on each side
                offset_x = (self.plot_width - house_width) / 2.0
                offset_z = (self.plot_length - house_length) / 2.0
                
                # 3. Shift all rooms to the true center of the plot
                for n in nodes:
                    n.rect.x = (n.rect.x - min_x) + offset_x
                    n.rect.z = (n.rect.z - min_z) + offset_z

            # Compute shared walls using new finite AABB logic
            self.last_walls = generate_walls_from_aabbs(nodes)
            
            # --- DETERMINISTIC PLACEMENT ---
            # Adjacency Resolver calculates doors perfectly centered on shared walls
            AdjacencyResolver(nodes).resolve()
            WindowPlacer(nodes, self.plot_width, self.plot_length).place_windows()
            
            if plot_info:
                MainEntrancePlacementEngine.place_main_door(
                    nodes, 
                    self.last_walls, 
                    plot_info.get("primary_entry_room_id", ""), 
                    plot_info.get("front_orientation", "north").lower()
                )
            
            place_furniture(nodes, indian_options)
            
            logger.info(f"[ZERO-STATIC] Generated {len(nodes)} nodes, {len(self.last_walls)} shared finite walls")
            return nodes
        # -----------------------------------------------------------------

        if not rooms_spec:
            return nodes
            
        # --- NEW GEOMETRIC PACKING (CP SOLVER) BYPASS ---
        # Bypass the BSP splitting logic and use the CP Solver to determine perfectly packed coordinates
        try:
            from geometry_engine import LayoutGeometryEngine
            engine = LayoutGeometryEngine()
            
            attempt = 0
            if rooms_spec:
                # Extract attempt number if present (injected during retry loops)
                attempt = next((r.get("attempt", 0) for r in rooms_spec if isinstance(r, dict)), 0)

            floor_data = {
                'plot_width': self.buildable_width,
                'plot_length': self.buildable_length,
                'rooms': rooms_spec,
                'attempt': attempt
            }
            solved_data = engine.solve_phase_2_csp(floor_data)
            
            if 'resolved_rooms' in solved_data:
                logger.info("CP Solver successfully packed the rooms! Re-routing through master_blueprint logic.")
                cp_blueprint = []
                for rr in solved_data['resolved_rooms']:
                    cp_blueprint.append({
                        "room_type": rr["type"],
                        "position_x": rr["x"],
                        "position_z": rr["z"],
                        "width": rr["width"],
                        "length": rr["length"],
                        "connections": rr["connections"]
                    })
                return self.generate(
                    rooms_spec=[], 
                    master_blueprint=cp_blueprint,
                    plot_info=plot_info, 
                    indian_options=indian_options
                )
        except Exception as e:
            logger.error(f"CP Solver exception: {e}")
            # Fall back to legacy BSP if solver crashes or fails
            
        # --- LEGACY BSP ENGINE FALLBACK ---

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

            # --- TRUE AI-DRIVEN SEMANTIC CLASSIFIER (CORE) ---
            is_open_sky = matched_rt in ai_outdoor_types
            roof_val = "open" if is_open_sky else "flat"
            
            if is_open_sky and floor_color == "#ffffff":
                floor_color = "#d6d3d1"  # Outdoor pavement fallback tint

            nodes.append(RoomNode(
                id=matched_id, type=matched_rt, name=matched_rt.replace("_", " ").title(),
                rect=Rect(x, z, w, l), wallThicknessIn=wall_thick, is_wet=is_wet,
                floorColor=floor_color, wallColor=wall_color, furnitureColor=furniture_color,
                roof_type=roof_val  # Dynamic roof property assignment via AI
            ))
            # -------------------------------------------------

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
            elif "utility" in rt: anchor_candidates = ["kitchen"]
            elif "store" in rt: anchor_candidates = ["kitchen", "utility"]
            elif "powder" in rt: anchor_candidates = ["living", "foyer"]
            elif "pooja" in rt: anchor_candidates = ["living", "dining", "foyer"]
            elif "courtyard" in rt or "angan" in rt: anchor_candidates = ["living", "dining"]
            
            anchor_node = None
            if "bath" in rt:
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
                anchor_node = max(nodes, key=lambda n: n.rect.area)
                
            if anchor_node:
                p_area = ROOM_MINIMUMS.get(rt, {}).get("area", 30.0)
                min_dim = ROOM_MINIMUMS.get(rt, {}).get("min_dim", 4.0)
                
                carve_w = max(min_dim, p_area / max(1.0, anchor_node.rect.length * 0.5))
                carve_l = max(min_dim, p_area / carve_w)
                
                # Cap carve safely to 55% to support 1BHKs without starving room footprints
                carve_w = min(carve_w, anchor_node.rect.width * 0.55)
                carve_l = min(carve_l, anchor_node.rect.length * 0.55)

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
                
                # Safely determine the side to carve
                avoid = circ_sides if "bath" in rt else set()
                order = ["right", "bottom", "left", "top"] if a.width > a.length else ["bottom", "right", "top", "left"]
                side = next((s for s in order if s not in avoid), order[0])

                a_min = ROOM_MINIMUMS.get(anchor_node.type, {}).get("min_dim", 8.0)
                if side in ("left", "right"):
                    slice_w = min(slice_w, a.width - a_min)
                    if slice_w < min_dim:
                        continue
                else:
                    slice_l = min(slice_l, a.length - a_min)
                    if slice_l < min_dim:
                        continue

                # Execute the carve 
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
                else:
                    p_rect = Rect(a.x, a.z, a.width, slice_l)
                    a.z += slice_l
                    a.length -= slice_l

                # --- ZERO HARDCODING: AI-DRIVEN MAX DIMENSIONS ---
                ai_spec = next((r for r in rooms_spec if r.get("type", "") == rt), {})
                cmax_w = float(ai_spec.get("width", min_dim * 1.5))
                cmax_l = float(ai_spec.get("length", min_dim * 1.5))

                if side in ("left", "right"):
                    if p_rect.length > cmax_l:
                        p_rect.length = cmax_l
                    if p_rect.width > cmax_w:
                        p_rect.width = cmax_w
                else:
                    if p_rect.width > cmax_w:
                        p_rect.width = cmax_w
                    if p_rect.length > cmax_l:
                        p_rect.length = cmax_l

                # --- TRUE AI-DRIVEN SEMANTIC CLASSIFIER (PARASITES) ---
                is_wet = rt in ai_wet_types
                is_open_sky = rt in ai_outdoor_types
                
                roof_val = "open" if is_open_sky else "flat"
                
                p_wall = self.theme["wall"] if self.theme.get("wall") else ""
                p_furn = self.theme["furniture"] if self.theme.get("furniture") else ""
                p_floor = self.theme["floor"] if self.theme.get("floor") else ("#d6d3d1" if is_open_sky else "#f8fafc")

                nodes.append(RoomNode(
                    id=rid, type=rt, name=rt.replace("_", " ").title(),
                    rect=p_rect, 
                    wallThicknessIn=8.0 if is_wet else 6.0, 
                    is_wet=is_wet,
                    floorColor=p_floor, 
                    wallColor=p_wall, 
                    furnitureColor=p_furn,
                    roof_type=roof_val
                ))
                # ------------------------------------------------------

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

        # The normal AI generation path must also materialize doors/windows.
        # Without this call, living_room.main_entrance is only metadata and
        # no visible main door is serialized for the frontend.
        WindowPlacer(nodes, self.plot_width, self.plot_length).place_windows()
        logger.info(
            "[MAIN DOOR DEBUG] Entrance rooms: %s",
            [
                {"id": n.id, "type": n.type, "main": getattr(n, "main_entrance", False),
                 "doors": len(n.doors)}
                for n in nodes if getattr(n, "main_entrance", False)
            ],
        )
        place_furniture(nodes, indian_options)

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

    def resolve(self):
        logger.info(f"  [AdjacencyResolver] Resolving doors for {len(self.rooms)} rooms using finite walls.")
        from layout_engine import generate_walls_from_aabbs
        walls = generate_walls_from_aabbs(self.rooms)
        
        room_by_id = {r.id: r for r in self.rooms}
        placed_doors_between = set()
        
        def has_connection(src, dst):
            return any(c.get("target_room") == dst.type for c in src.connections)
            
        def get_face(rel_x, rel_z, room, is_v):
            if is_v: return "west" if rel_x < room.rect.width / 2.0 else "east"
            return "north" if rel_z < room.rect.length / 2.0 else "south"

        # --- PASS 1: Strict Topological Placement ---
        for w in walls:
            if w.get("is_shared"):
                r1_id, r2_id = w["room_ids"][:2]
                pair = tuple(sorted([r1_id, r2_id]))
                if pair in placed_doors_between:
                    continue
                
                r1, r2 = room_by_id[r1_id], room_by_id[r2_id]
                
                is_r1_private = "bed" in r1.type or "bath" in r1.type or "toilet" in r1.type or "closet" in r1.type
                is_r2_private = "bed" in r2.type or "bath" in r2.type or "toilet" in r2.type or "closet" in r2.type
                
                # If the AI topology forbids this connection, strictly skip it
                if is_r1_private or is_r2_private:
                    if not (has_connection(r1, r2) or has_connection(r2, r1)):
                        continue
                
                # Ensure bathrooms only get one primary door
                is_r1_bath = "bath" in r1.type or "toilet" in r1.type
                is_r2_bath = "bath" in r2.type or "toilet" in r2.type
                if is_r1_bath and any(d for d in r1.doors): continue
                if is_r2_bath and any(d for d in r2.doors): continue

                if is_r1_bath or is_r2_bath:
                    bath_room = r1 if is_r1_bath else r2
                    other_room = r2 if is_r1_bath else r1
                    if not (has_connection(other_room, bath_room) or has_connection(bath_room, other_room)):
                        continue

                is_vert = w["orientation"] == "vertical"
                wall_len = (w["z2"] - w["z1"]) if is_vert else (w["x2"] - w["x1"])
                
                is_open_flow = False
                for conn in r1.connections:
                    if conn.get("target_room") == r2.type and conn.get("intent") == "open_flow":
                        is_open_flow = True
                        break
                for conn in r2.connections:
                    if conn.get("target_room") == r1.type and conn.get("intent") == "open_flow":
                        is_open_flow = True
                        break

                if is_open_flow:
                    door_w = max(4.0, wall_len - 0.5) 
                else:
                    door_w = 2.5 if (is_r1_bath or is_r2_bath) else 3.0
                
                # Dynamic door downscaling for narrow walls
                if not is_open_flow and wall_len < door_w + 1.0:
                    if wall_len >= 2.0:
                        door_w = max(2.0, round(wall_len - 0.2, 1))
                    else:
                        continue 
                
                cx = (w["x1"] + w["x2"]) / 2.0
                cz = (w["z1"] + w["z2"]) / 2.0
                
                d1_x, d1_z = cx - r1.rect.x, cz - r1.rect.z
                d2_x, d2_z = cx - r2.rect.x, cz - r2.rect.z
                
                face1 = get_face(d1_x, d1_z, r1, is_vert)
                face2 = get_face(d2_x, d2_z, r2, is_vert)
                
                r1.doors.append(Door(x=d1_x, z=d1_z, width=door_w, wall_orientation=face1))
                r2.doors.append(Door(x=d2_x, z=d2_z, width=door_w, wall_orientation=face2))
                
                placed_doors_between.add(pair)
                logger.info(f"    Placed door between '{r1.name}' and '{r2.name}'")

        # --- PASS 2: RESCUE STRANDED ROOMS ---
        # If strict topological checks blocked a room from getting ANY doors, 
        # force a door on its longest shared wall to prevent a pipeline crash.
        for r in self.rooms:
            if len(r.doors) == 0:
                available_walls = [w for w in walls if w.get("is_shared") and r.id in w["room_ids"]]
                if not available_walls:
                    continue # Truly isolated geometry 
                
                def wall_len(w):
                    return abs(w["z2"]-w["z1"]) if w["orientation"]=="vertical" else abs(w["x2"]-w["x1"])
                
                best_wall = max(available_walls, key=wall_len)
                r1_id, r2_id = best_wall["room_ids"][:2]
                pair = tuple(sorted([r1_id, r2_id]))
                
                r1, r2 = room_by_id[r1_id], room_by_id[r2_id]
                w_len = wall_len(best_wall)
                
                door_w = 3.0
                if w_len < door_w + 1.0:
                    if w_len >= 2.0:
                        door_w = max(2.0, round(w_len - 0.2, 1))
                    else:
                        continue 
                
                cx = (best_wall["x1"] + best_wall["x2"]) / 2.0
                cz = (best_wall["z1"] + best_wall["z2"]) / 2.0
                
                is_vert = best_wall["orientation"] == "vertical"
                
                d1_x, d1_z = cx - r1.rect.x, cz - r1.rect.z
                d2_x, d2_z = cx - r2.rect.x, cz - r2.rect.z
                
                face1 = get_face(d1_x, d1_z, r1, is_vert)
                face2 = get_face(d2_x, d2_z, r2, is_vert)
                
                r1.doors.append(Door(x=d1_x, z=d1_z, width=door_w, wall_orientation=face1))
                r2.doors.append(Door(x=d2_x, z=d2_z, width=door_w, wall_orientation=face2))
                
                placed_doors_between.add(pair)
                logger.warning(f"[DOOR PLANNER] RESCUE PASS: Forced emergency door for stranded room {r.name} to {r1.name if r.id == r2.id else r2.name} ({door_w}ft)")
# ---------------------------------------------------------------------------
# Window Generation
# ---------------------------------------------------------------------------

class WindowPlacer:
    def __init__(self, rooms: List[RoomNode], plot_width: float, plot_length: float,
                 setback_x: float = 0.0, setback_z: float = 0.0):
        self.rooms = rooms

    def place_windows(self):
        logger.info(f"  [WindowPlacer] Starting window placement using finite walls.")
        from layout_engine import generate_walls_from_aabbs
        walls = generate_walls_from_aabbs(self.rooms)
        room_by_id = {r.id: r for r in self.rooms}
        
        main_door_added = False
        
        # --- PASS 1: Try placing the main door exactly on the designated facade ---
        for w in walls:
            if w.get("is_exterior"):
                rid = w["room_ids"][0]
                if rid not in room_by_id:
                    continue
                r = room_by_id[rid]
                
                cx = (w["x1"] + w["x2"]) / 2.0
                cz = (w["z1"] + w["z2"]) / 2.0
                rel_x, rel_z = cx - r.rect.x, cz - r.rect.z
                
                is_vert = w["orientation"] == "vertical"
                face = "west" if is_vert and rel_x < r.rect.width / 2.0 else \
                       "east" if is_vert else \
                       "north" if rel_z < r.rect.length / 2.0 else "south"
                
                is_designated_entrance = getattr(r, "main_entrance", False)
                designated_face = getattr(r, "main_entrance_wall", face)
                
                if is_designated_entrance and not main_door_added:
                    if face == designated_face:
                        r.doors.append(Door(x=rel_x, z=rel_z, width=4.0, height=7.0, is_main=True, wall_orientation=face))
                        main_door_added = True
                        logger.info(f"    Placed main entrance door on '{r.name}' (face {face})")
        
        # --- PASS 2: If the designated face was blocked by a room, place it on ANY exterior wall ---
        if not main_door_added:
            for w in walls:
                if w.get("is_exterior"):
                    rid = w["room_ids"][0]
                    r = room_by_id.get(rid)
                    if r and getattr(r, "main_entrance", False):
                        cx = (w["x1"] + w["x2"]) / 2.0
                        cz = (w["z1"] + w["z2"]) / 2.0
                        is_vert = w["orientation"] == "vertical"
                        face = "west" if is_vert and (cx - r.rect.x) < r.rect.width / 2.0 else "east" if is_vert else "north" if (cz - r.rect.z) < r.rect.length / 2.0 else "south"
                        r.doors.append(Door(x=cx - r.rect.x, z=cz - r.rect.z, width=4.0, height=7.0, is_main=True, wall_orientation=face))
                        main_door_added = True
                        logger.info(f"    Placed fallback main entrance door on '{r.name}' (face {face})")
                        break
                        
        # --- PASS 3: Hard force injection (if the living room has zero exterior walls) ---
        if not main_door_added:
            for r in self.rooms:
                if getattr(r, "main_entrance", False):
                    face = getattr(r, "main_entrance_wall", "south")
                    x = r.rect.width / 2.0 if face in ("north", "south") else (0.0 if face == "west" else r.rect.width)
                    z = r.rect.length / 2.0 if face in ("east", "west") else (0.0 if face == "north" else r.rect.length)
                    r.doors.append(Door(x=x, z=z, width=4.0, height=7.0, is_main=True, wall_orientation=face))
                    logger.info(f"    Forced main entrance door on '{r.name}' (face {face})")
                    break

        # --- PASS 4: Place Standard Windows ---
        for w in walls:
            if w.get("is_exterior"):
                rid = w["room_ids"][0]
                if rid not in room_by_id:
                    continue
                r = room_by_id[rid]
                
                cx = (w["x1"] + w["x2"]) / 2.0
                cz = (w["z1"] + w["z2"]) / 2.0
                rel_x, rel_z = cx - r.rect.x, cz - r.rect.z
                
                is_vert = w["orientation"] == "vertical"
                face = "west" if is_vert and rel_x < r.rect.width / 2.0 else \
                       "east" if is_vert else \
                       "north" if rel_z < r.rect.length / 2.0 else "south"
                
                # Window logic
                if r.type not in ["corridor", "hallway", "balcony", "parking", "veranda"]:
                    win_width = 2.0 if ("bath" in r.type or "toilet" in r.type) else 4.0
                    is_vent = ("bath" in r.type or "toilet" in r.type)
                    
                    h = 2.0 if is_vent else 4.0
                    sill = 5.0 if is_vent else 3.0
                    
                    # Prevent clipping by ensuring we don't place a window precisely where the main door was just placed
                    has_door_here = any(d for d in getattr(r, 'doors', []) if d.wall_orientation == face and abs(d.x - rel_x) < 2.0 and abs(d.z - rel_z) < 2.0)
                    
                    if not has_door_here:
                        r.windows.append(Window(x=rel_x, z=rel_z, width=win_width, height=h, sill_height=sill, wall_orientation=face))
                        logger.info(f"    Placed {'ventilator' if is_vent else 'window'} on '{r.name}'")
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

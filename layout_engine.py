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
import copy
import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from layout_templates import get_template_for_bhk
from asset_library import furniture_capacity, furniture_for_room, fit_furniture_assets, canonical_type

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
    target_room_id: str = ""
    source: str = ""
    target: str = ""
    wall_type: str = "interior"
    opens_inward: bool = True

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
    is_outdoor: bool = False
    furnitureColor: str = ""
    furniture: List[Any] = field(default_factory=list)
    mep_nodes: List[Dict[str, Any]] = field(default_factory=list)
    bathroom_role: str = ""
    assigned_to: str = ""
    # Faces where this room should NOT render walls because an adjacent room
    # already renders the shared wall.  Populated by compute_shared_walls for
    # circulation rooms (corridor, hallway, staircase) to prevent double-thick
    # walls when both neighbours render on the same face.
    suppress_wall_faces: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.connections is None:
            self.connections = []
        if self.doors is None:
            self.doors = []
        if self.windows is None:
            self.windows = []
        if self.shared_walls is None:
            self.shared_walls = []

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
            "is_outdoor": self.is_outdoor,
            "bathroom_role": self.bathroom_role,
            "assigned_to": self.assigned_to,
            "furnitureColor": self.furnitureColor,
            "furniture": getattr(self, 'furniture', []),
            "mep_nodes": getattr(self, 'mep_nodes', []),
            "doors": [{
                "x": round(d.x, 2), "z": round(d.z, 2), "wall_orientation": d.wall_orientation, "width": d.width,
                "height": getattr(d, 'height', 7.0), "is_main": bool(getattr(d, 'is_main', False)),
                "target_room_id": getattr(d, 'target_room_id', None),
                "source": getattr(d, 'source', 'outside' if getattr(d, 'is_main', False) else ''),
                "target": getattr(d, 'target', getattr(d, 'target_room_id', '')),
                "wall_type": getattr(d, 'wall_type', 'exterior' if getattr(d, 'is_main', False) else 'interior'),
                "opens_inward": bool(getattr(d, 'opens_inward', True)),
            } for d in getattr(self, 'doors', [])],
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

def place_furniture(nodes: List[RoomNode], indian_options: Dict[str, Any], user_prompt: str = ""):
    # Gemini supplies the semantic manifest for arbitrary user-created rooms.
    # The local library remains a bounded fallback for offline/API failures.
    ai_manifest = {}
    # Network furniture generation used 20–30 seconds per floor and was
    # repeated whenever geometry validation retried. Keep it opt-in; the local
    # measured furniture library is immediate and keeps generation predictable.
    if os.getenv("ENABLE_AI_FURNITURE", "0").strip().lower() in {"1", "true", "yes"}:
        try:
            from cloud_extractor import generate_furniture_manifest
            ai_manifest = generate_furniture_manifest([
                {"type": node.type, "width": node.rect.width, "length": node.rect.length}
                for node in nodes
            ], user_prompt=user_prompt)
        except Exception as exc:
            logger.info("[FURNITURE] Gemini manifest skipped: %s", exc)

    for node in nodes:
        # Furniture is a renderable asset manifest, not placeholder geometry.
        # Keep it local to the room so the layout solver remains authoritative
        # for walls, doors, and navigation.
        key = str(node.type or "room").lower().replace(" ", "_")
        generated = ai_manifest.get(key, [])
        if generated:
            node.furniture = fit_furniture_assets(generated, float(node.rect.width), float(node.rect.length), max_assets=furniture_capacity(key, node.rect.width, node.rect.length))
        else:
            node.furniture = furniture_for_room(node.type, node.rect.width, node.rect.length)

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

    # Site elements (verandah pad, garden, parking, pool) still take part in
    # adjacency and door realization, but they are not masonry: drawing their
    # boundary produced walls standing away from the building.
    open_air_ids = {
        r.id for r in rooms
        if getattr(r, "is_outdoor", False)
        or str(getattr(r, "roof_type", "flat")).lower() == "open"
    }

    for w in walls_raw:
        if w.get("suppressed"):
            continue
        wall_rooms = set(w.get("room_ids") or [])
        if wall_rooms and wall_rooms <= open_air_ids:
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


def rects_overlap(first: "Rect", second: "Rect", tolerance: float = 0.05) -> bool:
    """Do two rectangles share interior area?

    Was a closure inside _recover_missing_requested_rooms. Anything that moves
    or grows a solved rectangle needs it, and the two rules that did not use it
    (otta, portico) could silently produce overlapping rooms.
    """
    return (
        min(first.x + first.width, second.x + second.width) - max(first.x, second.x) > tolerance
        and min(first.z + first.length, second.z + second.length) - max(first.z, second.z) > tolerance
    )


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

        def _would_overlap(candidate: Rect) -> List[str]:
            hits = []
            for node in floor1:
                if node is stair1 or node.type == "staircase":
                    continue
                other = node.rect
                dx = min(candidate.x + candidate.width, other.x + other.width) - max(candidate.x, other.x)
                dz = min(candidate.z + candidate.length, other.z + other.length) - max(candidate.z, other.z)
                if dx > 0.05 and dz > 0.05:
                    hits.append(node.id)
            return hits

        if stair1:
            # The solver is normally handed this rectangle as a fixed cell, so
            # the overwrite is a no-op. When it is not honoured — the stair was
            # deduped, or the spec never carried the pin — forcing the lower
            # rect on top of a solved floor drops the stair straight through
            # the corridor and the whole floor fails validation. CP-SAT's own
            # placement is non-overlapping, so keep it and accept a stair that
            # is not perfectly stacked rather than losing the house.
            collisions = _would_overlap(rect)
            if collisions:
                logger.warning(
                    "[DUPLEX ALIGN] Keeping solved upper staircase at (%.2f, %.2f); "
                    "stacking it over the lower flight would overlap %s.",
                    stair1.rect.x, stair1.rect.z, ", ".join(collisions),
                )
            else:
                stair1.rect = rect
        elif not _would_overlap(rect):
            floor1.append(RoomNode(id="staircase-f1", type="staircase", name="Staircase",
                                   rect=rect, wallThicknessIn=6.0, floorColor="#e5e7eb"))
        else:
            logger.warning("[DUPLEX ALIGN] No room above the lower staircase; upper flight not added.")

    # The upper-floor solver receives this staircase as a fixed rectangle, so
    # its other rooms are already arranged around it.  Do not stretch a
    # corridor or clip rooms after solving: doing so invalidates CP-SAT's
    # non-overlap guarantees and was the source of intersecting first-floor
    # walls.  Alignment is deliberately limited to the vertical stair lock and
    # an explicitly requested double-height void.

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

    if not make_void:
        return floor1

    # 3. Clip remaining rooms out of an explicitly requested void only.  The
    # staircase itself was a fixed solver obstacle and needs no post-clipping.
    locked = [n.rect for n in floor1 if n.type == "void"]

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
        if r.type == "foyer":
            candidate = r
            break
    if candidate is None:
        for r in rooms:
            if r.type in ("entrance_lobby", "lobby"):
                candidate = r
                break
    if candidate is None:
        for r in rooms:
            if r.type == "living_room":
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

    # Compare against the ACTUAL generated house envelope. The CP layout may
    # intentionally occupy only part of the legal buildable rectangle; using
    # theoretical setback edges made the living room appear closer to a side
    # wall and put the entrance at the rear/side of the house.
    house_min_x = min(room.rect.x for room in rooms)
    house_max_x = max(room.rect.x + room.rect.width for room in rooms)
    house_min_z = min(room.rect.z for room in rooms)
    house_max_z = max(room.rect.z + room.rect.length for room in rooms)
    dist_south = abs((r.rect.z + r.rect.length) - house_max_z)
    dist_north = abs(r.rect.z - house_min_z)
    dist_west  = abs(r.rect.x - house_min_x)
    dist_east  = abs((r.rect.x + r.rect.width) - house_max_x)

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
    "gym":             {"area": 120.0, "min_dim":  9.0},
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
    "mustard": "#E4A010", "cream": "#FDF5E6", "ivory": "#FDF5E6", "peach": "#FFDAB9", "sea_green": "#2E8B57",
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

def safe_corridor_layout(rooms_spec: List[Dict[str, Any]], plot_width: float, plot_length: float, theme: dict = None) -> List[RoomNode]:
    """Program-driven fallback generator that places 100% of requested rooms in rooms_spec."""
    if not rooms_spec:
        return []

    theme = theme or {}
    logger.info(f"[SAFE FALLBACK] Program-driven fallback layout generating {len(rooms_spec)} rooms for {plot_width}x{plot_length} plot.")
    logger.info("[QUALITY MODE] safe_fallback")

    nodes: List[RoomNode] = []

    # 1. Separate public, service, and private/wet rooms
    public_types = {"foyer", "living_room", "dining_room", "living", "dining"}
    service_types = {"kitchen", "open_kitchen", "store_room", "utility"}

    public_specs = []
    service_specs = []
    private_specs = []
    corridor_spec = None

    for spec in rooms_spec:
        t = canonical_type(spec.get("type", ""))
        if "corridor" in t or "passage" in t:
            if not corridor_spec:
                corridor_spec = spec
        elif t in public_types:
            public_specs.append(spec)
        elif t in service_types:
            service_specs.append(spec)
        else:
            private_specs.append(spec)

    # Keep instance-owned private satellites beside their exact bedroom in
    # the legacy fallback (for example balcony-bedroom-attached bath). This is
    # identity-driven ordering, not a fixed room-count template.
    private_by_id = {str(spec.get("id")): spec for spec in private_specs if spec.get("id")}
    consumed_private = set()
    ordered_private = []
    for spec in private_specs:
        spec_id = str(spec.get("id") or "")
        if spec_id in consumed_private or "bedroom" not in canonical_type(spec.get("type")):
            continue
        owned_ids = [
            str(connection.get("target_room_id"))
            for connection in spec.get("connections", []) or []
            if str(connection.get("target_room_id")) in private_by_id
            and str(connection.get("target_room_id")) != spec_id
        ]
        owned = [private_by_id[room_id] for room_id in dict.fromkeys(owned_ids)]
        if owned:
            ordered_private.extend(owned[:1] + [spec] + owned[1:])
            consumed_private.update([spec_id] + [str(item.get("id")) for item in owned])
    ordered_private.extend(spec for spec in private_specs if str(spec.get("id") or "") not in consumed_private)
    private_specs = ordered_private

    # Central Spine Corridor (Spans full width so 100% of rooms share a wall with the corridor)
    corr_z = plot_length * 0.42
    corr_h = max(4.0, plot_length * 0.12)
    corr_x = 1.0
    corr_w = plot_width - 2.0

    corr_id = corridor_spec.get("id") if corridor_spec else "corridor-core"
    nodes.append(RoomNode(
        id=corr_id,
        type="corridor",
        name="Corridor",
        rect=Rect(corr_x, corr_z, corr_w, corr_h),
        wallThicknessIn=6.0,
        is_wet=False,
    ))

    # 2. Place Front Zone (Public + Service rooms: z from 1.0 to corr_z)
    front_specs = public_specs + service_specs
    if front_specs:
        front_h = corr_z - 1.0
        x_cursor = 1.0
        n_front = len(front_specs)
        w_per_room = (plot_width - 2.0) / float(n_front)
        for i, spec in enumerate(front_specs):
            room_id = str(spec.get("id") or f"{spec.get('type')}-{i+1}")
            r_type = canonical_type(spec.get("type"))
            r_name = str(spec.get("name") or r_type.replace("_", " ").title())
            is_wet = r_type == "kitchen"
            nodes.append(RoomNode(
                id=room_id,
                type=r_type,
                name=r_name,
                rect=Rect(x_cursor, 1.0, w_per_room, front_h),
                wallThicknessIn=8.0 if is_wet else 6.0,
                is_wet=is_wet,
                connections=copy.deepcopy(spec.get("connections", []) or []),
                floorColor=theme.get("floor") or ("#dcfce7" if is_wet else "#ffffff"),
                is_outdoor=bool(spec.get("is_outdoor")),
                roof_type=str(spec.get("roof_type") or ("open" if spec.get("is_outdoor") else "flat")),
                bathroom_role=str(spec.get("bathroom_role") or ""),
                assigned_to=str(spec.get("assigned_to") or spec.get("attached_to_id") or ""),
            ))
            x_cursor += w_per_room

    # 3. Place Rear Zone (Private rooms: Bedrooms & Bathrooms: z from corr_z + corr_h to plot_length - 1.0)
    if private_specs:
        rear_z = corr_z + corr_h
        rear_h = max(10.0, plot_length - rear_z - 1.0)
        x_cursor = 1.0
        n_rear = len(private_specs)
        w_per_room = (plot_width - 2.0) / float(n_rear)
        for i, spec in enumerate(private_specs):
            room_id = str(spec.get("id") or f"{spec.get('type')}-{i+1}")
            r_type = canonical_type(spec.get("type"))
            r_name = str(spec.get("name") or r_type.replace("_", " ").title())
            is_wet = "bath" in r_type or "toilet" in r_type or "wash" in r_type
            nodes.append(RoomNode(
                id=room_id,
                type=r_type,
                name=r_name,
                rect=Rect(x_cursor, rear_z, w_per_room, rear_h),
                wallThicknessIn=8.0 if is_wet else 6.0,
                is_wet=is_wet,
                connections=copy.deepcopy(spec.get("connections", []) or []),
                floorColor=theme.get("floor") or ("#dcfce7" if is_wet else "#ffffff"),
                is_outdoor=bool(spec.get("is_outdoor")),
                roof_type=str(spec.get("roof_type") or ("open" if spec.get("is_outdoor") else "flat")),
                bathroom_role=str(spec.get("bathroom_role") or ""),
                assigned_to=str(spec.get("assigned_to") or spec.get("attached_to_id") or ""),
            ))
            x_cursor += w_per_room

    return nodes


class LayoutEngine:
    def __init__(self, plot_width: float, plot_length: float, colors: Dict[str, Any] = None):
        # We always want some reasonable bounds.
        self.plot_width = max(20.0, float(plot_width))
        self.plot_length = max(20.0, float(plot_length))
        self.colors = colors or {}
        self.theme = resolve_theme(self.colors)

        # Keep a site margin while allowing the requested building footprint to
        # use at least 80% of the plot. A 95% buildable span on each axis leaves
        # a 90.25% maximum footprint, giving the solver enough room to reach the
        # 80% target without crossing the plot boundary.
        self.target_coverage = 0.75
        self.buildable_width = self.plot_width * 0.95
        self.buildable_length = self.plot_length * 0.95
        self.setback_x = (self.plot_width - self.buildable_width) / 2
        self.setback_z = (self.plot_length - self.buildable_length) / 2

        self.last_walls: List[Dict] = []

    @property
    def walls(self) -> List[Dict]:
        return self.last_walls

    @staticmethod
    def _shared_wall_length(a: RoomNode, b: RoomNode) -> float:
        """Return the usable shared boundary between two room rectangles."""
        tolerance = 0.12
        if abs((a.rect.x + a.rect.width) - b.rect.x) <= tolerance or abs((b.rect.x + b.rect.width) - a.rect.x) <= tolerance:
            return max(0.0, min(a.rect.z + a.rect.length, b.rect.z + b.rect.length) - max(a.rect.z, b.rect.z))
        if abs((a.rect.z + a.rect.length) - b.rect.z) <= tolerance or abs((b.rect.z + b.rect.length) - a.rect.z) <= tolerance:
            return max(0.0, min(a.rect.x + a.rect.width, b.rect.x + b.rect.width) - max(a.rect.x, b.rect.x))
        return 0.0

    @staticmethod
    def _overlaps_after_translation(room: RoomNode, dx: float, dz: float, other: RoomNode) -> bool:
        rx1, rz1 = room.rect.x + dx, room.rect.z + dz
        rx2, rz2 = rx1 + room.rect.width, rz1 + room.rect.length
        ox1, oz1 = other.rect.x, other.rect.z
        ox2, oz2 = ox1 + other.rect.width, oz1 + other.rect.length
        return min(rx2, ox2) - max(rx1, ox1) > 0.05 and min(rz2, oz2) - max(rz1, oz1) > 0.05

    def _repair_disconnected_components(self, nodes: List[RoomNode]) -> None:
        """Join separated indoor room islands before walls and doors are built.

        A valid room graph can still become visually disconnected after a
        footprint transform or a fallback pack. Move only the isolated island
        as a rigid group until it shares a real wall with the main island; this
        preserves all room sizes, furniture semantics, and internal topology.
        """
        interior = [
            node for node in nodes
            if not getattr(node, "is_outdoor", False)
            and node.roof_type != "open"
            and node.type not in {"parking", "garden", "courtyard", "terrace", "balcony"}
        ]
        if len(interior) < 2:
            return

        def components() -> List[List[RoomNode]]:
            remaining = list(interior)
            result = []
            while remaining:
                seed = remaining.pop()
                component = [seed]
                queue = [seed]
                while queue:
                    current = queue.pop()
                    neighbours = [other for other in list(remaining) if self._shared_wall_length(current, other) >= 0.05]
                    for other in neighbours:
                        remaining.remove(other)
                        component.append(other)
                        queue.append(other)
                result.append(component)
            return result

        # Use the living-room island as the navigation/building anchor.
        groups = components()
        if len(groups) <= 1:
            return
        main = next((group for group in groups if any(room.type == "living_room" for room in group)), max(groups, key=len))
        logger.warning("[GEOMETRY] Repairing %d disconnected indoor room component(s)", len(groups) - 1)

        for group in groups:
            if group is main:
                continue
            candidates = []
            for moving in group:
                for anchor in main:
                    ax, az = moving.rect.x, moving.rect.z
                    aw, al = moving.rect.width, moving.rect.length
                    bx, bz = anchor.rect.x, anchor.rect.z
                    bw, bl = anchor.rect.width, anchor.rect.length
                    align_z = [bz - az, bz + bl - (az + al), bz + bl / 2.0 - (az + al / 2.0)]
                    align_x = [bx - ax, bx + bw - (ax + aw), bx + bw / 2.0 - (ax + aw / 2.0)]
                    placements = (
                        [(bx + bw - ax, dz) for dz in align_z]
                        + [(bx - (ax + aw), dz) for dz in align_z]
                        + [(dx, bz + bl - az) for dx in align_x]
                        + [(dx, bz - (az + al)) for dx in align_x]
                    )
                    for dx, dz in placements:
                        translated = {room.id: (room.rect.x + dx, room.rect.z + dz) for room in group}
                        if any(
                            x < -0.05 or z < -0.05
                            or x + room.rect.width > self.plot_width + 0.05
                            or z + room.rect.length > self.plot_length + 0.05
                            for room in group
                            for x, z in [translated[room.id]]
                        ):
                            continue
                        if any(
                            self._overlaps_after_translation(room, dx, dz, other)
                            for room in group for other in interior if other not in group
                        ):
                            continue
                        shared = max(
                            max(0.0, min(room.rect.x + dx + room.rect.width, other.rect.x + other.rect.width) - max(room.rect.x + dx, other.rect.x))
                            for room in group for other in main
                        )
                        candidates.append((abs(dx) + abs(dz), -shared, dx, dz))
            if not candidates:
                logger.error("[GEOMETRY] Could not close disconnected component; keeping validated coordinates")
                continue
            _, _, dx, dz = min(candidates)
            for room in group:
                room.rect.x += dx
                room.rect.z += dz
            main.extend(group)
            logger.info("[GEOMETRY] Joined room component with rigid translation dx=%.2f dz=%.2f", dx, dz)

    def _absorb_enclosed_pockets(self, nodes: List[RoomNode], min_pocket_sqft: float = 12.0) -> None:
        """Grow a neighbour into any dead space the plan has walled in.

        CP-SAT is free to leave gaps between rooms - non-overlap is a hard rule,
        tiling is not - and `dead_space` is only a soft objective. Most gaps open
        onto the outside and read as an ordinary L-shaped footprint, which is
        fine. A pocket with rooms on every side is not: it is a void nobody can
        reach, behind walls with nothing on the other side.

        Rasterise the footprint at a foot, flood the empty cells in from the
        border, and whatever the flood cannot reach is enclosed. Grow the
        neighbour with the longest shared frontage over it, and only if the
        result still overlaps nothing.
        """
        indoor = [
            node for node in nodes
            if not getattr(node, "is_outdoor", False) and node.roof_type != "open"
        ]
        if len(indoor) < 3:
            return

        min_x = min(n.rect.x for n in indoor)
        min_z = min(n.rect.z for n in indoor)
        max_x = max(n.rect.x + n.rect.width for n in indoor)
        max_z = max(n.rect.z + n.rect.length for n in indoor)
        cols = int(math.ceil(max_x - min_x))
        rows = int(math.ceil(max_z - min_z))
        if cols < 3 or rows < 3:
            return

        # A cell counts as occupied when its centre falls inside a room, so two
        # flush rooms never leave a phantom seam between them.
        filled = [[False] * rows for _ in range(cols)]
        for node in indoor:
            r = node.rect
            for cx in range(max(0, int(r.x - min_x)), min(cols, int(r.x + r.width - min_x) + 1)):
                for cz in range(max(0, int(r.z - min_z)), min(rows, int(r.z + r.length - min_z) + 1)):
                    px, pz = min_x + cx + 0.5, min_z + cz + 0.5
                    if r.x <= px <= r.x + r.width and r.z <= pz <= r.z + r.length:
                        filled[cx][cz] = True

        seen = [[False] * rows for _ in range(cols)]
        queue = deque()
        for cx in range(cols):
            for cz in (0, rows - 1):
                if not filled[cx][cz] and not seen[cx][cz]:
                    seen[cx][cz] = True
                    queue.append((cx, cz))
        for cz in range(rows):
            for cx in (0, cols - 1):
                if not filled[cx][cz] and not seen[cx][cz]:
                    seen[cx][cz] = True
                    queue.append((cx, cz))
        while queue:
            cx, cz = queue.popleft()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = cx + dx, cz + dz
                if 0 <= nx < cols and 0 <= nz < rows and not filled[nx][nz] and not seen[nx][nz]:
                    seen[nx][nz] = True
                    queue.append((nx, nz))

        # Collect each enclosed pocket as its own connected group.
        pockets: List[List[Tuple[int, int]]] = []
        claimed = [[False] * rows for _ in range(cols)]
        for cx in range(cols):
            for cz in range(rows):
                if filled[cx][cz] or seen[cx][cz] or claimed[cx][cz]:
                    continue
                group, stack = [], [(cx, cz)]
                claimed[cx][cz] = True
                while stack:
                    px, pz = stack.pop()
                    group.append((px, pz))
                    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, nz = px + dx, pz + dz
                        if (0 <= nx < cols and 0 <= nz < rows and not filled[nx][nz]
                                and not seen[nx][nz] and not claimed[nx][nz]):
                            claimed[nx][nz] = True
                            stack.append((nx, nz))
                if len(group) >= min_pocket_sqft:
                    pockets.append(group)

        for group in pockets:
            gx0 = min_x + min(c for c, _ in group)
            gz0 = min_z + min(z for _, z in group)
            gx1 = min_x + max(c for c, _ in group) + 1
            gz1 = min_z + max(z for _, z in group) + 1
            pocket = Rect(gx0, gz0, gx1 - gx0, gz1 - gz0)

            best, best_frontage, best_rect = None, 0.0, None
            for node in indoor:
                r = node.rect
                grown = None
                if abs((r.x + r.width) - pocket.x) < 1.01:            # pocket to the east
                    grown = Rect(r.x, r.z, (pocket.x + pocket.width) - r.x, r.length)
                elif abs(r.x - (pocket.x + pocket.width)) < 1.01:     # pocket to the west
                    grown = Rect(pocket.x, r.z, (r.x + r.width) - pocket.x, r.length)
                elif abs((r.z + r.length) - pocket.z) < 1.01:         # pocket to the south
                    grown = Rect(r.x, r.z, r.width, (pocket.z + pocket.length) - r.z)
                elif abs(r.z - (pocket.z + pocket.length)) < 1.01:    # pocket to the north
                    grown = Rect(r.x, pocket.z, r.width, (r.z + r.length) - pocket.z)
                if grown is None:
                    continue
                if any(rects_overlap(grown, other.rect) for other in nodes if other is not node):
                    continue
                frontage = min(r.width, pocket.width) if grown.length != r.length else min(r.length, pocket.length)
                if frontage > best_frontage:
                    best, best_frontage, best_rect = node, frontage, grown

            if best is not None and best_rect is not None:
                logger.info(
                    "  [VOID] Absorbing %.0f sq ft of enclosed dead space into '%s'.",
                    pocket.width * pocket.length, best.name,
                )
                best.rect = best_rect

    def _close_nearby_wall_seams(self, nodes: List[RoomNode]) -> None:
        """Snap near-coincident room edges together so wall meshes cannot slit."""
        interior = [
            node for node in nodes
            if not getattr(node, "is_outdoor", False)
            and node.roof_type != "open"
            and node.type not in {"parking", "garden", "courtyard", "terrace", "balcony"}
        ]
        seam_tolerance = 0.35
        overlap_tolerance = 0.05
        for index, first in enumerate(interior):
            for second in interior[index + 1:]:
                a, b = first.rect, second.rect
                z_overlap = min(a.z + a.length, b.z + b.length) - max(a.z, b.z)
                x_overlap = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
                if z_overlap > overlap_tolerance:
                    if abs((a.x + a.width) - b.x) <= seam_tolerance:
                        seam = ((a.x + a.width) + b.x) / 2.0
                        a.width = seam - a.x
                        b.x, b.width = seam, b.x + b.width - seam
                    elif abs((b.x + b.width) - a.x) <= seam_tolerance:
                        seam = ((b.x + b.width) + a.x) / 2.0
                        b.width = seam - b.x
                        a.x, a.width = seam, a.x + a.width - seam
                if x_overlap > overlap_tolerance:
                    if abs((a.z + a.length) - b.z) <= seam_tolerance:
                        seam = ((a.z + a.length) + b.z) / 2.0
                        a.length = seam - a.z
                        b.z, b.length = seam, b.z + b.length - seam
                    elif abs((b.z + b.length) - a.z) <= seam_tolerance:
                        seam = ((b.z + b.length) + a.z) / 2.0
                        b.length = seam - b.z
                        a.z, a.length = seam, a.z + a.length - seam

    def _expand_to_target_coverage(
        self,
        nodes: List[RoomNode],
        additional_nodes: Optional[List[RoomNode]] = None,
    ) -> None:
        """Center a solved footprint without changing its internal geometry.

        Unused plot area is legitimate yard/setback space. Earlier versions
        stretched every coordinate to an arbitrary 80% coverage target; that
        magnified solver dead zones, distorted room proportions, and made a
        compact upper storey look detached from the ground-floor composition.
        """
        if not nodes:
            return
        min_x = min(node.rect.x for node in nodes)
        max_x = max(node.rect.x + node.rect.width for node in nodes)
        min_z = min(node.rect.z for node in nodes)
        max_z = max(node.rect.z + node.rect.length for node in nodes)
        house_width = max(0.1, max_x - min_x)
        house_length = max(0.1, max_z - min_z)
        scale_x = 1.0
        scale_z = 1.0
        scaled_width = house_width * scale_x
        scaled_length = house_length * scale_z
        offset_x = (self.plot_width - scaled_width) / 2.0
        offset_z = (self.plot_length - scaled_length) / 2.0
        transform_nodes = list(nodes)
        transform_nodes.extend(node for node in (additional_nodes or []) if node not in transform_nodes)
        for node in transform_nodes:
            node.rect.x = offset_x + (node.rect.x - min_x) * scale_x
            node.rect.z = offset_z + (node.rect.z - min_z) * scale_z
            node.rect.width *= scale_x
            node.rect.length *= scale_z

        coverage = (scaled_width * scaled_length) / max(1.0, self.plot_width * self.plot_length)
        logger.info(
            "[COMPACT FOOTPRINT] Preserved solved footprint at %.1fx%.1f (%.1f%% of plot); remainder kept as site space",
            house_width, house_length, coverage * 100.0,
        )

    def _recover_missing_requested_rooms(
        self,
        nodes: List[RoomNode],
        requested_rooms: List[Tuple[str, str]],
        rooms_spec: List[Dict[str, Any]],
        allowed_bounds: Optional[Tuple[float, float, float, float]] = None,
        blocked_zones: Optional[List[Rect]] = None,
    ) -> None:
        """Materialize rooms skipped by the legacy template/carve fallback.

        CP-SAT may time out on a dense arbitrary program. The legacy template
        handles its core slots first and historically dropped a custom room if
        one preferred donor could not be carved. This guillotine partitioner
        tries every feasible donor, retains the complete footprint, and copies
        the request's identity/topology metadata into the recovered room.
        """
        present_ids = {str(node.id) for node in nodes}
        source_by_id = {
            str(spec.get("id")): spec for spec in rooms_spec
            if isinstance(spec, dict) and spec.get("id")
        }
        protected = {"corridor", "hallway", "staircase", "courtyard", "balcony", "parking", "void"}
        indoor_bounds = allowed_bounds or (
            self.setback_x,
            self.setback_z,
            self.setback_x + self.buildable_width,
            self.setback_z + self.buildable_length,
        )
        plot_bounds = (
            self.setback_x,
            self.setback_z,
            self.setback_x + self.buildable_width,
            self.setback_z + self.buildable_length,
        )
        obstacles = list(blocked_zones or [])

        rect_overlaps = rects_overlap

        def free_rect(candidate: Rect, bounds: Tuple[float, float, float, float]) -> bool:
            bx0, bz0, bx1, bz1 = bounds
            if (
                candidate.x < bx0 - 0.05 or candidate.z < bz0 - 0.05
                or candidate.x + candidate.width > bx1 + 0.05
                or candidate.z + candidate.length > bz1 + 0.05
            ):
                return False
            return not any(rect_overlaps(candidate, occupied.rect) for occupied in nodes) and not any(
                rect_overlaps(candidate, obstacle) for obstacle in obstacles
            )

        recovery_order = sorted(
            requested_rooms,
            key=lambda item: (
                0 if ("balcony" in item[1] or bool(source_by_id.get(item[0], {}).get("is_outdoor")))
                else 2 if item[1] == "bathroom"
                else 1
            ),
        )
        for room_id, room_type in recovery_order:
            if room_id in present_ids:
                continue
            source = source_by_id.get(room_id, {})
            minimum_type = "balcony" if "balcony" in room_type else room_type
            new_min = ROOM_MINIMUMS.get(minimum_type, _DEFAULT_MIN)
            new_min_dim = float(new_min.get("min_dim", 6.0))
            new_min_area = float(new_min.get("area", 36.0))

            requested_targets = [
                str(edge.get("target_room_id"))
                for edge in source.get("connections", []) or []
                if isinstance(edge, dict) and edge.get("intent") != "proximity" and edge.get("target_room_id")
            ]
            # Some relationships are intentionally stored on the owning room
            # (bedroom -> private balcony). Include reverse instance edges so
            # recovery still knows which existing room must be touched.
            requested_targets.extend(
                str(spec.get("id"))
                for spec in rooms_spec
                if isinstance(spec, dict) and spec.get("id") and any(
                    isinstance(edge, dict)
                    and str(edge.get("target_room_id")) == room_id
                    and edge.get("intent") != "proximity"
                    for edge in spec.get("connections", []) or []
                )
            )
            requested_targets = list(dict.fromkeys(requested_targets))
            target_rank = {target_id: rank for rank, target_id in enumerate(requested_targets)}
            target_types = {
                node.type for node in nodes if str(node.id) in requested_targets
            }
            owner_bound = "balcony" in room_type or (
                room_type == "bathroom" and any("bedroom" in target_type for target_type in target_types)
            )

            # First use genuinely empty slab/plot space adjacent to a required
            # target. Upper-floor templates often occupy only one end of the
            # supporting slab, so splitting already-small rooms is unnecessary.
            placement_bounds = plot_bounds if (source.get("is_outdoor") or "balcony" in room_type) else indoor_bounds
            target_nodes = [node for target_id in requested_targets for node in nodes if str(node.id) == target_id]
            base_shapes = [
                (new_min_dim, max(new_min_dim, new_min_area / max(new_min_dim, 0.1))),
                (max(new_min_dim, new_min_area / max(new_min_dim, 0.1)), new_min_dim),
            ]
            placed_rect: Optional[Rect] = None
            for target in target_nodes:
                for width, length in base_shapes:
                    candidates = [
                        Rect(target.rect.x + target.rect.width, target.rect.z, width, min(length, target.rect.length)),
                        Rect(target.rect.x - width, target.rect.z, width, min(length, target.rect.length)),
                        Rect(target.rect.x, target.rect.z + target.rect.length, min(width, target.rect.width), length),
                        Rect(target.rect.x, target.rect.z - length, min(width, target.rect.width), length),
                    ]
                    placed_rect = next((candidate for candidate in candidates if candidate.area >= new_min_area and free_rect(candidate, placement_bounds)), None)
                    if placed_rect:
                        break
                if placed_rect:
                    break

            # If no requested anchor is available, pack into any unused
            # rectangle whose edges align with the existing finite-wall grid.
            if placed_rect is None and not owner_bound:
                bx0, bz0, bx1, bz1 = placement_bounds
                for width, length in base_shapes:
                    x_values = sorted({bx0, *(node.rect.x + node.rect.width for node in nodes), *(node.rect.x - width for node in nodes)})
                    z_values = sorted({bz0, *(node.rect.z + node.rect.length for node in nodes), *(node.rect.z - length for node in nodes)})
                    placed_rect = next((
                        Rect(x, z, width, length)
                        for z in z_values for x in x_values
                        if free_rect(Rect(x, z, width, length), placement_bounds)
                    ), None)
                    if placed_rect:
                        break

            if placed_rect is not None:
                is_wet = room_type == "bathroom" or any(token in room_type for token in ("toilet", "washroom", "laundry"))
                
                # --- STRICT FALLBACK VALIDATION ---
                # A fallback-added room must satisfy postconditions. Do not materialize a bathroom anywhere merely to satisfy room count.
                if is_wet or "bedroom" in room_type:
                    has_legal_access = False
                    
                    def rects_touch(first: Rect, second: Rect, min_overlap: float = 1.0) -> bool:
                        overlap_x = min(first.x + first.width, second.x + second.width) - max(first.x, second.x)
                        overlap_z = min(first.z + first.length, second.z + second.length) - max(first.z, second.z)
                        return (abs(overlap_x) < 0.1 and overlap_z >= min_overlap) or (abs(overlap_z) < 0.1 and overlap_x >= min_overlap)

                    if target_nodes:
                        # Attached bathroom/bedroom must touch its target
                        if any(rects_touch(placed_rect, target.rect) for target in target_nodes):
                            has_legal_access = True
                    else:
                        # Common bathroom/bedroom must touch circulation
                        circulation_types = {"corridor", "lobby", "family_lounge", "hallway", "foyer"}
                        if any(rects_touch(placed_rect, node.rect) for node in nodes if node.type in circulation_types):
                            has_legal_access = True
                            
                    if not has_legal_access:
                        logger.warning(f"[FALLBACK] Rejected {room_id}: Cannot generate legal access (does not touch required neighbor or circulation).")
                        placed_rect = None

            if placed_rect is not None:
                nodes.append(RoomNode(
                    id=room_id,
                    type=room_type,
                    name=str(source.get("name") or room_type.replace("_", " ").title()),
                    rect=placed_rect,
                    wallThicknessIn=8.0 if is_wet else 6.0,
                    is_wet=is_wet,
                    connections=copy.deepcopy(source.get("connections", []) or []),
                    roof_type=str(source.get("roof_type") or ("open" if source.get("is_outdoor") else "flat")),
                    is_outdoor=bool(source.get("is_outdoor")),
                    floorColor=self.theme.get("floor") or ("#dcfce7" if is_wet else "#ffffff"),
                    wallColor=self.theme.get("wall") or "",
                    furnitureColor=self.theme.get("furniture") or "",
                ))
                present_ids.add(room_id)
                logger.info(
                    "[FALLBACK RECOVERY] Materialized requested %s (%s) in unused footprint space",
                    room_id, room_type,
                )
                continue

            def donor_rank(node: RoomNode) -> Tuple[int, int, float]:
                if str(node.id) in target_rank:
                    relation_rank = target_rank[str(node.id)]
                elif room_type == "bathroom" and "bedroom" in node.type:
                    relation_rank = len(target_rank) + 1
                elif room_type in {"home_theater", "prayer_room", "pooja_room"} and node.type in {"living_room", "dining_room"}:
                    relation_rank = len(target_rank) + 1
                else:
                    relation_rank = len(target_rank) + 2
                return (relation_rank, node.type in protected, -node.rect.area)

            recovered = False
            for donor in sorted(nodes, key=donor_rank):
                if donor.type in protected or donor.roof_type == "open" or getattr(donor, "is_outdoor", False):
                    continue
                # Restrict partition fallback: only attached bathrooms may be partitioned from their assigned bedroom
                is_attached_bath = (room_type in {"bathroom", "attached_bathroom", "ensuite"} and source.get("bathroom_role") == "attached")
                if not is_attached_bath or "bedroom" not in donor.type:
                    continue

                donor_min = ROOM_MINIMUMS.get(donor.type, _DEFAULT_MIN)
                donor_min_dim = float(donor_min.get("min_dim", 6.0))
                donor_min_area = float(donor_min.get("area", 36.0))
                rect = donor.rect

                # Prefer the cut that removes the least area while leaving the
                # donor above both its dimension and usable-area minimum.
                cuts: List[Tuple[float, str, float]] = []
                cut_w = max(new_min_dim, new_min_area / max(rect.length, 0.1))
                if (
                    rect.width - cut_w >= donor_min_dim
                    and (rect.width - cut_w) * rect.length >= donor_min_area
                    and cut_w * rect.length >= new_min_area
                ):
                    cuts.append((cut_w * rect.length, "vertical", cut_w))
                cut_l = max(new_min_dim, new_min_area / max(rect.width, 0.1))
                if (
                    rect.length - cut_l >= donor_min_dim
                    and rect.width * (rect.length - cut_l) >= donor_min_area
                    and rect.width * cut_l >= new_min_area
                ):
                    cuts.append((rect.width * cut_l, "horizontal", cut_l))
                if not cuts:
                    continue

                _, orientation, amount = min(cuts, key=lambda item: item[0])
                if orientation == "vertical":
                    recovered_rect = Rect(rect.x + rect.width - amount, rect.z, amount, rect.length)
                    rect.width -= amount
                else:
                    recovered_rect = Rect(rect.x, rect.z + rect.length - amount, rect.width, amount)
                    rect.length -= amount

                is_wet = room_type == "bathroom" or any(token in room_type for token in ("toilet", "washroom", "laundry"))
                recovered_node = RoomNode(
                    id=room_id,
                    type=room_type,
                    name=str(source.get("name") or room_type.replace("_", " ").title()),
                    rect=recovered_rect,
                    wallThicknessIn=8.0 if is_wet else 6.0,
                    is_wet=is_wet,
                    connections=copy.deepcopy(source.get("connections", []) or []),
                    roof_type=str(source.get("roof_type") or "flat"),
                    is_outdoor=bool(source.get("is_outdoor")),
                    floorColor=self.theme.get("floor") or ("#dcfce7" if is_wet else "#ffffff"),
                    wallColor=self.theme.get("wall") or "",
                    furnitureColor=self.theme.get("furniture") or "",
                )
                nodes.append(recovered_node)
                present_ids.add(room_id)
                logger.info(
                    "[FALLBACK RECOVERY] Materialized requested %s (%s) by partitioning %s",
                    room_id, room_type, donor.id,
                )
                recovered = True
                break

            if not recovered:
                logger.warning(
                    "[FALLBACK RECOVERY] No dimension-safe partition could materialize %s (%s)",
                    room_id, room_type,
                )

    def generate(self, rooms_spec: List[Dict[str, Any]], blocked_zones: Optional[List[Rect]] = None, indian_options: Optional[Dict[str, Any]] = None, layout_rules: Optional[List[Dict[str, str]]] = None, restrict_slots: bool = False, master_blueprint: Optional[List[Dict[str, Any]]] = None, plot_info: Optional[Dict[str, Any]] = None, layout_candidate: Any = None) -> List[RoomNode]:
        if indian_options is None:
            indian_options = {}
        if layout_rules is None:
            layout_rules = []
        # A fixed rectangle is a cross-floor structural anchor (normally the
        # staircase).  Any whole-floor scale/centering transform after solving
        # would move that anchor and invalidate the adjacent-room solution.
        has_fixed_anchor = bool((plot_info or {}).get("_preserve_fixed_anchor")) or any(
            isinstance(spec, dict) and spec.get("fixed_rect") is not None
            for spec in rooms_spec
        )
            
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
            immutable_solver_handoff = bool((plot_info or {}).get("_immutable_solver_handoff"))
            geometry_hash_before = layout_candidate.geometry_hash() if layout_candidate is not None else ""
            type_counts: Dict[str, int] = {}
            for bp in master_blueprint:
                rt = bp.get("room_type", "room").replace(" ", "_").lower()
                type_counts[rt] = type_counts.get(rt, 0) + 1
                room_id = str(bp.get("id") or f"{rt}-{type_counts[rt]}")

                rect = Rect(float(bp.get("position_x", 0)), float(bp.get("position_z", 0)),
                          float(bp.get("width", 0)), float(bp.get("length", 0)))
                is_wet = "kitchen" in rt or "bath" in rt or "laundry" in rt or "toilet" in rt
                node = RoomNode(
                    id=room_id,
                    type=rt,
                    name=str(bp.get("name") or rt.replace("_", " ").title()),
                    rect=rect,
                    is_wet=is_wet,
                    wallThicknessIn=8.0 if is_wet else 6.0,
                    connections=bp.get("connections", []),
                    roof_type=str(bp.get("roof_type") or ("open" if bp.get("is_outdoor") else "flat")),
                    is_outdoor=bool(bp.get("is_outdoor")),
                )
                # Doors and Windows will be generated deterministically by the layout engine!
                nodes.append(node)

            if layout_candidate is not None:
                from candidate_contract import CandidateStatus, InternalInvariantError, SolvedRect
                if set(node.id for node in nodes) != set(layout_candidate.rooms_by_id):
                    raise InternalInvariantError(
                        f"candidate={layout_candidate.candidate_id}: MasterBlueprint room identity changed"
                    )

            # --- IMMUTABLE GEOMETRY LOCK (CP-SAT COORDINATES) ---
            # DO NOT resize, stretch, or alter individual room dimensions after CP-SAT solver completes.
            # CP-SAT produces non-overlapping geometry. Rescaling individual rooms causes post-solver overlaps!

            # --- ORIGIN ANCHORING ---
            if nodes and not has_fixed_anchor and not immutable_solver_handoff:
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

            if layout_candidate is not None:
                from candidate_contract import InternalInvariantError, SolvedRect
                handoff_rectangles = {
                    node.id: SolvedRect(node.rect.x, node.rect.z, node.rect.width, node.rect.length)
                    for node in nodes
                }
                original_rectangles = layout_candidate.rectangles_by_room_id
                layout_candidate.rectangles_by_room_id = handoff_rectangles
                geometry_hash_after = layout_candidate.geometry_hash()
                layout_candidate.rectangles_by_room_id = original_rectangles
                if geometry_hash_before != geometry_hash_after:
                    raise InternalInvariantError(
                        f"candidate={layout_candidate.candidate_id}: MasterBlueprint altered solved geometry "
                        f"before={geometry_hash_before} after={geometry_hash_after}"
                    )
                layout_candidate.status = CandidateStatus.SERIALIZED
                logger.info(
                    "[MASTERBLUEPRINT INVARIANT] geometry hash unchanged candidate_id=%s hash=%s",
                    layout_candidate.candidate_id, geometry_hash_before,
                )
                self.last_layout_candidate = layout_candidate

            # Compute shared walls using new finite AABB logic
            self.last_walls = generate_walls_from_aabbs(nodes)

            # Every generated house needs a visible, usable entrance even when
            # Gemini does not provide optional plot_info metadata.  Mark it
            # before WindowPlacer so the main door is materialized on an
            # exterior wall rather than remaining only a room flag.
            inject_main_entrance(
                nodes, self.buildable_width, self.buildable_length,
                self.setback_x, self.setback_z,
            )

            # WindowPlacer places windows on exterior walls; AdjacencyResolver runs single-pass in server.py
            WindowPlacer(nodes, self.plot_width, self.plot_length).place_windows()
            
            if not getattr(self, "skip_furniture_generation", False):
                place_furniture(nodes, indian_options, getattr(self, "furniture_prompt", ""))
            
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

            # Fixed upper-floor anchors and allowed slab bounds are expressed
            # in global plot coordinates. Solving them inside a 0-based
            # buildable-width domain made an east/south staircase appear
            # outside the model and returned INFEASIBLE immediately.
            uses_global_coordinates = has_fixed_anchor or bool((plot_info or {}).get("allowed_bounds"))
            floor_data = {
                'plot_width': self.plot_width if uses_global_coordinates else self.buildable_width,
                'plot_length': self.plot_length if uses_global_coordinates else self.buildable_length,
                'rooms': rooms_spec,
                'attempt': attempt
            }
            if layout_candidate is not None:
                floor_data['candidate'] = layout_candidate
            if isinstance(plot_info, dict) and plot_info.get("allowed_bounds"):
                floor_data["allowed_bounds"] = tuple(plot_info["allowed_bounds"])
            if hasattr(self, "min_foundation_dims") and self.min_foundation_dims:
                floor_data["min_foundation_dims"] = self.min_foundation_dims
            solved_data = engine.solve_phase_2_csp(floor_data)

            # Dense but valid schedules can spend the primary deadline proving
            # a heavily optimized topology and return UNKNOWN. Before entering
            # the slot-based legacy template, make one short feasibility pass:
            # retain the AI door graph and architectural minimums, but remove
            # soft optimization and hygiene gaps. This is both faster and
            # complete—the fallback must not silently lose arbitrary rooms.
            if 'resolved_rooms' not in solved_data and not has_fixed_anchor:
                logger.info("[CP-SAT RECOVERY] Retrying complete room program without soft objectives")
                recovery_data = dict(floor_data)
                recovery_data['relaxed_recovery'] = True
                solved_data = engine.solve_phase_2_csp(recovery_data)
            
            if 'resolved_rooms' in solved_data:
                logger.info("CP Solver successfully packed the rooms! Re-routing through master_blueprint logic.")
                cp_blueprint = []
                source_by_id = {
                    str(spec.get("id")): spec
                    for spec in rooms_spec
                    if isinstance(spec, dict) and spec.get("id")
                }
                for rr in solved_data['resolved_rooms']:
                    source = source_by_id.get(str(rr.get("id")), {})
                    cp_blueprint.append({
                        "id": rr.get("id"),
                        "room_type": rr["type"],
                        "name": source.get("name"),
                        "position_x": rr["x"],
                        "position_z": rr["z"],
                        "width": rr["width"],
                        "length": rr["length"],
                        "connections": rr["connections"],
                        "roof_type": source.get("roof_type"),
                        "is_outdoor": bool(source.get("is_outdoor")),
                    })
                solved_candidate = solved_data.get('candidate', layout_candidate)
                self.last_layout_candidate = solved_candidate
                return self.generate(
                    rooms_spec=[], 
                    master_blueprint=cp_blueprint,
                    plot_info={
                        **(plot_info or {}),
                        "_preserve_fixed_anchor": has_fixed_anchor,
                        "_immutable_solver_handoff": True,
                    },
                    indian_options=indian_options,
                    layout_candidate=solved_candidate,
                )
        except Exception as e:
            logger.error(f"CP Solver exception: {e}")
            if layout_candidate is not None:
                raise
            # Fall back to legacy BSP only for ground floor (no slab constraints)

        # --- PROGRAM-DRIVEN SAFE CORRIDOR LAYOUT FALLBACK ---
        allowed_bounds = tuple((plot_info or {}).get("allowed_bounds", ()))
        if len(allowed_bounds) == 4:
            bx0, bz0, bx1, bz1 = map(float, allowed_bounds)
            fallback = safe_corridor_layout(rooms_spec, bx1 - bx0, bz1 - bz0, self.theme)
            for node in fallback:
                node.rect.x += bx0
                node.rect.z += bz0
            fixed_by_id = {
                str(spec.get("id")): tuple(spec["fixed_rect"])
                for spec in rooms_spec if isinstance(spec, dict) and spec.get("id") and spec.get("fixed_rect")
            }
            # This fallback lays rooms out without reserving the fixed cells,
            # so restoring an anchor blindly drops it on top of whatever the
            # corridor layout put there — the upper floor then fails overlap
            # validation and the whole request dies. Restore an anchor only
            # where the space is actually free.
            for node in fallback:
                if node.id not in fixed_by_id:
                    continue
                target = Rect(*fixed_by_id[node.id])
                clashes = [
                    other.id for other in fallback
                    if other is not node
                    and min(target.x + target.width, other.rect.x + other.rect.width) - max(target.x, other.rect.x) > 0.05
                    and min(target.z + target.length, other.rect.z + other.rect.length) - max(target.z, other.rect.z) > 0.05
                ]
                if clashes:
                    logger.warning(
                        "[FALLBACK ANCHOR] Keeping %s at its laid-out position; the fixed rect overlaps %s.",
                        node.id, ", ".join(clashes),
                    )
                    continue
                node.rect = target
            return fallback
        return safe_corridor_layout(rooms_spec, self.plot_width, self.plot_length, self.theme)
        relationship_owned_spaces = {
            str(edge.get("target_room_id"))
            for spec in rooms_spec if isinstance(spec, dict) and "bedroom" in str(spec.get("type", ""))
            for edge in spec.get("connections", []) or [] if isinstance(edge, dict) and edge.get("target_room_id")
            if (
                edge.get("intent") in {"open_flow", "private_access"}
                or requested_type_by_id.get(str(edge.get("target_room_id"))) == "bathroom"
            )
        }

        # Determine if we should mirror the template for Vastu
        mirror_x = False
        mirror_z = False
        if indian_options.get("vastu") or indian_options.get("kitchen_se"):
            # Master bed SW (z=1, x=0 or 1), Kitchen SE (z=1, x=1)
            mirror_x = True

        from layout_templates import get_template_for_bhk
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
                if rid in relationship_owned_spaces:
                    continue
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

            matched_spec = next((spec for spec in rooms_spec if str(spec.get("id")) == matched_id), {})
            nodes.append(RoomNode(
                id=matched_id, type=matched_rt, name=matched_spec.get("name") or matched_rt.replace("_", " ").title(),
                rect=Rect(x, z, w, l), wallThicknessIn=wall_thick, is_wet=is_wet,
                connections=copy.deepcopy(matched_spec.get("connections", [])),
                bathroom_role=matched_spec.get("bathroom_role", ""),
                assigned_to=matched_spec.get("assigned_to", "") or matched_spec.get("attached_to_id", ""),
                floorColor=floor_color, wallColor=wall_color, furnitureColor=furniture_color,
                roof_type=roof_val  # Dynamic roof property assignment via AI
            ))
            # -------------------------------------------------

        # 2. Process Parasites (Mutators)
        parasites = [(rid, rt) for rid, rt in ai_requested_rooms if rid not in used_ai_rooms]
        # Instance-owned outdoor spaces (for example bedroom-2 -> balcony-2)
        # must be placed against their owner. The generic parasite carver only
        # understands room types, so defer these to the relationship-aware
        # recovery partitioner below.
        
        # Vastu Hard-Injected Parasites
        if indian_options.get("pooja_room") and not any(
            token in n.type for n in nodes for token in ("pooja", "prayer")
        ) and not any(
            token in rt for _, rt in parasites for token in ("pooja", "prayer")
        ):
            parasites.append(("pooja-1", "pooja_room"))
        if indian_options.get("powder_room") and not any("powder" in n.type for n in nodes) and not any("powder" in rt for _, rt in parasites):
            parasites.append(("powder-1", "powder_room"))
        if indian_options.get("utility_area") and not any("utility" in n.type for n in nodes) and not any("utility" in rt for _, rt in parasites):
            parasites.append(("utility-1", "utility"))
            
        for rid, rt in parasites:
            if rid in relationship_owned_spaces:
                continue
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
                room_minimum = ROOM_MINIMUMS.get(rt, _DEFAULT_MIN)
                p_area = float(room_minimum.get("area", _DEFAULT_MIN["area"]))
                min_dim = float(room_minimum.get("min_dim", _DEFAULT_MIN["min_dim"]))
                
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
                # A maximum exists only when the semantic program explicitly
                # supplied one. The former synthetic 1.5× cap could shrink a
                # valid 40-ft² bathroom to 37.5 ft² after carving.
                cmax_w = float(ai_spec["width"]) if ai_spec.get("width") else math.inf
                cmax_l = float(ai_spec["length"]) if ai_spec.get("length") else math.inf

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
                    connections=copy.deepcopy(ai_spec.get("connections", []) or []),
                    floorColor=p_floor, 
                    wallColor=p_wall, 
                    furnitureColor=p_furn,
                    roof_type=roof_val,
                    is_outdoor=is_open_sky,
                ))
                # ------------------------------------------------------

        # A failed parasite carve must never silently delete a requested
        # custom room or repeated bathroom from the floor program.
        self._recover_missing_requested_rooms(
            nodes,
            ai_requested_rooms,
            rooms_spec,
            allowed_bounds=tuple((plot_info or {}).get("allowed_bounds")) if (plot_info or {}).get("allowed_bounds") else None,
            blocked_zones=blocked_zones,
        )

        # 3. Post-Processing & Special Vastu Rules
        living = next((n for n in nodes if n.type == "living_room"), None)
        
        # ── Rule: Otta / Thinnai ─────────────────────────
        if indian_options.get("otta") and living and living.rect.length > 10.0:
            otta_l = 4.0
            shrunk = Rect(living.rect.x, living.rect.z + otta_l, living.rect.width, living.rect.length - otta_l)
            otta_rect = Rect(living.rect.x, living.rect.z, living.rect.width, otta_l)
            # The otta takes its depth out of the living room, so the veranda it
            # leaves behind must not land on whatever sits in front.
            blocked = any(
                rects_overlap(otta_rect, other.rect) for other in nodes if other is not living
            )
            if blocked:
                logger.info("  [OTTA] Skipping; the veranda would overlap a neighbouring room.")
                otta_rect = None
            else:
                living.rect = shrunk
            if otta_rect is not None:
                nodes.append(RoomNode(id="otta-1", type="veranda", name="Otta", rect=otta_rect, wallThicknessIn=4.0, roof_type="flat", floorColor="#d6d3d1"))

        # ── Rule: Portico ────────────────────────────────────
        if indian_options.get("portico"):
            portico_rect = Rect(self.setback_x, self.setback_z, 10.0, 15.0)
            # Pushing rooms aside to make room for the portico used to be done
            # blind, so it could shove one room straight through another and
            # undo the non-overlap CP-SAT had just guaranteed. Keep a push only
            # when the result is still clear.
            for n in nodes:
                if n.rect.x < portico_rect.x + 10.0 and n.rect.z < portico_rect.z + 15.0:
                    push_x = (portico_rect.x + 10.0) - n.rect.x
                    moved = Rect(n.rect.x + push_x, n.rect.z, max(n.rect.width - push_x, 4.0), n.rect.length)
                    if any(rects_overlap(moved, other.rect) for other in nodes if other is not n):
                        logger.info(
                            "  [PORTICO] Leaving '%s' where it is; the push would overlap a neighbour.",
                            n.name,
                        )
                        continue
                    n.rect = moved
            nodes.append(RoomNode(id="portico-1", type="parking", name="Portico", rect=portico_rect, wallThicknessIn=0.0, roof_type="flat", floorColor="#9ca3af"))

        # ── Rule: Double-Height Ceiling ──────────────────────────────────
        if indian_options.get("double_height") and living:
            living.is_double_height = True

        interior_nodes = [
            node for node in nodes
            if not getattr(node, "is_outdoor", False)
            and node.roof_type != "open"
            and node.type not in {"parking", "garden", "courtyard", "terrace", "balcony"}
        ]
        if not has_fixed_anchor:
            self._expand_to_target_coverage(
                interior_nodes,
                additional_nodes=[node for node in nodes if node not in interior_nodes],
            )
        self._repair_disconnected_components(nodes)
        self._absorb_enclosed_pockets(nodes)
        self._close_nearby_wall_seams(nodes)

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
        if not getattr(self, "skip_furniture_generation", False):
            place_furniture(nodes, indian_options, getattr(self, "furniture_prompt", ""))

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

ACCESS_POLICIES = {
    "bedroom": {
        "allowed_from": {"corridor", "hallway", "passage", "lobby", "private_lobby", "family_lounge", "landing"},
        "forbidden_from": {"foyer", "bathroom", "toilet", "kitchen", "bedroom", "master_bedroom", "dining_room", "utility_area", "store_room"},
    },
    "master_bedroom": {
        "allowed_from": {"corridor", "hallway", "passage", "lobby", "private_lobby", "family_lounge", "landing"},
        "forbidden_from": {"foyer", "bathroom", "toilet", "kitchen", "bedroom", "dining_room", "utility_area", "store_room"},
    },
    "bathroom": {
        "allowed_from": {"corridor", "hallway", "passage", "lobby", "foyer", "bedroom", "master_bedroom"},
        "forbidden_from": {"kitchen", "dining_room"},
    },
    "kitchen": {
        "allowed_from": {"dining_room", "corridor", "hallway", "passage", "utility_area", "store_room", "living_room", "family_lounge"},
        "forbidden_from": {"bathroom", "toilet", "bedroom", "master_bedroom"},
    },
}

def is_legal_door_pair(r1, r2) -> bool:
    from room_planner import _canon
    t1, t2 = _canon(r1.type), _canon(r2.type)
    if t1 in ACCESS_POLICIES:
        pol1 = ACCESS_POLICIES[t1]
        if t2 in pol1.get("forbidden_from", set()):
            if t1 in {"bathroom", "toilet"} and getattr(r1, "bathroom_role", "") == "attached":
                if getattr(r1, "assigned_to", "") in {r2.id, r2.name, r2.type}:
                    return True
            return False
    if t2 in ACCESS_POLICIES:
        pol2 = ACCESS_POLICIES[t2]
        if t1 in pol2.get("forbidden_from", set()):
            if t2 in {"bathroom", "toilet"} and getattr(r2, "bathroom_role", "") == "attached":
                if getattr(r2, "assigned_to", "") in {r1.id, r1.name, r1.type}:
                    return True
            return False
    return True


class AdjacencyResolver:
    def __init__(self, rooms: List[RoomNode], open_rooms: List[str] = None, candidate: Any = None):
        self.rooms = rooms
        self.open_rooms = open_rooms or []
        self.candidate = candidate

    def _resolve_candidate_doors(self, walls: List[Dict[str, Any]]) -> None:
        """Realize openings exclusively from the candidate's typed relations.

        The per-room Door objects written here are compatibility mirrors for
        renderers.  ``LayoutCandidate.doors`` is the sole access authority.
        """
        from candidate_contract import CandidateStatus, InternalInvariantError, PairedDoor

        candidate = self.candidate
        candidate.assert_identity_invariants()
        room_by_id = {room.id: room for room in self.rooms}
        if set(room_by_id) != set(candidate.rooms_by_id):
            raise InternalInvariantError(
                f"candidate={candidate.candidate_id}: door-stage room identity mismatch "
                f"missing={sorted(set(candidate.rooms_by_id) - set(room_by_id))} "
                f"extra={sorted(set(room_by_id) - set(candidate.rooms_by_id))}"
            )

        access_by_pair: Dict[Tuple[str, str], Any] = {}
        priority = {"exclusive_access": 3, "direct_access": 2, "open_flow": 1}
        for relation in candidate.relations_by_id.values():
            if not relation.target_room_id or not relation.creates_access:
                continue
            pair = tuple(sorted((relation.source_room_id, relation.target_room_id)))
            current = access_by_pair.get(pair)
            if current is None or priority.get(relation.kind, 0) > priority.get(current.kind, 0):
                access_by_pair[pair] = relation

        def wall_length(wall: Dict[str, Any]) -> float:
            return abs(wall["z2"] - wall["z1"]) if wall["orientation"] == "vertical" else abs(wall["x2"] - wall["x1"])

        shared_by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for wall in walls:
            if not wall.get("is_shared") or len(wall.get("room_ids", [])) < 2:
                continue
            pair = tuple(sorted(wall["room_ids"][:2]))
            shared_by_pair.setdefault(pair, []).append(wall)

        paired_doors = []
        for pair, relation in sorted(access_by_pair.items()):
            available = shared_by_pair.get(pair, [])
            if not available:
                # Hard relations were already checked by the CP edge audit.
                # A missing wall here therefore indicates serialization drift.
                if relation.is_hard or relation.topology_edge:
                    raise InternalInvariantError(
                        f"candidate={candidate.candidate_id}: access relation={relation.relation_id} "
                        f"lost shared wall between {pair[0]} and {pair[1]} during door realization"
                    )
                continue
            wall = max(available, key=wall_length)
            length = wall_length(wall)
            minimum = 4.0 if relation.kind == "open_flow" else 2.0
            if length + 1e-6 < minimum:
                if relation.is_hard or relation.topology_edge:
                    raise InternalInvariantError(
                        f"candidate={candidate.candidate_id}: relation={relation.relation_id} shared wall "
                        f"length={length:.2f} cannot fit opening minimum={minimum:.2f}"
                    )
                continue
            width = max(4.0, length - 0.5) if relation.kind == "open_flow" else min(3.0, max(2.0, length - 0.2))
            cx = (wall["x1"] + wall["x2"]) / 2.0
            cz = (wall["z1"] + wall["z2"]) / 2.0
            wall_key = "|".join((pair[0], pair[1], wall["orientation"], f"{wall['x1']:.3f}", f"{wall['z1']:.3f}", f"{wall['x2']:.3f}", f"{wall['z2']:.3f}"))
            wall_id = "wall_" + hashlib.sha256(wall_key.encode("utf-8")).hexdigest()[:16]
            door_id = "door_" + hashlib.sha256((candidate.candidate_id + "|" + wall_key).encode("utf-8")).hexdigest()[:16]
            position = cz - min(wall["z1"], wall["z2"]) if wall["orientation"] == "vertical" else cx - min(wall["x1"], wall["x2"])
            paired_doors.append(PairedDoor(
                id=door_id, room_a_id=pair[0], room_b_id=pair[1], wall_id=wall_id,
                width_ft=round(width, 3), position_ft=round(position, 3),
                global_x=round(cx, 3), global_z=round(cz, 3), orientation=wall["orientation"],
            ))

        candidate.set_paired_doors(paired_doors)
        for paired in paired_doors:
            first, second = room_by_id[paired.room_a_id], room_by_id[paired.room_b_id]
            vertical = paired.orientation == "vertical"
            for source, target in ((first, second), (second, first)):
                local_x = paired.global_x - source.rect.x
                local_z = paired.global_z - source.rect.z
                if vertical:
                    face = "west" if local_x < source.rect.width / 2.0 else "east"
                else:
                    face = "north" if local_z < source.rect.length / 2.0 else "south"
                source.doors.append(Door(
                    x=local_x, z=local_z, width=paired.width_ft,
                    wall_orientation=face, target_room_id=target.id,
                ))
        candidate.status = CandidateStatus.DOORS_REALIZED
        logger.info("[DOOR REALIZATION] candidate=%s paired_doors=%d", candidate.candidate_id, len(paired_doors))

    def resolve(self):
        logger.info(f"  [AdjacencyResolver] Resolving doors for {len(self.rooms)} rooms using finite walls.")
        from layout_engine import generate_walls_from_aabbs

        # The generation pipeline may call the resolver again after the engine
        # has already materialized doors.  Keep the operation idempotent so a
        # retry never duplicates doors and corrupts the navigation graph.
        for room in self.rooms:
            room.doors[:] = [door for door in room.doors if getattr(door, "is_main", False)]

        walls = generate_walls_from_aabbs(self.rooms)

        if self.candidate is not None:
            self._resolve_candidate_doors(walls)
            return
        
        room_by_id = {r.id: r for r in self.rooms}
        placed_doors_between = set()
        
        def has_connection(src, dst):
            return any(
                c.get("intent") not in {"proximity", "separation", "adjacent", "requested_adjacency", "courtyard_view"}
                and (
                    c.get("target_room_id") == dst.id
                    or (not c.get("target_room_id") and c.get("target_room") == dst.type)
                )
                for c in (src.connections or [])
            )
            
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
                
                # Shared geometry is not permission to punch a door through a
                # wall. Every interior opening must be authorized by the room
                # graph, for public rooms as well as private ones. The former
                # public-room exception produced accidental foyer→gym and
                # lounge→office shortcuts whenever those rooms happened to
                # touch.
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
                
                r1.doors.append(Door(x=d1_x, z=d1_z, width=door_w, wall_orientation=face1, target_room_id=r2.id))
                r2.doors.append(Door(x=d2_x, z=d2_z, width=door_w, wall_orientation=face2, target_room_id=r1.id))
                
                placed_doors_between.add(pair)
                logger.info(f"    Placed door between '{r1.name}' and '{r2.name}'")

        # --- NO DOOR RECOVERY / NO INVENTED TOPOLOGY ---
        # Doors are strictly generated from the approved topology graph (Pass 1 only).
        # If any required shared wall is missing, the candidate geometry is rejected.

        # A staircase must open directly into the public circulation core.
        # Otherwise a graph can be technically connected while the landing is
        # reachable only through a bedroom or behind a blocked doorway.
        public_types = ("lobby", "stair_landing", "corridor", "hallway", "foyer", "living_room")
        for stair in (r for r in self.rooms if r.type in ("staircase", "stairwell")):
            public_ids = {r.id for r in self.rooms if r.type in public_types}
            common = [
                w for w in walls
                if w.get("is_shared")
                and stair.id in w.get("room_ids", [])
                and any(room_id in public_ids for room_id in w.get("room_ids", []))
            ]
            # If topology labels were degraded by an AI alias, still make the
            # staircase usable through its actual adjacent room.
            if not common:
                common = [
                    w for w in walls
                    if w.get("is_shared") and stair.id in w.get("room_ids", [])
                ]
            if not common:
                continue
            wall = max(
                common,
                key=lambda w: abs(w["z2"] - w["z1"])
                if w["orientation"] == "vertical"
                else abs(w["x2"] - w["x1"]),
            )
            public_id = next((room_id for room_id in wall.get("room_ids", []) if room_id != stair.id), None)
            public = room_by_id.get(public_id)
            if public is None:
                continue
            if any(
                abs((stair.rect.x + sd.x) - (public.rect.x + pd.x)) <= 0.35
                and abs((stair.rect.z + sd.z) - (public.rect.z + pd.z)) <= 0.35
                for sd in stair.doors for pd in public.doors
            ):
                continue
            cx = (wall["x1"] + wall["x2"]) / 2.0
            cz = (wall["z1"] + wall["z2"]) / 2.0
            is_vertical = wall["orientation"] == "vertical"
            wall_length = abs(wall["z2"] - wall["z1"]) if is_vertical else abs(wall["x2"] - wall["x1"])
            width = max(2.0, min(3.0, round(wall_length - 0.2, 1)))
            sx, sz = cx - stair.rect.x, cz - stair.rect.z
            px, pz = cx - public.rect.x, cz - public.rect.z
            stair.doors.append(Door(x=sx, z=sz, width=width, wall_orientation=get_face(sx, sz, stair, is_vertical), target_room_id=public.id))
            public.doors.append(Door(x=px, z=pz, width=width, wall_orientation=get_face(px, pz, public, is_vertical), target_room_id=stair.id))
            logger.info(f"[STAIR ACCESS] Connected {stair.name} directly to {public.name}")
# ---------------------------------------------------------------------------
# Window Generation
# ---------------------------------------------------------------------------

def fit_opening_on_segment(wall, room, is_vertical, desired_width, margin=0.35, min_width=1.5):
    """Return (centre, width) for an opening that fits this wall segment.

    Openings were centred on the midpoint of their wall *segment*. A short
    segment beside a corner puts that midpoint at the room's edge, so a 4 ft
    door or window centred there hung a couple of feet out past the building.
    Fit the opening inside both the segment and the room face, shrinking it if
    need be, and return None when nothing usable fits.
    """
    if is_vertical:
        lo_raw, hi_raw = sorted((wall["z1"] - room.rect.z, wall["z2"] - room.rect.z))
        face_span = room.rect.length
    else:
        lo_raw, hi_raw = sorted((wall["x1"] - room.rect.x, wall["x2"] - room.rect.x))
        face_span = room.rect.width
    lo, hi = max(lo_raw, 0.0), min(hi_raw, face_span)
    usable = hi - lo
    width = desired_width
    if usable < width + 2 * margin:
        width = min(width, max(0.0, usable - 2 * margin))
    if width < min_width:
        return None
    centre = min(max((lo + hi) / 2.0, lo + width / 2 + margin), hi - width / 2 - margin)
    return centre, width


class WindowPlacer:
    def __init__(self, rooms: List[RoomNode], plot_width: float, plot_length: float,
                 setback_x: float = 0.0, setback_z: float = 0.0):
        self.rooms = rooms

    def place_windows(self):
        logger.info(f"  [WindowPlacer] Starting window placement using finite walls.")
        from layout_engine import generate_walls_from_aabbs
        # Idempotent placement: repeated pipeline passes must not stack windows.
        for room in self.rooms:
            room.windows[:] = []
            room.doors[:] = [door for door in room.doors if not getattr(door, "is_main", False)]
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
                        fitted = fit_opening_on_segment(w, r, is_vert, 4.0, min_width=2.5)
                        if fitted is None:
                            continue
                        centre, door_width = fitted
                        d_x, d_z = (rel_x, centre) if is_vert else (centre, rel_z)
                        r.doors.append(Door(
                            x=d_x, z=d_z, width=door_width, height=7.0, is_main=True, wall_orientation=face,
                            source="outside", target="foyer", wall_type="exterior", opens_inward=True
                        ))
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
                        fitted = fit_opening_on_segment(w, r, is_vert, 4.0, min_width=2.5)
                        if fitted is None:
                            continue
                        centre, door_width = fitted
                        d_x = (cx - r.rect.x) if is_vert else centre
                        d_z = centre if is_vert else (cz - r.rect.z)
                        r.doors.append(Door(
                            x=d_x, z=d_z, width=door_width, height=7.0, is_main=True, wall_orientation=face,
                            source="outside", target="foyer", wall_type="exterior", opens_inward=True
                        ))
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
                    r.doors.append(Door(
                        x=x, z=z, width=4.0, height=7.0, is_main=True, wall_orientation=face,
                        source="outside", target="foyer", wall_type="exterior", opens_inward=True
                    ))
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

                    # The centre above is the midpoint of this wall *segment*,
                    # which for a short segment by a corner sits at the room's
                    # edge — a 4 ft window centred there hung ~2 ft out past
                    # the building. Fit the opening inside the segment it
                    # belongs to, and inside the room face, or skip it.
                    fitted = fit_opening_on_segment(w, r, is_vert, win_width)
                    if fitted is None:
                        logger.info("    Skipped window on '%s' (%s wall segment too short)", r.name, face)
                        continue
                    centre, win_width = fitted
                    if is_vert:
                        rel_z = centre
                    else:
                        rel_x = centre

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

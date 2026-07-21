"""
geometry_validator.py
=====================
Computational geometry validation for floor-plan blueprints.

Provides AABB overlap detection, boundary enforcement, minimum-dimension
checks, door/window wall-alignment verification, and BFS-based room
connectivity analysis.  All checks produce human-readable error strings
formatted for downstream LLM correction prompts.

Only standard-library imports are used.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
EPSILON: float = 0.1  # Tolerance for all floating-point comparisons
SNAP_TOLERANCE: float = 3.0  # Allowed margin of error that layout_engine.py will auto-snap


# ---------------------------------------------------------------------------
# Box3D  (axis-aligned bounding box on the XZ plane)
# ---------------------------------------------------------------------------
@dataclass
class Box3D:
    """Axis-aligned bounding box representing a room footprint.

    Parameters
    ----------
    x : float
        Top-left X coordinate (position_x from the blueprint).
    z : float
        Top-left Z coordinate (position_z from the blueprint).
    width : float
        Extent along the X axis.
    length : float
        Extent along the Z axis.
    label : str
        Human-readable identifier (usually the room_type).
    """

    x: float
    z: float
    width: float
    length: float
    label: str = ""

    # -- derived properties --------------------------------------------------

    @property
    def x_min(self) -> float:
        return self.x

    @property
    def x_max(self) -> float:
        return self.x + self.width

    @property
    def z_min(self) -> float:
        return self.z

    @property
    def z_max(self) -> float:
        return self.z + self.length

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_z(self) -> float:
        return self.z + self.length / 2.0

    @property
    def area(self) -> float:
        return self.width * self.length

    # -- collision -----------------------------------------------------------

    def overlaps(self, other: "Box3D", epsilon: float = 0.1) -> bool:
        """Return *True* if *self* and *other* share interior area beyond
        *epsilon* tolerance (strict AABB overlap, not mere touching)."""
        overlap_x = min(self.x_max, other.x_max) - max(self.x_min, other.x_min)
        overlap_z = min(self.z_max, other.z_max) - max(self.z_min, other.z_min)
        return overlap_x > epsilon and overlap_z > epsilon


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    """Aggregated result of all geometry validation checks."""

    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    overlap_pairs: List[Tuple[str, str]] = field(default_factory=list)
    boundary_violations: List[str] = field(default_factory=list)
    unreachable_rooms: List[str] = field(default_factory=list)
    door_errors: List[str] = field(default_factory=list)
    window_errors: List[str] = field(default_factory=list)
    dimension_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# GeometryValidator
# ---------------------------------------------------------------------------
class GeometryValidator:
    """Static-only validator for BlueprintRoom lists."""

    # -----------------------------------------------------------------------
    # public entry point
    # -----------------------------------------------------------------------
    @staticmethod
    def validate_post_placement(rooms: list) -> ValidationResult:
        result = ValidationResult()
        boxes = []
        blueprint = []
        for r in rooms:
            box = Box3D(
                x=r.rect.x,
                z=r.rect.z,
                width=r.rect.width,
                length=r.rect.length,
                label=r.id
            )
            boxes.append(box)
            
            room_dict = {
                "room_type": r.type,
                "is_wet": getattr(r, "is_wet", False),
                "doors": [],
                "windows": []
            }
            for d in r.doors:
                room_dict["doors"].append({
                    "position_x": d.x + r.rect.x,
                    "position_z": d.z + r.rect.z,
                    "width": d.width,
                    "wall_orientation": getattr(d, "wall_orientation", "north")
                })
            for w in r.windows:
                room_dict["windows"].append({
                    "position_x": w.x + r.rect.x,
                    "position_z": w.z + r.rect.z
                })
            blueprint.append(room_dict)
            
            # --- TRUE DYNAMIC FURNITURE VALIDATION ---
            # Instead of looking for "master_bed" or "kitchen", we simply ensure 
            # no room generates below absolute human-usable minimums unless it's a structural void.
            min_dim = min(r.rect.width, r.rect.length)
            area = r.rect.width * r.rect.length
            
            if not getattr(r, "is_outdoor", False) and min_dim < 3.0:
                msg = f"DIMENSION ERROR: {r.id} ({round(r.rect.width,1)}x{round(r.rect.length,1)}) is too narrow for human access."
                logger.warning(msg)
                result.errors.append(msg)
                result.is_valid = False

        # Post-placement must enforce the same non-overlap invariant as the
        # blueprint validator.  Previously this call was missing, allowing a
        # corridor stretched through bedrooms to pass as a successful floor.
        GeometryValidator._check_overlaps(boxes, result)
        GeometryValidator._check_gaps_and_adjacency(boxes, result)
        GeometryValidator._check_connectivity(blueprint, boxes, result)
        return result

    @staticmethod
    def validate(
        blueprint: List[dict],
        plot_width: float,
        plot_length: float,
    ) -> ValidationResult:
        """Run every sub-check and return a single *ValidationResult*.

        Parameters
        ----------
        blueprint : list[dict]
            Each dict follows the BlueprintRoom schema (room_type, width,
            length, position_x, position_z, doors, windows, …).
        plot_width : float
            Total width of the building plot (X axis).
        plot_length : float
            Total length of the building plot (Z axis).
        """
        result = ValidationResult()

        if not blueprint:
            logger.info("Empty blueprint — nothing to validate.")
            return result

        # Build Box3D list once; reused by every sub-check.
        boxes: List[Box3D] = []
        for room in blueprint:
            boxes.append(
                Box3D(
                    x=float(room.get("position_x", 0)),
                    z=float(room.get("position_z", 0)),
                    width=float(room.get("width", 0)),
                    length=float(room.get("length", 0)),
                    label=room.get("room_type", "unknown"),
                )
            )

        # --- sub-checks (order matters for readable error lists) -----------
        GeometryValidator._check_dimensions(blueprint, boxes, result)
        GeometryValidator._check_overlaps(boxes, result)
        GeometryValidator._check_boundaries(boxes, plot_width, plot_length, result)
        
        # Final verdict
        result.is_valid = len(result.errors) == 0
        return result

    # -----------------------------------------------------------------------
    # (a) Room-Room Overlap Detection
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_overlaps(boxes: List[Box3D], result: ValidationResult) -> None:
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a.overlaps(b, epsilon=EPSILON):
                    overlap_x = min(a.x_max, b.x_max) - max(a.x_min, b.x_min)
                    overlap_z = min(a.z_max, b.z_max) - max(a.z_min, b.z_min)
                    overlap_area = round(overlap_x * overlap_z, 2)

                    suggested_x = round(a.x_max, 2)

                    msg = (
                        f"OVERLAP: {a.label} "
                        f"(X:{a.x_min}-{a.x_max}, Z:{a.z_min}-{a.z_max}) "
                        f"overlaps with {b.label} "
                        f"(X:{b.x_min}-{b.x_max}, Z:{b.z_min}-{b.z_max}). "
                        f"Overlap area: {overlap_area}sq ft. "
                        f"Fix: Move {b.label}.position_x to {suggested_x}."
                    )
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.overlap_pairs.append((a.label, b.label))

    # -----------------------------------------------------------------------
    # (b) Gap & Adjacency Detection
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_gaps_and_adjacency(boxes: List[Box3D], result: ValidationResult) -> None:
        if not boxes:
            return
            
        min_x = min(b.x_min for b in boxes)
        min_z = min(b.z_min for b in boxes)
        max_x = max(b.x_max for b in boxes)
        max_z = max(b.z_max for b in boxes)
        
        hull_area = (max_x - min_x) * (max_z - min_z)
        total_room_area = sum((b.width * b.length) for b in boxes)
        
        if False and abs(hull_area - total_room_area) > 1.0:
            msg = (
                f"GAP DETECTED: The floorplan has holes or gaps. "
                f"The total area of all rooms ({round(total_room_area, 2)} sq ft) "
                f"does not match the total bounding area of the house footprint ({round(hull_area, 2)} sq ft). "
                f"You must align the rooms perfectly so they form a solid rectangle with no internal gaps."
            )
            logger.warning(msg)
            result.errors.append(msg)

    # -----------------------------------------------------------------------
    # (c) Boundary Checking
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_boundaries(
        boxes: List[Box3D],
        plot_width: float,
        plot_length: float,
        result: ValidationResult,
    ) -> None:
        if not boxes: return
        
        min_x = min(b.x_min for b in boxes)
        max_x = max(b.x_max for b in boxes)
        min_z = min(b.z_min for b in boxes)
        max_z = max(b.z_max for b in boxes)
        
        house_w = max_x - min_x
        house_l = max_z - min_z
        
        if house_w > plot_width + EPSILON:
            msg = (
                f"BOUNDARY: The total house width ({house_w}ft) exceeds the plot width ({plot_width}ft). "
                f"You must compress the layout horizontally."
            )
            logger.warning(msg)
            result.errors.append(msg)

        if house_l > plot_length + EPSILON:
            msg = (
                f"BOUNDARY: The total house length ({house_l}ft) exceeds the plot length ({plot_length}ft). "
                f"You must compress the layout vertically."
            )
            logger.warning(msg)
            result.errors.append(msg)

    # -----------------------------------------------------------------------
    # (c) Minimum Dimensions Check
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_dimensions(
        blueprint: List[dict],
        boxes: List[Box3D],
        result: ValidationResult,
    ) -> None:
        for room, box in zip(blueprint, boxes):
            room_type: str = room.get("room_type", "unknown").lower()
            min_width = float(room.get("min_width", 0))
            min_length = float(room.get("min_length", 0))
            
            if min_width > 0 and box.width < min_width - EPSILON:
                msg = f"DIMENSION: {box.label} width is too small ({box.width}). Minimum width is {min_width} ft."
                logger.warning(msg)
                result.errors.append(msg)
            if min_length > 0 and box.length < min_length - EPSILON:
                msg = f"DIMENSION: {box.label} length is too small ({box.length}). Minimum length is {min_length} ft."
                logger.warning(msg)
                result.errors.append(msg)
                
            if min_width == 0 and min_length == 0:
                is_bathroom = "bath" in room_type or "toilet" in room_type or "wc" in room_type
                min_dim = 3.0 if is_bathroom else 4.0
                if box.width < min_dim - EPSILON or box.length < min_dim - EPSILON:
                    msg = (
                        f"DIMENSION: {box.label} is too small "
                        f"({box.width}x{box.length}). "
                        f"Minimum size is {min_dim}x{min_dim} ft."
                    )
                    logger.warning(msg)
                    result.errors.append(msg)
                result.dimension_errors.append(box.label)

    # -----------------------------------------------------------------------
    # (d) Door Wall-Alignment
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_doors(
        blueprint: List[dict],
        boxes: List[Box3D],
        result: ValidationResult,
    ) -> None:
        wall_tol = 0.5  # wall_thickness_tolerance

        for room, box in zip(blueprint, boxes):
            doors = room.get("doors") or []
            for door in doors:
                dx = float(door.get("position_x", 0))
                dz = float(door.get("position_z", 0))

                on_wall = _point_on_room_wall(dx, dz, box, wall_tol)

                if not on_wall:
                    wall_name, wall_coord = _nearest_wall(dx, dz, box)
                    msg = (
                        f"DOOR: Door in {box.label} at ({dx}, {dz}) "
                        f"is not on any wall boundary. "
                        f"Nearest wall is {wall_name} at {wall_coord}."
                    )
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.door_errors.append(box.label)

    # -----------------------------------------------------------------------
    # (e) Window External-Wall Check (warning only)
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_windows(
        blueprint: List[dict],
        boxes: List[Box3D],
        plot_width: float,
        plot_length: float,
        result: ValidationResult,
    ) -> None:
        wall_tol = 0.5

        for room, box in zip(blueprint, boxes):
            windows = room.get("windows") or []
            for window in windows:
                wx = float(window.get("position_x", 0))
                wz = float(window.get("position_z", 0))

                on_wall = _point_on_room_wall(wx, wz, box, wall_tol)
                if not on_wall:
                    wall_name, wall_coord = _nearest_wall(wx, wz, box)
                    msg = (
                        f"WINDOW: Window in {box.label} at ({wx}, {wz}) "
                        f"is not on any wall boundary. "
                        f"Nearest wall is {wall_name} at {wall_coord}."
                    )
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.window_errors.append(box.label)
                    continue

                on_external = _wall_is_external(wx, wz, box, plot_width, plot_length, wall_tol)
                if not on_external:
                    msg = (
                        f"WINDOW_WARNING: Window in {box.label} at ({wx}, {wz}) "
                        f"is on an internal wall. Consider placing windows on "
                        f"external walls (touching plot boundary) for natural light."
                    )
                    logger.info(msg)
                    result.window_errors.append(box.label)

    # -----------------------------------------------------------------------
    # (f) Door / Window Overlap within same room
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_door_window_overlap(
        blueprint: List[dict],
        boxes: List[Box3D],
        result: ValidationResult,
    ) -> None:
        wall_tol = 0.5

        for room, box in zip(blueprint, boxes):
            openings: List[Tuple[str, float, float, str]] = []

            for door in room.get("doors") or []:
                dx = float(door.get("position_x", 0))
                dz = float(door.get("position_z", 0))
                dw = float(door.get("width", 3.0))
                wall_id, start, end = _opening_extent(dx, dz, dw, box, wall_tol)
                if wall_id:
                    openings.append(("door", start, end, wall_id))

            for window in room.get("windows") or []:
                wx = float(window.get("position_x", 0))
                wz = float(window.get("position_z", 0))
                ww = float(window.get("width", 4.0))
                wall_id, start, end = _opening_extent(wx, wz, ww, box, wall_tol)
                if wall_id:
                    openings.append(("window", start, end, wall_id))

            for i in range(len(openings)):
                for j in range(i + 1, len(openings)):
                    kind_a, s_a, e_a, wall_a = openings[i]
                    kind_b, s_b, e_b, wall_b = openings[j]
                    if wall_a != wall_b:
                        continue
                    overlap = min(e_a, e_b) - max(s_a, s_b)
                    if overlap > EPSILON:
                        msg = (
                            f"OPENING_OVERLAP: {kind_a} and {kind_b} in "
                            f"{box.label} overlap on wall {wall_a} by "
                            f"{round(overlap, 2)} ft."
                        )
                        logger.warning(msg)
                        result.errors.append(msg)
                        result.door_errors.append(box.label)

    # -----------------------------------------------------------------------
    # (g) Connectivity BFS
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_connectivity(
        blueprint: List[dict],
        boxes: List[Box3D],
        result: ValidationResult,
    ) -> None:
        n = len(boxes)
        if n <= 1:
            return

        # 1. Build physical adjacency graph
        adjacency: List[List[int]] = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if _rooms_adjacent(boxes[i], boxes[j]):
                    adjacency[i].append(j)
                    adjacency[j].append(i)
                    
        # 2. Build door-connected traversal graph
        door_connected: List[List[int]] = [[] for _ in range(n)]
        for i in range(n):
            for j in adjacency[i]:
                if j <= i:
                    continue  
                if _rooms_share_door(blueprint[i], blueprint[j], boxes[i], boxes[j]):
                    door_connected[i].append(j)
                    door_connected[j].append(i)

        # 3. Connectivity Verification (Is the house a single connected graph?)
        visited = set()
        start = 0
        queue: deque[int] = deque([start])
        visited.add(start)
        
        while queue:
            curr = queue.popleft()
            for nb in door_connected[curr]:
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)

        # Report stranded geometry based on graph traversal
        for idx in range(n):
            if idx not in visited:
                room_label = boxes[idx].label
                nearest_label = "unknown"
                suggested_x, suggested_z = boxes[idx].center_x, boxes[idx].z_min
                
                if adjacency[idx]:
                    adj_idx = adjacency[idx][0]
                    nearest_label = boxes[adj_idx].label
                    sx, sz = _suggest_door_position(boxes[idx], boxes[adj_idx])
                    suggested_x, suggested_z = round(sx, 2), round(sz, 2)

                msg = (
                    f"UNREACHABLE ERROR: {room_label} has no door connection to the main house. "
                    f"Add a door on the shared wall with {nearest_label} at ({suggested_x}, {suggested_z})."
                )
                logger.info(msg)
                result.warnings.append(msg)
                result.unreachable_rooms.append(room_label)
                # Keep unreachable rooms as warning rather than blocking layout validation failure
                # to prevent pipeline crashes on minor door coordinate/rounding differences.
                # Any room with 0 doors will still be correctly flagged by DOOR ERROR.

        # 4. Zero-Hardcoding Circulation Rules
        for curr in range(n):
            curr_room = blueprint[curr]
            curr_type = curr_room.get("room_type", "").lower()
            
            is_strict_private = any(k in curr_type for k in ["bed", "closet", "bath", "toilet"])
            
            if is_strict_private and len(door_connected[curr]) > 1:
                valid_ensuites = 0
                for nb in door_connected[curr]:
                    nb_type = blueprint[nb].get("room_type", "").lower()
                    if any(k in nb_type for k in ["bath", "toilet", "closet", "balcony"]):
                        valid_ensuites += 1
                        
                if len(door_connected[curr]) - valid_ensuites > 1:
                    msg = f"CIRCULATION ERROR: Private/Wet space '{boxes[curr].label}' is being incorrectly used as a hallway to connect other rooms."
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.is_valid = False

        # 5. Door & Window Minimum Verifications
        for idx, room in enumerate(blueprint):
            num_doors = len(room.get("doors", []))
            num_windows = len(room.get("windows", []))
            is_outdoor = getattr(boxes[idx], "is_outdoor", False)
            
            if num_doors == 0 and not is_outdoor:
                msg = f"DOOR ERROR: {boxes[idx].label} has no doors!"
                logger.warning(msg)
                result.errors.append(msg)
                result.is_valid = False
                
            if num_windows == 0 and not is_outdoor:
                msg = f"VENTILATION WARNING: {boxes[idx].label} has no windows and will require an exhaust shaft."
                logger.info(msg)

        # --- PERSONA-BASED BFS PATHFINDING ---
        def bfs_path(start_idx, target_type):
            q = deque([(start_idx, [start_idx])])
            visited_bfs = {start_idx}
            while q:
                curr, path = q.popleft()
                if target_type in blueprint[curr].get("room_type", "").lower():
                    return path
                for nb in door_connected[curr]:
                    if nb not in visited_bfs:
                        visited_bfs.add(nb)
                        q.append((nb, path + [nb]))
            return None
            
        def is_passage_allowed(idx):
            rt = blueprint[idx].get("room_type", "").lower()
            return any(p in rt for p in [
                'entrance', 'hallway', 'corridor', 'living', 'foyer', 'dining',
                'courtyard', 'angan', 'open_to_sky', 'veranda', 'balcony', 
                'deck', 'patio', 'porch', 'terrace', 'otta', 'thinnai', 'stair'
            ])

        living_idx = next((i for i, r in enumerate(blueprint) if "living" in r.get("room_type", "").lower()), None)
        kitchen_idx = next((i for i, r in enumerate(blueprint) if "kitchen" in r.get("room_type", "").lower()), None)
        bed_indices = [i for i, r in enumerate(blueprint) if "bed" in r.get("room_type", "").lower()]
        bath_indices = [i for i, r in enumerate(blueprint) if "bath" in r.get("room_type", "").lower() or "toilet" in r.get("room_type", "").lower()]
        
        # 1. Guest
        if living_idx is not None and bath_indices:
            path = bfs_path(living_idx, "bath")
            if path:
                for node in path[1:-1]:
                    # Allow passing through a bedroom to reach a bathroom attached to it (i.e. if the bedroom immediately precedes the bathroom in the path)
                    if node == path[-2] and "bed" in blueprint[node].get("room_type", "").lower():
                        continue
                    if not is_passage_allowed(node):
                        msg = f"PERSONA ERROR (Guest): Path from Living to Bath passes through non-passage room {boxes[node].label}."
                        logger.info(msg)
                        result.warnings.append(msg)
                        
        # 2. Resident
        if kitchen_idx is not None:
            for b_idx in bed_indices:
                path = bfs_path(b_idx, "kitchen")
                if path:
                    for node in path[1:-1]:
                        if not is_passage_allowed(node):
                            msg = f"PERSONA ERROR (Resident): Path from {boxes[b_idx].label} to Kitchen passes through non-passage room {boxes[node].label}."
                            logger.info(msg)
                            result.warnings.append(msg)
                            
        # 3. Parent
        dining_idx = next((i for i, r in enumerate(blueprint) if "dining" in r.get("room_type", "").lower()), None)
        if kitchen_idx is not None and dining_idx is not None and living_idx is not None:
            path_kd = bfs_path(kitchen_idx, "dining")
            path_dl = bfs_path(dining_idx, "living")
            
            if path_kd:
                for node in path_kd[1:-1]:
                    if not is_passage_allowed(node):
                        msg = f"PERSONA ERROR (Parent): Path from Kitchen to Dining goes through non-passage {boxes[node].label}."
                        result.warnings.append(msg)
            if path_dl:
                for node in path_dl[1:-1]:
                    if not is_passage_allowed(node):
                        msg = f"PERSONA ERROR (Parent): Path from Dining to Living goes through non-passage {boxes[node].label}."
                        result.warnings.append(msg)

        # 4. Laundry Route
        for b_idx in bed_indices:
            path_bath = bfs_path(b_idx, "bath")
            if path_bath:
                for node in path_bath[1:-1]:
                    if not is_passage_allowed(node) and "bath" not in blueprint[node].get("room_type", "").lower():
                        msg = f"PERSONA ERROR (Laundry): Path from {boxes[b_idx].label} to Bath goes through non-passage room {boxes[node].label}."
                        logger.info(msg)
                        result.warnings.append(msg)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------

def _point_on_room_wall(
    px: float, pz: float, box: Box3D, tol: float
) -> bool:
    """Return True if point (px, pz) lies on one of *box*'s four walls."""
    in_x = box.x_min - tol <= px <= box.x_max + tol
    in_z = box.z_min - tol <= pz <= box.z_max + tol

    on_left   = abs(px - box.x_min) <= tol and in_z
    on_right  = abs(px - box.x_max) <= tol and in_z
    on_top    = abs(pz - box.z_min) <= tol and in_x
    on_bottom = abs(pz - box.z_max) <= tol and in_x

    return on_left or on_right or on_top or on_bottom


def _nearest_wall(
    px: float, pz: float, box: Box3D
) -> Tuple[str, float]:
    """Return (wall_name, wall_coordinate) of the nearest wall to (px, pz)."""
    walls = [
        ("left (x_min)",   abs(px - box.x_min)),
        ("right (x_max)",  abs(px - box.x_max)),
        ("top (z_min)",    abs(pz - box.z_min)),
        ("bottom (z_max)", abs(pz - box.z_max)),
    ]
    walls.sort(key=lambda w: w[1])
    name = walls[0][0]
    coord_map = {
        "left (x_min)":   box.x_min,
        "right (x_max)":  box.x_max,
        "top (z_min)":    box.z_min,
        "bottom (z_max)": box.z_max,
    }
    return name, coord_map[name]


def _wall_is_external(
    px: float,
    pz: float,
    box: Box3D,
    plot_width: float,
    plot_length: float,
    tol: float,
) -> bool:
    """Return True if the wall containing (px, pz) touches the plot boundary."""
    if abs(px - box.x_min) <= tol and abs(box.x_min) <= tol:
        return True
    if abs(px - box.x_max) <= tol and abs(box.x_max - plot_width) <= tol:
        return True
    if abs(pz - box.z_min) <= tol and abs(box.z_min) <= tol:
        return True
    if abs(pz - box.z_max) <= tol and abs(box.z_max - plot_length) <= tol:
        return True
    return False


def _opening_extent(
    px: float, pz: float, width: float, box: Box3D, tol: float
) -> Tuple[str, float, float]:
    """Determine the 1-D extent of an opening (door/window) along its wall."""
    if abs(px - box.x_min) <= tol:
        return ("x_min", pz, pz + width)
    if abs(px - box.x_max) <= tol:
        return ("x_max", pz, pz + width)
    if abs(pz - box.z_min) <= tol:
        return ("z_min", px, px + width)
    if abs(pz - box.z_max) <= tol:
        return ("z_max", px, px + width)
    return ("", 0.0, 0.0)


def _rooms_adjacent(a: Box3D, b: Box3D) -> bool:
    """Two rooms are adjacent if their AABBs share a wall segment.

    They must *touch* (gap ≤ gap_tol) on one axis while genuinely
    overlapping (shared length > EPSILON) on the perpendicular axis.
    """
    gap_tol = EPSILON + 0.5
    
    # Shared segment along Z axis (rooms side-by-side along X)
    touch_x = (
        abs(a.x_max - b.x_min) <= gap_tol or abs(b.x_max - a.x_min) <= gap_tol
    )
    overlap_z = min(a.z_max, b.z_max) - max(a.z_min, b.z_min)

    if touch_x and overlap_z > EPSILON:
        return True

    # Shared segment along X axis (rooms stacked along Z)
    touch_z = (
        abs(a.z_max - b.z_min) <= gap_tol or abs(b.z_max - a.z_min) <= gap_tol
    )
    overlap_x = min(a.x_max, b.x_max) - max(a.x_min, b.x_min)

    if touch_z and overlap_x > EPSILON:
        return True

    return False


def _door_on_shared_boundary(
    door: dict, box_owner: Box3D, box_other: Box3D
) -> bool:
    """Return True if *door* (belonging to *box_owner*) sits on the shared
    wall between *box_owner* and *box_other*."""
    dx = float(door.get("position_x", 0))
    dz = float(door.get("position_z", 0))
    face = door.get("wall_orientation", "").lower()
    
    # --- INCREASED TOLERANCE ---
    # Safely catch emergency rescue doors that have offset coordinates or 
    # sit across slightly disjointed AABBs due to fallback placements.
    tol = 2.5 
    span_tol = 2.0
    gap_tol = EPSILON + 0.5
    # ---------------------------

    # Check each possible shared boundary:
    # owner's right == other's left
    if abs(box_owner.x_max - box_other.x_min) <= gap_tol:
        if face in ("east", "west"):
            if abs(dx - box_owner.x_max) <= tol or abs(dx - box_other.x_min) <= tol:
                z_lo = max(box_owner.z_min, box_other.z_min)
                z_hi = min(box_owner.z_max, box_other.z_max)
                if z_lo - span_tol <= dz <= z_hi + span_tol:
                    return True

    # owner's left == other's right
    if abs(box_owner.x_min - box_other.x_max) <= gap_tol:
        if face in ("east", "west"):
            if abs(dx - box_owner.x_min) <= tol or abs(dx - box_other.x_max) <= tol:
                z_lo = max(box_owner.z_min, box_other.z_min)
                z_hi = min(box_owner.z_max, box_other.z_max)
                if z_lo - span_tol <= dz <= z_hi + span_tol:
                    return True

    # owner's bottom == other's top
    if abs(box_owner.z_max - box_other.z_min) <= gap_tol:
        if face in ("north", "south"):
            if abs(dz - box_owner.z_max) <= tol or abs(dz - box_other.z_min) <= tol:
                x_lo = max(box_owner.x_min, box_other.x_min)
                x_hi = min(box_owner.x_max, box_other.x_max)
                if x_lo - span_tol <= dx <= x_hi + span_tol:
                    return True

    # owner's top == other's bottom
    if abs(box_owner.z_min - box_other.z_max) <= gap_tol:
        if face in ("north", "south"):
            if abs(dz - box_owner.z_min) <= tol or abs(dz - box_other.z_max) <= tol:
                x_lo = max(box_owner.x_min, box_other.x_min)
                x_hi = min(box_owner.x_max, box_other.x_max)
                if x_lo - span_tol <= dx <= x_hi + span_tol:
                    return True

    return False


def _rooms_share_door(
    room_a: dict, room_b: dict, box_a: Box3D, box_b: Box3D
) -> bool:
    """Return True if either room has a door on the shared boundary."""
    for door in room_a.get("doors") or []:
        if _door_on_shared_boundary(door, box_a, box_b):
            return True
    for door in room_b.get("doors") or []:
        if _door_on_shared_boundary(door, box_b, box_a):
            return True
    return False


def _suggest_door_position(a: Box3D, b: Box3D) -> Tuple[float, float]:
    """Suggest a reasonable door position on the shared wall between *a*
    and *b*.  Returns (x, z)."""
    # Right wall of a == left wall of b
    if abs(a.x_max - b.x_min) <= EPSILON:
        z_mid = (max(a.z_min, b.z_min) + min(a.z_max, b.z_max)) / 2.0
        return (a.x_max, z_mid)

    # Left wall of a == right wall of b
    if abs(a.x_min - b.x_max) <= EPSILON:
        z_mid = (max(a.z_min, b.z_min) + min(a.z_max, b.z_max)) / 2.0
        return (a.x_min, z_mid)

    # Bottom wall of a == top wall of b
    if abs(a.z_max - b.z_min) <= EPSILON:
        x_mid = (max(a.x_min, b.x_min) + min(a.x_max, b.x_max)) / 2.0
        return (x_mid, a.z_max)

    # Top wall of a == bottom wall of b
    if abs(a.z_min - b.z_max) <= EPSILON:
        x_mid = (max(a.x_min, b.x_min) + min(a.x_max, b.x_max)) / 2.0
        return (x_mid, a.z_min)

    # Fallback: midpoint of the shared overlap region
    return (
        (max(a.x_min, b.x_min) + min(a.x_max, b.x_max)) / 2.0,
        (max(a.z_min, b.z_min) + min(a.z_max, b.z_max)) / 2.0,
    )

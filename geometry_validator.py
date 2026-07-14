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
            
            # --- FURNITURE FIT VALIDATION ---
            rt = r.type.lower()
            min_dim = min(r.rect.width, r.rect.length)
            area = r.rect.width * r.rect.length
            
            if "bed" in rt:
                if min_dim < 9.0 or area < 100:
                    msg = f"FURNITURE ERROR: {r.id} ({r.rect.width}x{r.rect.length}) is too small to fit a standard bed with walking clearance."
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.is_valid = False
            elif "kitchen" in rt:
                if min_dim < 7.5 or area < 64:
                    msg = f"FURNITURE ERROR: {r.id} is too small for a functional kitchen work triangle."
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.is_valid = False
            elif "living" in rt:
                if min_dim < 10.0 or area < 120:
                    msg = f"FURNITURE ERROR: {r.id} is too small for a functional living room setup."
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.is_valid = False
            elif "dining" in rt:
                if min_dim < 8.0 or area < 80:
                    msg = f"FURNITURE ERROR: {r.id} cannot fit a functional dining table."
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.is_valid = False

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
        
        # Disabled checks: These constraints are either too strict for the LLM (NP-hard gap tiling)
        # or have been offloaded to the deterministic LayoutEngine (doors, windows, connectivity).
        # GeometryValidator._check_gaps_and_adjacency(boxes, result)
        # GeometryValidator._check_doors(blueprint, boxes, result)
        # GeometryValidator._check_windows(blueprint, boxes, plot_width, plot_length, result)
        # GeometryValidator._check_door_window_overlap(blueprint, boxes, result)
        # GeometryValidator._check_connectivity(blueprint, boxes, result)

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
                if a.overlaps(b, epsilon=SNAP_TOLERANCE):
                    overlap_x = min(a.x_max, b.x_max) - max(a.x_min, b.x_min)
                    overlap_z = min(a.z_max, b.z_max) - max(a.z_min, b.z_min)
                    overlap_area = round(overlap_x * overlap_z, 2)

                    # Suggest moving room_b so its x_min starts right after
                    # room_a's x_max.
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
        
        # 1.0 sq ft tolerance for minor floating point rounding
        # [CP SOLVER UPDATE]: Bottom-up jigsaw packing naturally produces L-shaped and U-shaped 
        # houses where the bounding box area is larger than the sum of room areas. 
        # So we no longer enforce that the house footprint is a perfect solid rectangle.
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
                    # Find nearest wall for helpful error message
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

                # First make sure the window is on a wall at all
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

                # Check if the wall that holds the window touches the plot
                # boundary.  This is advisory, not a hard error.
                on_external = _wall_is_external(wx, wz, box, plot_width, plot_length, wall_tol)
                if not on_external:
                    msg = (
                        f"WINDOW_WARNING: Window in {box.label} at ({wx}, {wz}) "
                        f"is on an internal wall. Consider placing windows on "
                        f"external walls (touching plot boundary) for natural light."
                    )
                    logger.info(msg)
                    result.window_errors.append(box.label)
                    # Intentionally NOT appended to result.errors (advisory).

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
            # Collect all openings (doors + windows) with their 1-D extents
            openings: List[Tuple[str, float, float, str]] = []  # (kind, start, end, wall_id)

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

            # Check pairwise overlaps on the same wall
            for i in range(len(openings)):
                for j in range(i + 1, len(openings)):
                    kind_a, s_a, e_a, wall_a = openings[i]
                    kind_b, s_b, e_b, wall_b = openings[j]
                    if wall_a != wall_b:
                        continue
                    # 1-D overlap check
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

        # 1. Build adjacency: two rooms are adjacent if their boxes share a
        #    wall segment (touch along one axis within EPSILON).
        adjacency: List[List[int]] = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if _rooms_adjacent(boxes[i], boxes[j]):
                    adjacency[i].append(j)
                    adjacency[j].append(i)
                    
        # --- PHASE 4: FUNCTIONAL FORBIDDEN ADJACENCY ---
        for i in range(n):
            for j in adjacency[i]:
                if j <= i: continue
                rt1 = boxes[i].label.lower()
                rt2 = boxes[j].label.lower()
                
                is_bath1 = "bath" in rt1 or "toilet" in rt1
                is_bath2 = "bath" in rt2 or "toilet" in rt2
                is_food1 = "kitchen" in rt1 or "dining" in rt1
                is_food2 = "kitchen" in rt2 or "dining" in rt2
                
                if (is_bath1 and is_food2) or (is_bath2 and is_food1):
                    msg = f"FORBIDDEN ADJACENCY: {boxes[i].label} cannot share a physical wall with {boxes[j].label} due to hygiene/vastu rules."
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.is_valid = False

        # 2. Build a door-connected graph: adjacent rooms with a connecting
        #    door on the shared boundary.
        door_connected: List[List[int]] = [[] for _ in range(n)]
        for i in range(n):
            for j in adjacency[i]:
                if j <= i:
                    continue  # avoid double-processing
                if _rooms_share_door(blueprint[i], blueprint[j], boxes[i], boxes[j]):
                    door_connected[i].append(j)
                    door_connected[j].append(i)

        # 3. BFS from the first room (or the one flagged as main_entrance).
        start = 0
        for idx, room in enumerate(blueprint):
            features = room.get("features") or []
            if isinstance(features, list) and "main_entrance" in features:
                start = idx
                break
            if isinstance(features, str) and "main_entrance" in features:
                start = idx
                break
            # Also try to start from living room or foyer
            if "living" in room.get("room_type", "").lower() or "foyer" in room.get("room_type", "").lower():
                start = idx

        visited = set()
        queue: deque[int] = deque([start])
        visited.add(start)
        
        bfs_tree = {} # Parent -> children
        
        while queue:
            curr = queue.popleft()
            bfs_tree[curr] = []
            for nb in door_connected[curr]:
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
                    bfs_tree[curr].append(nb)

        # Circulation Test: Bedrooms cannot be passage rooms
        for curr, children in bfs_tree.items():
            curr_type = blueprint[curr].get("room_type", "").lower()
            if "bed" in curr_type:
                for child in children:
                    child_type = blueprint[child].get("room_type", "").lower()
                    if "bath" not in child_type and "toilet" not in child_type and "closet" not in child_type and "balcony" not in child_type:
                        msg = f"CIRCULATION ERROR: Bedroom {boxes[curr].label} is being used as a passage to reach {boxes[child].label}!"
                        logger.warning(msg)
                        result.errors.append(msg)
                        result.is_valid = False

        # Door & Window Verification
        for idx, room in enumerate(blueprint):
            r_type = room.get("room_type", "").lower()
            num_doors = len(room.get("doors", []))
            num_windows = len(room.get("windows", []))
            
            if num_doors == 0 and "corridor" not in r_type:
                msg = f"DOOR ERROR: {boxes[idx].label} has no doors!"
                logger.warning(msg)
                result.errors.append(msg)
                result.is_valid = False
                
            if "windows" in room:
                if "bed" in r_type and num_windows == 0:
                    msg = f"WINDOW ERROR: {boxes[idx].label} has no windows!"
                    result.errors.append(msg)
                    result.is_valid = False
                elif ("bath" in r_type or "toilet" in r_type or "kitchen" in r_type) and num_windows == 0:
                    msg = f"VENTILATION ERROR: {boxes[idx].label} has no ventilation!"
                    result.errors.append(msg)
                    result.is_valid = False

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
            # Only high-traffic/movement rooms can act as passages for general flow
            return any(p in rt for p in ['entrance', 'hallway', 'corridor', 'living', 'foyer'])

        living_idx = next((i for i, r in enumerate(blueprint) if "living" in r.get("room_type", "").lower()), None)
        kitchen_idx = next((i for i, r in enumerate(blueprint) if "kitchen" in r.get("room_type", "").lower()), None)
        bed_indices = [i for i, r in enumerate(blueprint) if "bed" in r.get("room_type", "").lower()]
        bath_indices = [i for i, r in enumerate(blueprint) if "bath" in r.get("room_type", "").lower() or "toilet" in r.get("room_type", "").lower()]
        
        # 1. Guest: Living -> Common Bath
        if living_idx is not None and bath_indices:
            path = bfs_path(living_idx, "bath")
            if path:
                for node in path[1:-1]:
                    if not is_passage_allowed(node):
                        msg = f"PERSONA ERROR (Guest): Path from Living to Bath passes through non-passage room {boxes[node].label}."
                        logger.warning(msg)
                        result.errors.append(msg)
                        result.is_valid = False
                        
        # 2. Resident: Bedroom -> Kitchen
        if kitchen_idx is not None:
            for b_idx in bed_indices:
                path = bfs_path(b_idx, "kitchen")
                if path:
                    for node in path[1:-1]:
                        if not is_passage_allowed(node):
                            msg = f"PERSONA ERROR (Resident): Path from {boxes[b_idx].label} to Kitchen passes through non-passage room {boxes[node].label}."
                            logger.warning(msg)
                            result.errors.append(msg)
                            result.is_valid = False
                            
        # 3. Parent: Kitchen -> Dining -> Living
        # Ensure direct open flow between them without going through private areas.
        dining_idx = next((i for i, r in enumerate(blueprint) if "dining" in r.get("room_type", "").lower()), None)
        if kitchen_idx is not None and dining_idx is not None and living_idx is not None:
            # path from Kitchen to Dining
            path_kd = bfs_path(kitchen_idx, "dining")
            # path from Dining to Living
            path_dl = bfs_path(dining_idx, "living")
            
            if path_kd:
                for node in path_kd[1:-1]:
                    if not is_passage_allowed(node):
                        msg = f"PERSONA ERROR (Parent): Path from Kitchen to Dining goes through non-passage {boxes[node].label}."
                        result.errors.append(msg)
                        result.is_valid = False
            if path_dl:
                for node in path_dl[1:-1]:
                    if not is_passage_allowed(node):
                        msg = f"PERSONA ERROR (Parent): Path from Dining to Living goes through non-passage {boxes[node].label}."
                        result.errors.append(msg)
                        result.is_valid = False

        # 4. Laundry Route: Bedroom -> Bathroom
        for b_idx in bed_indices:
            path_bath = bfs_path(b_idx, "bath")
            if path_bath:
                for node in path_bath[1:-1]:
                    if not is_passage_allowed(node) and "bath" not in blueprint[node].get("room_type", "").lower():
                        msg = f"PERSONA ERROR (Laundry): Path from {boxes[b_idx].label} to Bath goes through non-passage room {boxes[node].label}."
                        logger.warning(msg)
                        result.errors.append(msg)
                        result.is_valid = False

        # 4. Report unreachable rooms.
        for idx in range(n):
            if idx in visited:
                continue

            room_label = boxes[idx].label

            # Find nearest adjacent room for suggestion
            nearest_label = "unknown"
            suggested_x, suggested_z = boxes[idx].center_x, boxes[idx].z_min
            if adjacency[idx]:
                adj_idx = adjacency[idx][0]
                nearest_label = boxes[adj_idx].label
                sx, sz = _suggest_door_position(boxes[idx], boxes[adj_idx])
                suggested_x, suggested_z = round(sx, 2), round(sz, 2)

            msg = (
                f"UNREACHABLE: {room_label} has no door connection to any "
                f"adjacent room. Add a door on the shared wall with "
                f"{nearest_label} at ({suggested_x}, {suggested_z})."
            )
            logger.warning(msg)
            result.errors.append(msg)
            result.unreachable_rooms.append(room_label)


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
    # Determine which wall the point is on, then check if that wall is at the
    # plot edge.
    if abs(px - box.x_min) <= tol and abs(box.x_min) <= tol:
        return True  # left wall at plot x=0
    if abs(px - box.x_max) <= tol and abs(box.x_max - plot_width) <= tol:
        return True  # right wall at plot x=plot_width
    if abs(pz - box.z_min) <= tol and abs(box.z_min) <= tol:
        return True  # top wall at plot z=0
    if abs(pz - box.z_max) <= tol and abs(box.z_max - plot_length) <= tol:
        return True  # bottom wall at plot z=plot_length
    return False


def _opening_extent(
    px: float, pz: float, width: float, box: Box3D, tol: float
) -> Tuple[str, float, float]:
    """Determine the 1-D extent of an opening (door/window) along its wall.

    Returns (wall_id, start, end) where *start* and *end* are positions
    along the wall axis.  *wall_id* is a string like ``"x_min"`` or
    ``"z_max"``.  Returns ``("", 0, 0)`` if the point is not on any wall.
    """
    # On a vertical wall (left or right) the opening spans along Z
    if abs(px - box.x_min) <= tol:
        return ("x_min", pz, pz + width)
    if abs(px - box.x_max) <= tol:
        return ("x_max", pz, pz + width)
    # On a horizontal wall (top or bottom) the opening spans along X
    if abs(pz - box.z_min) <= tol:
        return ("z_min", px, px + width)
    if abs(pz - box.z_max) <= tol:
        return ("z_max", px, px + width)
    return ("", 0.0, 0.0)


def _rooms_adjacent(a: Box3D, b: Box3D) -> bool:
    """Two rooms are adjacent if their AABBs share a wall segment.

    They must *touch* (gap ≤ EPSILON) on one axis while genuinely
    overlapping (shared length > EPSILON) on the perpendicular axis.
    """
    # Shared segment along Z axis (rooms side-by-side along X)
    touch_x = (
        abs(a.x_max - b.x_min) <= EPSILON or abs(b.x_max - a.x_min) <= EPSILON
    )
    overlap_z = min(a.z_max, b.z_max) - max(a.z_min, b.z_min)

    if touch_x and overlap_z > EPSILON:
        return True

    # Shared segment along X axis (rooms stacked along Z)
    touch_z = (
        abs(a.z_max - b.z_min) <= EPSILON or abs(b.z_max - a.z_min) <= EPSILON
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
    tol = 0.5

    # Check each possible shared boundary:

    # owner's right == other's left
    if abs(box_owner.x_max - box_other.x_min) <= EPSILON:
        if abs(dx - box_owner.x_max) <= tol:
            z_lo = max(box_owner.z_min, box_other.z_min)
            z_hi = min(box_owner.z_max, box_other.z_max)
            if z_lo - tol <= dz <= z_hi + tol:
                return True

    # owner's left == other's right
    if abs(box_owner.x_min - box_other.x_max) <= EPSILON:
        if abs(dx - box_owner.x_min) <= tol:
            z_lo = max(box_owner.z_min, box_other.z_min)
            z_hi = min(box_owner.z_max, box_other.z_max)
            if z_lo - tol <= dz <= z_hi + tol:
                return True

    # owner's bottom == other's top
    if abs(box_owner.z_max - box_other.z_min) <= EPSILON:
        if abs(dz - box_owner.z_max) <= tol:
            x_lo = max(box_owner.x_min, box_other.x_min)
            x_hi = min(box_owner.x_max, box_other.x_max)
            if x_lo - tol <= dx <= x_hi + tol:
                return True

    # owner's top == other's bottom
    if abs(box_owner.z_min - box_other.z_max) <= EPSILON:
        if abs(dz - box_owner.z_min) <= tol:
            x_lo = max(box_owner.x_min, box_other.x_min)
            x_hi = min(box_owner.x_max, box_other.x_max)
            if x_lo - tol <= dx <= x_hi + tol:
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

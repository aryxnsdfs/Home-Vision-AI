"""Post-geometry objective vector computed from rectangles and actual doors."""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, Iterable, List, Set, Tuple

from constraint_schema import ConstraintKind, IntentContract
from candidate_contract import LayoutCandidate
from topology_grammar import is_bathroom, room_zone


def _dict(room: Any) -> dict:
    return room.to_dict() if hasattr(room, "to_dict") else dict(room)


def actual_door_graph(candidate: LayoutCandidate) -> Dict[str, Set[str]]:
    """Return the authoritative graph; room Door arrays are never consulted."""
    if not isinstance(candidate, LayoutCandidate):
        raise TypeError("actual_door_graph requires the authoritative LayoutCandidate")
    return candidate.access_graph()


def _distance(graph: Dict[str, Set[str]], source: str, target: str) -> int:
    seen, queue = {source}, deque([(source, 0)])
    while queue:
        node, distance = queue.popleft()
        if node == target:
            return distance
        for neighbor in graph.get(node, set()):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, distance + 1))
    return 10_000


def _resolve(ref: str, rooms: List[dict]) -> List[dict]:
    direct = [room for room in rooms if str(room.get("id")) == ref]
    if direct:
        return direct
    value = str(ref or "").lower().replace(" ", "_")
    return [room for room in rooms if str(room.get("type", "")).lower().replace(" ", "_") == value]


def score_layout_objectives(
    room_values: Iterable[Any], plot_width: float, plot_length: float,
    contract: IntentContract, candidate: LayoutCandidate,
) -> Dict[str, float]:
    rooms = [_dict(room) for room in room_values]
    graph = actual_door_graph(candidate)
    objectives = {
        "user_preference_cost": 0.0, "circulation_cost": 0.0, "privacy_cost": 0.0,
        "zoning_cost": 0.0, "area_deviation": 0.0, "aspect_ratio_cost": 0.0,
        "wall_irregularity": 0.0, "dead_space": 0.0, "daylight_cost": 0.0,
        "plumbing_cost": 0.0, "aesthetic_cost": 0.0,
    }

    entry = candidate.entry_room_id or next((str(room.get("id")) for room in rooms if any(
        door.get("is_main") for door in room.get("doors", []) or []
    )), next(iter(graph), ""))
    objectives["circulation_cost"] = sum(min(20, _distance(graph, entry, room_id))
                                          for room_id in graph if room_id != entry)

    for constraint in contract.constraints:
        sources, targets = _resolve(constraint.source, rooms), _resolve(constraint.target or "", rooms)
        if constraint.kind == ConstraintKind.DIRECTION:
            for room in sources:
                cx = float(room.get("x", 0)) + float(room.get("width", 0)) / 2
                cz = float(room.get("z", 0)) + float(room.get("length", 0)) / 2
                direction = str(constraint.value or "")
                violation = 0.0
                if "east" in direction: violation += max(0.0, plot_width * 0.5 - cx)
                if "west" in direction: violation += max(0.0, cx - plot_width * 0.5)
                if "north" in direction: violation += max(0.0, cz - plot_length * 0.5)
                if "south" in direction: violation += max(0.0, plot_length * 0.5 - cz)
                objectives["user_preference_cost"] += violation * constraint.priority_weight
        elif sources and targets:
            graph_distance = min(_distance(graph, str(a.get("id")), str(b.get("id"))) for a in sources for b in targets)
            center_distance = min(
                abs(float(a.get("x", 0)) + float(a.get("width", 0)) / 2 - float(b.get("x", 0)) - float(b.get("width", 0)) / 2)
                + abs(float(a.get("z", 0)) + float(a.get("length", 0)) / 2 - float(b.get("z", 0)) - float(b.get("length", 0)) / 2)
                for a in sources for b in targets
            )
            if constraint.kind in {ConstraintKind.DIRECT_CONNECTION, ConstraintKind.OPEN_FLOW, ConstraintKind.EXCLUSIVE_ACCESS} and graph_distance != 1:
                objectives["user_preference_cost"] += constraint.priority_weight
            elif constraint.kind in {ConstraintKind.NEAR, ConstraintKind.ADJACENT}:
                objectives["user_preference_cost"] += center_distance * constraint.priority_weight * 0.05
            elif constraint.kind == ConstraintKind.SEPARATION:
                objectives["user_preference_cost"] += max(0.0, min(plot_width, plot_length) * 0.25 - center_distance) * constraint.priority_weight

    by_id = {str(room.get("id")): room for room in rooms}
    for grp in contract.group_constraints:
        sources = [by_id[r] for r in grp.resolved_source_room_ids if r in by_id]
        targets = [by_id[r] for r in grp.resolved_target_room_ids if r in by_id]
        if not sources or not targets:
            continue
            
        weight = 100.0 if grp.strength == "hard" else 20.0
        
        if grp.kind in {"public_private_zoning", "zone_separation"}:
            for src in sources:
                for tgt in targets:
                    if _distance(graph, str(src["id"]), str(tgt["id"])) == 1:
                        objectives["zoning_cost"] += weight
            center_distance = min(
                abs(float(a.get("x", 0)) + float(a.get("width", 0)) / 2 - float(b.get("x", 0)) - float(b.get("width", 0)) / 2)
                + abs(float(a.get("z", 0)) + float(a.get("length", 0)) / 2 - float(b.get("z", 0)) - float(b.get("length", 0)) / 2)
                for a in sources for b in targets
            )
            objectives["zoning_cost"] += max(0.0, 10.0 - center_distance) * (weight * 0.1)
        elif grp.kind == "group_separation":
            center_distance = min(
                abs(float(a.get("x", 0)) + float(a.get("width", 0)) / 2 - float(b.get("x", 0)) - float(b.get("width", 0)) / 2)
                + abs(float(a.get("z", 0)) + float(a.get("length", 0)) / 2 - float(b.get("z", 0)) - float(b.get("length", 0)) / 2)
                for a in sources for b in targets
            )
            objectives["user_preference_cost"] += max(0.0, min(plot_width, plot_length) * 0.25 - center_distance) * weight
        elif grp.kind == "wet_area_clustering":
            center_distance = max(
                abs(float(a.get("x", 0)) + float(a.get("width", 0)) / 2 - float(b.get("x", 0)) - float(b.get("width", 0)) / 2)
                + abs(float(a.get("z", 0)) + float(a.get("length", 0)) / 2 - float(b.get("z", 0)) - float(b.get("length", 0)) / 2)
                for a in sources for b in sources # Clustering within sources
            )
            objectives["plumbing_cost"] += center_distance * weight

    total_area = 0.0
    circulation_area = 0.0
    wet_centers: List[Tuple[float, float]] = []
    for room in rooms:
        width, length = float(room.get("width", 0)), float(room.get("length", 0))
        area = width * length
        total_area += area
        if room_zone(room.get("type", "")) == "circulation":
            circulation_area += area
        if min(width, length) > 0:
            ratio = max(width, length) / min(width, length)
            if room_zone(room.get("type", "")) != "circulation":
                objectives["aspect_ratio_cost"] += max(0.0, ratio - 2.0) ** 2
        target = room.get("target_area")
        if target:
            objectives["area_deviation"] += abs(area - float(target)) / max(1.0, float(target))
        if room_zone(room.get("type", "")) in {"public", "private", "semi_public"} and not room.get("windows"):
            objectives["daylight_cost"] += 10
        if is_bathroom(room.get("type", "")) or "kitchen" in str(room.get("type", "")).lower():
            wet_centers.append((float(room.get("x", 0)) + width / 2, float(room.get("z", 0)) + length / 2))

        zone = room_zone(room.get("type", ""))
        center_z = float(room.get("z", 0)) + length / 2
        if zone == "public":
            objectives["zoning_cost"] += max(0.0, plot_length * 0.5 - center_z) / max(1.0, plot_length)
        elif zone == "private":
            objectives["zoning_cost"] += max(0.0, center_z - plot_length * 0.5) / max(1.0, plot_length)

    minimize_circulation = any(
        item.kind == "circulation_area" and item.goal == "minimize"
        for item in contract.optimization_preferences
    )
    objectives["circulation_cost"] += circulation_area * (2.0 if minimize_circulation else 1.0)
    objectives["dead_space"] = max(0.0, plot_width * plot_length - total_area) + (
        circulation_area if minimize_circulation else 0.0
    )
    if rooms:
        min_x = min(float(room.get("x", 0)) for room in rooms)
        min_z = min(float(room.get("z", 0)) for room in rooms)
        max_x = max(float(room.get("x", 0)) + float(room.get("width", 0)) for room in rooms)
        max_z = max(float(room.get("z", 0)) + float(room.get("length", 0)) for room in rooms)
        bounding_area = max(0.0, max_x - min_x) * max(0.0, max_z - min_z)
        objectives["wall_irregularity"] = max(0.0, bounding_area - total_area)
        objectives["aesthetic_cost"] = objectives["aspect_ratio_cost"] + objectives["wall_irregularity"] * 0.1
    if wet_centers:
        cx = sum(point[0] for point in wet_centers) / len(wet_centers)
        cz = sum(point[1] for point in wet_centers) / len(wet_centers)
        objectives["plumbing_cost"] = sum(abs(x - cx) + abs(z - cz) for x, z in wet_centers)

    # Privacy uses the *actual* door graph, not intended connections.
    # The graph can still name a room the program shed on a relaxation round,
    # and indexing by_id blind on that killed the whole request with a
    # KeyError. An id with no room behind it contributes no cost.
    def room_type_of(room_id: str) -> str:
        return str((by_id.get(room_id) or {}).get("type", ""))

    for room_id, neighbors in graph.items():
        if room_id not in by_id:
            continue
        zone = room_zone(room_type_of(room_id))
        if zone == "private" and len(neighbors) > 1:
            objectives["privacy_cost"] += (len(neighbors) - 1) ** 2 * 12
        if room_id == entry:
            objectives["privacy_cost"] += sum(room_zone(room_type_of(n)) == "private" for n in neighbors) * 20
        if zone == "public":
            objectives["zoning_cost"] += sum(room_zone(room_type_of(n)) == "private" for n in neighbors) * 4
        if "dining" in room_type_of(room_id).lower():
            objectives["privacy_cost"] += sum(is_bathroom(room_type_of(n)) for n in neighbors) * 25
    # Compatibility alias for older UI telemetry. It is a soft score only;
    # hard violations are held separately on LayoutCandidate and never ranked.
    objectives["prompt_violation"] = objectives["user_preference_cost"]
    return {key: round(value, 4) for key, value in objectives.items()}

"""Deterministic final validation of one authoritative LayoutCandidate."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, Iterable, List, Set

from candidate_contract import CandidateStatus, LayoutCandidate, ValidationError, shared_wall_overlap
from constraint_schema import IntentContract
from layout_scorer import actual_door_graph
from topology_grammar import is_bathroom, room_zone

logger = logging.getLogger(__name__)


@dataclass
class FinalValidationReport:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    door_graph: Dict[str, Set[str]] = field(default_factory=dict)


def _dict(room: Any) -> dict:
    return room.to_dict() if hasattr(room, "to_dict") else dict(room)


def _reachable(graph: Dict[str, Set[str]], start: str, blocked: str = "") -> Set[str]:
    if not start or start == blocked or start not in graph:
        return set()
    seen, queue = {start}, deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor != blocked and neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def validate_final_layout(
    candidate: LayoutCandidate, room_values: Iterable[Any], plot_width: float,
    plot_length: float, contract: IntentContract,
) -> FinalValidationReport:
    """Validate geometry and graph paths without reconstructing intended access.

    ``contract`` remains an input for API stability and traceability. Its hard
    semantics have already been compiled into exact-ID candidate relations.
    """
    if not isinstance(candidate, LayoutCandidate):
        raise TypeError("validate_final_layout requires the authoritative LayoutCandidate")
    rooms = [_dict(room) for room in room_values]
    by_id = {str(room.get("id")): room for room in rooms}
    graph = actual_door_graph(candidate)
    errors: List[ValidationError] = []

    if set(by_id) != set(candidate.rooms_by_id):
        errors.append(ValidationError(
            "ROOM_IDENTITY_MISMATCH",
            f"Final room IDs differ from candidate: missing={sorted(set(candidate.rooms_by_id)-set(by_id))} "
            f"extra={sorted(set(by_id)-set(candidate.rooms_by_id))}",
        ))

    for index, room in enumerate(rooms):
        room_id = str(room.get("id"))
        x, z = float(room.get("x", 0)), float(room.get("z", 0))
        width, length = float(room.get("width", 0)), float(room.get("length", 0))
        if width <= 0 or length <= 0:
            errors.append(ValidationError("IMPOSSIBLE_DIMENSIONS", f"Impossible dimensions for {room_id}", room_ids=(room_id,)))
        if x < -0.05 or z < -0.05 or x + width > plot_width + 0.05 or z + length > plot_length + 0.05:
            errors.append(ValidationError("OUTSIDE_PLOT", f"Room outside plot boundary: {room_id}", room_ids=(room_id,)))
        for other in rooms[index + 1:]:
            other_id = str(other.get("id"))
            ox, oz = float(other.get("x", 0)), float(other.get("z", 0))
            ow, ol = float(other.get("width", 0)), float(other.get("length", 0))
            if min(x + width, ox + ow) - max(x, ox) > 0.05 and min(z + length, oz + ol) - max(z, oz) > 0.05:
                errors.append(ValidationError("ROOM_OVERLAP", f"Room overlap: {room_id} and {other_id}", room_ids=(room_id, other_id)))

    entry = candidate.entry_room_id
    if not entry:
        entry = next((str(room.get("id")) for room in rooms if any(
            door.get("is_main") for door in room.get("doors", []) or []
        )), "")
        candidate.entry_room_id = entry
    if not entry or entry not in graph:
        errors.append(ValidationError("NO_ENTRANCE", "No realized main entrance"))
    reachable = _reachable(graph, entry)
    missing = candidate.required_room_ids() - reachable
    if missing:
        errors.append(ValidationError(
            "INACCESSIBLE_ROOMS",
            f"Rooms inaccessible from realized entrance: {', '.join(sorted(missing))}",
            room_ids=tuple(sorted(missing)),
        ))
    else:
        logger.info("[ACCESS GRAPH] candidate=%s all required rooms reachable", candidate.candidate_id)

    for relation in candidate.relations_by_id.values():
        source, target = relation.source_room_id, relation.target_room_id
        if not relation.is_hard or not target:
            continue
        if relation.kind in {"direct_access", "open_flow", "exclusive_access"}:
            if target not in graph.get(source, set()):
                errors.append(ValidationError(
                    "HARD_ACCESS_MISSING", f"Hard access not realized: {source} -> {target}",
                    relation.relation_id, (source, target),
                ))
        elif relation.kind == "reachable":
            if target not in _reachable(graph, source):
                errors.append(ValidationError(
                    "HARD_PATH_MISSING", f"Required access path not realized: {source} -> {target}",
                    relation.relation_id, (source, target),
                ))
        elif relation.kind == "adjacent":
            if shared_wall_overlap(candidate.rectangles_by_room_id[source], candidate.rectangles_by_room_id[target]) < max(0.1, relation.required_overlap_ft):
                errors.append(ValidationError(
                    "HARD_ADJACENCY_MISSING", f"Hard adjacency not realized: {source} -> {target}",
                    relation.relation_id, (source, target),
                ))
        elif relation.kind == "direction":
            rect = candidate.rectangles_by_room_id[source]
            cx, cz = rect.x + rect.width / 2, rect.z + rect.length / 2
            direction = str(relation.value or "").lower().replace("-", "_")
            violates = (
                ("east" in direction and cx < plot_width * 0.5 - 0.05)
                or ("west" in direction and cx > plot_width * 0.5 + 0.05)
                or ("north" in direction and cz > plot_length * 0.5 + 0.05)
                or ("south" in direction and cz < plot_length * 0.5 - 0.05)
            )
            if violates:
                errors.append(ValidationError(
                    "HARD_DIRECTION_MISSING", f"Hard direction not realized: {source} on {direction} side",
                    relation.relation_id, (source,),
                ))
        if relation.kind == "exclusive_access" and graph.get(source, set()) != {target}:
            errors.append(ValidationError(
                "EXCLUSIVE_ACCESS_VIOLATION", f"Attached room {source} connects outside owner {target}",
                relation.relation_id, (source, target),
            ))

    for grp in candidate.group_constraints_by_id.values():
        if grp.strength != "hard":
            continue
        if grp.kind == "group_reachability":
            for src in grp.resolved_source_room_ids:
                if src not in graph and src != entry:
                    continue
                r_set = _reachable(graph, src)
                for tgt in grp.resolved_target_room_ids:
                    if tgt not in r_set:
                        errors.append(ValidationError(
                            "HARD_GROUP_REACHABILITY_MISSING",
                            f"Hard group reachability not realized: {src} -> {tgt} (Constraint {grp.id})",
                            grp.id, (src, tgt)
                        ))
        elif grp.kind in {"group_separation", "zone_separation", "public_private_zoning"}:
            for src in grp.resolved_source_room_ids:
                for tgt in grp.resolved_target_room_ids:
                    if tgt in graph.get(src, set()):
                        errors.append(ValidationError(
                            "HARD_GROUP_SEPARATION_VIOLATION",
                            f"Rooms cannot be directly connected: {src} -> {tgt} (Constraint {grp.id})",
                            grp.id, (src, tgt)
                        ))

    attached_exception_ids: Set[str] = set()
    attached_by_owner: Dict[str, Set[str]] = {}
    # The ensuite exception is semantic and deliberately narrow: the room
    # must be role=attached, point to one exact owner, and be that owner's
    # terminal leaf in the realized PairedDoor graph.
    for room_id, room in candidate.rooms_by_id.items():
        role = str(room.get("bathroom_role") or room.get("role") or "").lower()
        owner = str(room.get("owner_room_id") or room.get("assigned_to") or room.get("attached_to_id") or "")
        if role == "attached" and owner in candidate.rooms_by_id and graph.get(room_id, set()) == {owner}:
            attached_exception_ids.add(room_id)
            attached_by_owner.setdefault(owner, set()).add(room_id)
            continue
        # A bathroom whose only realized door is into a single bedroom simply
        # is an ensuite, whatever the program labelled it. Rejecting that as
        # "private room used as transit" threw away otherwise valid layouts,
        # so recognise the arrangement from the geometry itself.
        neighbours = graph.get(room_id, set())
        if is_bathroom(room.get("type", "")) and len(neighbours) == 1:
            only_neighbour = next(iter(neighbours))
            neighbour = candidate.rooms_by_id.get(only_neighbour, {})
            if "bedroom" in str(neighbour.get("type", "")).lower():
                logger.info(
                    "[ENSUITE INFERRED] %s opens only into %s; treating it as that bedroom's ensuite.",
                    room_id, only_neighbour,
                )
                attached_exception_ids.add(room_id)
                attached_by_owner.setdefault(only_neighbour, set()).add(room_id)
    for relation in candidate.relations_by_id.values():
        if relation.kind == "exclusive_access" and relation.target_room_id:
            room = candidate.rooms_by_id.get(relation.source_room_id, {})
            role = str(room.get("bathroom_role") or room.get("role") or "").lower()
            owner = str(room.get("owner_room_id") or room.get("assigned_to") or room.get("attached_to_id") or "")
            if role == "attached" and owner == relation.target_room_id and graph.get(relation.source_room_id, set()) == {owner}:
                attached_exception_ids.add(relation.source_room_id)
                attached_by_owner.setdefault(owner, set()).add(relation.source_room_id)
    for room_id, room in by_id.items():
        is_private_or_wet = room_zone(room.get("type", "")) == "private" or is_bathroom(room.get("type", ""))
        if room_id == entry or not is_private_or_wet or room_id in attached_exception_ids:
            continue
        without = _reachable(graph, entry, blocked=room_id)
        stranded = reachable - {room_id} - without
        stranded -= attached_by_owner.get(room_id, set())
        if stranded:
            errors.append(ValidationError(
                "PRIVATE_TRANSIT", f"Private/wet room used as the only transit route: {room_id} -> {', '.join(sorted(stranded))}",
                room_ids=(room_id,) + tuple(sorted(stranded)),
            ))

    candidate.validation_errors = errors
    if not any(error.code == "PRIVATE_TRANSIT" for error in errors):
        logger.info("[PRIVATE TRANSIT] candidate=%s passed", candidate.candidate_id)
    candidate.status = CandidateStatus.VALIDATED if not errors else CandidateStatus.REJECTED
    logger.info("[FINAL VALIDATION] candidate=%s valid=%s errors=%d", candidate.candidate_id, not errors, len(errors))
    return FinalValidationReport(not errors, errors=[error.message for error in errors], door_graph=graph)

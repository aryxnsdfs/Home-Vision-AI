"""Authoritative data contract for one architectural layout candidate.

All planning stages exchange this object.  Legacy room ``connections`` and
per-room ``doors`` are renderer mirrors only; identity, typed relations,
solved geometry, paired doors, validation, and status live here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from semantic_models import GroupSpatialConstraint


class CandidateStatus(str, Enum):
    GENERATED = "generated"
    HARD_FEASIBLE = "hard_feasible"
    GEOMETRY_SOLVED = "geometry_solved"
    GEOMETRY_AUDITED = "geometry_audited"
    SERIALIZED = "serialized"
    DOORS_REALIZED = "doors_realized"
    VALIDATED = "validated"
    REJECTED = "rejected"


class RoomProvenance(str, Enum):
    EXPLICIT_USER = "explicit_user"
    IMPLIED_BHK = "implied_bhk"
    BUILDING_REQUIREMENT = "building_requirement"
    ARCHITECTURAL_DEFAULT = "architectural_default"
    GEMINI_SUGGESTION = "gemini_suggestion"
    TOPOLOGY_SYNTHESIZED = "topology_synthesized"


@dataclass(frozen=True)
class SolvedRect:
    x: float
    z: float
    width: float
    length: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.z, self.width, self.length)

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class SpatialRelation:
    relation_id: str
    source_room_id: str
    target_room_id: Optional[str]
    kind: str
    strength: str
    provenance: str
    weight: float = 1.0
    value: Optional[str] = None
    required_overlap_ft: float = 0.0
    topology_edge: bool = False
    original_source_selector: str = ""
    original_target_selector: str = ""

    @property
    def is_hard(self) -> bool:
        return self.strength == "hard"

    @property
    def needs_shared_wall(self) -> bool:
        return self.kind in {"direct_access", "exclusive_access"} or (
            self.kind in {"open_flow", "adjacent"} and self.is_hard
        )

    @property
    def creates_access(self) -> bool:
        return self.kind in {"direct_access", "exclusive_access", "open_flow"}


@dataclass(frozen=True)
class PairedDoor:
    id: str
    room_a_id: str
    room_b_id: str
    wall_id: str
    width_ft: float
    position_ft: float
    global_x: float
    global_z: float
    orientation: str

    def other(self, room_id: str) -> str:
        if room_id == self.room_a_id:
            return self.room_b_id
        if room_id == self.room_b_id:
            return self.room_a_id
        raise KeyError(f"Room {room_id!r} is not referenced by door {self.id!r}")


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str
    relation_id: str = ""
    room_ids: tuple[str, ...] = ()


class InternalInvariantError(RuntimeError):
    """A pipeline stage changed or contradicted an already accepted contract."""


def stable_relation_id(
    source_room_id: str, target_room_id: Optional[str], kind: str,
    provenance: str, value: Optional[str] = None,
) -> str:
    raw = "|".join((source_room_id, target_room_id or "", kind, provenance, value or ""))
    return "rel_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def shared_wall_overlap(first: SolvedRect, second: SolvedRect, tolerance: float = 0.05) -> float:
    ax1, az1 = first.x, first.z
    ax2, az2 = first.x + first.width, first.z + first.length
    bx1, bz1 = second.x, second.z
    bx2, bz2 = second.x + second.width, second.z + second.length
    if abs(ax2 - bx1) <= tolerance or abs(bx2 - ax1) <= tolerance:
        return max(0.0, min(az2, bz2) - max(az1, bz1))
    if abs(az2 - bz1) <= tolerance or abs(bz2 - az1) <= tolerance:
        return max(0.0, min(ax2, bx2) - max(ax1, bx1))
    return 0.0


@dataclass
class LayoutCandidate:
    candidate_id: str
    topology_id: str
    topology_family: str
    rooms_by_id: Dict[str, Dict[str, Any]]
    relations_by_id: Dict[str, SpatialRelation]
    entry_room_id: str = ""
    rectangles_by_room_id: Dict[str, SolvedRect] = field(default_factory=dict)
    doors: List[PairedDoor] = field(default_factory=list)
    objective_vector: Dict[str, float] = field(default_factory=dict)
    validation_errors: List[ValidationError] = field(default_factory=list)
    status: CandidateStatus = CandidateStatus.GENERATED
    encoded_relation_ids: Set[str] = field(default_factory=set)
    hard_user_violations: List[ValidationError] = field(default_factory=list)
    hard_code_violations: List[ValidationError] = field(default_factory=list)
    hard_topology_violations: List[ValidationError] = field(default_factory=list)
    group_constraints_by_id: Dict[str, GroupSpatialConstraint] = field(default_factory=dict)

    def assert_identity_invariants(self) -> None:
        room_ids = list(self.rooms_by_id)
        if len(room_ids) != len(set(room_ids)):
            raise InternalInvariantError(f"candidate={self.candidate_id}: canonical room IDs are not unique")
        for room_id, room in self.rooms_by_id.items():
            if str(room.get("id")) != room_id:
                raise InternalInvariantError(
                    f"candidate={self.candidate_id}: room map key {room_id!r} != room.id {room.get('id')!r}"
                )
        for relation in self.relations_by_id.values():
            if relation.source_room_id not in self.rooms_by_id:
                raise InternalInvariantError(
                    f"candidate={self.candidate_id}: relation={relation.relation_id} "
                    f"original_selector={relation.original_source_selector or relation.source_room_id!r} "
                    f"attempted_resolved_id={relation.source_room_id!r} missing source; "
                    f"available_canonical_room_ids={sorted(self.rooms_by_id)}"
                )
            if relation.target_room_id and relation.target_room_id not in self.rooms_by_id:
                raise InternalInvariantError(
                    f"candidate={self.candidate_id}: relation={relation.relation_id} "
                    f"original_selector={relation.original_target_selector or relation.target_room_id!r} "
                    f"attempted_resolved_id={relation.target_room_id!r} missing target; "
                    f"available_canonical_room_ids={sorted(self.rooms_by_id)}"
                )
            if relation.target_room_id == relation.source_room_id:
                raise InternalInvariantError(
                    f"candidate={self.candidate_id}: relation={relation.relation_id} is self-referential"
                )

        for constraint in self.group_constraints_by_id.values():
            if not constraint.resolved_source_room_ids:
                pass # Can be empty if pruned safely
            for rid in constraint.resolved_source_room_ids:
                if rid not in self.rooms_by_id:
                    raise InternalInvariantError(
                        f"candidate={self.candidate_id}: group_constraint={constraint.id} "
                        f"missing resolved source room {rid!r}"
                    )
            for rid in constraint.resolved_target_room_ids:
                if rid not in self.rooms_by_id:
                    raise InternalInvariantError(
                        f"candidate={self.candidate_id}: group_constraint={constraint.id} "
                        f"missing resolved target room {rid!r}"
                    )

    @property
    def hard_feasible(self) -> bool:
        return not (self.hard_user_violations or self.hard_code_violations or self.hard_topology_violations)

    def set_rectangles(self, rectangles: Mapping[str, SolvedRect]) -> None:
        if set(rectangles) != set(self.rooms_by_id):
            missing = sorted(set(self.rooms_by_id) - set(rectangles))
            extra = sorted(set(rectangles) - set(self.rooms_by_id))
            raise InternalInvariantError(
                f"candidate={self.candidate_id}: solved rectangle identity mismatch missing={missing} extra={extra}"
            )
        self.rectangles_by_room_id = dict(rectangles)
        self.status = CandidateStatus.GEOMETRY_SOLVED

    def geometry_hash(self) -> str:
        payload = [
            (room_id,) + tuple(round(float(value), 6) for value in rect.as_tuple())
            for room_id, rect in sorted(self.rectangles_by_room_id.items())
        ]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()

    def assert_encoded_edges_realized(self) -> None:
        for relation_id in sorted(self.encoded_relation_ids):
            relation = self.relations_by_id[relation_id]
            if not relation.needs_shared_wall:
                continue
            if not relation.target_room_id:
                continue
            first = self.rectangles_by_room_id[relation.source_room_id]
            second = self.rectangles_by_room_id[relation.target_room_id]
            measured = shared_wall_overlap(first, second)
            required = max(0.1, relation.required_overlap_ft)
            if measured + 1e-6 < required:
                raise InternalInvariantError(
                    "CP-SAT edge invariant failed: "
                    f"candidate_id={self.candidate_id} topology_id={self.topology_id} "
                    f"relation_id={relation.relation_id} source={relation.source_room_id} "
                    f"target={relation.target_room_id} source_rect={first.as_tuple()} "
                    f"target_rect={second.as_tuple()} required_overlap={required} measured_overlap={measured}"
                )
        self.status = CandidateStatus.GEOMETRY_AUDITED

    def set_paired_doors(self, doors: Iterable[PairedDoor]) -> None:
        values = list(doors)
        for door in values:
            if door.room_a_id not in self.rooms_by_id or door.room_b_id not in self.rooms_by_id:
                raise InternalInvariantError(
                    f"candidate={self.candidate_id}: door={door.id} references unknown rooms "
                    f"{door.room_a_id},{door.room_b_id}"
                )
            if door.room_a_id == door.room_b_id:
                raise InternalInvariantError(f"candidate={self.candidate_id}: door={door.id} is self-referential")
        ids = [door.id for door in values]
        if len(ids) != len(set(ids)):
            raise InternalInvariantError(f"candidate={self.candidate_id}: paired door IDs are not unique")
        self.doors = values
        self.status = CandidateStatus.DOORS_REALIZED

    def access_graph(self) -> Dict[str, Set[str]]:
        graph = {room_id: set() for room_id in self.rooms_by_id}
        for door in self.doors:
            graph[door.room_a_id].add(door.room_b_id)
            graph[door.room_b_id].add(door.room_a_id)
        return graph

    def required_room_ids(self) -> Set[str]:
        return {
            room_id for room_id, room in self.rooms_by_id.items()
            if bool(room.get("required", True)) and not room.get("is_outdoor") and room.get("type") != "void"
        }

    def to_summary(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "topology_id": self.topology_id,
            "topology_family": self.topology_family,
            "entry_room_id": self.entry_room_id,
            "status": self.status.value,
            "room_count": len(self.rooms_by_id),
            "relation_count": len(self.relations_by_id),
            "group_constraint_count": len(self.group_constraints_by_id),
            "paired_door_count": len(self.doors),
            "objective_vector": dict(self.objective_vector),
            "validation_errors": [asdict(error) for error in self.validation_errors],
        }

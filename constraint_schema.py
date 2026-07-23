"""Typed architectural requirements shared by planning pipeline stages.

The schema deliberately separates what a relationship *means* from how
important it is and where it came from.  Geometry code should never have to
guess that ``near`` means ``direct_door`` merely because both mention rooms.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from typing import Any, Dict, Iterable, List, Optional

from semantic_models import GroupSpatialConstraint


class ConstraintKind(str, Enum):
    DIRECTION = "direction"
    NEAR = "near"
    ADJACENT = "adjacent"
    DIRECT_CONNECTION = "direct_connection"
    REACHABLE = "reachable"
    SEPARATION = "separation"
    BETWEEN = "between"
    OPEN_FLOW = "open_flow"
    EXCLUSIVE_ACCESS = "exclusive_access"


class ConstraintStrength(str, Enum):
    HARD = "hard"
    STRONG = "strong"
    PREFERENCE = "preference"


class ConstraintOrigin(str, Enum):
    BUILDING_CODE = "building_code"
    USER = "user"
    ARCHITECTURAL_DEFAULT = "architectural_default"
    GEMINI_SUGGESTION = "gemini_suggestion"


ORIGIN_PRIORITY = {
    ConstraintOrigin.BUILDING_CODE: 4,
    ConstraintOrigin.USER: 3,
    ConstraintOrigin.ARCHITECTURAL_DEFAULT: 2,
    ConstraintOrigin.GEMINI_SUGGESTION: 1,
}

STRENGTH_WEIGHT = {
    ConstraintStrength.HARD: 1_000.0,
    ConstraintStrength.STRONG: 100.0,
    ConstraintStrength.PREFERENCE: 20.0,
}


def _enum_value(value: Any, enum_type: type[Enum], fallback: Enum) -> Enum:
    try:
        return enum_type(str(value).strip().lower())
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class ArchitecturalConstraint:
    kind: ConstraintKind
    source: str
    target: Optional[str] = None
    value: Optional[str] = None
    strength: ConstraintStrength = ConstraintStrength.PREFERENCE
    origin: ConstraintOrigin = ConstraintOrigin.ARCHITECTURAL_DEFAULT
    weight: float = 1.0
    evidence: str = ""
    original_source_selector: str = ""
    original_target_selector: str = ""

    @property
    def relation_id(self) -> str:
        raw = "|".join((
            self.kind.value,
            self.original_source_selector or self.source,
            self.original_target_selector or self.target or "",
            self.origin.value,
        ))
        return "intent_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ArchitecturalConstraint":
        return cls(
            kind=_enum_value(value.get("kind"), ConstraintKind, ConstraintKind.NEAR),
            source=str(value.get("source") or value.get("subject_room") or ""),
            target=(str(value.get("target") or value.get("target_room"))
                    if value.get("target") or value.get("target_room") else None),
            value=str(value.get("value")) if value.get("value") is not None else None,
            strength=_enum_value(value.get("strength"), ConstraintStrength, ConstraintStrength.PREFERENCE),
            origin=_enum_value(value.get("origin"), ConstraintOrigin, ConstraintOrigin.GEMINI_SUGGESTION),
            weight=max(0.0, float(value.get("weight", 1.0) or 1.0)),
            evidence=str(value.get("evidence") or ""),
            original_source_selector=str(
                value.get("original_source_selector") or value.get("source")
                or value.get("subject_room") or ""
            ),
            original_target_selector=str(
                value.get("original_target_selector") or value.get("target")
                or value.get("target_room") or ""
            ),
        )

    @property
    def priority_weight(self) -> float:
        return (
            STRENGTH_WEIGHT[self.strength]
            * ORIGIN_PRIORITY[self.origin]
            * max(0.05, self.weight)
        )

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        result["strength"] = self.strength.value
        result["origin"] = self.origin.value
        return result


@dataclass(frozen=True)
class OptimizationPreference:
    kind: str
    goal: str
    strength: ConstraintStrength
    origin: ConstraintOrigin
    evidence: str = ""
    weight: float = 1.0


@dataclass
class IntentContract:
    constraints: List[ArchitecturalConstraint] = field(default_factory=list)
    open_plan: bool = False
    topology_hint: str = ""
    entry_room_id: str = ""
    optimization_preferences: List[OptimizationPreference] = field(default_factory=list)
    program_id: str = ""
    group_constraints: List[GroupSpatialConstraint] = field(default_factory=list)

    def hard_constraints(self) -> Iterable[ArchitecturalConstraint]:
        return (item for item in self.constraints if item.strength == ConstraintStrength.HARD)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "constraints": [item.to_dict() for item in self.constraints],
            "open_plan": self.open_plan,
            "topology_hint": self.topology_hint,
            "entry_room_id": self.entry_room_id,
            "optimization_preferences": [
                {
                    **asdict(item),
                    "strength": item.strength.value,
                    "origin": item.origin.value,
                }
                for item in self.optimization_preferences
            ],
            "program_id": self.program_id,
            "group_constraints": [item.to_dict() for item in self.group_constraints],
        }

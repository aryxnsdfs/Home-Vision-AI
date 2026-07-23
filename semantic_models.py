"""Pure data models for open-vocabulary room semantics and constraint grouping."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class SelectorCardinality(str, Enum):
    SINGLE = "single"
    SET = "set"
    SYNTHETIC = "synthetic"
    PREDICATE = "predicate"


@dataclass(frozen=True)
class SemanticProfile:
    privacy_level: float = 0.5
    visitor_access: str = "controlled"
    circulation_role: str = "destination"
    activities: tuple[str, ...] = ("unspecified",)
    capabilities: tuple[str, ...] = ()
    environmental_needs: tuple[str, ...] = ()
    wet_area: bool = False
    habitable: bool = True
    requires_exterior_wall: bool = False
    requires_plumbing: bool = False
    can_be_transit: bool = False
    owner_room_id: Optional[str] = None
    provenance: str = "generic_fallback"
    semantic_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "privacy_level": self.privacy_level,
            "visitor_access": self.visitor_access,
            "circulation_role": self.circulation_role,
            "activities": list(self.activities),
            "capabilities": list(self.capabilities),
            "environmental_needs": list(self.environmental_needs),
            "wet_area": self.wet_area,
            "habitable": self.habitable,
            "requires_exterior_wall": self.requires_exterior_wall,
            "requires_plumbing": self.requires_plumbing,
            "can_be_transit": self.can_be_transit,
            "owner_room_id": self.owner_room_id,
            "provenance": self.provenance,
            "semantic_confidence": self.semantic_confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticProfile":
        return cls(
            privacy_level=float(data.get("privacy_level", 0.5)),
            visitor_access=str(data.get("visitor_access", "controlled")),
            circulation_role=str(data.get("circulation_role", "destination")),
            activities=tuple(data.get("activities", ["unspecified"])),
            capabilities=tuple(data.get("capabilities", [])),
            environmental_needs=tuple(data.get("environmental_needs", [])),
            wet_area=bool(data.get("wet_area", False)),
            habitable=bool(data.get("habitable", True)),
            requires_exterior_wall=bool(data.get("requires_exterior_wall", False)),
            requires_plumbing=bool(data.get("requires_plumbing", False)),
            can_be_transit=bool(data.get("can_be_transit", False)),
            owner_room_id=data.get("owner_room_id"),
            provenance=str(data.get("provenance", "generic_fallback")),
            semantic_confidence=float(data.get("semantic_confidence", 0.0)),
        )


@dataclass(frozen=True)
class SemanticPredicate:
    operator: str
    property_name: Optional[str] = None
    value: Any = None
    children: tuple["SemanticPredicate", ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operator": self.operator,
            "property_name": self.property_name,
            "value": self.value,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticPredicate":
        return cls(
            operator=data["operator"],
            property_name=data.get("property_name"),
            value=data.get("value"),
            children=tuple(cls.from_dict(c) for c in data.get("children", [])),
        )


@dataclass(frozen=True)
class SemanticSelector:
    original_selector: str
    predicate: Optional[SemanticPredicate] = None


@dataclass(frozen=True)
class SelectorResolution:
    original_selector: str
    cardinality: SelectorCardinality
    room_ids: tuple[str, ...]
    predicate: Optional[SemanticPredicate]
    confidence: float


@dataclass(frozen=True)
class GroupSpatialConstraint:
    id: str
    source_selector: str
    target_selector: Optional[str]
    resolved_source_room_ids: tuple[str, ...]
    resolved_target_room_ids: tuple[str, ...]
    kind: str
    strength: str
    provenance: str
    original_source_selector: str
    original_target_selector: Optional[str]
    objective_parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_selector": self.source_selector,
            "target_selector": self.target_selector,
            "resolved_source_room_ids": list(self.resolved_source_room_ids),
            "resolved_target_room_ids": list(self.resolved_target_room_ids),
            "kind": self.kind,
            "strength": self.strength,
            "provenance": self.provenance,
            "original_source_selector": self.original_source_selector,
            "original_target_selector": self.original_target_selector,
            "objective_parameters": dict(self.objective_parameters),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GroupSpatialConstraint":
        return cls(
            id=data["id"],
            source_selector=data["source_selector"],
            target_selector=data.get("target_selector"),
            resolved_source_room_ids=tuple(data.get("resolved_source_room_ids", [])),
            resolved_target_room_ids=tuple(data.get("resolved_target_room_ids", [])),
            kind=data["kind"],
            strength=data["strength"],
            provenance=data["provenance"],
            original_source_selector=data["original_source_selector"],
            original_target_selector=data.get("original_target_selector"),
            objective_parameters=dict(data.get("objective_parameters", {})),
        )

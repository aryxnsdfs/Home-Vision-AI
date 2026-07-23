"""Centralized evaluator for open-vocabulary room semantics and predicate resolution."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from semantic_models import (
    SelectorCardinality,
    SelectorResolution,
    SemanticPredicate,
    SemanticProfile,
    SemanticSelector,
)


def evaluate_predicate(predicate: SemanticPredicate, profile: SemanticProfile) -> bool:
    """Evaluate a generic predicate tree against a semantic profile."""
    if predicate.operator == "and":
        return all(evaluate_predicate(c, profile) for c in predicate.children)
    if predicate.operator == "or":
        return any(evaluate_predicate(c, profile) for c in predicate.children)
    if predicate.operator == "not":
        return not evaluate_predicate(predicate.children[0], profile)

    if predicate.property_name is None:
        return False

    value = getattr(profile, predicate.property_name, None)

    if predicate.operator == "equals":
        return value == predicate.value
    if predicate.operator == "contains":
        if isinstance(value, (list, tuple)):
            return predicate.value in value
        if isinstance(value, str):
            return str(predicate.value) in value
        return False
    if predicate.operator == "contains_any":
        if not isinstance(value, (list, tuple, set)):
            return False
        return any(v in value for v in predicate.value)
    if predicate.operator == "contains_all":
        if not isinstance(value, (list, tuple, set)):
            return False
        return all(v in value for v in predicate.value)
    if predicate.operator == "less_than_or_equal":
        try:
            return float(value) <= float(predicate.value)
        except (TypeError, ValueError):
            return False
    if predicate.operator == "greater_than_or_equal":
        try:
            return float(value) >= float(predicate.value)
        except (TypeError, ValueError):
            return False

    return False


def get_semantic_alias_predicate(selector: str) -> Optional[SemanticPredicate]:
    """Map common group terms strictly to predicates, NEVER to room-name lists."""
    key = str(selector).strip().lower().replace(" ", "_")
    
    if key in {"public_rooms", "public"}:
        return SemanticPredicate(
            operator="or",
            children=(
                SemanticPredicate("equals", "visitor_access", "public"),
                SemanticPredicate("less_than_or_equal", "privacy_level", 0.4),
            )
        )
    if key in {"private_rooms", "private"}:
        return SemanticPredicate(
            operator="or",
            children=(
                SemanticPredicate("equals", "visitor_access", "private"),
                SemanticPredicate("greater_than_or_equal", "privacy_level", 0.6),
            )
        )
    if key in {"wet_rooms", "wet_areas", "plumbing_rooms", "plumbing"}:
        return SemanticPredicate(
            operator="or",
            children=(
                SemanticPredicate("equals", "wet_area", True),
                SemanticPredicate("equals", "requires_plumbing", True),
            )
        )
    if key in {"sleeping_rooms", "sleeping"}:
        return SemanticPredicate("contains", "activities", "sleeping")
    if key in {"noisy_rooms", "noisy"}:
        return SemanticPredicate("contains", "capabilities", "noise_generating")
    if key in {"noise_sensitive_rooms", "quiet_rooms", "quiet"}:
        return SemanticPredicate("contains", "capabilities", "noise_sensitive")
    if key in {"circulation_rooms", "circulation", "transit_rooms"}:
        return SemanticPredicate(
            operator="contains_any", 
            property_name="circulation_role", 
            value=["primary_circulation", "secondary_circulation", "entrance", "landing"]
        )
    if key in {"rooms_requiring_daylight", "daylight_rooms"}:
        return SemanticPredicate("contains", "environmental_needs", "daylight")
    if key in {"rooms_requiring_plumbing", "plumbing_required"}:
        return SemanticPredicate("equals", "requires_plumbing", True)

    return None


def infer_semantic_profile(room: Dict[str, Any], prompt: str) -> SemanticProfile:
    """Infer robust semantic properties for any room, prioritizing explicit data."""
    # 1. Start with conservative generic fallback
    privacy_level = 0.5
    visitor_access = "controlled"
    circulation_role = "destination"
    activities = ["unspecified"]
    capabilities = []
    environmental_needs = []
    wet_area = False
    habitable = True
    requires_exterior_wall = False
    requires_plumbing = False
    can_be_transit = False
    semantic_confidence = 0.0

    room_type = str(room.get("type") or room.get("room_type") or "room").lower()
    user_label = str(room.get("name") or room_type).lower()

    # 2. Hardcoded baseline (deterministic normalization)
    if "bed" in room_type or "bed" in user_label:
        privacy_level = 0.8
        visitor_access = "private"
        activities = ["sleeping", "dressing", "resting"]
        can_be_transit = False
        requires_exterior_wall = True
        semantic_confidence = 0.9
    elif "bath" in room_type or "toilet" in room_type or "washroom" in room_type or "powder" in room_type:
        privacy_level = 0.9
        visitor_access = "private" if "attached" in str(room.get("role", "")) else "controlled"
        activities = ["washing", "hygiene"]
        wet_area = True
        requires_plumbing = True
        can_be_transit = False
        semantic_confidence = 0.9
    elif "living" in room_type or "lounge" in room_type or "hall" in room_type:
        privacy_level = 0.2
        visitor_access = "public"
        activities = ["socializing", "relaxing", "entertainment"]
        can_be_transit = True
        semantic_confidence = 0.9
    elif "kitchen" in room_type:
        privacy_level = 0.3
        visitor_access = "controlled"
        activities = ["cooking", "food_preparation"]
        wet_area = True
        requires_plumbing = True
        can_be_transit = True
        semantic_confidence = 0.9
    elif "dining" in room_type:
        privacy_level = 0.3
        visitor_access = "public"
        activities = ["eating", "socializing"]
        can_be_transit = True
        semantic_confidence = 0.9
    elif "corridor" in room_type or "passage" in room_type or "hallway" in room_type or "foyer" in room_type or "lobby" in room_type:
        privacy_level = 0.1
        visitor_access = "public"
        circulation_role = "primary_circulation" if "foyer" in room_type else "secondary_circulation"
        activities = ["transit"]
        can_be_transit = True
        habitable = False
        semantic_confidence = 0.9
    elif "utility" in room_type or "laundry" in room_type or "washing" in user_label:
        privacy_level = 0.6
        visitor_access = "restricted"
        activities = ["cleaning", "storage"]
        wet_area = True
        requires_plumbing = True
        can_be_transit = False
        semantic_confidence = 0.8
    elif "store" in room_type or "storage" in room_type:
        privacy_level = 0.6
        visitor_access = "restricted"
        activities = ["storage"]
        can_be_transit = False
        habitable = False
        semantic_confidence = 0.8
    elif "balcony" in room_type or "terrace" in room_type or "deck" in room_type:
        privacy_level = 0.4
        visitor_access = "controlled"
        activities = ["outdoor_recreation"]
        requires_exterior_wall = True
        habitable = False
        semantic_confidence = 0.8
    elif "pooja" in room_type or "puja" in room_type or "prayer" in room_type or "mandir" in room_type:
        privacy_level = 0.7
        visitor_access = "controlled"
        activities = ["prayer", "meditation"]
        capabilities = ["noise_sensitive"]
        semantic_confidence = 0.8
    elif "study" in room_type or "office" in room_type or "library" in room_type:
        privacy_level = 0.7
        visitor_access = "controlled"
        activities = ["working", "reading", "studying"]
        capabilities = ["noise_sensitive"]
        semantic_confidence = 0.8

    # 3. Gemini extraction mapping (if provided)
    # Allows Gemini to return these fields in the room dict directly
    if "privacy_level" in room:
        privacy_level = float(room["privacy_level"])
        semantic_confidence = max(semantic_confidence, 0.7)
    if "visitor_access" in room:
        visitor_access = str(room["visitor_access"])
    if "circulation_role" in room:
        circulation_role = str(room["circulation_role"])
    if "activities" in room and isinstance(room["activities"], list):
        activities = list(set(activities + room["activities"]))
    if "capabilities" in room and isinstance(room["capabilities"], list):
        capabilities = list(set(capabilities + room["capabilities"]))
    if "environmental_needs" in room and isinstance(room["environmental_needs"], list):
        environmental_needs = list(set(environmental_needs + room["environmental_needs"]))
    if "wet_area" in room:
        wet_area = bool(room["wet_area"])
    if "habitable" in room:
        habitable = bool(room["habitable"])
    if "requires_exterior_wall" in room:
        requires_exterior_wall = bool(room["requires_exterior_wall"])
    if "requires_plumbing" in room:
        requires_plumbing = bool(room["requires_plumbing"])

    # 4. Contextual inference from prompt
    prompt_l = str(prompt or "").lower()
    
    # Infer wet/plumbing from adjacent keywords or custom room names
    if "wash" in user_label or "clean" in user_label or "dog" in user_label and "bath" in user_label:
        wet_area = True
        requires_plumbing = True
        if "activities" not in room:
            activities.append("washing")
            
    # Infer privacy/noise from keywords
    if "clinic" in user_label or "therapy" in user_label:
        visitor_access = "controlled"
        privacy_level = 0.8
        if "activities" not in room:
            activities.append("consultation")
    if "studio" in user_label and ("podcast" in user_label or "music" in user_label or "recording" in user_label):
        capabilities.append("noise_sensitive")
        if "activities" not in room:
            activities.append("recording")
    if "meditation" in user_label:
        privacy_level = max(privacy_level, 0.8)
        capabilities.append("noise_sensitive")
    if "garden" in user_label and "indoor" in user_label:
        environmental_needs.extend(["daylight", "ventilation"])
    if "photography" in user_label:
        if "no window" in prompt_l or "without window" in prompt_l or "dark" in prompt_l:
            requires_exterior_wall = False
            if "daylight" in environmental_needs:
                environmental_needs.remove("daylight")

    return SemanticProfile(
        privacy_level=privacy_level,
        visitor_access=visitor_access,
        circulation_role=circulation_role,
        activities=tuple(sorted(set(activities))),
        capabilities=tuple(sorted(set(capabilities))),
        environmental_needs=tuple(sorted(set(environmental_needs))),
        wet_area=wet_area,
        habitable=habitable,
        requires_exterior_wall=requires_exterior_wall,
        requires_plumbing=requires_plumbing,
        can_be_transit=can_be_transit,
        owner_room_id=room.get("owner_room_id"),
        provenance=room.get("provenance", "generic_fallback"),
        semantic_confidence=semantic_confidence,
    )

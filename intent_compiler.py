"""Compile Gemini output and prompt wording into a typed intent contract."""
from __future__ import annotations

import re
import copy
import dataclasses
from dataclasses import replace
import logging
from typing import Any, Dict, Iterable, List, Optional

from constraint_schema import (
    ArchitecturalConstraint,
    ConstraintKind,
    ConstraintOrigin,
    ConstraintStrength,
    IntentContract,
    OptimizationPreference,
)
from candidate_contract import InternalInvariantError, RoomProvenance
from semantic_models import (
    GroupSpatialConstraint,
    SelectorCardinality,
    SelectorResolution,
    SemanticProfile,
)
from semantic_evaluator import (
    evaluate_predicate,
    get_semantic_alias_predicate,
    infer_semantic_profile,
)


logger = logging.getLogger("homevision")


_TYPE_ALIASES = {
    "living": "living_room", "living room": "living_room", "hall": "living_room",
    "dining": "dining_room", "dining room": "dining_room",
    "master bedroom": "master_bedroom", "master room": "master_bedroom",
    "washroom": "bathroom", "toilet": "bathroom",
    "passage": "corridor", "hallway": "corridor",
}


def _canonical(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    text = re.sub(r"\s+", " ", text)
    return _TYPE_ALIASES.get(text, text.replace(" ", "_"))


def _room_index(rooms: Iterable[Dict[str, Any]]) -> tuple[Dict[str, str], Dict[str, List[str]]]:
    by_id: Dict[str, str] = {}
    by_type: Dict[str, List[str]] = {}
    for index, room in enumerate(rooms):
        room_type = _canonical(room.get("type") or room.get("room_type") or "room")
        room_id = str(room.get("id") or f"{room_type}-{index + 1}")
        by_id[_canonical(room_id)] = room_id
        by_type.setdefault(room_type, []).append(room_id)
    return by_id, by_type


def _is_bedroom(room: Dict[str, Any]) -> bool:
    return "bedroom" in _canonical(room.get("type"))


def _is_bathroom(room: Dict[str, Any]) -> bool:
    room_type = _canonical(room.get("type"))
    return any(token in room_type for token in ("bath", "toilet", "washroom", "powder"))


def _is_circulation(room: Dict[str, Any]) -> bool:
    return _canonical(room.get("type")) in {
        "corridor", "hallway", "passage", "lobby", "entrance_lobby",
    }


_COUNT_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def requested_utility_count(prompt: str) -> Optional[int]:
    """Return an explicit utility count, preserving separately named functions."""
    text = re.sub(r"[^a-z0-9]+", " ", str(prompt or "").lower())
    pattern = re.compile(
        r"\b(?:(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+)?"
        r"(?:(?:separate|distinct|dedicated|laundry|storage|service|washing)\s+){0,3}"
        r"utilit(?:y|ies)(?:\s+areas?|\s+rooms?)?\b"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        # Laundry/washing area is a utility role when explicitly requested as
        # a room, but the word "utility" itself need not be present.
        if re.search(r"\b(?:a|an|one)\s+(?:laundry|washing)\s+(?:room|area)\b", text):
            return 1
        return None
    explicit_total = 0
    implicit_singulars = 0
    for match in matches:
        token = match.group(1)
        if token:
            explicit_total += int(token) if token.isdigit() else _COUNT_WORDS[token]
        elif "utilities" not in match.group(0):
            implicit_singulars += 1
    if explicit_total:
        return explicit_total + implicit_singulars
    if len(matches) > 1 or implicit_singulars:
        return max(1, implicit_singulars or len(matches))
    return None


def circulation_minimization_requested(prompt: str) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", str(prompt or "").lower())
    return bool(re.search(
        r"\b(?:minimal|minimize|minimise|little|least|reduced|compact)\b.{0,35}"
        r"\b(?:corridor|circulation|hallway|passage)(?:s|\s+space|\s+area)?\b"
        r"|\b(?:very\s+little|minimal)\s+(?:corridor|circulation)\s+space\b",
        text,
    ))


def circulation_preferences(prompt: str) -> List[OptimizationPreference]:
    if not circulation_minimization_requested(prompt):
        return []
    evidence = next((part.strip() for part in re.split(r"[.;\n]", str(prompt or ""))
                     if re.search(r"corridor|circulation|hallway|passage", part, re.I)), str(prompt or ""))
    logger.info(
        "[CIRCULATION INTENT] %r compiled to strong circulation-area minimization",
        evidence,
    )
    return [OptimizationPreference(
        kind="circulation_area", goal="minimize",
        strength=ConstraintStrength.STRONG, origin=ConstraintOrigin.USER,
        evidence=evidence, weight=1.0,
    )]


def _selector_matches(ref: Any, rooms: Iterable[Dict[str, Any]]) -> List[str]:
    """Resolve an architectural selector by identity, role, owner, and floor."""
    values = list(rooms)
    key = _canonical(ref)
    if not key:
        return []
    exact = [str(room.get("id")) for room in values if str(room.get("id")) == str(ref)]
    if exact:
        return exact

    master_ids = [
        str(room.get("id")) for room in values
        if _is_bedroom(room) and (
            _canonical(room.get("type")) == "master_bedroom"
            or _canonical(room.get("bedroom_role")) == "master"
            or _canonical(room.get("role")) == "master"
        )
    ]
    if key in {"master_bedroom", "primary_bedroom", "master_room"}:
        return master_ids

    attached = [
        str(room.get("id")) for room in values
        if _is_bathroom(room) and (
            _canonical(room.get("bathroom_role")) == "attached"
            or _canonical(room.get("role")) == "attached"
        )
    ]
    if key in {"master_bathroom", "master_bath", "master_ensuite"}:
        master_set = set(master_ids)
        return [
            str(room.get("id")) for room in values
            if str(room.get("id")) in attached
            and str(room.get("owner_room_id") or room.get("assigned_to") or room.get("attached_to_id")) in master_set
        ]
    if key in {"attached_bathroom", "attached_bath", "ensuite", "ensuite_bathroom"}:
        return attached
    if key in {"common_bathroom", "common_bath", "shared_bathroom", "general_bathroom", "powder_room"}:
        return [
            str(room.get("id")) for room in values
            if _is_bathroom(room) and (
                _canonical(room.get("bathroom_role")) == "common"
                or _canonical(room.get("role")) == "common"
                or (key == "powder_room" and _canonical(room.get("type")) == "powder_room")
            )
        ]
    if key in {"utility", "utility_area", "utility_room"}:
        return [str(room.get("id")) for room in values if _canonical(room.get("type")) in {
            "utility", "utility_area", "utility_room", "laundry",
        }]
    if key in {"corridor", "hallway", "passage", "circulation"}:
        return [str(room.get("id")) for room in values if _is_circulation(room)]
    if key in {"bedroom", "bedrooms"}:
        return [str(room.get("id")) for room in values if _is_bedroom(room)]
    if key in {"bathroom", "bathrooms", "toilet", "washroom"}:
        return [str(room.get("id")) for room in values if _is_bathroom(room)]

    return [
        str(room.get("id")) for room in values
        if _canonical(room.get("type")) == key
    ]


def _resolve_selector_exact(ref: Any, rooms: Iterable[Dict[str, Any]]) -> str:
    matches = _selector_matches(ref, rooms)
    return matches[0] if len(matches) == 1 else ""


def _resolve(ref: Any, by_id: Dict[str, str], by_type: Dict[str, List[str]]) -> str:
    key = _canonical(ref)
    if key in by_id:
        return by_id[key]
    # Gemini often supplies a room type rather than an instance id.  Resolve
    # only when deterministic; repeated types should remain a type reference
    # for the topology generator to assign safely.
    matches = by_type.get(key, [])
    if len(matches) == 1:
        return matches[0]
    if key == "bedroom":
        matches = [room_id for room_type, ids in by_type.items() if "bedroom" in room_type for room_id in ids]
    elif key in {"bathroom", "toilet", "washroom"}:
        matches = [room_id for room_type, ids in by_type.items() if any(token in room_type for token in ("bath", "toilet", "washroom")) for room_id in ids]
    return matches[0] if len(matches) == 1 else key


def _relationship_constraint(
    item: Dict[str, Any], by_id: Dict[str, str], by_type: Dict[str, List[str]]
) -> Optional[ArchitecturalConstraint]:
    relation = _canonical(item.get("relation") or item.get("kind") or "near")
    source = _resolve(item.get("subject_room") or item.get("source"), by_id, by_type)
    target = _resolve(item.get("target_room") or item.get("target"), by_id, by_type)
    if not source:
        return None

    mapping = {
        "near": (ConstraintKind.NEAR, ConstraintStrength.STRONG),
        "close_to": (ConstraintKind.NEAR, ConstraintStrength.STRONG),
        "beside": (ConstraintKind.ADJACENT, ConstraintStrength.STRONG),
        "next_to": (ConstraintKind.ADJACENT, ConstraintStrength.STRONG),
        "adjacent": (ConstraintKind.ADJACENT, ConstraintStrength.STRONG),
        "attached": (ConstraintKind.DIRECT_CONNECTION, ConstraintStrength.HARD),
        "connected": (ConstraintKind.DIRECT_CONNECTION, ConstraintStrength.HARD),
        "direct_door": (ConstraintKind.DIRECT_CONNECTION, ConstraintStrength.HARD),
        "directly_connected": (ConstraintKind.DIRECT_CONNECTION, ConstraintStrength.HARD),
        "accessible": (ConstraintKind.REACHABLE, ConstraintStrength.HARD),
        "accessible_from": (ConstraintKind.REACHABLE, ConstraintStrength.HARD),
        "away_from": (ConstraintKind.SEPARATION, ConstraintStrength.STRONG),
        "not_near": (ConstraintKind.SEPARATION, ConstraintStrength.STRONG),
        "between": (ConstraintKind.BETWEEN, ConstraintStrength.STRONG),
        "open_flow": (ConstraintKind.OPEN_FLOW, ConstraintStrength.HARD),
    }
    if relation in {"north", "south", "east", "west", "north_east", "north_west", "south_east", "south_west"}:
        return ArchitecturalConstraint(
            ConstraintKind.DIRECTION, source, value=relation,
            strength=ConstraintStrength.HARD, origin=ConstraintOrigin.USER,
            weight=float(item.get("weight", 1.0) or 1.0),
            original_source_selector=str(item.get("subject_room") or item.get("source") or ""),
        )
    kind, strength = mapping.get(relation, (ConstraintKind.NEAR, ConstraintStrength.PREFERENCE))
    return ArchitecturalConstraint(
        kind, source, target=target or None, strength=strength,
        origin=ConstraintOrigin.USER if item.get("required", True) else ConstraintOrigin.GEMINI_SUGGESTION,
        weight=float(item.get("weight", 1.0) or 1.0),
        original_source_selector=str(item.get("subject_room") or item.get("source") or ""),
        original_target_selector=str(item.get("target_room") or item.get("target") or ""),
    )


def _dedupe(constraints: Iterable[ArchitecturalConstraint]) -> List[ArchitecturalConstraint]:
    chosen: Dict[tuple, ArchitecturalConstraint] = {}
    for item in constraints:
        key = (item.kind, item.source, item.target, item.value)
        prior = chosen.get(key)
        if prior is None or item.priority_weight > prior.priority_weight:
            chosen[key] = item
    return list(chosen.values())


def _requested_attached_count(prompt: str, bedroom_count: int) -> Optional[int]:
    text = re.sub(r"[^a-z0-9]+", " ", str(prompt or "").lower())
    if re.search(r"\b(?:no|without|do not (?:include|add|create)|zero)\b.{0,25}\battached bathrooms?\b", text):
        return 0
    if re.search(r"\b(?:both|each|every)\s+bedrooms?\b.{0,45}\b(?:attached|own|ensuite)\b", text):
        return bedroom_count
    match = re.search(
        r"\b(a|an|one|two|three|four|five|\d+)\s+(?:private\s+)?(?:attached|ensuite)\s+bathrooms?\b",
        text,
    )
    if match:
        token = match.group(1)
        return int(token) if token.isdigit() else _COUNT_WORDS[token]
    if re.search(r"\b(?:master|primary)\s+bedroom\b.{0,45}\b(?:attached|ensuite)\s+bathroom\b", text):
        return 1
    if re.search(r"\b(?:attached|ensuite)\s+bathroom\b", text):
        return 1
    return None


def _requested_corridor_count(prompt: str) -> Optional[int]:
    text = re.sub(r"[^a-z0-9]+", " ", str(prompt or "").lower())
    match = re.search(
        r"\b(a|an|one|two|three|four|five|\d+)\s+(?:short\s+|central\s+|separate\s+)?"
        r"(?:corridors?|hallways?|passages?)\b",
        text,
    )
    if not match:
        return None
    token = match.group(1)
    return int(token) if token.isdigit() else _COUNT_WORDS[token]


def bind_room_roles(
    prompt: str,
    extraction: Optional[Dict[str, Any]],
    rooms: Iterable[Dict[str, Any]],
    *,
    bhk: int = 0,
    floor_index: int = 0,
    program_id: str = "",
) -> List[Dict[str, Any]]:
    """Create canonical IDs, normalize optional counts, and bind semantic roles.

    This is the single pre-topology identity boundary.  It intentionally does
    not create coordinates or choose a graph family.
    """
    result = copy.deepcopy(list(rooms))
    extraction = extraction or {}
    program_id = program_id or str(extraction.get("program_id") or extraction.get("job_id") or f"floor-{floor_index}")

    # Canonical room IDs are assigned before any ownership or selector work.
    counters: Dict[str, int] = {}
    seen_ids: set[str] = set()
    for room in result:
        room_type = _canonical(room.get("type") or room.get("room_type") or "room")
        room["type"] = room_type
        
        # Attach semantic profile immediately
        profile = infer_semantic_profile(room, prompt)
        room["semantic_profile"] = profile.to_dict()
        logger.info(
            "[SEMANTIC PROFILE] room_id=pending privacy=%s visitor_access=%s circulation_role=%s confidence=%s",
            profile.privacy_level, profile.visitor_access, profile.circulation_role, profile.semantic_confidence
        )

        counters[room_type] = counters.get(room_type, 0) + 1
        room_id = str(room.get("id") or f"{room_type}-{counters[room_type]}")
        if room_id in seen_ids:
            raise InternalInvariantError(
                f"program={program_id}: duplicate canonical room ID {room_id!r}; "
                f"available_canonical_room_ids={sorted(seen_ids)}"
            )
        room["id"] = room_id
        room["floor_index"] = int(room.get("floor_index", floor_index) or floor_index)
        seen_ids.add(room_id)

    # An explicitly singular utility beats a duplicated model schedule. Two
    # separately named utility functions remain two distinct rooms.
    utility_count = requested_utility_count(prompt)
    utilities = [room for room in result if _canonical(room.get("type")) in {
        "utility", "utility_area", "utility_room", "laundry",
    }]
    if utility_count is not None and len(utilities) > utility_count:
        keep_ids = {str(room["id"]) for room in utilities[:utility_count]}
        old_count = len(utilities)
        result = [room for room in result if room not in utilities or str(room["id"]) in keep_ids]
        logger.info(
            "[ROOM NORMALIZATION] utility %s -> %s because prompt explicitly requested %s utility",
            old_count, utility_count, "singular" if utility_count == 1 else utility_count,
        )
        utilities = [room for room in result if str(room.get("id")) in keep_ids]
    for index, room in enumerate(utilities, 1):
        room["type"] = "utility"
        room["provenance"] = RoomProvenance.EXPLICIT_USER.value if utility_count else room.get(
            "provenance", RoomProvenance.GEMINI_SUGGESTION.value
        )
        room["required"] = bool(utility_count) or bool(room.get("required"))
        room["required_by_user"] = bool(utility_count)
        name = _canonical(room.get("name"))
        if "laundry" in name:
            room["utility_purpose"] = "laundry"
        elif "storage" in name or "store" in name:
            room["utility_purpose"] = "storage"

    # A request to minimize circulation is a cost objective. It neither asks
    # for multiple corridors nor makes a corridor an explicit user room.
    corridor_count = _requested_corridor_count(prompt)
    circulation = [room for room in result if _is_circulation(room)]
    if circulation_minimization_requested(prompt) and corridor_count is None and len(circulation) > 1:
        old_circulation_count = len(circulation)
        retained = circulation[0]
        result = [room for room in result if not _is_circulation(room) or room is retained]
        circulation = [retained]
        logger.info(
            "[ROOM NORMALIZATION] corridor %s -> 1 because prompt requests minimal circulation area",
            old_circulation_count,
        )
    needs_synthetic = (
        corridor_count is None
        and not circulation
        and len(result) > 4
        and any(_is_bedroom(room) for room in result)
    )
    if needs_synthetic:
        candidate_id = "corridor-1"
        suffix = 1
        while candidate_id in {str(room.get("id")) for room in result}:
            suffix += 1
            candidate_id = f"corridor-{suffix}"
        synthesized = {
            "id": candidate_id,
            "type": "corridor",
            "name": "Corridor",
            "floor_index": floor_index,
            "connections": [],
        }
        result.append(synthesized)
        circulation = [synthesized]
    if corridor_count is None:
        for room in circulation:
            room.update({
                "provenance": RoomProvenance.TOPOLOGY_SYNTHESIZED.value,
                "required": False,
                "required_by_user": False,
                "role": "circulation",
                "can_be_passage": True,
            })
            logger.info(
                "[CIRCULATION SYNTHESIS] corridor -> %s provenance=topology_synthesized",
                room["id"],
            )

    bedrooms = [room for room in result if _is_bedroom(room)]
    masters = [room for room in bedrooms if _canonical(room.get("type")) == "master_bedroom"]
    prompt_l = str(prompt or "").lower()
    if not masters and bedrooms and re.search(r"\b(?:master|primary)\s+bedroom\b", prompt_l):
        masters = [bedrooms[0]]
        masters[0]["type"] = "master_bedroom"
    for room in bedrooms:
        is_master = room in masters
        room["bedroom_role"] = "master" if is_master else "standard"
        room["role"] = room["bedroom_role"]
        room["can_be_passage"] = False
        if is_master:
            logger.info("[ROLE BINDING] master_bedroom -> %s", room["id"])

    bathrooms = [room for room in result if _is_bathroom(room)]
    requested_attached = _requested_attached_count(prompt, len(bedrooms))
    attached = [room for room in bathrooms if _canonical(room.get("bathroom_role")) == "attached"]
    has_explicit_bath_roles = any(
        _canonical(room.get("bathroom_role_provenance")) in {"explicit_user", "extraction", "floor_schedule"}
        or bool(room.get("owner_room_id") or room.get("assigned_to") or room.get("attached_to_id"))
        for room in bathrooms
    )
    if requested_attached is not None and not has_explicit_bath_roles:
        attached = bathrooms[:min(requested_attached, len(bathrooms))]
    owner_order = masters + [room for room in bedrooms if room not in masters]
    for index, bathroom in enumerate(bathrooms):
        if bathroom in attached:
            raw_owner = bathroom.get("owner_room_id") or bathroom.get("assigned_to") or bathroom.get("attached_to_id")
            owner_id = _resolve_selector_exact(raw_owner, result) if raw_owner else ""
            if not owner_id and owner_order:
                owner_id = str(owner_order[min(attached.index(bathroom), len(owner_order) - 1)]["id"])
            if not owner_id:
                raise InternalInvariantError(
                    f"program={program_id}: attached bathroom {bathroom['id']} has no resolvable bedroom owner; "
                    f"available_canonical_room_ids={sorted(str(room['id']) for room in result)}"
                )
            bathroom.update({
                "bathroom_role": "attached", "role": "attached",
                "owner_room_id": owner_id, "assigned_to": owner_id,
                "attached_to_id": owner_id, "can_be_passage": False,
            })
            selector = "attached_bathroom"
            if owner_id in {str(room["id"]) for room in masters}:
                selector = "master_bathroom"
            logger.info("[ROLE BINDING] %s -> %s owner=%s", selector, bathroom["id"], owner_id)
        else:
            bathroom.update({
                "bathroom_role": "common", "role": "common",
                "owner_room_id": None, "can_be_passage": False,
            })
            bathroom.pop("assigned_to", None)
            bathroom.pop("attached_to_id", None)
            logger.info("[ROLE BINDING] common_bathroom -> %s", bathroom["id"])

    # Resolve legacy connection endpoints too. Topology generation recreates
    # graph edges, but no symbolic alias is allowed to cross this boundary.
    available = sorted(str(room["id"]) for room in result)
    requested_connections = resolved_connections = 0
    for room in result:
        normalized_connections: List[Dict[str, Any]] = []
        for index, connection in enumerate(room.get("connections", []) or []):
            requested_connections += 1
            original = str(connection.get("original_target_selector") or connection.get("target_room_id")
                           or connection.get("target_room") or "")
            target_id = _resolve_selector_exact(original, result)
            if not target_id:
                if str(connection.get("origin") or "architectural_default").lower() != "user":
                    logger.info(
                        "[OPTIONAL RELATION NORMALIZATION] dropped relation=connection:%s:%s "
                        "target=%r because its optional room was pruned",
                        room["id"], index, original,
                    )
                    continue
                raise InternalInvariantError(
                    f"program={program_id}: relation=connection:{room['id']}:{index} "
                    f"original_selector={original!r} attempted_resolved_id={target_id!r} "
                    f"available_canonical_room_ids={available}"
                )
            connection["original_target_selector"] = original
            connection["target_room_id"] = target_id
            normalized_connections.append(connection)
            resolved_connections += 1
        room["connections"] = normalized_connections
    return result


def reassign_bathroom_owner(
    rooms: Iterable[Dict[str, Any]], bathroom_id: str, owner_room_id: str,
) -> List[Dict[str, Any]]:
    """Atomically convert one canonical bathroom to an owner-only ensuite."""
    result = copy.deepcopy(list(rooms))
    by_id = {str(room.get("id")): room for room in result}
    bathroom = by_id.get(str(bathroom_id))
    owner = by_id.get(str(owner_room_id))
    if not bathroom or not _is_bathroom(bathroom):
        raise InternalInvariantError(f"Cannot reassign unknown bathroom {bathroom_id!r}")
    if not owner or not _is_bedroom(owner):
        raise InternalInvariantError(f"Cannot assign bathroom to unknown bedroom {owner_room_id!r}")

    # Remove the old common-access edge in both legacy directions. The exact
    # exclusive owner edge is recompiled from owner_room_id.
    for room in result:
        room["connections"] = [
            connection for connection in room.get("connections", []) or []
            if not (
                (str(room.get("id")) == bathroom_id and str(connection.get("target_room_id")) != owner_room_id)
                or (str(room.get("id")) != owner_room_id and str(connection.get("target_room_id")) == bathroom_id)
            )
        ]
    bathroom.update({
        "bathroom_role": "attached", "bathroom_role_provenance": "explicit_user",
        "role": "attached", "owner_room_id": owner_room_id,
        "assigned_to": owner_room_id, "attached_to_id": owner_room_id,
        "can_be_passage": False,
    })
    bathroom["connections"] = [{
        "target_room": owner.get("type"), "target_room_id": owner_room_id,
        "original_target_selector": owner_room_id,
        "intent": "direct_door", "kind": "exclusive_access",
        "strength": "hard", "origin": "user", "weight": 30,
    }]
    return result


def compile_intent(
    prompt: str,
    extraction: Optional[Dict[str, Any]],
    rooms: Iterable[Dict[str, Any]],
    program_id: str = "",
) -> IntentContract:
    """Return a typed contract; language relationships are never door edges by accident."""
    extraction = extraction or {}
    rooms = list(rooms)
    program_id = program_id or str(extraction.get("program_id") or extraction.get("job_id") or "topology-program")
    by_id, by_type = _room_index(rooms)
    constraints: List[ArchitecturalConstraint] = []

    for raw in extraction.get("typed_constraints", []) or extraction.get("constraints", []) or []:
        if not isinstance(raw, dict):
            continue
        value = dict(raw)
        value["original_source_selector"] = str(value.get("original_source_selector") or value.get("source") or "")
        value["original_target_selector"] = str(value.get("original_target_selector") or value.get("target") or "")
        value["source"] = _resolve(value.get("source"), by_id, by_type)
        if value.get("target"):
            value["target"] = _resolve(value.get("target"), by_id, by_type)
        constraints.append(ArchitecturalConstraint.from_dict(value))

    for raw in extraction.get("requested_relationships", []) or []:
        if isinstance(raw, dict):
            compiled = _relationship_constraint(raw, by_id, by_type)
            if compiled:
                constraints.append(compiled)

    # Deterministic prompt helpers may already have resolved an explicit room
    # instance pair. Preserve that provenance in the same typed contract.
    for room in rooms:
        for connection in room.get("connections", []) or []:
            if connection.get("origin") != "user" or not connection.get("target_room_id"):
                continue
            kind_value = connection.get("kind") or {
                "proximity": "near", "adjacent": "adjacent",
                "direct_door": "direct_connection", "open_flow": "open_flow",
            }.get(connection.get("intent"), "near")
            try:
                kind = ConstraintKind(str(kind_value))
            except ValueError:
                continue
            constraints.append(ArchitecturalConstraint(
                kind=kind,
                source=str(room.get("id")),
                target=str(connection.get("target_room_id")),
                strength=ConstraintStrength(str(connection.get("strength", "strong"))),
                origin=ConstraintOrigin.USER,
                weight=float(connection.get("weight", 1.0) or 1.0),
            ))

    for raw in extraction.get("vastu_specifics", []) or []:
        if isinstance(raw, dict) and raw.get("room") and raw.get("location"):
            constraints.append(ArchitecturalConstraint(
                ConstraintKind.DIRECTION,
                _resolve(raw["room"], by_id, by_type),
                value=_canonical(raw["location"]),
                strength=ConstraintStrength.HARD,
                origin=ConstraintOrigin.USER,
            ))

    # Preserve explicit instance ownership for ensuites even when Gemini only
    # put the assignment on the room program.
    for room in rooms:
        room_type = _canonical(room.get("type"))
        role = _canonical(room.get("bathroom_role"))
        owner = room.get("owner_room_id") or room.get("assigned_to") or room.get("attached_to_id")
        if (role == "attached" or "ensuite" in room_type or "attached_bath" in room_type) and owner:
            constraints.append(ArchitecturalConstraint(
                ConstraintKind.EXCLUSIVE_ACCESS, str(room.get("id")),
                target=_resolve(owner, by_id, by_type),
                strength=ConstraintStrength.HARD,
                origin=ConstraintOrigin.USER,
            ))

    prompt_l = str(prompt or "").lower()
    open_plan = bool(re.search(r"\b(open[ -]?plan|open kitchen|open living|open dining|central open hall)\b", prompt_l))
    topology_hint = str(extraction.get("topology") or extraction.get("circulation_topology") or "")
    entry_ref = extraction.get("primary_entry_room_id") or ""
    entry_room_id = _resolve(entry_ref, by_id, by_type) if entry_ref else ""
    known_ids = set(by_id.values())
    if entry_room_id not in known_ids:
        entry_room_id = next((by_type[kind][0] for kind in ("foyer", "entrance_lobby", "living_room", "lobby", "corridor") if by_type.get(kind)), "")

    def _resolve_with_semantics(selector: Any) -> SelectorResolution:
        if not selector:
            return SelectorResolution("", SelectorCardinality.SINGLE, (), None, 0.0)
        sel_str = str(selector)
        
        # Check predicate alias
        predicate = get_semantic_alias_predicate(sel_str)
        if predicate:
            matched_ids = []
            for r in rooms:
                prof = SemanticProfile.from_dict(r.get("semantic_profile", {}))
                if evaluate_predicate(predicate, prof):
                    matched_ids.append(str(r["id"]))
            logger.info("[SELECTOR RESOLUTION] selector=%s cardinality=predicate_set matched_ids=%s", sel_str, matched_ids)
            return SelectorResolution(sel_str, SelectorCardinality.PREDICATE, tuple(matched_ids), predicate, 1.0)
        
        # Fallback to scalar match
        matches = _selector_matches(sel_str, rooms)
        if matches:
            if len(matches) > 1 and sel_str in {"bedroom", "bedrooms", "bathroom", "bathrooms", "toilet", "washroom", "corridor", "hallway", "passage", "circulation"}:
                 return SelectorResolution(sel_str, SelectorCardinality.SET, tuple(matches), None, 1.0)
            return SelectorResolution(sel_str, SelectorCardinality.SINGLE, tuple(matches), None, 1.0)
            
        return SelectorResolution(sel_str, SelectorCardinality.SINGLE, (), None, 0.0)

    resolved_scalars: List[ArchitecturalConstraint] = []
    resolved_groups: List[GroupSpatialConstraint] = []
    available = sorted(known_ids)
    requested_relations = 0
    
    for constraint in _dedupe(constraints):
        requested_relations += 1
        original_source = constraint.original_source_selector or constraint.source
        original_target = constraint.original_target_selector or constraint.target or ""
        
        source_res = _resolve_with_semantics(original_source)
        target_res = _resolve_with_semantics(original_target) if original_target else None

        source_ids = list(source_res.room_ids)
        target_ids = list(target_res.room_ids) if target_res else []
        
        # If it's a semantic group logic constraint
        if source_res.cardinality in (SelectorCardinality.PREDICATE, SelectorCardinality.SET) or (target_res and target_res.cardinality in (SelectorCardinality.PREDICATE, SelectorCardinality.SET)):
            import hashlib
            raw_id = f"{constraint.kind.value}|{original_source}|{original_target}|{constraint.origin.value}"
            c_id = "group_" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
            
            # Zoning constraints, reachability from groups, etc.
            grp = GroupSpatialConstraint(
                id=c_id,
                source_selector=original_source,
                target_selector=original_target if original_target else None,
                resolved_source_room_ids=tuple(source_ids),
                resolved_target_room_ids=tuple(target_ids),
                kind=constraint.kind.value,
                strength=constraint.strength.value,
                provenance=constraint.origin.value,
                original_source_selector=original_source,
                original_target_selector=original_target,
            )
            resolved_groups.append(grp)
            logger.info("[GROUP CONSTRAINT] kind=%s source_count=%s target_count=%s provenance=%s", grp.kind, len(source_ids), len(target_ids), grp.provenance)
            continue

        if not source_ids:
            if constraint.origin != ConstraintOrigin.USER:
                logger.info(
                    "[OPTIONAL RELATION NORMALIZATION] dropped relation=%s source=%r "
                    "because its optional room was pruned",
                    constraint.relation_id, original_source,
                )
                continue
            # The planner sometimes attributes a constraint to the user that
            # names a room the program never contains (a "master_bathroom"
            # that was canonicalised into an attached bath). Losing one
            # adjacency preference is far better than losing the house; the
            # room program itself is still enforced by the semantic gate.
            logger.warning(
                "[RELATION DROPPED] program=%s relation=%s source=%r does not resolve to any "
                "room in the program; available=%s",
                program_id, constraint.relation_id, original_source, available,
            )
            continue
        if constraint.target and not target_ids:
            if constraint.origin != ConstraintOrigin.USER:
                if constraint.kind == ConstraintKind.REACHABLE and entry_room_id:
                    target_ids = [entry_room_id]
                    logger.info(
                        "[OPTIONAL RELATION NORMALIZATION] relation=%s target=%r -> entry=%s",
                        constraint.relation_id, original_target, entry_room_id,
                    )
                else:
                    logger.info(
                        "[OPTIONAL RELATION NORMALIZATION] dropped relation=%s target=%r "
                        "because its optional room was pruned",
                        constraint.relation_id, original_target,
                    )
                    continue
        if constraint.target and not target_ids:
            logger.warning(
                "[RELATION DROPPED] program=%s relation=%s target=%r does not resolve to any "
                "room in the program; available=%s",
                program_id, constraint.relation_id, original_target, available,
            )
            continue

        pairs: List[tuple[str, Optional[str]]]
        if not constraint.target:
            pairs = [(source, None) for source in source_ids]
        elif len(source_ids) == len(target_ids) and len(source_ids) > 1:
            pairs = list(zip(source_ids, target_ids))
        elif len(source_ids) == 1:
            pairs = [(source_ids[0], target) for target in target_ids]
        elif len(target_ids) == 1:
            pairs = [(source, target_ids[0]) for source in source_ids]
        else:
            raise InternalInvariantError(
                f"program={program_id}: relation={constraint.relation_id} selectors are ambiguous "
                f"source={original_source!r}->{source_ids} target={original_target!r}->{target_ids}; "
                f"available_canonical_room_ids={available}"
            )
        for source_id, target_id in pairs:
            if target_id and source_id == target_id:
                continue
            current = replace(
                constraint,
                source=source_id,
                target=target_id,
                original_source_selector=original_source,
                original_target_selector=original_target,
            )
            resolved_scalars.append(current)
            if original_source != source_id:
                logger.info("[SELECTOR RESOLUTION] %s -> %s", original_source, source_id)
            if original_target and original_target != target_id:
                logger.info("[SELECTOR RESOLUTION] %s -> %s", original_target, target_id)

    contract = IntentContract(
        constraints=_dedupe(resolved_scalars),
        open_plan=open_plan,
        topology_hint=topology_hint,
        entry_room_id=entry_room_id,
        optimization_preferences=circulation_preferences(prompt),
        program_id=program_id,
        group_constraints=resolved_groups,
    )
    assert_relation_endpoints(contract, rooms, candidate_id=program_id)
    logger.info(
        "[CONSTRAINT AUDIT] scalar_relations=%s group_constraints=%s resolved_selectors=%s unresolved=0",
        len(contract.constraints), len(contract.group_constraints), requested_relations
    )
    return contract


def assert_relation_endpoints(
    contract: IntentContract,
    rooms: Iterable[Dict[str, Any]],
    *,
    candidate_id: str = "",
) -> None:
    """Drop contract relations that no longer name a room on this floor.

    Rooms move between floors and get pruned after the contract is compiled,
    which used to abort the whole request. A relation whose endpoint is gone is
    simply no longer meaningful, so it is dropped and reported.
    """
    room_ids = {str(room.get("id")) for room in rooms}
    available = sorted(room_ids)
    owner = candidate_id or contract.program_id or "topology-program"
    live_relations = []
    for relation in contract.constraints:
        if relation.source not in room_ids:
            logger.warning(
                "[RELATION DROPPED] %s: relation=%s source=%r is not on this floor; available=%s",
                owner, relation.relation_id,
                relation.original_source_selector or relation.source, available,
            )
            continue
        if relation.target and relation.target not in room_ids:
            logger.warning(
                "[RELATION DROPPED] %s: relation=%s target=%r is not on this floor; available=%s",
                owner, relation.relation_id,
                relation.original_target_selector or relation.target, available,
            )
            continue
        live_relations.append(relation)
    contract.constraints = live_relations

    
    # Group constraints get the same treatment: narrow the group to the rooms
    # actually present, and drop it once it no longer refers to anything.
    live_groups = []
    for grp in contract.group_constraints:
        source_ids = [rid for rid in grp.resolved_source_room_ids if rid in room_ids]
        target_ids = [rid for rid in grp.resolved_target_room_ids if rid in room_ids]
        if len(source_ids) != len(grp.resolved_source_room_ids) or                 len(target_ids) != len(grp.resolved_target_room_ids):
            logger.warning(
                "[GROUP CONSTRAINT NARROWED] %s: group=%s kept %d/%d sources and %d/%d targets "
                "present on this floor",
                owner, grp.id, len(source_ids), len(grp.resolved_source_room_ids),
                len(target_ids), len(grp.resolved_target_room_ids),
            )
        if not source_ids or (grp.resolved_target_room_ids and not target_ids):
            continue
        # GroupSpatialConstraint is frozen, so narrow it by rebuilding.
        live_groups.append(dataclasses.replace(
            grp,
            resolved_source_room_ids=tuple(source_ids),
            resolved_target_room_ids=tuple(target_ids),
        ))
    contract.group_constraints = live_groups


def apply_contract_to_room_specs(
    rooms: Iterable[Dict[str, Any]], contract: IntentContract
) -> List[Dict[str, Any]]:
    """Attach geometry-relevant typed constraints without flattening semantics."""
    import copy

    result = copy.deepcopy(list(rooms))
    by_id = {str(room.get("id")): room for room in result}
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for room in result:
        by_type.setdefault(_canonical(room.get("type")), []).append(room)

    def targets(ref: str) -> List[Dict[str, Any]]:
        if ref in by_id:
            return [by_id[ref]]
        return by_type.get(_canonical(ref), [])

    for constraint in contract.constraints:
        source_rooms = targets(constraint.source)
        for source in source_rooms:
            source.setdefault("typed_constraints", []).append(constraint.to_dict())
            if constraint.kind == ConstraintKind.DIRECTION:
                source.setdefault("direction_constraints", []).append(constraint.to_dict())
            elif constraint.target and constraint.kind in {ConstraintKind.NEAR, ConstraintKind.SEPARATION}:
                for target in targets(constraint.target)[:1]:
                    source.setdefault("connections", []).append({
                        "target_room": target.get("type"),
                        "target_room_id": target.get("id"),
                        "intent": "proximity" if constraint.kind == ConstraintKind.NEAR else "separation",
                        "kind": constraint.kind.value,
                        "strength": constraint.strength.value,
                        "origin": constraint.origin.value,
                        "weight": max(1, int(constraint.priority_weight / 20)),
                    })
    return result


def annotate_room_provenance(
    rooms: Iterable[Dict[str, Any]], prompt: str, extraction: Optional[Dict[str, Any]],
    bhk: int = 0,
) -> List[Dict[str, Any]]:
    """Mark why every room exists and whether omission is permissible."""
    result = copy.deepcopy(list(rooms))
    prompt_l = re.sub(r"[^a-z0-9]+", " ", str(prompt or "").lower())
    extraction = extraction or {}
    user_refs = set()
    for relationship in extraction.get("requested_relationships", []) or []:
        if isinstance(relationship, dict):
            user_refs.add(_canonical(relationship.get("subject_room")))
            user_refs.add(_canonical(relationship.get("target_room")))
    for constraint in extraction.get("typed_constraints", []) or []:
        if isinstance(constraint, dict) and str(constraint.get("origin", "user")) == "user":
            user_refs.add(_canonical(constraint.get("source")))
            user_refs.add(_canonical(constraint.get("target")))

    bedroom_seen = bathroom_seen = 0
    for room in result:
        room_type = _canonical(room.get("type"))
        label = room_type.replace("_", " ")
        explicitly_named = bool(label and re.search(rf"\b{re.escape(label)}s?\b", prompt_l))
        explicitly_named = explicitly_named or room_type in user_refs or str(room.get("id")) in user_refs
        if room.get("required") is True and room.get("provenance"):
            provenance = RoomProvenance(str(room["provenance"]))
        elif explicitly_named and "bhk" not in label:
            provenance = RoomProvenance.EXPLICIT_USER
        elif "bedroom" in room_type:
            bedroom_seen += 1
            provenance = RoomProvenance.IMPLIED_BHK if bedroom_seen <= max(1, bhk) else RoomProvenance.GEMINI_SUGGESTION
        elif any(token in room_type for token in ("bath", "toilet", "washroom")):
            bathroom_seen += 1
            # One common bathroom is the minimal usable BHK program. Further
            # bathrooms remain suggestions unless the user requested them or
            # they have explicit attached ownership.
            if room.get("bathroom_role") == "attached" or room.get("assigned_to") or room.get("attached_to_id"):
                provenance = RoomProvenance.IMPLIED_BHK
            else:
                provenance = RoomProvenance.IMPLIED_BHK if bathroom_seen == 1 and bhk else RoomProvenance.GEMINI_SUGGESTION
        elif room_type in {"living_room", "kitchen"} and bhk:
            provenance = RoomProvenance.IMPLIED_BHK
        elif room_type in {"corridor", "hallway", "passage", "lobby", "staircase", "stairwell"}:
            provenance = RoomProvenance.BUILDING_REQUIREMENT
        elif room.get("required") is True:
            provenance = RoomProvenance.EXPLICIT_USER
        else:
            provenance = RoomProvenance.GEMINI_SUGGESTION
        room["provenance"] = provenance.value
        room["required"] = provenance in {
            RoomProvenance.EXPLICIT_USER,
            RoomProvenance.IMPLIED_BHK,
            RoomProvenance.BUILDING_REQUIREMENT,
        }
    return result


def prune_optional_suggestions(rooms: Iterable[Dict[str, Any]], prompt: str) -> List[Dict[str, Any]]:
    """Drop unsupported optional-room suggestions before topology search.

    This is provenance-driven rather than a global room-count limit. Explicit
    rooms and distinct user-requested utilities are always retained.
    """
    prompt_l = re.sub(r"[^a-z0-9]+", " ", str(prompt or "").lower())
    optional_types = {
        "utility", "utility_room", "courtyard", "foyer", "pooja_room",
        "powder_room", "extra_bathroom", "additional_bathroom",
    }
    result: List[Dict[str, Any]] = []
    seen_optional: Dict[str, int] = {}
    for room in rooms:
        room_type = _canonical(room.get("type"))
        if room.get("required", True) or room.get("provenance") != RoomProvenance.GEMINI_SUGGESTION.value:
            result.append(room)
            continue
        label = room_type.replace("_", " ")
        mentioned = bool(label and re.search(rf"\b{re.escape(label)}s?\b", prompt_l))
        if room_type in optional_types and not mentioned:
            continue
        seen_optional[room_type] = seen_optional.get(room_type, 0) + 1
        if seen_optional[room_type] > 1 and not re.search(rf"\b(?:two|2|three|3|multiple)\s+{re.escape(label)}s?\b", prompt_l):
            continue
        result.append(room)
    return result

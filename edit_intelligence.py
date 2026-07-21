"""Transactional validation for natural-language floor-plan edits.

The language model may interpret intent and the geometry engine may propose a
layout, but neither is allowed to declare success.  This module compiles the
user's measurable requirements and independently accepts or rejects candidate
layouts.  It intentionally has no AI or solver dependency.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


OPEN_TYPES = {"courtyard", "void", "balcony", "veranda", "portico", "parking", "terrace"}
MINIMUMS = {
    "corridor": (3.0, 0.0), "hallway": (3.0, 0.0), "bathroom": (4.0, 20.0),
    "pooja_room": (4.0, 20.0), "kitchen": (6.0, 42.0), "bedroom": (7.0, 70.0),
    "master_bedroom": (8.0, 90.0), "living_room": (8.0, 90.0),
    "dining_room": (6.0, 48.0), "staircase": (3.0, 30.0),
}


def canonical(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    for word, digit in {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}.items():
        text = re.sub(rf"(?:^|_){word}(?=_|$)", lambda match: ("_" if match.group(0).startswith("_") else "") + digit, text)
    aliases = {
        "living": "living_room", "drawing_room": "living_room", "dining": "dining_room",
        "pooja": "pooja_room", "puja": "pooja_room", "puja_room": "pooja_room",
        "masterbedroom": "master_bedroom", "master_bed": "master_bedroom",
        "toilet": "bathroom", "washroom": "bathroom", "hallway": "corridor",
    }
    text = aliases.get(text, text)
    # Access modifiers and harmless misspellings must not create a new room
    # taxonomy in the independent edit validator. Preserve an instance suffix
    # (bathroom_2) while resolving common_bathroom, genral_bathroom,
    # attached_bathroom, washroom, etc. to the same canonical room type.
    suffix_match = re.search(r"(_\d+)$", text)
    suffix = suffix_match.group(1) if suffix_match else ""
    descriptor = text[: -len(suffix)] if suffix else text
    words = set(descriptor.split("_"))
    bathroom_words = {
        "bath", "bathroom", "toilet", "washroom", "ensuite",
        "attached", "common", "shared", "guest", "general", "genral", "en", "suite",
    }
    is_room_label = len(words) <= 4 and words.issubset(bathroom_words)
    if is_room_label and (
        words.intersection({"bath", "bathroom", "toilet", "washroom", "ensuite"})
        or any(word.startswith("bath") for word in words)
    ):
        return "bathroom" + suffix
    return text


def room_keys(room: Dict[str, Any]) -> Set[str]:
    keys = {
        canonical(room.get("id")), canonical(room.get("legacy_id")),
        canonical(room.get("type")), canonical(room.get("name")),
    }
    room_id = canonical(room.get("id"))
    keys.add(re.sub(r"_\d+$", "", room_id))
    return {key for key in keys if key}


def floor_key(room: Dict[str, Any]) -> int:
    try:
        return int(room.get("floorIndex", room.get("floor", 1 if room.get("isFloor1") else 0)) or 0)
    except (TypeError, ValueError):
        return 0


def share_boundary(a: Dict[str, Any], b: Dict[str, Any], tolerance: float = 0.2) -> bool:
    if floor_key(a) != floor_key(b):
        return False
    ax, az, aw, al = (float(a.get(k, 0)) for k in ("x", "z", "width", "length"))
    bx, bz, bw, bl = (float(b.get(k, 0)) for k in ("x", "z", "width", "length"))
    overlap_x = min(ax + aw, bx + bw) - max(ax, bx)
    overlap_z = min(az + al, bz + bl) - max(az, bz)
    return (
        overlap_x >= 2.0 and (abs(az + al - bz) <= tolerance or abs(bz + bl - az) <= tolerance)
    ) or (
        overlap_z >= 2.0 and (abs(ax + aw - bx) <= tolerance or abs(bx + bw - ax) <= tolerance)
    )


def _world_doors(room: Dict[str, Any]) -> List[Tuple[float, float, Dict[str, Any]]]:
    result = []
    for door in room.get("doors", []) or []:
        if isinstance(door, dict):
            try:
                result.append((float(room.get("x", 0)) + float(door.get("x", 0)),
                               float(room.get("z", 0)) + float(door.get("z", 0)), door))
            except (TypeError, ValueError):
                continue
    return result


def paired_door(a: Dict[str, Any], b: Dict[str, Any], tolerance: float = 0.4) -> bool:
    if not share_boundary(a, b):
        return False
    return any(
        abs(ax - bx) <= tolerance and abs(az - bz) <= tolerance
        for ax, az, ad in _world_doors(a) if not ad.get("is_main")
        for bx, bz, bd in _world_doors(b) if not bd.get("is_main")
    )


@dataclass(frozen=True)
class Relationship:
    subject: str
    target: str
    kind: str = "adjacent"  # adjacent, direct_door, no_direct_door, not_adjacent, zone_*
    required: bool = True


@dataclass
class EditContract:
    action: str = ""
    subject: str = ""
    relationships: List[Relationship] = field(default_factory=list)
    ambiguity: List[str] = field(default_factory=list)


@dataclass
class CandidateReport:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    score: float = math.inf


def _mentions(prompt: str, rooms: Sequence[Dict[str, Any]]) -> List[str]:
    text = canonical(prompt)
    matches: List[Tuple[int, int, int, str]] = []
    for room_index, room in enumerate(rooms):
        for key in room_keys(room):
            pos = text.find(key)
            if pos >= 0:
                matches.append((pos, -len(key), room_index, str(room.get("id") or key)))
    ordered = []
    for _, _, _, room_id in sorted(set(matches)):
        if room_id not in ordered:
            ordered.append(room_id)
    return ordered


def _infer_added_subject(prompt: str) -> str:
    match = re.search(
        r"\badd\s+(?:(?:a|an|the)\s+)?([a-z][a-z\s]*?)(?=\s+(?:to|near|beside|next\s+to|adjacent\s+to|by|at|on|with)\b|[,.]|$)",
        prompt.lower(),
    )
    if not match:
        return ""
    subject = re.sub(r"^(?:new\s+|proper\s+|attached\s+|ensuite\s+|en[- ]?suite\s+)+", "", match.group(1).strip())
    return canonical(subject)


def compile_contract(
    prompt: str, previous_rooms: Sequence[Dict[str, Any]], ai: Optional[Dict[str, Any]] = None,
) -> EditContract:
    text = (prompt or "").lower()
    ai = ai or {}
    action = str(ai.get("intent") or "").lower()
    if action not in {"add", "move", "remove", "delete", "resize"}:
        if re.search(r"\b(?:remove|delete)\b", text): action = "remove"
        elif re.search(r"\bmove|relocate|position\b", text): action = "move"
        elif re.search(r"\bresize|expand|increase|shrink|reduce|larger|smaller\b", text): action = "resize"
        elif re.search(r"\badd\b", text) and not re.search(
            r"\badd\s+(?:a|an|the)?\s*(?:proper\s+)?(?:door|doorway|window|furniture|fixture|light|wiring|plumbing)\b",
            text,
        ):
            action = "add"

    mentioned = _mentions(prompt, previous_rooms)
    subject = canonical(ai.get("move_target_room"))
    if action == "add":
        # The phrase after ADD is authoritative. AI target lists often place
        # the existing anchor before the new room.
        subject = _infer_added_subject(prompt) or canonical(next(iter(ai.get("target_rooms", []) or []), ""))
    elif not subject and mentioned:
        subject = mentioned[0]

    relationships: List[Relationship] = []
    for item in ai.get("requested_relationships", []) or []:
        if not isinstance(item, dict):
            continue
        kind = canonical(item.get("relation") or "adjacent")
        if kind in {"beside", "near", "next_to", "attached"}: kind = "adjacent"
        if kind in {"accessible", "direct_access", "door", "doorway", "connected"}: kind = "direct_door"
        relationships.append(Relationship(
            canonical(item.get("subject_room") or subject), canonical(item.get("target_room")),
            kind, bool(item.get("required", True)),
        ))

    # Deterministic extraction guards against incomplete AI JSON.
    other_ids = [room_id for room_id in mentioned if canonical(room_id) != canonical(subject)]
    if re.search(r"\b(?:near|beside|next\s+to|adjacent|attached)\b", text) and other_ids:
        relationships.append(Relationship(subject, canonical(other_ids[0]), "adjacent"))
    if re.search(r"\b(?:attached|ensuite|en[- ]?suite)\b", text) and other_ids:
        relationships.append(Relationship(subject, canonical(other_ids[0]), "direct_door"))
        circulation_ids = [
            str(room.get("id")) for room in previous_rooms
            if canonical(room.get("type")) in {"corridor", "hallway", "foyer"}
        ]
        relationships.extend(Relationship(subject, canonical(room_id), "no_direct_door") for room_id in circulation_ids)
    if re.search(r"\b(?:direct(?:ly)?\s+(?:access|accessible)|entrance.*direct|doorway\s+connecting|access(?:ible)?\s+direct)\b", text):
        direct_target = next((room_id for room_id in other_ids if canonical(room_id) in {"living_room_1", "living_room", "corridor_1", "corridor"}), "")
        if not direct_target and other_ids:
            direct_target = other_ids[-1]
        if direct_target:
            relationships.append(Relationship(subject, canonical(direct_target), "direct_door"))
    transit_match = re.search(r"\bwithout\s+passing\s+through\s+(?:the\s+)?([a-z][a-z\s]*?)(?=[,.]|\s+(?:or|and)\b|$)", text)
    if transit_match:
        relationships.append(Relationship(subject, canonical(transit_match.group(1)), "no_direct_door"))
    if re.search(r"\b(?:must\s+not|do\s+not|not)\s+(?:be\s+)?(?:near|beside|adjacent|attached)\b", text) and other_ids:
        relationships.append(Relationship(subject, canonical(other_ids[-1]), "not_adjacent"))

    compass = [direction for direction in ("north", "south", "east", "west") if re.search(rf"\b{direction}(?:ern)?\b", text)]
    compact_text = re.sub(r"[\s-]+", "", text)
    for compound, parts in {
        "northeast": ("north", "east"), "northwest": ("north", "west"),
        "southeast": ("south", "east"), "southwest": ("south", "west"),
    }.items():
        if compound in compact_text:
            compass.extend(parts)
    compass = list(dict.fromkeys(compass))
    if compass and action == "move":
        zone = "zone_" + "_".join(compass[:2])
        relationships.append(Relationship(subject, "", zone))

    unique = list(dict.fromkeys(relationships))
    ambiguity = []
    if action in {"remove", "move", "resize"} and not subject:
        ambiguity.append("No existing target room could be identified unambiguously.")
    if action in {"remove", "move", "resize"} and len(mentioned) > 1 and not re.search(
        r"\b(?:north|south|east|west|first|second|third|one|two|three|four|five|six|master|guest|children|kids)\b|(?:^|[_\s-])\d+(?:$|[_\s-])",
        text,
    ):
        mentioned_rooms = [next((room for room in previous_rooms if str(room.get("id")) == room_id), None) for room_id in mentioned]
        mentioned_types = [canonical(room.get("type")) for room in mentioned_rooms if room]
        if mentioned_types and len(set(mentioned_types)) == 1:
            ambiguity.append(f"More than one {mentioned_types[0].replace('_', ' ')} matches the request.")
    relationship_kinds: Dict[Tuple[str, str], Set[str]] = {}
    for item in unique:
        relationship_kinds.setdefault((item.subject, item.target), set()).add(item.kind)
    for (rel_subject, rel_target), kinds in relationship_kinds.items():
        if {"adjacent", "not_adjacent"}.issubset(kinds) or {"direct_door", "no_direct_door"}.issubset(kinds):
            ambiguity.append(f"Contradictory requirements were given for {rel_subject} and {rel_target}.")
    return EditContract(action=action, subject=subject, relationships=unique, ambiguity=ambiguity)


def _resolve(token: str, rooms: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    key = canonical(token)
    exact = [room for room in rooms if key in room_keys(room)]
    if exact:
        return exact
    base = re.sub(r"_\d+$", "", key)
    return [room for room in rooms if base and any(base == re.sub(r"_\d+$", "", item) for item in room_keys(room))]


def _geometry_errors(rooms: Sequence[Dict[str, Any]], plot_width: float, plot_length: float) -> List[str]:
    errors = []
    seen = set()
    valid_rects = []
    for room in rooms:
        room_id = str(room.get("id") or "")
        if not room_id or room_id in seen:
            errors.append(f"Room identity is missing or duplicated: {room_id or '<missing>'}.")
        seen.add(room_id)
        try:
            x, z, width, length = (float(room.get(k, 0)) for k in ("x", "z", "width", "length"))
        except (TypeError, ValueError):
            errors.append(f"{room_id} has non-numeric geometry.")
            continue
        valid_rects.append((room, x, z, width, length))
        if width <= 0 or length <= 0:
            errors.append(f"{room_id} has a non-positive room size.")
        if x < -0.1 or z < -0.1 or x + width > plot_width + 0.1 or z + length > plot_length + 0.1:
            errors.append(f"{room_id} lies outside the {plot_width:g}x{plot_length:g} plot.")
        room_type = canonical(room.get("type"))
        min_dim, min_area = MINIMUMS.get(room_type, (0.0 if room_type in OPEN_TYPES else 3.0, 0.0))
        if min(width, length) + 0.1 < min_dim or width * length + 0.5 < min_area:
            errors.append(f"{room_id} is below the usable minimum for {room_type.replace('_', ' ')}.")
    for index, (first, ax, az, aw, al) in enumerate(valid_rects):
        for second, bx, bz, bw, bl in valid_rects[index + 1:]:
            if floor_key(first) != floor_key(second):
                continue
            if min(ax + aw, bx + bw) - max(ax, bx) > 0.1 and min(az + al, bz + bl) - max(az, bz) > 0.1:
                errors.append(f"{first.get('id')} overlaps {second.get('id')}.")
    return errors


def _door_graph(rooms: Sequence[Dict[str, Any]]) -> Dict[str, Set[str]]:
    graph = {str(room.get("id")): set() for room in rooms}
    for index, first in enumerate(rooms):
        for second in rooms[index + 1:]:
            if paired_door(first, second):
                a, b = str(first.get("id")), str(second.get("id"))
                graph[a].add(b); graph[b].add(a)
    return graph


def _reachable_count(rooms: Sequence[Dict[str, Any]]) -> int:
    if not rooms:
        return 0
    graph = _door_graph(rooms)
    entries = {
        str(room.get("id")) for room in rooms
        if any(door.get("is_main") for _, _, door in _world_doors(room))
    }
    if not entries:
        entries = {str(room.get("id")) for room in rooms if canonical(room.get("type")) in {"living_room", "corridor", "foyer"}}
    pending = list(entries); visited = set(entries)
    while pending:
        current = pending.pop()
        for neighbour in graph.get(current, set()) - visited:
            visited.add(neighbour); pending.append(neighbour)
    return len({room_id for room_id in visited if canonical(next((r.get("type") for r in rooms if str(r.get("id")) == room_id), "")) not in OPEN_TYPES})


def evaluate_candidate(
    previous_rooms: Sequence[Dict[str, Any]], candidate_rooms: Sequence[Dict[str, Any]],
    contract: EditContract, plot_width: float, plot_length: float,
) -> CandidateReport:
    errors = list(contract.ambiguity)
    # Legacy projects may already contain a marginal room. Permit incremental
    # repair, but never introduce a new geometry violation.
    previous_geometry_errors = set(_geometry_errors(previous_rooms, float(plot_width), float(plot_length)))
    errors.extend(
        error for error in _geometry_errors(candidate_rooms, float(plot_width), float(plot_length))
        if error not in previous_geometry_errors
    )
    previous_ids = {str(room.get("id")) for room in previous_rooms}
    candidate_ids = {str(room.get("id")) for room in candidate_rooms}
    subject_before = _resolve(contract.subject, previous_rooms)
    subject_after = _resolve(contract.subject, candidate_rooms)

    if contract.action == "add" and not subject_after:
        errors.append(f"The requested {contract.subject.replace('_', ' ')} was not added.")
    if contract.action in {"remove", "delete"} and subject_before and any(str(r.get("id")) in candidate_ids for r in subject_before):
        errors.append("The requested room still exists after the removal.")
    if contract.action in {"move", "resize"} and not subject_after:
        errors.append("The target room was lost during the edit.")
    allowed_removed = {str(room.get("id")) for room in subject_before} if contract.action in {"remove", "delete"} else set()
    lost = previous_ids - candidate_ids - allowed_removed
    if lost:
        errors.append("Unrequested rooms disappeared: " + ", ".join(sorted(lost)) + ".")

    for relationship in contract.relationships:
        subjects = _resolve(relationship.subject or contract.subject, candidate_rooms)
        if relationship.kind.startswith("zone_"):
            if not subjects:
                errors.append(f"Could not resolve required position for {relationship.subject or contract.subject}.")
                continue
            directions = set(relationship.kind.removeprefix("zone_").split("_"))
            satisfied = True
            for subject_room in subjects:
                center_x = float(subject_room.get("x", 0)) + float(subject_room.get("width", 0)) / 2.0
                center_z = float(subject_room.get("z", 0)) + float(subject_room.get("length", 0)) / 2.0
                satisfied = satisfied and ("west" not in directions or center_x <= plot_width / 2.0)
                satisfied = satisfied and ("east" not in directions or center_x >= plot_width / 2.0)
                satisfied = satisfied and ("north" not in directions or center_z <= plot_length / 2.0)
                satisfied = satisfied and ("south" not in directions or center_z >= plot_length / 2.0)
            if relationship.required and not satisfied:
                errors.append(f"{relationship.subject or contract.subject} is not in the requested {' '.join(sorted(directions))} zone.")
            continue
        targets = _resolve(relationship.target, candidate_rooms)
        if not subjects or not targets:
            if relationship.required:
                errors.append(f"Could not resolve required relationship {relationship.subject} -> {relationship.target}.")
            continue
        satisfied = any(
            (not share_boundary(a, b) if relationship.kind == "not_adjacent"
             else not paired_door(a, b) if relationship.kind == "no_direct_door"
             else paired_door(a, b) if relationship.kind == "direct_door"
             else share_boundary(a, b))
            for a in subjects for b in targets if a is not b
        )
        if relationship.required and not satisfied:
            label = {
                "direct_door": "a direct paired doorway to", "no_direct_door": "a route avoiding",
                "not_adjacent": "separation from",
            }.get(relationship.kind, "adjacency to")
            errors.append(f"{relationship.subject or contract.subject} does not have {label} {relationship.target}.")

    # Never make overall door accessibility worse. Full accessibility is only a
    # hard gate when the existing project already had it; legacy plans can still
    # be repaired incrementally instead of becoming permanently uneditable.
    old_reachable = _reachable_count(previous_rooms)
    new_reachable = _reachable_count(candidate_rooms)
    old_habitable = sum(canonical(room.get("type")) not in OPEN_TYPES for room in previous_rooms)
    if old_reachable >= old_habitable and new_reachable < sum(canonical(room.get("type")) not in OPEN_TYPES for room in candidate_rooms):
        errors.append("The edit makes one or more rooms inaccessible from the entrance/circulation network.")
    elif new_reachable < old_reachable - len(allowed_removed):
        errors.append("The edit breaks existing room accessibility.")

    alternatives = []
    if errors:
        if contract.action == "add": alternatives.extend(["Allow a local floor re-layout.", "Reduce the new room size or expand the plot."])
        elif contract.action == "move": alternatives.extend(["Allow a room-cell swap.", "Relax one adjacency requirement."])
        elif contract.action in {"remove", "delete"}: alternatives.append("Convert the removed cell into circulation or merge it with a named neighbour.")
        alternatives.append("Specify exact room names/IDs when more than one room has the same type.")

    previous_by_id = {str(room.get("id")): room for room in previous_rooms}
    score = 0.0
    for room in candidate_rooms:
        old = previous_by_id.get(str(room.get("id")))
        if not old:
            score += 25.0
            continue
        score += sum(abs(float(room.get(key, 0)) - float(old.get(key, 0))) for key in ("x", "z", "width", "length"))
    score += 50.0 * len(errors)
    return CandidateReport(not errors, errors, [], list(dict.fromkeys(alternatives)), score)


def select_best_candidate(
    previous_rooms: Sequence[Dict[str, Any]], candidates: Iterable[Sequence[Dict[str, Any]]],
    contract: EditContract, plot_width: float, plot_length: float,
) -> Tuple[Optional[List[Dict[str, Any]]], CandidateReport]:
    reports = [(list(candidate), evaluate_candidate(previous_rooms, candidate, contract, plot_width, plot_length)) for candidate in candidates if candidate is not None]
    valid = [item for item in reports if item[1].valid]
    if valid:
        return min(valid, key=lambda item: item[1].score)
    if reports:
        return None, min(reports, key=lambda item: item[1].score)[1]
    return None, CandidateReport(False, ["No geometry strategy produced a candidate layout."], alternatives=["Relax one constraint or permit a broader re-layout."])

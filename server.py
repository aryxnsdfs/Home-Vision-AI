"""
server.py — Home Vision AI Backend with 3-layer NLP matching + Physics BitMLP.

Endpoints:
    POST /api/generate   — Parse user prompt → structured layout params
    POST /api/template   — Generate layout from predefined template
    GET  /api/health      — Health check

NLP Pipeline:
    1. Regex extraction (numbers: BHK count, area, budget)
    2. Token-level 3-layer matching (exact → fuzzy → semantic)
    3. Physics BitMLP inference for cost/carbon/safety (if model loaded)

No external API calls. All processing is local.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import os
import copy
from dotenv import load_dotenv
load_dotenv()

import queue as _queue_mod
import re
import threading as _threading
import traceback
import random
import uuid
from difflib import SequenceMatcher
from datetime import datetime
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Sequence
from blueprint_renderer import BlueprintRenderer

import numpy as np
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import mep_generator
import structural_generator
from cost_engine import CostEngine

from layout_engine import (
    LayoutEngine, AdjacencyResolver, WindowPlacer, ArchitecturalRules,
    Door, Window, Rect, RoomNode, compute_minimum_plot_area, ROOM_MINIMUMS, resolve_theme,
    align_duplex_floors, compute_shared_walls, validate_layout
)
from room_planner import (
    sort_spec_by_generation_order, strip_structural, split_duplex_specs,
    final_layout_validation, INSUFFICIENT_SPACE_MSG,
    requested_type_set, enforce_requested_only,
)
from matcher import MultiVocabularyMatcher, VocabularyMatcher
from vocabulary import (
    ALL_VOCABULARIES,
    INTENT_ACTIONS,
    MATERIALS,
    ROOMS,
    SIZE_MODIFIERS,
    STYLES,
    TYPOLOGY,
)
from cloud_extractor import extract_keywords_groq, reason_modifications_deepseek
from local_extractor import extract_keywords_to_json
from semantic_analyzer import evaluate_complexity
from geometry_engine import LayoutGeometryEngine
from asset_library import (
    build_outdoor_areas,
    canonical_type,
    furniture_for_room,
    is_basement_type,
    requested_custom_specs,
    requested_outdoor_specs,
    split_outdoor_specs,
)


def split_site_specs(specs: List[Dict[str, Any]], prompt: str = "") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separate site/roof requests before the indoor layout solver runs."""
    indoor, outdoor = split_outdoor_specs(specs)
    existing_outdoor = {canonical_type(s.get("type")) for s in outdoor}
    for requested in requested_outdoor_specs(prompt):
        if canonical_type(requested.get("type")) not in existing_outdoor:
            outdoor.append(requested)
            existing_outdoor.add(canonical_type(requested.get("type")))

    existing_indoor = {canonical_type(s.get("type")) for s in indoor}
    for requested in requested_custom_specs(prompt):
        if canonical_type(requested.get("type")) not in existing_indoor:
            indoor.append(requested)
            existing_indoor.add(canonical_type(requested.get("type")))

    basement = [s for s in indoor if is_basement_type(s.get("type"))]
    indoor = [s for s in indoor if not is_basement_type(s.get("type"))]
    if "basement" in (prompt or "").lower() and not basement:
        basement = [{"type": "basement", "confidence": 100, "requested_by_prompt": True}]
    return indoor, outdoor, basement


def materialize_site_layers(
    engine: LayoutEngine,
    outdoor_specs: List[Dict[str, Any]],
    basement_specs: List[Dict[str, Any]],
    building_nodes: List[RoomNode],
    plot_width: float,
    plot_length: float,
    floors: int,
    prompt: str = "",
    indian_options: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[RoomNode]]:
    """Create site features and an aligned basement without touching indoor nodes."""
    outdoor = build_outdoor_areas(
        outdoor_specs, plot_width, plot_length, building_nodes, floors=floors, prompt=prompt
    )
    basement_nodes: List[RoomNode] = []
    basement_walls: List[Dict[str, Any]] = []
    if basement_specs and building_nodes:
        min_x = min(n.rect.x for n in building_nodes)
        max_x = max(n.rect.x + n.rect.width for n in building_nodes)
        min_z = min(n.rect.z for n in building_nodes)
        max_z = max(n.rect.z + n.rect.length for n in building_nodes)
        basement_spec = {
            "type": "basement", "id": "basement-1",
            "fixed_rect": (min_x, min_z, max_x - min_x, max_z - min_z),
            "connections": [{"target_room": "staircase", "weight": 10}],
        }
        try:
            basement_nodes = engine.generate(
                [basement_spec], indian_options=indian_options or {}, restrict_slots=False
            )
            for node in basement_nodes:
                node.is_basement = True
                node.floorIndex = -1
            basement_walls = compute_shared_walls(basement_nodes)
        except Exception as exc:
            logger.warning("Basement generation skipped after safe fallback: %s", exc)
            basement_nodes = []
    for area in outdoor:
        area["assets"] = area.get("assets", [])
    return outdoor, basement_walls, basement_nodes


def serialize_floor_nodes(nodes: Iterable[RoomNode], floor_index: int) -> List[Dict[str, Any]]:
    payload = []
    for node in nodes or []:
        item = node.to_dict()
        item["floorIndex"] = floor_index
        item["isFloor1"] = floor_index == 1
        if floor_index < 0:
            item["is_basement"] = True
        payload.append(item)
    return payload


def attach_requested_outdoor_areas(
    response: Dict[str, Any], current_rooms: List[Dict[str, Any]],
    prompt: str, plot_width: float, plot_length: float, floors: int,
) -> bool:
    """Handle site additions on an existing project without carving an indoor room."""
    specs = requested_outdoor_specs(prompt)
    if not specs or not current_rooms:
        return False
    nodes = []
    for index, room in enumerate(current_rooms):
        try:
            nodes.append(RoomNode(
                id=str(room.get("id", f"existing-{index}")),
                type=str(room.get("type", "room")),
                name=str(room.get("name", "Room")),
                rect=Rect(float(room.get("x", 0)), float(room.get("z", 0)),
                          float(room.get("width", 10)), float(room.get("length", 10))),
            ))
        except (TypeError, ValueError):
            continue
    if not nodes:
        return False
    layout_data, _ = _preserve_modified_project_rooms(current_rooms)
    layout_data["outdoor_areas"] = build_outdoor_areas(specs, plot_width, plot_length, nodes, floors=floors, prompt=prompt)
    response["layout_data"] = layout_data
    response.setdefault("understood", []).append("Placed requested outdoor areas separately from the house")
    return True


def generate_additional_floors(
    engine: LayoutEngine,
    source_specs: List[Dict[str, Any]],
    ground_nodes: List[RoomNode],
    start_floor: int,
    floor_count: int,
    indian_options: Optional[Dict[str, Any]] = None,
    floor_specs_by_level: Optional[Dict[int, List[Dict[str, Any]]]] = None,
) -> Dict[int, List[RoomNode]]:
    """Extend the established duplex recipe to every requested upper level."""
    import copy
    result: Dict[int, List[RoomNode]] = {}
    if floor_count <= start_floor:
        return result
    specs = copy.deepcopy(source_specs or [{"type": "living_room"}, {"type": "staircase"}])
    ground_stair = next((node for node in ground_nodes if node.type == "staircase"), None)
    for level in range(start_floor, floor_count):
        level_specs = copy.deepcopy((floor_specs_by_level or {}).get(level) or specs)
        if ground_stair and not any(canonical_type(s.get("type")) == "staircase" for s in level_specs):
            level_specs.append({
                "type": "staircase", "id": f"staircase-f{level}",
                "fixed_rect": (ground_stair.rect.x, ground_stair.rect.z, ground_stair.rect.width, ground_stair.rect.length),
            })
        nodes = engine.generate(level_specs, indian_options=indian_options or {}, restrict_slots=True)
        apply_requested_room_names(nodes, level_specs)
        align_duplex_floors(ground_nodes, nodes, make_void=False)
        ArchitecturalRules.optimize_wet_walls(nodes)
        AdjacencyResolver(nodes).resolve()
        WindowPlacer(nodes, engine.plot_width, engine.plot_length,
                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
        for node in nodes:
            node.doors = [door for door in node.doors if not getattr(door, "is_main", False)]
            node.floorIndex = level
        result[level] = nodes
    return result


def apply_requested_room_names(nodes: Iterable[RoomNode], specs: Iterable[Dict[str, Any]]) -> None:
    """Keep the user's requested wording in the UI while geometry uses canonical types."""
    available = list(specs or [])
    used: set[int] = set()
    for node in nodes or []:
        node_type = canonical_type(node.type)
        if node_type in {"staircase", "stairwell"}:
            node.name = "Staircase"
            continue
        if node_type in {"corridor", "circulation", "hallway", "passage", "lobby"}:
            node.name = "Corridor"
            continue
        for index, spec in enumerate(available):
            if index in used or canonical_type(spec.get("type")) != canonical_type(node.type):
                continue
            requested_name = str(spec.get("name") or spec.get("label") or "").strip()
            if requested_name:
                node.name = requested_name
            if canonical_type(node.type) in {"courtyard", "angan", "open_to_sky"} or str(spec.get("roof_type", "")).lower() == "open":
                node.roof_type = "open"
                node.is_outdoor = True
            used.add(index)
            break


_GENERIC_AI_ROOM_TYPES = {"room", "space", "area", "other", "custom_room", "custom_space"}
_INTERNAL_OPEN_TYPES = {"courtyard", "angan", "aangan", "open_courtyard", "open_to_sky"}


def is_instruction_like_room_label(value: Any) -> bool:
    """Reject action clauses accidentally emitted as open-ended room names."""
    label = re.sub(r"[_-]+", " ", str(value or "").lower()).strip()
    if any(stair in label for stair in ("staircase", "stairwell", "stair", "landing", "lift", "elevator")):
        return False
    has_floor_clause = bool(re.search(
        r"\b(?:ground|first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)\s+floor\b", label,
    ))
    has_action = bool(re.search(r"\b(?:add\d*|add|generate|create|build|make|put)\b", label))
    return has_floor_clause and has_action


def normalize_ai_room_spec(raw_spec: Any) -> Optional[Dict[str, Any]]:
    """Preserve a model-supplied room name when its coarse type is generic.

    Gemini occasionally emits ``{"type": "room", "name": "dining_room"}``.
    Treating that as a literal ``room`` loses furniture, minimum dimensions,
    adjacency rules, labels, and edit targeting.  The name is the more precise
    semantic signal in that case.
    """
    if isinstance(raw_spec, str):
        item: Dict[str, Any] = {"type": raw_spec, "name": raw_spec.replace("_", " ")}
    elif isinstance(raw_spec, dict):
        item = dict(raw_spec)
    else:
        return None

    raw_type_hint = canonical_type(item.get("type"))
    if is_instruction_like_room_label(item.get("type")) or (
        raw_type_hint in _GENERIC_AI_ROOM_TYPES and is_instruction_like_room_label(item.get("name"))
    ):
        logger.warning("[SEMANTIC GUARD] Rejected instruction-shaped room label: %s", item.get("name") or item.get("type"))
        return None

    raw_type = canonical_type(item.get("type"))
    raw_name = canonical_type(item.get("name") or item.get("label"))
    if raw_type in _GENERIC_AI_ROOM_TYPES and raw_name and raw_name not in _GENERIC_AI_ROOM_TYPES:
        raw_type = raw_name
    if not raw_type:
        raw_type = raw_name
    if not raw_type:
        return None
    if raw_type in {"angan", "aangan", "open_courtyard"}:
        raw_type = "courtyard"
    if raw_type in {"staircase_landing", "stair_landing", "stairwell_landing"}:
        raw_type = "staircase"

    bath_signal = f"{raw_type} {raw_name}".lower()
    if any(token in bath_signal for token in ("bath", "toilet", "washroom", "ensuite")):
        # Bathroom modifiers describe access/ownership; they are not distinct
        # geometric room types. Canonicalise even unknown or misspelled labels
        # such as ``genral_bathroom`` so edit identities remain stable.
        bath_words = re.findall(r"[a-z]+", bath_signal.replace("_", " "))
        is_common = any(
            word in {"common", "shared", "guest", "general"}
            or SequenceMatcher(None, word, "general").ratio() >= 0.82
            for word in bath_words
        )
        raw_type = "bathroom"
        if any(token in bath_signal for token in ("attached", "ensuite", "en_suite")):
            item["bathroom_role"] = "attached"
            if not item.get("name") or canonical_type(item.get("name")) in {"bathroom", "attached_bathroom"}:
                item["name"] = "Attached Bathroom"
        elif is_common:
            item["bathroom_role"] = "common"
            item["name"] = "Common Bathroom"

    item["type"] = raw_type
    item["name"] = str(item.get("name") or raw_type.replace("_", " ")).strip()
    item["confidence"] = 100
    if raw_type in _INTERNAL_OPEN_TYPES or raw_type == "courtyard":
        item["placement"] = "internal"
        item["roof_type"] = "open"
        item["is_outdoor"] = True
    return item


def ensure_internal_open_spaces(
    program: Dict[int, List[Dict[str, Any]]], extraction: Optional[Dict[str, Any]],
) -> Dict[int, List[Dict[str, Any]]]:
    """Materialize courtyards/angans that the extractor classified separately.

    ``outdoor_rooms`` is classification metadata, not permission to omit the
    space from the geometric program. Internal courtyards remain solver rooms
    so surrounding rooms can be constrained against their real boundary.
    """
    extraction = extraction or {}
    requested = [canonical_type(value) for value in extraction.get("outdoor_rooms", []) or []]
    if extraction.get("angan"):
        requested.append("courtyard")
    internal = ["courtyard" if value in _INTERNAL_OPEN_TYPES else value for value in requested if value in _INTERNAL_OPEN_TYPES]
    upper_outdoor = [value for value in requested if value in {"balcony", "terrace", "open_terrace"}]
    if internal:
        ground = program.setdefault(0, [])
        existing = {canonical_type(spec.get("type")) for specs in program.values() for spec in specs}
        for room_type in dict.fromkeys(internal):
            if room_type not in existing:
                ground.append(normalize_ai_room_spec({"type": room_type, "name": room_type}) or {"type": room_type})
                existing.add(room_type)
    if upper_outdoor:
        upper = program.setdefault(1 if len(program) > 1 else 0, [])
        existing_upper = {canonical_type(spec.get("type")) for spec in upper}
        for room_type in dict.fromkeys(upper_outdoor):
            if room_type not in existing_upper:
                upper.append({"type": room_type, "name": room_type.replace("_", " ").title(), "is_outdoor": True})
                existing_upper.add(room_type)
    return program


def apply_spatial_analysis_defaults(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """Promote the AI's explicit relationship plan into legacy edit fields."""
    if not isinstance(extraction, dict):
        return extraction
    relationships = [
        item for item in extraction.get("requested_relationships", []) or []
        if isinstance(item, dict) and item.get("subject_room") and item.get("target_room")
    ]
    if not relationships:
        return extraction
    primary = next((item for item in relationships if item.get("required", True)), relationships[0])
    subject = str(primary.get("subject_room", "")).strip()
    target = str(primary.get("target_room", "")).strip()
    intent = str(extraction.get("intent", "")).upper()
    if intent == "MOVE":
        extraction["move_target_room"] = extraction.get("move_target_room") or subject
        extraction["move_destination"] = extraction.get("move_destination") or target
    elif intent == "ADD" and subject:
        clean_subject = re.sub(r"[-_]\d+$", "", canonical_type(subject))
        if clean_subject and clean_subject not in [canonical_type(value) for value in extraction.get("target_rooms", []) or []]:
            extraction.setdefault("target_rooms", []).insert(0, clean_subject)
        extraction["move_destination"] = extraction.get("move_destination") or target
    return extraction


def spatial_analysis_messages(extraction: Optional[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    extraction = extraction or {}
    understood: List[str] = []
    warnings: List[str] = []
    summary = str(extraction.get("analysis_summary") or "").strip()
    strategy = str(extraction.get("spatial_strategy") or "").strip()
    feasibility = str(extraction.get("feasibility") or "").strip()
    if summary:
        understood.append(f"Spatial analysis: {summary}")
    if strategy and strategy != "preserve":
        understood.append(f"Placement strategy: {strategy.replace('_', ' ')}")
    blockers = [str(item).strip() for item in extraction.get("blocking_constraints", []) or [] if str(item).strip()]
    if feasibility == "impossible_without_scope_change" and blockers:
        warnings.append("AI feasibility concern: " + "; ".join(blockers[:3]))
    return understood, warnings


def extract_explicit_floor_program(prompt: str, candidates: Iterable[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Read floor headings and preserve the spaces written beneath each one.

    The room vocabulary remains open-ended: known AI types are reused when
    they occur in the text, while unfamiliar user terms remain valid canonical
    labels instead of being discarded or substituted.
    """
    # Accept ordinary prose headings as well as Markdown (``**Ground Floor**``
    # and ``### First Floor``).  Requiring a trailing colon caused detailed
    # prompts to disappear whenever the semantic model timed out.
    marker = re.compile(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
        r"(ground|first|second|third|fourth|fifth)\s+floor"
        r"\s*(?:\*\*|__)?\s*:?\s*$"
    )
    levels = {"ground": 0, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    matches = list(marker.finditer(prompt or ""))
    if not matches:
        return {}
    program: Dict[int, List[Dict[str, Any]]] = {}
    count_words = {
        "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }

    # This is a grammar vocabulary, not a layout template: it identifies room
    # nouns while all sizing, placement and adjacency remain solver/model work.
    # Longer phrases must be checked first (``family lounge`` before ``lounge``).
    room_phrases = {
        "master bedrooms": "master_bedroom", "master bedroom": "master_bedroom",
        "primary bedrooms": "bedroom", "primary bedroom": "bedroom",
        "attached bathrooms": "bathroom", "attached bathroom": "bathroom",
        "common bathrooms": "bathroom", "common bathroom": "bathroom",
        "guest bathrooms": "bathroom", "guest bathroom": "bathroom",
        "general bathrooms": "bathroom", "general bathroom": "bathroom",
        "powder rooms": "powder_room", "powder room": "powder_room",
        "family lounges": "family_lounge", "family lounge": "family_lounge",
        "sitting areas": "family_lounge", "sitting area": "family_lounge",
        "living rooms": "living_room", "living room": "living_room",
        "dining rooms": "dining_room", "dining room": "dining_room",
        "dining areas": "dining_room", "dining area": "dining_room",
        "study rooms": "study_room", "study room": "study_room",
        "study areas": "study_room", "study area": "study_room",
        "bedrooms": "bedroom", "bedroom": "bedroom",
        "bathrooms": "bathroom", "bathroom": "bathroom",
        "washrooms": "bathroom", "washroom": "bathroom",
        "balconies": "balcony", "balcony": "balcony",
        "kitchens": "kitchen", "kitchen": "kitchen",
        "corridors": "corridor", "corridor": "corridor",
        "hallways": "corridor", "hallway": "corridor",
        "staircases": "staircase", "staircase": "staircase",
        "stairs": "staircase", "foyers": "foyer", "foyer": "foyer",
    }

    def add_specs(specs: List[Dict[str, Any]], room_type: str, count: int = 1,
                  role: str = "", name: str = "") -> None:
        existing = sum(1 for item in specs if canonical_type(item.get("type")) == room_type)
        if room_type in {"corridor", "staircase"} and existing:
            return
        for offset in range(max(0, count)):
            display = name or room_type.replace("_", " ").title()
            if count > 1:
                display = f"{display} {existing + offset + 1}"
            item: Dict[str, Any] = {"type": room_type, "name": display, "confidence": 100}
            if role:
                item["bathroom_role"] = role
            if room_type == "balcony":
                item.update({"is_outdoor": True, "roof_type": "open"})
            specs.append(item)

    for index, match in enumerate(matches):
        level = levels[match.group(1).lower()]
        section = (prompt or "")[match.end(): matches[index + 1].start() if index + 1 < len(matches) else None]
        labels = [
            re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", part).strip()
            for part in re.split(r"[\r\n]+", section)
            if re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", part).strip()
        ]
        specs: List[Dict[str, Any]] = []
        for label in labels:
            lower = re.sub(r"[*_]", "", label.lower()).strip(" .")
            # Quality/relationship statements constrain existing rooms; they
            # are not additional rooms.  Balcony access is the one relationship
            # that also materializes one outdoor space per bedroom.
            if re.match(r"^(?:each|every)\s+bedroom\b", lower):
                if "balcon" in lower:
                    bedroom_count = sum(1 for item in specs if "bedroom" in canonical_type(item.get("type")))
                    current = sum(1 for item in specs if canonical_type(item.get("type")) == "balcony")
                    # “Each bedroom has access to a private balcony” uses one
                    # singular balcony shared by both rooms unless the user
                    # explicitly asks for separate/individual balconies or
                    # plural balconies.
                    separate = bool(re.search(
                        r"\b(?:separate|individual|one\s+for\s+each|balconies)\b", lower,
                    ))
                    wanted = bedroom_count if separate else 1
                    add_specs(specs, "balcony", max(0, wanted - current), name="Private Balcony")
                continue
            if lower.startswith(("adequate ", "proper ")):
                if "corridor" in lower or "circulation" in lower:
                    if not any(canonical_type(item.get("type")) == "corridor" for item in specs):
                        add_specs(specs, "corridor")
                continue
            # Everything after these clauses describes adjacency/access for
            # the room already named at the start of the bullet.  Without this
            # cut, “2 attached bathrooms ... two primary bedrooms” incorrectly
            # created two additional bedrooms.
            lower = re.split(
                r",\s*(?:each|which|with\s+each)\b|\s+(?:connected|connecting|allowing)\s+to\b",
                lower,
                maxsplit=1,
            )[0]
            if re.search(r"\bshould\s+connect\b", lower):
                lower = re.split(r"\bshould\s+connect\b", lower, maxsplit=1)[0]
            # Optional alternatives use the first stated room unless capacity
            # later allows an upgrade; they must not be counted twice.
            lower = re.split(r"\s*(?:\(\s*)?\bor\b\s+", lower, maxsplit=1)[0]
            occupied: List[Tuple[int, int]] = []
            for phrase, room_type in sorted(room_phrases.items(), key=lambda item: len(item[0]), reverse=True):
                for found in re.finditer(rf"\b{re.escape(phrase)}\b", lower):
                    if any(found.start() < end and found.end() > start for start, end in occupied):
                        continue
                    prefix = lower[max(0, found.start() - 28):found.start()]
                    count_match = re.search(
                        r"\b(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
                        r"(?:\s+(?:spacious|private|primary|attached|common|general|guest|open|well[- ]planned)){0,3}\s*$",
                        prefix,
                    )
                    token = count_match.group(1) if count_match else "one"
                    count = int(token) if token.isdigit() else count_words.get(token, 1)
                    role = ""
                    context = lower[max(0, found.start() - 18):found.end()]
                    if room_type == "bathroom":
                        if "attached" in context or "ensuite" in context:
                            role = "attached"
                        elif any(word in context for word in ("common", "general", "guest", "shared")):
                            role = "common"
                    add_specs(specs, room_type, count, role=role)
                    occupied.append((found.start(), found.end()))
        if specs:
            program[level] = specs
    return program


def _program_room_class(value: Any) -> str:
    """Normalize only equivalences that do not change a room's meaning."""
    room_type = canonical_type(value)
    if "bedroom" in room_type:
        return "bedroom"
    if room_type in {"bathroom", "toilet", "washroom", "ensuite", "attached_bathroom", "common_bathroom"}:
        return "bathroom"
    if room_type in {"hallway", "circulation", "passage", "lobby"}:
        return "corridor"
    if room_type in {"stairwell", "stairs"}:
        return "staircase"
    return room_type


def floor_program_fidelity_errors(nodes: Iterable[RoomNode], specs: Iterable[Dict[str, Any]], level: int) -> List[str]:
    """Require the realized floor to match the accepted semantic contract."""
    from collections import Counter
    specs_list = [spec for spec in (specs or []) if isinstance(spec, dict)]
    spec_map = { _program_room_class(spec.get("type")): spec for spec in specs_list }

    requested = Counter(
        _program_room_class(spec.get("type")) for spec in specs_list
        if _program_room_class(spec.get("type"))
    )
    generated = Counter(_program_room_class(node.type) for node in nodes or [] if _program_room_class(node.type))
    errors: List[str] = []
    for room_type in sorted(set(requested) | set(generated)):
        req_count = requested[room_type]
        gen_count = generated[room_type]
        if req_count != gen_count:
            room_spec = spec_map.get(room_type, {})
            role = room_spec.get('role')
            is_circulation = (
                role == 'circulation'
                or (isinstance(role, dict) and role.get('can_be_passage') is True)
                or room_spec.get('can_be_passage') is True
                or canonical_type(room_type) in {'corridor', 'circulation', 'foyer', 'hallway', 'passage', 'staircase_landing', 'lobby'}
            )
            if is_circulation:
                logger.info(
                    f"[VALIDATION BYPASS] Allowed {room_type} count mismatch "
                    f"({req_count} requested vs {gen_count} generated on floor {level}) due to dynamic corridor merging."
                )
                continue

            errors.append(
                f"FLOOR PROGRAM ERROR: floor {level} requested {req_count} "
                f"{room_type.replace('_', ' ')} room(s), but generated {gen_count}."
            )
    return errors


def upper_floor_containment_errors(ground: Iterable[RoomNode], upper: Iterable[RoomNode], level: int = 1) -> List[str]:
    """Ensure upper indoor rooms sit on the ground-floor structural footprint."""
    base = [node for node in ground or [] if not getattr(node, "is_outdoor", False) and node.roof_type != "open"]
    if not base:
        return [f"SLAB ERROR: floor {level} has no ground-floor support footprint."]
    min_x = min(node.rect.x for node in base)
    max_x = max(node.rect.x + node.rect.width for node in base)
    min_z = min(node.rect.z for node in base)
    max_z = max(node.rect.z + node.rect.length for node in base)
    errors = []
    for node in upper or []:
        if getattr(node, "is_outdoor", False) or node.roof_type == "open" or _program_room_class(node.type) == "balcony":
            continue
        if (node.rect.x < min_x - 0.1 or node.rect.z < min_z - 0.1 or
                node.rect.x + node.rect.width > max_x + 0.1 or
                node.rect.z + node.rect.length > max_z + 0.1):
            errors.append(
                f"SLAB ERROR: {node.id} on floor {level} extends outside the ground-floor footprint."
            )
    return errors


def bridge_staircase_grid_seam(nodes: Iterable[RoomNode]) -> bool:
    """Place a paired landing door across a sub-wall-thickness CP grid seam.

    The upstairs solver reserves a half-foot enclosing cell around the exact
    lower stair. Restoring the true stair can leave <= 0.5 ft between it and
    its corridor. Mutating either rectangle creates overlaps, so bridge only
    this numerical seam with one shared doorway at the common wall centre.
    """
    rooms = list(nodes or [])
    stair = next((node for node in rooms if _program_room_class(node.type) == "staircase"), None)
    if not stair or stair.doors:
        return False
    hubs = [node for node in rooms if _program_room_class(node.type) == "corridor" or node.type in {"foyer", "family_lounge"}]
    candidates = []
    s = stair.rect
    for hub in hubs:
        h = hub.rect
        overlap_z = min(s.z + s.length, h.z + h.length) - max(s.z, h.z)
        overlap_x = min(s.x + s.width, h.x + h.width) - max(s.x, h.x)
        if overlap_z >= 2.5:
            if s.x + s.width <= h.x:
                candidates.append((h.x - (s.x + s.width), "vertical", "east", "west", hub, (max(s.z, h.z) + min(s.z + s.length, h.z + h.length)) / 2))
            elif h.x + h.width <= s.x:
                candidates.append((s.x - (h.x + h.width), "vertical", "west", "east", hub, (max(s.z, h.z) + min(s.z + s.length, h.z + h.length)) / 2))
        if overlap_x >= 2.5:
            if s.z + s.length <= h.z:
                candidates.append((h.z - (s.z + s.length), "horizontal", "south", "north", hub, (max(s.x, h.x) + min(s.x + s.width, h.x + h.width)) / 2))
            elif h.z + h.length <= s.z:
                candidates.append((s.z - (h.z + h.length), "horizontal", "north", "south", hub, (max(s.x, h.x) + min(s.x + s.width, h.x + h.width)) / 2))
    candidates = [candidate for candidate in candidates if -0.01 <= candidate[0] <= 0.55]
    if not candidates:
        return False
    _, orientation, stair_face, hub_face, hub, axis_mid = min(candidates, key=lambda item: item[0])
    if orientation == "vertical":
        global_x = ((s.x + s.width) + hub.rect.x) / 2 if stair_face == "east" else ((hub.rect.x + hub.rect.width) + s.x) / 2
        global_z = axis_mid
    else:
        global_x = axis_mid
        global_z = ((s.z + s.length) + hub.rect.z) / 2 if stair_face == "south" else ((hub.rect.z + hub.rect.length) + s.z) / 2
    stair.doors.append(Door(global_x - s.x, global_z - s.z, stair_face, width=3.0))
    hub.doors.append(Door(global_x - hub.rect.x, global_z - hub.rect.z, hub_face, width=3.0))
    logger.info("[DUPLEX] Bridged %.2fft staircase grid seam to %s", min(candidates)[0], hub.id)
    return True

USE_SLM_ENGINE = True
# Frontend palette IDs are normalized here so template and AI generation use
# exactly the colors selected in ProjectSetupModal, including custom hex values.
PALETTE_HEX = {
    "off_white": "#F8F8FF", "warm_beige": "#F5F5DC", "light_grey": "#D3D3D3",
    "red": "#E2725B", "sage": "#9CA986", "charcoal": "#36454F", "beige": "#F5F5DC",
    "mustard": "#E4A010", "yellow": "#E4A010", "terracotta": "#E2725B",
    "cream": "#FDF5E6", "ivory": "#FDF5E6", "peach": "#FFDAB9", "sea_green": "#2E8B57",
    "indigo": "#4B0082", "white": "#FFFFFF", "concrete": "#808080",
    "brick": "#B22222", "wood": "#DEB887",
    "light_wood": "#C8A878", "dark_wood": "#5A3A22", "walnut": "#4B3621",
    "modern_gray": "#6B7280", "white_oak": "#D8C2A0", "teak": "#9C6B3F",
    "marble_white": "#F1F0EC", "beige_marble": "#E6DCC8", "granite": "#4A4A52",
    "wooden_flooring": "#8B5A2B", "ceramic_tile": "#D7DDE5", "concrete_finish": "#8B929D",
    "dark_grey": "#2F4F4F", "brown": "#654321",
}

def _palette_color(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("#"):
        return value
    return PALETTE_HEX.get(value.lower().replace(" ", "_"), value)


def _apply_selected_palette(nodes: List[RoomNode], colors: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    colors = colors or {}
    interior = _palette_color(colors.get("interior"))
    exterior = _palette_color(colors.get("exterior"))
    floor = _palette_color(colors.get("floor"))
    furniture = _palette_color(colors.get("furniture"))
    roof = _palette_color(colors.get("roof"))
    logger.info("[PALETTE DEBUG] Resolved palette request=%s resolved=%s node_count=%d", colors, {"interior": interior, "exterior": exterior, "floor": floor, "furniture": furniture, "roof": roof}, len(nodes))
    for node in nodes:
        if interior:
            node.wallColor = interior
        if floor:
            node.floorColor = floor
        if furniture:
            node.furnitureColor = furniture
    return {
        **({"wallFinish": interior} if interior else {}),
        **({"exteriorColor": exterior} if exterior else {}),
        **({"floorMaterial": floor} if floor else {}),
        **({"furnitureColor": furniture} if furniture else {}),
        **({"roofColor": roof} if roof else {}),
        "selectedColors": {k: v for k, v in {
            "interior": interior, "exterior": exterior, "floor": floor,
            "furniture": furniture, "roof": roof,
        }.items() if v},
    }

def smart_layout_validation(
    rooms_spec: list,
    plot_width: float,
    plot_length: float,
) -> tuple:
    """
    Validate and scale room specs dynamically based on available plot sizes.
    """
    warnings: list = []
    
    # 1. Procedural Area Memory Registration (FIXED SCHEMA)
    for r in rooms_spec:
        rtype = r.get("type", "room")
        if rtype not in ROOM_MINIMUMS:
            semantic_base = None
            lowered = str(rtype).lower()
            if any(token in lowered for token in ("bath", "toilet", "washroom")):
                semantic_base = ROOM_MINIMUMS.get("bathroom")
            elif "bedroom" in lowered:
                semantic_base = ROOM_MINIMUMS.get("bedroom")
            ai_w = float(r.get("width", math.sqrt(semantic_base["area"]) if semantic_base else 10.0))
            ai_l = float(r.get("length", math.sqrt(semantic_base["area"]) if semantic_base else 10.0))
            # FIX: Include min_dim so the final layout validator doesn't crash!
            ROOM_MINIMUMS[rtype] = {
                "area": semantic_base["area"] if semantic_base else ai_w * ai_l,
                "min_dim": semantic_base["min_dim"] if semantic_base else min(ai_w, ai_l)
            }

    buildable_area = plot_width * plot_length * 0.85
    room_types = [r["type"] for r in rooms_spec]
    min_needed = compute_minimum_plot_area(room_types)

    if buildable_area <= 0:
        buildable_area = 1.0
        
    # 2. Dynamic Proportional Plot Scaling
    if buildable_area > min_needed * 1.5:
        scale_factor = min(2.5, (buildable_area / min_needed) ** 0.45)
        for room in rooms_spec:
            if "width" in room:
                room["width"] = round(room["width"] * scale_factor, 1)
            if "length" in room:
                room["length"] = round(room["length"] * scale_factor, 1)
            
            # Do not mutate the process-global architectural minimums during
            # proportional plot scaling. Mutating them made every subsequent
            # generation larger until CP-SAT eventually had invalid domains.
        
        warnings.append(f"Scaled room dimensions dynamically by {scale_factor:.2f}x to optimally utilize the {plot_width}'x{plot_length}' plot boundary.")
        return rooms_spec, warnings, float(plot_width), float(plot_length)
        
    if buildable_area >= min_needed:
        return rooms_spec, warnings, float(plot_width), float(plot_length)

    # Never enlarge the user's legal plot silently.  That made upper floors
    # appear outside the boundary drawn by the frontend.  Keep the requested
    # boundary and let the hard solver either find a compliant plan or return a
    # concise size recommendation.
    multiplier = (min_needed / buildable_area) ** 0.5
    recommended_width = round(plot_width * multiplier + 1)
    recommended_length = round(plot_length * multiplier + 1)
    warnings.append(
        f"The provided plot size ({plot_width}'x{plot_length}') was too small for the requested layout. "
        f"A plot near {recommended_width}'x{recommended_length}' is recommended; the plot boundary was not changed."
    )
    return rooms_spec, warnings, float(plot_width), float(plot_length)


def space_recommendations(room_specs: Sequence[Dict[str, Any]], plot_width: float, plot_length: float) -> List[str]:
    """Return short, user-facing guidance without blocking feasible layouts."""
    specs = [item for item in (room_specs or []) if isinstance(item, dict)]
    types = [canonical_type(item.get("type")) for item in specs]
    plot_area = max(0.0, float(plot_width or 0)) * max(0.0, float(plot_length or 0))
    buildable_area = plot_area * 0.85
    required_area = sum(float(ROOM_MINIMUMS.get(room_type, {"area": 40.0}).get("area", 40.0)) for room_type in types) * 1.2
    bedrooms = sum(1 for room_type in types if "bedroom" in room_type)
    recommendations: List[str] = []
    if required_area > buildable_area:
        recommended_area = math.ceil(required_area / 0.85)
        recommendations.append(
            f"Space recommendation: this program needs about {math.ceil(required_area):,} sq ft including circulation; "
            f"a plot near {recommended_area:,} sq ft is more comfortable than {math.ceil(plot_area):,} sq ft. "
            "Reduce room count, reduce sizes, or allow a broader re-layout if the current plot must remain fixed."
        )
    elif bedrooms >= 4:
        recommendations.append(
            "Space recommendation: keep each bedroom at least 10×14 ft, with a 3–4 ft clear circulation path. "
            "Adding more bedrooms may require reducing living/dining space."
        )
    else:
        recommendations.append(
            "Planning note: rooms are kept above their minimum usable dimensions; the main door needs an exterior wall "
            "opening of roughly 4 ft with a clear approach."
        )
    return recommendations


def calculate_area_budget(
    room_specs: Sequence[Dict[str, Any]], plot_width: float, plot_length: float, floors: int = 1,
) -> Dict[str, Any]:
    """Fast pre-solver capacity signal for UI feedback and recovery choices."""
    specs = [item for item in (room_specs or []) if isinstance(item, dict)]
    indoor = [
        item for item in specs
        if not item.get("is_outdoor") and canonical_type(item.get("type")) not in {
            "balcony", "courtyard", "garden", "parking", "terrace", "veranda", "void"
        }
    ]
    minimum_room_area = sum(
        float(ROOM_MINIMUMS.get(canonical_type(item.get("type")), {"area": 40.0}).get("area", 40.0))
        for item in indoor
    )
    required = int(math.ceil(minimum_room_area * 1.20))  # walls + circulation allowance
    levels = max(1, int(floors or 1))
    available = int(math.floor(max(0.0, float(plot_width)) * max(0.0, float(plot_length)) * 0.85 * levels))
    ratio = required / max(1, available)
    one_floor_needed = required / levels
    aspect = max(0.25, float(plot_width) / max(float(plot_length), 1.0))
    recommended_width = int(math.ceil(math.sqrt(one_floor_needed / 0.85 * aspect) / 5.0) * 5)
    recommended_length = int(math.ceil((recommended_width / aspect) / 5.0) * 5)
    return {
        "required_sqft": required,
        "available_sqft": available,
        "usage_percent": round(ratio * 100.0, 1),
        "fits": ratio <= 1.0,
        "plot_sqft": int(float(plot_width) * float(plot_length)),
        "floors": levels,
        "recommended_plot": {"width": recommended_width, "length": recommended_length},
        "actions": [
            {"id": "add_floor", "label": "Add a Second Floor"},
            {"id": "increase_plot", "label": "Increase Plot Size"},
            {"id": "optimize", "label": "Optimize Layout"},
        ],
    }
def get_base_rooms_for_bhk(bhk: int) -> list:
    """Return the standard set of rooms for a given BHK count.

    Bedrooms are plain bedrooms by default. A Master Bedroom is NEVER invented
    here — it is only created when the user explicitly asks for one or when a
    bedroom is given an attached bathroom (see apply_bedroom_intelligence).
    """
    rooms = [
        {"type": "living_room", "confidence": 100},
        {"type": "kitchen", "confidence": 100},
        {"type": "bathroom", "confidence": 100},
    ]
    for _ in range(max(1, bhk)):
        rooms.append({"type": "bedroom", "confidence": 100})
    if bhk >= 2:
        rooms.append({"type": "bathroom", "confidence": 100})
    if bhk >= 3:
        rooms.append({"type": "dining_room", "confidence": 100})
    if bhk >= 4:
        rooms.append({"type": "bathroom", "confidence": 100})
    if bhk >= 5:
        rooms.append({"type": "store_room", "confidence": 100})
    return rooms


# Keywords that indicate the user explicitly wants a master/primary bedroom.
_MASTER_KEYWORDS = ("master bedroom", "master bed", "primary bedroom", "primary bed", "master suite")
# Keywords that indicate an attached/ensuite bathroom is wanted for a bedroom.
_ATTACHED_BATH_KEYWORDS = (
    "attached bath", "attached toilet", "attached washroom", "attached bathroom",
    "ensuite", "en-suite", "en suite",
)


def apply_bedroom_intelligence(rooms: list, prompt: str = "", requested_types=None) -> list:
    """Decide whether a bedroom should be promoted to a Master Bedroom.

    Rule: only create a Master Bedroom when the user explicitly requested one,
    OR when an attached bathroom is requested. In that case exactly one bedroom
    is promoted (the rest stay plain bedrooms). Never auto-invent a master.
    """
    text = (prompt or "").lower()
    requested_types = set(requested_types or [])

    wants_master = (
        any(kw in text for kw in _MASTER_KEYWORDS)
        or "master_bedroom" in requested_types
    )
    wants_attached_bath = any(kw in text for kw in _ATTACHED_BATH_KEYWORDS)

    masters = [r for r in rooms if r["type"] == "master_bedroom"]
    bedrooms = [r for r in rooms if r["type"] == "bedroom"]

    if not (wants_master or wants_attached_bath):
        # No justification for a master — collapse any stray master to a bedroom.
        for r in masters:
            r["type"] = "bedroom"
        return rooms

    # Justified: ensure exactly one master bedroom.
    if not masters:
        if bedrooms:
            bedrooms[0]["type"] = "master_bedroom"
    else:
        # Keep the first master, demote any extras.
        for r in masters[1:]:
            r["type"] = "bedroom"
    return rooms


def apply_bathroom_relationships(rooms: list, prompt: str = "") -> list:
    """Materialize requested ensuite/common bathroom counts and roles.

    Gemini correctly recognizes relationship labels, but a single target-room
    token does not carry multiplicity. This parser only extracts count and
    relationship semantics; room names remain open-ended.
    """
    text = (prompt or "").lower()
    count_pattern = r"(one|two|three|four|five|six|\d+)"
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

    attached_match = re.search(
        rf"\b{count_pattern}\s+(?:attached\s+)?(?:ba[a-z]*room|bat[a-z]*om|toilet|washroom)s?(?:\s+\w+){{0,3}}\s+atta\w*"
        rf"|\b{count_pattern}\s+atta\w*\s+(?:ba[a-z]*room|bat[a-z]*om|toilet|washroom)s?",
        text,
    )
    if not attached_match:
        return rooms

    token = next((group for group in attached_match.groups() if group), "1")
    attached_count = words.get(token, int(token) if token.isdigit() else 1)

    common_match = re.search(
        rf"(?:(?:\b{count_pattern}\s+)?(?:normal|common|shared|guest)\s+(?:ba[a-z]*room|bat[a-z]*om|toilet|washroom)s?)",
        text,
    )
    common_count = 0
    if common_match:
        common_token = next((group for group in common_match.groups() if group), None)
        common_count = words.get(common_token, int(common_token) if common_token and common_token.isdigit() else 1)

    non_bathrooms = [
        room for room in rooms
        if not any(word in str(room.get("type", "")).lower() for word in ("bath", "toilet", "washroom"))
    ]
    for index in range(attached_count):
        non_bathrooms.append({
            "type": "attached_bathroom",
            "id": f"attached_bathroom-{index + 1}",
            "name": f"Attached Bathroom {index + 1}",
            "bathroom_role": "attached",
            "confidence": 100,
        })
    for index in range(common_count):
        non_bathrooms.append({
            "type": "common_bathroom",
            "id": f"common_bathroom-{index + 1}",
            "name": "Common Bathroom" if common_count == 1 else f"Common Bathroom {index + 1}",
            "bathroom_role": "common",
            "confidence": 100,
        })
    return non_bathrooms


def apply_floor_bathroom_roles(
    program: Dict[int, List[Dict[str, Any]]], prompt: str = "",
) -> Dict[int, List[Dict[str, Any]]]:
    """Preserve ensuite/common semantics in an explicit multi-floor program."""
    result = {int(level): [normalize_ai_room_spec(spec) or dict(spec) for spec in specs]
              for level, specs in (program or {}).items()}
    text = (prompt or "").lower()
    number_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    level_names = {"ground": 0, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    markers = list(re.finditer(r"\b(ground|first|second|third|fourth|fifth)\s+floor\b", text))

    def requested_count(segment: str, role: str) -> int:
        # Parenthetical/``or`` clauses are fallbacks, not additional rooms.
        # Count the primary alternative only, matching the floor-program
        # compiler's feasibility semantics.
        segment = re.sub(r"\(\s*or\b[^)]*\)", "", segment, flags=re.I)
        segment = "\n".join(re.split(r"\bor\b[^.\n]*", line, maxsplit=1, flags=re.I)[0]
                            for line in segment.splitlines())
        qualifier = r"(?:attached|ensuite|en[- ]?suite)" if role == "attached" else r"(?:common|general|shared|guest)"
        match = re.search(
            rf"\b(?:(one|two|three|four|five|six|\d+)\s+)?{qualifier}\s+(?:bathrooms?|baths?|toilets?|washrooms?)\b",
            segment,
        )
        if role == "attached":
            # “Two bedrooms should have attached bathrooms” states the
            # ensuite cardinality through the bedroom subject rather than
            # immediately before “bathrooms”.
            subject_match = re.search(
                r"\b(one|two|three|four|five|six|\d+)\s+(?:primary\s+|master\s+)?bedrooms?"
                r"[^.\n]{0,70}\b(?:have|with|include|including)\s+(?:their\s+own\s+)?"
                r"(?:attached|ensuite|en[- ]?suite)\s+(?:bathrooms?|baths?|toilets?|washrooms?)\b",
                segment,
            )
            if subject_match:
                match = subject_match
        if not match:
            return 0
        token = match.group(1)
        return number_words.get(token, int(token) if token and token.isdigit() else 1)

    requests: Dict[int, Dict[str, int]] = {}
    if markers:
        for index, marker in enumerate(markers):
            level = level_names[marker.group(1)]
            segment = text[marker.start(): markers[index + 1].start() if index + 1 < len(markers) else None]
            counts = {
                "attached": requested_count(segment, "attached"),
                "common": requested_count(segment, "common"),
            }
            existing = requests.setdefault(level, {"attached": 0, "common": 0})
            # Repeated mentions of the same floor refine one program; they do
            # not erase an earlier explicit count or count it twice.
            existing["attached"] = max(existing["attached"], counts["attached"])
            existing["common"] = max(existing["common"], counts["common"])
    else:
        requests[0] = {
            "attached": requested_count(text, "attached"),
            "common": requested_count(text, "common"),
        }

    explicit_bath_total = sum(
        counts.get("attached", 0) + counts.get("common", 0)
        for counts in requests.values()
    )
    if explicit_bath_total:
        # Explicit cardinality is a hard semantic constraint. Gemini may add
        # plausible but unrequested ensuites on another floor; remove those
        # before geometry rather than silently changing the user's program.
        for level, specs in result.items():
            allowed = requests.get(level, {}).get("attached", 0) + requests.get(level, {}).get("common", 0)
            seen = 0
            filtered = []
            for spec in specs:
                is_bath = "bath" in canonical_type(spec.get("type")) or canonical_type(spec.get("type")) in {"toilet", "washroom"}
                if is_bath:
                    seen += 1
                    if seen > allowed:
                        continue
                filtered.append(spec)
            result[level] = filtered

    for level, specs in result.items():
        baths = [spec for spec in specs if "bath" in canonical_type(spec.get("type")) or canonical_type(spec.get("type")) in {"toilet", "washroom"}]
        requested = requests.get(level, {})
        attached_needed = int(requested.get("attached", 0))
        common_needed = int(requested.get("common", 0))
        while len(baths) < attached_needed + common_needed:
            new_bath = {"type": "bathroom", "name": "Bathroom", "confidence": 100}
            specs.append(new_bath)
            baths.append(new_bath)
        for index, bath in enumerate(baths):
            existing_role = str(bath.get("bathroom_role") or "").lower()
            if existing_role:
                continue
            if index < attached_needed:
                bath.update({"type": "bathroom", "bathroom_role": "attached", "name": f"Attached Bathroom {index + 1}"})
            elif index < attached_needed + common_needed:
                bath.update({"type": "bathroom", "bathroom_role": "common", "name": "Common Bathroom"})
    return result


def normalize_floor_program_payload(payload: Any) -> Dict[int, List[Any]]:
    """Accept both legacy keyed maps and Gemini-compatible floor lists.

    Room types remain entirely open-ended strings; this only normalizes the
    container used to associate those rooms with absolute floor numbers.
    """
    normalized: Dict[int, List[Any]] = {}
    if isinstance(payload, dict):
        entries = payload.items()
    elif isinstance(payload, list):
        entries = (
            (item.get("floor_number"), item.get("rooms"))
            for item in payload if isinstance(item, dict)
        )
    else:
        return normalized
    for raw_level, raw_rooms in entries:
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level >= 0 and isinstance(raw_rooms, list):
            normalized[level] = raw_rooms
    return normalized


def parse_added_floor_request(
    prompt: str, ai_program: Any = None,
) -> Tuple[Optional[int], List[Dict[str, Any]]]:
    """Compile an ADD-storey instruction into clean room specifications.

    Gemini's structured floor_program is preferred. The clause parser is a
    deterministic safety net for older workers/model responses and accepts
    open-ended room names without accepting verbs or whole instructions.
    """
    text = (prompt or "").lower()
    if not re.search(r"\b(?:add|create|make|convert|duplex)\b", text) or not re.search(
        r"\b(?:floor|storey|story|level|duplex|upstairs)\b", text,
    ):
        return None, []
    level_map = {
        "first": 1, "1st": 1, "second": 2, "2nd": 2,
        "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
    }
    level_matches = list(re.finditer(
        r"\b(first|1st|second|2nd|third|3rd|fourth|4th)\s+(?:floor|storey|story|level)\b",
        text,
    ))
    target_level = level_map[level_matches[-1].group(1)] if level_matches else 1

    structured: List[Dict[str, Any]] = []
    normalized_program = normalize_floor_program_payload(ai_program)
    raw_specs = normalized_program.get(target_level, [])
    for raw in raw_specs:
        normalized = normalize_ai_room_spec(raw)
        if normalized:
            structured.append(normalized)
    if structured:
        return target_level, apply_floor_bathroom_roles({target_level: structured}, prompt)[target_level]

    clause = text[level_matches[-1].end():] if level_matches else text
    clause = re.sub(r"^\s*(?:and\s+)?(?:in\s+the\s+)?(?:please\s+)?(?:add\s*|generate\s+|create\s+|make\s+|put\s+|include\s+|with\s+|consists?\s+of\s+|should\s+(?:contain|have)\s+)", "", clause)
    pieces = [piece.strip(" .;:-") for piece in re.split(r"\s*(?:,|\band\b|\bwith\b)\s*", clause) if piece.strip(" .;:-")]
    count_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    specs: List[Dict[str, Any]] = []
    for piece in pieces:
        piece = re.sub(r"^(?:add\s*|generate\s+|create\s+|make\s+|put\s+|include\s+)", "", piece).strip()
        count_match = re.match(r"^(?:(a|an|one|two|three|four|five|six|\d+)\s+)?(.+)$", piece)
        if not count_match:
            continue
        count_token, label = count_match.groups()
        count = count_words.get(count_token, int(count_token) if count_token and count_token.isdigit() else 1)
        role = ""
        if re.match(r"^(?:general|common|shared|guest)\s+", label):
            role = "common"
        elif re.match(r"^(?:attached|ensuite|en[- ]?suite)\s+", label):
            role = "attached"
        label = re.sub(r"^(?:general|common|shared|guest|attached|ensuite|en[- ]?suite)\s+", "", label).strip()
        room_type = canonical_type(label)
        if not room_type or is_instruction_like_room_label(room_type) or room_type in {"floor", "storey", "story", "level"}:
            continue
        if room_type in {"bath", "toilet", "washroom"}:
            room_type = "bathroom"
        for ordinal in range(count):
            name = room_type.replace("_", " ").title()
            if room_type == "bathroom" and role == "common":
                name = "Common Bathroom"
            elif room_type == "bathroom" and role == "attached":
                name = "Attached Bathroom"
            specs.append({
                "type": room_type, "name": name, "confidence": 100,
                **({"bathroom_role": role} if role else {}),
            })
    return target_level, specs


def apply_prompt_proximities(rooms: list, prompt: str = "") -> list:
    """Turn explicit relationship language into instance-specific hard edges."""
    text = re.sub(r"[^a-z0-9]+", " ", (prompt or "").lower()).strip()
    for source in rooms:
        source_label = str(source.get("name") or source.get("type", "")).lower().replace("_", " ").strip()
        if not source_label:
            continue
        for target in rooms:
            if target is source:
                continue
            target_label = str(target.get("name") or target.get("type", "")).lower().replace("_", " ").strip()
            pattern = rf"\b{re.escape(source_label)}\s+(?:should\s+be\s+|must\s+be\s+)?(?:at|by|near|beside|alongside|adjacent\s+to|next\s+to)\s+(?:the\s+)?{re.escape(target_label)}\b"
            if not re.search(pattern, text):
                continue
            connections = source.setdefault("connections", [])
            if not any(conn.get("target_room_id") == target.get("id") for conn in connections):
                connections.append({
                    "target_room": target.get("type"),
                    "target_room_id": target.get("id"),
                    "intent": "requested_adjacency",
                    "weight": 30,
                })

            # Explicit "A connected to B" language is a real access edge,
            # unlike merely being near. Preserve open-plan intent without
            # restoring the old automatic public-room chain.
        for target in rooms:
            if target is source:
                continue
            target_label = str(target.get("name") or target.get("type", "")).lower().replace("_", " ").strip()
            connected_pattern = (
                rf"\b(?:open\s+)?{re.escape(source_label)}\s+"
                rf"(?:is\s+)?(?:directly\s+)?connected\s+to\s+(?:the\s+)?{re.escape(target_label)}\b"
            )
            if not re.search(connected_pattern, text):
                continue
            connections = source.setdefault("connections", [])
            if not any(conn.get("target_room_id") == target.get("id") for conn in connections):
                connections.append({
                    "target_room": target.get("type"),
                    "target_room_id": target.get("id"),
                    "intent": "open_flow" if re.search(rf"\bopen\s+{re.escape(source_label)}\b", text) else "standard",
                    "weight": 30,
                })
    return rooms


def apply_courtyard_and_suite_relationships(rooms: list, prompt: str = "") -> list:
    """Compile courtyard views and suite ownership into instance-level edges."""
    text = re.sub(r"[^a-z0-9]+", " ", (prompt or "").lower()).strip()
    courtyard = next((room for room in rooms if canonical_type(room.get("type")) == "courtyard"), None)
    if courtyard and re.search(r"\bcenter(?:ed|red)?\s+(?:around|on)\b[^.]{0,80}\bcourtyard\b", text):
        # "Centered" is a design preference, not an exact coordinate.  An
        # exact fixed rectangle can make three requested courtyard-facing
        # adjacencies infeasible even though many nearby valid layouts exist.
        courtyard["preferred_location"] = "center"
        courtyard["location_weight"] = 12
    if courtyard and re.search(r"\b(?:overlook|face|facing|open\s+onto|around|centered\s+around)\b", text):
        # The three courtyard-facing rooms provide natural access. Remove the
        # generic corridor→courtyard edge added by zone wiring; requiring it as
        # a fourth shared side over-constrains narrow plots.
        for room in rooms:
            if canonical_type(room.get("type")) not in {"living_room", "dining_room", "kitchen"}:
                room["connections"] = [
                    edge for edge in room.get("connections", [])
                    if edge.get("target_room_id") != courtyard.get("id")
                ]
        for room in rooms:
            room_type = canonical_type(room.get("type"))
            if room_type not in {"living_room", "dining_room", "kitchen"}:
                continue
            connections = room.setdefault("connections", [])
            if not any(connection.get("target_room_id") == courtyard.get("id") for connection in connections):
                connections.append({
                    "target_room": "courtyard",
                    "target_room_id": courtyard.get("id"),
                    "intent": "courtyard_view",
                    "weight": 40,
                })

    closets = [room for room in rooms if canonical_type(room.get("type")) in {"walk_in_closet", "walkin_closet", "dressing_room"}]
    masters = [room for room in rooms if canonical_type(room.get("type")) == "master_bedroom"]
    if closets and masters and re.search(r"\bmaster\s+bedroom\b[^.]{0,100}\b(?:walk[ -]?in\s+closet|dressing\s+room)\b", text):
        master = masters[0]
        closet = closets[0]
        closet["connections"] = []
        master.setdefault("connections", []).append({
            "target_room": closet.get("type"),
            "target_room_id": closet.get("id"),
            "intent": "suite_access",
            "weight": 40,
        })
    return rooms


def place_courtyard_facing_windows(nodes: Iterable[RoomNode], prompt: str = "") -> int:
    """Add windows on requested room/courtyard shared walls."""
    text = (prompt or "").lower()
    if "courtyard" not in text or not re.search(r"\b(?:overlook|face|facing|open\s+onto)\b", text):
        return 0
    rooms = list(nodes or [])
    courtyard = next((node for node in rooms if canonical_type(node.type) == "courtyard"), None)
    if not courtyard:
        return 0
    placed = 0
    tolerance = 0.15
    for room in rooms:
        if canonical_type(room.type) not in {"living_room", "dining_room", "kitchen"}:
            continue
        a, c = room.rect, courtyard.rect
        overlap_z = min(a.z + a.length, c.z + c.length) - max(a.z, c.z)
        overlap_x = min(a.x + a.width, c.x + c.width) - max(a.x, c.x)
        face = None
        if overlap_z >= 3.0 and abs((a.x + a.width) - c.x) <= tolerance:
            gx, gz, face = a.x + a.width, (max(a.z, c.z) + min(a.z + a.length, c.z + c.length)) / 2, "east"
        elif overlap_z >= 3.0 and abs((c.x + c.width) - a.x) <= tolerance:
            gx, gz, face = a.x, (max(a.z, c.z) + min(a.z + a.length, c.z + c.length)) / 2, "west"
        elif overlap_x >= 3.0 and abs((a.z + a.length) - c.z) <= tolerance:
            gx, gz, face = (max(a.x, c.x) + min(a.x + a.width, c.x + c.width)) / 2, a.z + a.length, "south"
        elif overlap_x >= 3.0 and abs((c.z + c.length) - a.z) <= tolerance:
            gx, gz, face = (max(a.x, c.x) + min(a.x + a.width, c.x + c.width)) / 2, a.z, "north"
        if not face:
            continue
        if any(abs((room.rect.x + window.x) - gx) < 0.2 and abs((room.rect.z + window.z) - gz) < 0.2 for window in room.windows):
            continue
        room.windows.append(Window(gx - room.rect.x, gz - room.rect.z, face, width=4.0))
        placed += 1
        logger.info("[COURTYARD VIEW] Added %s-facing window for %s", face, room.id)
    return placed


def apply_floor_outdoor_connections(
    room_specs: List[Dict[str, Any]], outdoor_types: set[str], prompt: str = "",
) -> List[Dict[str, Any]]:
    """Attach floor-level outdoor spaces to their requested rooms by instance."""
    bedrooms = [spec for spec in room_specs if "bedroom" in canonical_type(spec.get("type"))]
    outdoors = [spec for spec in room_specs if canonical_type(spec.get("type")) in outdoor_types]
    explicit_pairing = bool(re.search(
        r"\b(?:bedrooms?|rooms?)\b[^.]{0,100}(?:\bwith\b|\bdirect\s+access\b)[^.]{0,100}\b(?:balcon|outside|outdoor)",
        (prompt or "").lower(),
    )) or bool(re.search(
        r"\beach\b[^.]{0,80}\b(?:bedroom|room)\b[^.]{0,80}\b(?:have|has|include|with)\b[^.]{0,80}\b(?:private\s+)?balcon",
        (prompt or "").lower(),
    ))
    if not outdoors or not bedrooms or not explicit_pairing:
        return room_specs
    outdoor_ids = {str(spec.get("id")) for spec in outdoors}
    for spec in room_specs:
        spec["connections"] = [
            connection for connection in spec.get("connections", []) or []
            if str(connection.get("target_room_id")) not in outdoor_ids
        ]
    pairs = (
        [(bedroom, outdoors[0]) for bedroom in bedrooms]
        if len(outdoors) == 1
        else list(zip(bedrooms, outdoors))
    )
    for bedroom, outdoor in pairs:
        bedroom.setdefault("connections", []).append({
            "target_room": outdoor.get("type"),
            "target_room_id": outdoor.get("id"),
            "intent": "open_flow",
            "weight": 30,
        })
    return room_specs

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("homevision")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Home Vision AI Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Model paths
# ---------------------------------------------------------------------------

WORK_DIR = Path(os.getenv("WORK_DIR", "."))
PHYSICS_MODEL_DIR = WORK_DIR / "model_artifacts" / "physics_bitmlp"
NLP_MODEL_DIR = WORK_DIR / "model_artifacts" / "indian_nlp_qlora"

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str
    width: Optional[float] = None
    length: Optional[float] = None
    floors: Optional[int] = 1
    currentProject: Optional[Dict[str, Any]] = None
    indianOptions: Optional[Dict[str, Any]] = None
    colors: Optional[Dict[str, Any]] = None
    package: Optional[str] = "Standard"
    customMaterials: Optional[Dict[str, Any]] = None
    state: Optional[str] = "Maharashtra"
    district: Optional[str] = "Mumbai"
    layoutRules: Optional[List[Dict[str, str]]] = None
    # from being misrouted through modification logic merely because an older
    # project is still present in browser state.
    requestMode: Optional[str] = None  # "create" or "edit"
    analysis_id: Optional[str] = None
    clarifications: Optional[Dict[str, Any]] = None


class TemplateRequest(BaseModel):
    template: str  # e.g. "2BHK", "3BHK", "CUSTOM"
    
    # 1. FIX: Make these Optional so Pydantic doesn't crash if the frontend sends `null`
    width: Optional[float] = 40.0  
    length: Optional[float] = 40.0  
    floors: Optional[int] = 1
    
    customRooms: Optional[List[str]] = None
    
    # 2. FIX: Change `bool` to `Any` to match GenerateRequest. 
    # If the frontend passes an object or string inside this dictionary, bool strictness causes a 422.
    indianOptions: Optional[Dict[str, Any]] = None 
    
    colors: Optional[Dict[str, Any]] = None
    package: Optional[str] = "Standard"
    customMaterials: Optional[Dict[str, Any]] = None
    state: Optional[str] = "Maharashtra"
    district: Optional[str] = "Mumbai"

class MEPRequest(BaseModel):
    project: dict
    options: dict


class CostRequest(BaseModel):
    project: dict
    package: Optional[str] = "Standard"
    location: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None  # seismicZone, sbc, windExposure


class HealthResponse(BaseModel):
    status: str
    service: str
    nlp_matchers_loaded: bool
    physics_model_loaded: bool
    nlp_adapter_found: bool


# ---------------------------------------------------------------------------
# Initialize matchers
# ---------------------------------------------------------------------------

room_matcher = VocabularyMatcher(ROOMS)


def _dynamic_room_candidates(prompt: str) -> List[str]:
    """Extract user-named spaces without requiring a vocabulary entry.

    The vocabulary remains useful for aliases, but it is not the source of
    truth.  A requested noun phrase ending in a spatial facility marker is
    preserved as-is so new rooms can flow through the generic layout path.
    """
    text = re.sub(r"\s+", " ", (prompt or "").strip().lower())
    if not text:
        return []
    segments = re.findall(
        r"\b(?:with|include|includes|including|containing|contain|add|create|build|make|need|want|having)\b\s+(.+?)(?=\.|;|$)",
        text,
    )
    candidates: List[str] = []
    suffix = re.compile(r"(?:room|space|area|hall|studio|lab|laboratory|gallery|lounge|suite|center|centre|workshop|theatre|theater|pool|court|arena|shed|bay|zone|wing)\b")
    for segment in segments:
        parts = re.split(r"\s*(?:,|\band\b|\bor\b|\bplus\b)\s*", segment)
        for part in parts:
            part = re.sub(r"\b(?:a|an|the|one|two|three|four|five|with|also)\b", " ", part)
            part = re.split(r"\b(?:for|with|that|which|where|next to|near|on the)\b", part, maxsplit=1)[0]
            part = re.sub(r"[^a-z0-9 ]+", " ", part).strip()
            if not part or len(part.split()) > 5 or not suffix.search(part):
                continue
            candidates.append(re.sub(r"\s+", "_", part).strip("_"))
    return list(dict.fromkeys(candidates))
style_matcher = VocabularyMatcher(STYLES)
material_matcher = VocabularyMatcher(MATERIALS)
size_matcher = VocabularyMatcher(SIZE_MODIFIERS)
intent_matcher = VocabularyMatcher(INTENT_ACTIONS)
typology_matcher = VocabularyMatcher(TYPOLOGY)

multi_matcher = MultiVocabularyMatcher({
    "rooms": room_matcher,
    "styles": style_matcher,
    "materials": material_matcher,
    "size_modifiers": size_matcher,
    "intent_actions": intent_matcher,
    "typology": typology_matcher,
})

logger.info("Vocabulary matchers initialized with %d categories", len(ALL_VOCABULARIES))

# ---------------------------------------------------------------------------
# Physics BitMLP loader
# ---------------------------------------------------------------------------

_physics_model = None
_physics_metadata = None


def _load_physics_model():
    """Load the trained Physics BitMLP TorchScript model."""
    global _physics_model, _physics_metadata

    meta_path = PHYSICS_MODEL_DIR / "physics_feature_metadata.json"
    script_path = PHYSICS_MODEL_DIR / "physics_bitmlp_torchscript.pt"

    if not meta_path.exists() or not script_path.exists():
        logger.warning(
            "Physics model not found at %s — physics predictions disabled. "
            "Train with: python train_indian_physics_bitmlp.py",
            PHYSICS_MODEL_DIR,
        )
        return

    try:
        import torch

        _physics_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        _physics_model = torch.jit.load(str(script_path), map_location="cpu")
        _physics_model.eval()
        logger.info(
            "Physics BitMLP loaded: input_dim=%d, hidden=%d, depth=%d",
            _physics_metadata.get("input_dim", "?"),
            _physics_metadata.get("hidden_dim", "?"),
            _physics_metadata.get("depth", "?"),
        )
    except ImportError:
        logger.warning("PyTorch not installed — physics predictions disabled")
    except Exception as exc:
        logger.error("Failed to load physics model: %s", exc)


_load_physics_model()

# ---------------------------------------------------------------------------
# NLP adapter status check
# ---------------------------------------------------------------------------

_nlp_adapter_found = (NLP_MODEL_DIR / "adapter" / "adapter_model.safetensors").exists()
if _nlp_adapter_found:
    logger.info("NLP QLoRA adapter found at %s (inference requires GPU + transformers)", NLP_MODEL_DIR / "adapter")
else:
    logger.info("NLP QLoRA adapter not found — using local 3-layer matcher only")


# ---------------------------------------------------------------------------
# Regex extractors
# ---------------------------------------------------------------------------

def extract_numbers(prompt: str) -> Dict[str, Any]:
    """Extract numeric values from prompt: BHK count, area, budget, floors."""
    text = prompt.lower()
    result: Dict[str, Any] = {}

    # BHK count: "3BHK", "3 BHK", "three bhk"
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "single": 1, "double": 2, "triple": 3,
        "ek": 1, "do": 2, "teen": 3, "chaar": 4, "paanch": 5,
    }
    bhk_match = re.search(r'(\d+)\s*bhk', text)
    if bhk_match:
        result["bhk"] = int(bhk_match.group(1))
    else:
        for word, num in word_to_num.items():
            if re.search(rf'\b{word}\s*bhk\b', text):
                result["bhk"] = num
                break

    if "bhk" not in result:
        bed_match = re.search(r'(\d+)\s*(?:bedroom|bed|bedrooms)', text)
        if bed_match:
            result["bhk"] = int(bed_match.group(1))
        else:
            for word, num in word_to_num.items():
                if re.search(rf'\b{word}\s*(?:bedroom|bed|bedrooms)\b', text):
                    result["bhk"] = num
                    break

    # Area in sq ft
    area_match = re.search(r'(\d+(?:,\d+)*)\s*(?:sq\.?\s*(?:ft|feet)|square\s*(?:ft|feet)|sqft|sft)', text)
    if area_match:
        result["area_sqft"] = int(area_match.group(1).replace(",", ""))

    # Area in gaj
    gaj_match = re.search(r'(\d+(?:,\d+)*)\s*gaj', text)
    if gaj_match:
        result["area_sqft"] = int(float(gaj_match.group(1).replace(",", "")) * 9)

    # Budget in lakhs/crores
    lakh_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:lakh|lac|lakhs)', text)
    if lakh_match:
        result["budget_inr"] = int(float(lakh_match.group(1)) * 100_000)

    crore_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:crore|crores|cr)', text)
    if crore_match:
        result["budget_inr"] = int(float(crore_match.group(1)) * 10_000_000)

    # Floors. Explicit named levels take precedence over a generic "floor"
    # count, so "Ground + First + Second" reliably becomes three levels.
    named_levels = {
        "basement": -1, "ground": 0, "first": 1, "second": 2,
        "third": 3, "fourth": 4, "fifth": 5,
    }
    mentioned_levels = [
        index for name, index in named_levels.items()
        if re.search(rf'\b{name}\s+floor\b', text)
    ]
    if mentioned_levels:
        result["floors"] = max(1, max(mentioned_levels) + 1)
    elif "duplex" in text:
        result["floors"] = 2
    else:
        floor_match = re.search(r'g\+(\d+)', text)
        if floor_match:
            result["floors"] = int(floor_match.group(1)) + 1
        else:
            floor_match = re.search(r'(\d+)\s*(?:floor|story|storey|manzil)', text)
            if floor_match:
                result["floors"] = int(floor_match.group(1))

    # Plot dimensions: "30x40", "30 x 40", "30ft x 40ft"
    plot_match = re.search(r'(\d+)\s*(?:ft|feet)?\s*(?:[xX×]|by|into)\s*(\d+)\s*(?:ft|feet)?', text)
    if plot_match:
        result["plot_width"] = int(plot_match.group(1))
        result["plot_length"] = int(plot_match.group(2))

    # Ceiling height
    ceiling_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:ft|feet)\s*ceiling', text)
    if ceiling_match:
        result["ceiling_height_ft"] = float(ceiling_match.group(1))

    return result


# ---------------------------------------------------------------------------
# Token-based NLP pipeline
# ---------------------------------------------------------------------------

def tokenize_prompt(prompt: str) -> List[str]:
    """
    Split prompt into meaningful tokens for matching.
    Preserves multi-word phrases by first trying bigrams/trigrams.
    """
    # Clean up
    text = prompt.lower().strip()
    text = re.sub(r'[^\w\s/+]', ' ', text)  # keep alphanumeric + space
    text = re.sub(r'\s+', ' ', text)

    words = text.split()
    tokens: List[str] = []

    i = 0
    while i < len(words):
        # Try trigram
        if i + 2 < len(words):
            trigram = f"{words[i]} {words[i+1]} {words[i+2]}"
            cat, result = multi_matcher.match_best(trigram)
            if result.found and result.confidence >= 90:
                tokens.append(trigram)
                i += 3
                continue

        # Try bigram
        if i + 1 < len(words):
            bigram = f"{words[i]} {words[i+1]}"
            cat, result = multi_matcher.match_best(bigram)
            if result.found and result.confidence >= 85:
                tokens.append(bigram)
                i += 2
                continue

        # Single word
        tokens.append(words[i])
        i += 1

    return tokens


def analyze_prompt(prompt: str) -> Dict[str, Any]:
    """
    Full NLP analysis of a user prompt.

    Returns:
        layout_params: Extracted entities for BSP layout engine
        understood: Human-readable list of what was parsed
        warnings: List of unrecognized terms with suggestions
    """
    # Step 1: Regex number extraction
    numbers = extract_numbers(prompt)

    # Step 2: Tokenize
    tokens = tokenize_prompt(prompt)

    # Step 3: Match each token through all matchers
    matched_rooms: List[Dict[str, Any]] = []
    matched_styles: List[Dict[str, Any]] = []
    matched_materials: List[Dict[str, Any]] = []
    matched_sizes: List[Dict[str, Any]] = []
    matched_intents: List[Dict[str, Any]] = []
    matched_typology: List[Dict[str, Any]] = []
    matched_colors: List[Dict[str, Any]] = []
    unmatched_terms: List[Dict[str, Any]] = []

    # Skip common stop words for matching
    stop_words = {
        "i", "a", "an", "the", "is", "am", "are", "was", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "shall", "should", "may", "might", "must",
        "can", "could", "to", "of", "in", "for", "on", "with", "at",
        "by", "from", "up", "about", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off",
        "over", "under", "again", "further", "then", "once", "here",
        "there", "when", "where", "why", "how", "all", "both", "each",
        "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very",
        "just", "don", "now", "and", "but", "or", "if", "my", "me",
        "we", "our", "you", "your", "it", "its", "this", "that",
        "these", "those", "what", "which", "who", "whom", "whose",
        "mera", "meri", "mere", "ka", "ke", "ki", "ko", "se", "me",
        "hai", "hain", "ho", "tha", "thi", "the", "wala", "wali",
        "type", "kind", "like", "also", "please", "bhai", "sir",
        "yaar", "dude", "hey",
        # Generic architectural descriptors that cause false fuzzy matches
        "flooring", "floors", "floor", "walls", "wall", "ceiling",
        "ceilings", "doors", "door", "windows", "window", "room",
        "rooms", "house", "home", "ghar", "building", "area",
        "space", "layout", "plan", "design", "style", "look",
        "feel", "vibe", "give", "side", "ft", "feet", "sqft",
        "sq", "bhk", "want", "need", "keep",
        # Hindi/Hinglish particles and common words
        "ek", "do", "zaroor", "bhi", "lakh", "lakhs", "crore", "crores",
        "budget", "chahiye", "karo", "karna", "dena", "rakhna",
        "de", "kar", "wala", "wali", "wale", "aur", "ya",
        "nahi", "nahin", "bas", "sirf", "bilkul", "accha",
        "theek", "thik", "sahi", "pakka", "abhi",
        "compliant", "friendly", "based", "concept", "open",
    }

    # Also skip tokens that are just numbers (already extracted by regex)
    for token in tokens:
        if token in stop_words:
            continue
        if re.match(r'^\d+$', token):
            continue
        # Skip BHK tokens (already extracted)
        if re.match(r'^\d*\s*bhk$', token):
            continue

        # Try matching in each category
        results = multi_matcher.match(token)

        if results:
            # Take the highest confidence match
            best_cat = max(results, key=lambda c: results[c].confidence)
            best = results[best_cat]

            entry = {
                "term": token,
                "canonical": best.canonical,
                "confidence": best.confidence,
                "layer": best.layer,
            }

            if best_cat == "rooms":
                matched_rooms.append(entry)
            elif best_cat == "styles":
                matched_styles.append(entry)
            elif best_cat == "materials":
                matched_materials.append(entry)
            elif best_cat == "size_modifiers":
                matched_sizes.append(entry)
            elif best_cat == "intent_actions":
                matched_intents.append(entry)
            elif best_cat == "typology":
                matched_typology.append(entry)
            elif best_cat == "colors":
                matched_colors.append(entry)
        else:
            # No match in any category — get closest suggestions
            closest: List[str] = []
            for cat_name in ["rooms", "styles", "materials"]:
                cat_matcher = multi_matcher.matchers[cat_name]
                result = cat_matcher.match(token)
                if result.closest:
                    closest.extend(result.closest[:1])

            unmatched_terms.append({
                "term": token,
                "suggestions": closest[:3],
            })

    # Recover unfamiliar user-created spaces without making the vocabulary
    # exhaustive.  Known aliases stay canonical; unknown names remain valid
    # room types and are handled by the generic layout/furniture fallback.
    dynamic_candidates = _dynamic_room_candidates(prompt)
    for candidate in dynamic_candidates:
        # A fuzzy vocabulary hit must not overwrite an explicit custom name
        # (e.g. "recording room" becoming "study room").
        phrase = candidate.replace("_", " ")
        full_match = room_matcher.match(phrase)
        candidate_canonical = (
            full_match.canonical
            if full_match.found and full_match.layer == "exact"
            else candidate
        )
        candidate_canonical = str(candidate_canonical).replace(" ", "_")
        phrase_words = set(phrase.split())
        matched_rooms[:] = [
            r for r in matched_rooms
            if not (
                str(r.get("term", "")).lower() in phrase_words
                and str(r.get("canonical", "")) != candidate_canonical
            )
        ]
    existing_room_types = {str(r.get("canonical", "")) for r in matched_rooms}
    for candidate in dynamic_candidates:
        match = room_matcher.match(candidate.replace("_", " "))
        # Exact aliases are canonicalized; fuzzy/semantic guesses remain the
        # user's own room name so arbitrary spaces are never silently changed.
        canonical = (match.canonical if match.found and match.layer == "exact" else candidate).replace(" ", "_")
        if canonical not in existing_room_types:
            matched_rooms.append({
                "term": candidate.replace("_", " "),
                "canonical": canonical,
                "confidence": match.confidence if match.found else 72,
                "layer": match.layer if match.found else "dynamic",
            })
            existing_room_types.add(canonical)

    # Remove any earlier fuzzy alias for the exact custom phrase after all
    # candidates have been collected.  This keeps "recording room" from also
    # becoming an unrelated "study room".
    dynamic_canonical = {c.replace("_", " "): c for c in dynamic_candidates}
    matched_rooms[:] = [
        r for r in matched_rooms
        if str(r.get("term", "")).lower() not in dynamic_canonical
        or str(r.get("canonical", "")).replace(" ", "_") == dynamic_canonical[str(r.get("term", "")).lower()]
    ]

    # Step 4: Build understood list
    understood: List[str] = []

    if "bhk" in numbers:
        understood.append(f"Configuration: {numbers['bhk']}BHK")
    if "area_sqft" in numbers:
        understood.append(f"Area: {numbers['area_sqft']} sq ft")
    if "budget_inr" in numbers:
        budget_lakhs = numbers["budget_inr"] / 100_000
        understood.append(f"Budget: {budget_lakhs:.1f} Lakhs")
    if "floors" in numbers:
        understood.append(f"Floors: {numbers['floors']}")
    if "plot_width" in numbers and "plot_length" in numbers:
        understood.append(f"Plot: {numbers['plot_width']}×{numbers['plot_length']} ft")
    if "ceiling_height_ft" in numbers:
        understood.append(f"Ceiling: {numbers['ceiling_height_ft']} ft")

    for item in matched_typology:
        understood.append(f"Typology: {item['canonical'].upper()}")
    for item in matched_rooms:
        understood.append(f"Room: {item['canonical'].title()}")
    for item in matched_styles:
        understood.append(f"Style: {item['canonical'].title()}")
    for item in matched_materials:
        understood.append(f"Material: {item['canonical'].title()}")
    for item in matched_sizes:
        understood.append(f"Size: {item['canonical'].title()}")
    for item in matched_intents:
        understood.append(f"Action: {item['canonical'].title()}")
    for item in matched_colors:
        understood.append(f"Color: {item['canonical'].title()}")

    # Step 5: Build warnings
    warnings: List[str] = []
    for item in unmatched_terms:
        if item["suggestions"]:
            suggestions = "', '".join(item["suggestions"])
            warnings.append(
                f"Could not understand '{item['term']}'. "
                f"Did you mean '{suggestions}'?"
            )
        else:
            warnings.append(f"Could not understand '{item['term']}'.")

    # Step 6: Build layout_params
    layout_params: Dict[str, Any] = {**numbers}

    if matched_rooms:
        normalized_rooms = []
        seen_room_types = set()
        for r in matched_rooms:
            room_type = str(r["canonical"]).replace(" ", "_")
            if room_type in seen_room_types:
                continue
            seen_room_types.add(room_type)
            normalized_rooms.append({"type": room_type, "confidence": r["confidence"]})
        layout_params["rooms"] = normalized_rooms

    if matched_styles:
        layout_params["styles"] = [s["canonical"] for s in matched_styles]

    if matched_materials:
        layout_params["materials"] = {
            m["canonical"]: m["confidence"] for m in matched_materials
        }

    if matched_sizes:
        layout_params["size_modifiers"] = [s["canonical"] for s in matched_sizes]
        
    if matched_colors:
        # Add colors to styles so the front-end will apply it
        if "styles" not in layout_params:
            layout_params["styles"] = []
        layout_params["styles"].extend([c["canonical"] for c in matched_colors])

    if matched_intents:
        layout_params["intents"] = [i["canonical"] for i in matched_intents]

    if matched_typology:
        layout_params["typology"] = matched_typology[0]["canonical"]

    return {
        "layout_params": layout_params,
        "understood": understood,
        "warnings": warnings,
        "matched_details": {
            "rooms": matched_rooms,
            "styles": matched_styles,
            "materials": matched_materials,
            "sizes": matched_sizes,
            "intents": matched_intents,
            "typology": matched_typology,
            "colors": matched_colors,
            "unmatched": unmatched_terms,
        },
    }


# ---------------------------------------------------------------------------
# Physics inference
# ---------------------------------------------------------------------------

def run_physics_prediction(
    room_width: float,
    room_length: float,
    floors: int = 1,
    ceiling_height: float = 10.0,
    wall_material: str = "AAC Blocks",
    city: str = "Pune",
    state: str = "Maharashtra",
    seismic_zone: str = "III",
    climate: str = "Moderate/Deccan",
    cost_tier: str = "Tier 2",
) -> Optional[Dict[str, Any]]:
    """Run Physics BitMLP inference if model is loaded."""
    if _physics_model is None or _physics_metadata is None:
        return None

    try:
        import torch

        meta = _physics_metadata
        cats = meta["categories"]
        stats = meta["numeric_stats"]

        # Build numeric features
        area = room_width * room_length
        aspect = max(room_width, room_length) / max(min(room_width, room_length), 0.1)
        governing_span = max(room_width, room_length)

        zone_factors = {"II": 0.10, "III": 0.16, "IV": 0.24, "V": 0.36}
        seismic_factor = zone_factors.get(seismic_zone, 0.16)

        # Determine derived flags
        is_coastal = "Coastal" in climate or "coastal" in climate.lower()
        is_heavy_rain = "Monsoon" in climate or "Rain" in climate
        is_extreme_heat = "Extreme" in climate or "Heat" in climate
        is_snow = "Snow" in climate or "Mountain" in climate
        is_high_seismic = seismic_zone in ("IV", "V")

        tier_map = {"Tier 1": 1.30, "Tier 2": 1.00, "Tier 3": 0.85}
        tier_mult = tier_map.get(cost_tier, 1.0)

        column_width_mm = 230 if not is_high_seismic else 300
        required_col_mm = column_width_mm

        numerics = {
            "room_width_ft": room_width,
            "room_length_ft": room_length,
            "column_width_mm": float(column_width_mm),
            "floors": float(floors),
            "ceiling_height_ft": ceiling_height,
            "has_beam": 1.0 if governing_span > 12 else 0.0,
            "ductile_detailing": 1.0 if is_high_seismic else 0.0,
            "tier_multiplier": tier_mult,
            "governing_span_ft": governing_span,
            "area_sqft": area,
            "aspect_ratio": aspect,
            "effective_span_limit_ft": governing_span * 1.1,
            "seismic_zone_factor": seismic_factor,
            "required_column_width_mm": float(required_col_mm),
            "epoxy_tmt_required": 1.0 if is_coastal else 0.0,
            "damp_proofing_required": 1.0 if (is_coastal or is_heavy_rain) else 0.0,
            "thermal_mass_required": 1.0 if is_extreme_heat else 0.0,
            "snow_roof_required": 1.0 if is_snow else 0.0,
            "engine_override_active": 1.0,
        }

        # Normalize numerics
        feature_vec = []
        for feat in meta["numeric_features"]:
            val = numerics.get(feat, 0.0)
            mean = stats[feat]["mean"]
            std = stats[feat]["std"]
            feature_vec.append((val - mean) / max(std, 1e-8))

        # One-hot categoricals
        cat_values = {
            "material_type": "1",  # RCC
            "wall_material": wall_material,
            "roofing_type": "Flat RCC Slab" if not is_snow else "Sloped/Pitched Roof",
            "foundation_type": "Strip Footing",
            "steel_grade": "Fe550D" if is_high_seismic else "Fe500",
            "soil_type": "Medium Soil",
            "city": city,
            "state": state,
            "cost_tier": cost_tier,
            "seismic_zone": seismic_zone,
            "climate": climate,
            "required_steel_grade": "Fe550D" if is_high_seismic else "Fe500",
            "required_foundation_type": "Raft Foundation" if is_high_seismic else "Isolated Footing",
            "required_roofing_type": "Sloped/Pitched Roof" if is_snow else "Flat RCC Slab",
        }

        for cat_feat in meta["categorical_features"]:
            values = cats[cat_feat]
            actual = cat_values.get(cat_feat, "")
            for v in values:
                feature_vec.append(1.0 if v == actual else 0.0)

        # Pad or truncate to input_dim
        input_dim = meta["input_dim"]
        while len(feature_vec) < input_dim:
            feature_vec.append(0.0)
        feature_vec = feature_vec[:input_dim]

        # Run inference
        x = torch.tensor([feature_vec], dtype=torch.float32)
        with torch.no_grad():
            out = _physics_model(x)

        safe_logit = float(out[0, 0])
        cost_scaled = float(out[0, 1])
        carbon_scaled = float(out[0, 2])

        target_stats = meta["target_stats"]
        cost_inr = cost_scaled * target_stats["cost_inr"]["std"] + target_stats["cost_inr"]["mean"]
        carbon_kg = carbon_scaled * target_stats["carbon_kg"]["std"] + target_stats["carbon_kg"]["mean"]

        import torch.nn.functional as F
        safety_prob = float(torch.sigmoid(torch.tensor(safe_logit)))

        return {
            "is_safe": safety_prob >= 0.5,
            "safety_confidence": round(safety_prob * 100, 1),
            "cost_inr": max(0, int(round(cost_inr, -3))),
            "carbon_kg": max(0, round(carbon_kg, 1)),
        }

    except Exception as exc:
        logger.error("Physics prediction failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Style extraction (local, from old server.py — enhanced)
# ---------------------------------------------------------------------------

def extract_style(prompt: str, matched_materials: list, matched_styles: list) -> Dict[str, Any]:
    """Build style dict from matched materials and styles."""
    style: Dict[str, Any] = {}

    # Floor material from matched materials
    floor_map = {
        "italian marble": "italian_marble",
        "indian marble": "indian_marble",
        "vitrified tiles": "vitrified_tiles",
        "kota stone": "kota_stone",
        "granite": "granite",
        "wooden laminate": "wood_laminate",
        "terrazzo": "terrazzo",
    }
    for mat in matched_materials:
        canonical = mat.get("canonical", "")
        if canonical in floor_map:
            style["floorMaterial"] = floor_map[canonical]

    # Wall finish from matched materials
    wall_map = {
        "distemper": "distemper",
        "acrylic paint": "acrylic_paint",
        "texture paint": "texture_paint",
        "exposed brick": "exposed_brick",
        "wallpaper": "wallpaper",
    }
    for mat in matched_materials:
        canonical = mat.get("canonical", "")
        if canonical in wall_map:
            style["wallFinish"] = wall_map[canonical]

    # Door material
    door_map = {
        "teak wood": "teak_wood",
        "flush doors": "flush_door",
    }
    for mat in matched_materials:
        canonical = mat.get("canonical", "")
        if canonical in door_map:
            style["doorMaterial"] = door_map[canonical]

    # Style-based environment
    style_env_map = {
        "coastal": {"site": "coastal_villa", "environment": "sunset"},
        "farmhouse": {"site": "garden_courtyard", "environment": "park"},
        "industrial": {"site": "urban_luxury", "environment": "city"},
    }
    for s in matched_styles:
        canonical = s.get("canonical", "")
        if canonical in style_env_map:
            style.update(style_env_map[canonical])

    # Accent colors from prompt text
    text = prompt.lower()
    color_map = {
        "green": "#22c55e",
        "blue": "#2563eb",
        "amber": "#f59e0b",
        "gold": "#f59e0b",
        "pink": "#ec4899",
        "red": "#ef4444",
        "purple": "#8b5cf6",
        "orange": "#f97316",
        "neon": "#39ff14",
        "yellow": "#eab308",
    }
    for color_name, hex_val in color_map.items():
        if color_name in text:
            style["accentColor"] = hex_val
            break

    if style:
        style["lastPrompt"] = prompt

    return style


# ---------------------------------------------------------------------------
# Room list builder for existing project modifications
# ---------------------------------------------------------------------------

def _overlaps_rect(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return not (
        a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
        or a["z"] + a["length"] <= b["z"] or b["z"] + b["length"] <= a["z"]
    )


def _room_floor_key(room: Dict[str, Any]) -> int:
    """Return a stable floor key for edits to a stacked project."""
    try:
        return int(room.get("floorIndex", 1 if room.get("isFloor1") else 0))
    except (TypeError, ValueError):
        return 1 if room.get("isFloor1") else 0


def _clamp_room_contents(room: Dict[str, Any]) -> None:
    """Keep local furniture and opening coordinates inside a resized room."""
    width = max(1.0, float(room.get("width", 1)))
    length = max(1.0, float(room.get("length", 1)))
    furniture = []
    for raw in room.get("furniture", []) or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item_width = min(max(0.5, float(item.get("width", 1) or 1)), max(0.5, width - 1.0))
        item_length = min(max(0.5, float(item.get("length", 1) or 1)), max(0.5, length - 1.0))
        item["width"], item["length"] = round(item_width, 2), round(item_length, 2)
        item["x"] = round(min(max(float(item.get("x", width / 2)), item_width / 2 + 0.25), width - item_width / 2 - 0.25), 2)
        item["z"] = round(min(max(float(item.get("z", length / 2)), item_length / 2 + 0.25), length - item_length / 2 - 0.25), 2)
        furniture.append(item)
    room["furniture"] = furniture

    for door in room.get("doors", []) or []:
        orientation = str(door.get("wall_orientation", "")).lower()
        if orientation in ("north", "south"):
            door["x"] = round(min(max(float(door.get("x", width / 2)), 0.75), width - 0.75), 2)
        elif orientation in ("east", "west"):
            door["z"] = round(min(max(float(door.get("z", length / 2)), 0.75), length - 0.75), 2)


def _resize_room_in_place(room: Dict[str, Any], rooms: List[Dict[str, Any]], delta: float) -> bool:
    """Resize one room by transferring a shared boundary without overlaps."""
    floor_rooms = [candidate for candidate in rooms if _room_floor_key(candidate) == _room_floor_key(room)]
    x = float(room.get("x", 0)); z = float(room.get("z", 0))
    width = float(room.get("width", 10)); length = float(room.get("length", 10))
    tolerance = 0.15
    min_dimension = 6.0

    # Each option is (shared span, available transfer, side, neighbour).  A
    # shared-boundary transfer keeps the building envelope intact and avoids
    # the overlap/no-op behaviour of the old one-sided expansion.
    options = []
    for neighbour in floor_rooms:
        if neighbour is room:
            continue
        nx = float(neighbour.get("x", 0)); nz = float(neighbour.get("z", 0))
        nw = float(neighbour.get("width", 1)); nl = float(neighbour.get("length", 1))
        z_span = min(z + length, nz + nl) - max(z, nz)
        x_span = min(x + width, nx + nw) - max(x, nx)
        if z_span > 1.0 and abs((x + width) - nx) <= tolerance:
            options.append((z_span, nw - min_dimension, "east", neighbour))
        if z_span > 1.0 and abs((nx + nw) - x) <= tolerance:
            options.append((z_span, nw - min_dimension, "west", neighbour))
        if x_span > 1.0 and abs((z + length) - nz) <= tolerance:
            options.append((x_span, nl - min_dimension, "south", neighbour))
        if x_span > 1.0 and abs((nz + nl) - z) <= tolerance:
            options.append((x_span, nl - min_dimension, "north", neighbour))

    amount = abs(float(delta))
    if delta > 0:
        viable = [option for option in options if option[1] >= 0.5]
        if not viable:
            return False
        _, available, side, neighbour = max(viable, key=lambda option: (min(amount, option[1]), option[0]))
        amount = min(amount, available)
        if side == "east":
            room["width"] = width + amount
            neighbour["x"] = float(neighbour.get("x", 0)) + amount
            neighbour["width"] = float(neighbour.get("width", 1)) - amount
        elif side == "west":
            room["x"] = x - amount
            room["width"] = width + amount
            neighbour["width"] = float(neighbour.get("width", 1)) - amount
        elif side == "south":
            room["length"] = length + amount
            neighbour["z"] = float(neighbour.get("z", 0)) + amount
            neighbour["length"] = float(neighbour.get("length", 1)) - amount
        else:
            room["z"] = z - amount
            room["length"] = length + amount
            neighbour["length"] = float(neighbour.get("length", 1)) - amount
        _clamp_room_contents(room)
        _clamp_room_contents(neighbour)
        return True

    # Shrinking needs no donor space. Prefer a side that already has an
    # adjacent room and give it the released strip so no wall gap is created.
    viable = [option for option in options if (width if option[2] in ("east", "west") else length) - amount >= min_dimension]
    if viable:
        _, _, side, neighbour = max(viable, key=lambda option: option[0])
        if side == "east":
            room["width"] = width - amount
            neighbour["x"] = float(neighbour.get("x", 0)) - amount
            neighbour["width"] = float(neighbour.get("width", 1)) + amount
        elif side == "west":
            room["x"] = x + amount
            room["width"] = width - amount
            neighbour["width"] = float(neighbour.get("width", 1)) + amount
        elif side == "south":
            room["length"] = length - amount
            neighbour["z"] = float(neighbour.get("z", 0)) - amount
            neighbour["length"] = float(neighbour.get("length", 1)) + amount
        else:
            room["z"] = z + amount
            room["length"] = length - amount
            neighbour["length"] = float(neighbour.get("length", 1)) + amount
        _clamp_room_contents(room)
        _clamp_room_contents(neighbour)
        return True

    # Detached edge rooms can still shrink safely; keep their origin stable.
    if width - amount >= min_dimension:
        room["width"] = width - amount
    elif length - amount >= min_dimension:
        room["length"] = length - amount
    else:
        return False
    _clamp_room_contents(room)
    return True


def _prompt_room_matches(prompt: str, rooms: List[Dict[str, Any]]) -> List[int]:
    """Resolve a room reference from existing room metadata, without a type table."""
    text = re.sub(r"[^a-z0-9]+", " ", (prompt or "").lower()).strip()
    for word, digit in {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    matches = []
    for index, room in enumerate(rooms):
        aliases = set()
        for value in (room.get("id"), room.get("legacy_id"), room.get("name"), room.get("type")):
            alias = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
            if alias and alias != "room":
                aliases.add(alias)
        for alias in aliases:
            found = re.search(rf"\b{re.escape(alias)}\b", text)
            if found:
                matches.append((len(alias.split()), len(alias), -found.start(), index, alias))
    if not matches:
        return []
    best = max(matches)[:3]
    chosen_alias = max((match for match in matches if match[:3] == best), key=lambda match: match[1])[4]
    return sorted({match[3] for match in matches if match[4] == chosen_alias})


def _all_prompt_room_matches(prompt: str, rooms: List[Dict[str, Any]]) -> List[int]:
    """Return every existing room explicitly named in the prompt, in text order."""
    text = re.sub(r"[^a-z0-9]+", " ", (prompt or "").lower()).strip()
    for word, digit in {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    found: List[Tuple[int, int, int]] = []
    for index, room in enumerate(rooms):
        aliases = []
        for value in (room.get("id"), room.get("legacy_id"), room.get("name"), room.get("type")):
            alias = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
            if alias and alias != "room":
                aliases.append(alias)
        positions = [match.start() for alias in aliases if (match := re.search(rf"\b{re.escape(alias)}\b", text))]
        if positions:
            found.append((min(positions), -max(len(alias) for alias in aliases), index))
    return [index for _, _, index in sorted(found)]


def _ensure_door_between_rooms(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    """Cut a paired doorway into a real shared wall and clear its approach."""
    tolerance = 0.2
    ax, az = float(first.get("x", 0)), float(first.get("z", 0))
    aw, al = float(first.get("width", 0)), float(first.get("length", 0))
    bx, bz = float(second.get("x", 0)), float(second.get("z", 0))
    bw, bl = float(second.get("width", 0)), float(second.get("length", 0))
    door_data = None
    z0, z1 = max(az, bz), min(az + al, bz + bl)
    x0, x1 = max(ax, bx), min(ax + aw, bx + bw)
    if z1 - z0 >= 2.2 and abs((ax + aw) - bx) <= tolerance:
        wz = (z0 + z1) / 2.0
        door_data = ((aw, wz - az, "east"), (0.0, wz - bz, "west"), z1 - z0)
    elif z1 - z0 >= 2.2 and abs((bx + bw) - ax) <= tolerance:
        wz = (z0 + z1) / 2.0
        door_data = ((0.0, wz - az, "west"), (bw, wz - bz, "east"), z1 - z0)
    elif x1 - x0 >= 2.2 and abs((az + al) - bz) <= tolerance:
        wx = (x0 + x1) / 2.0
        door_data = ((wx - ax, al, "south"), (wx - bx, 0.0, "north"), x1 - x0)
    elif x1 - x0 >= 2.2 and abs((bz + bl) - az) <= tolerance:
        wx = (x0 + x1) / 2.0
        door_data = ((wx - ax, 0.0, "north"), (wx - bx, bl, "south"), x1 - x0)
    if door_data is None:
        return False

    first_door, second_door, shared_length = door_data
    width = max(2.2, min(3.5, shared_length - 0.4))

    def add_door(room: Dict[str, Any], data: Tuple[float, float, str]) -> None:
        x, z, orientation = data
        doors = room.setdefault("doors", [])
        if not any(
            abs(float(door.get("x", -99)) - x) <= 0.25
            and abs(float(door.get("z", -99)) - z) <= 0.25
            for door in doors if isinstance(door, dict)
        ):
            doors.append({
                "x": round(x, 2), "z": round(z, 2),
                "wall_orientation": orientation,
                "width": round(width, 2), "height": 7.0,
            })

        # Keep a natural clear approach around the doorway. Furniture records
        # are room-local center points, so remove only items intersecting a
        # compact door clearance zone rather than emptying the room.
        clear_radius = width / 2.0 + 1.25
        room["furniture"] = [
            item for item in room.get("furniture", []) or []
            if not isinstance(item, dict)
            or math.hypot(float(item.get("x", -100)) - x, float(item.get("z", -100)) - z)
            > clear_radius + max(float(item.get("width", 0)), float(item.get("length", 0))) / 2.0
        ]

    add_door(first, first_door)
    add_door(second, second_door)
    first.setdefault("connections", []).append({
        "target_room": second.get("type"), "target_room_id": second.get("id"),
        "intent": "standard", "weight": 40,
    })
    return True


def _remove_door_between_rooms(first: Dict[str, Any], second: Dict[str, Any]) -> None:
    """Remove only paired openings between two rooms, preserving other doors."""
    first_doors = [door for door in first.get("doors", []) or [] if isinstance(door, dict)]
    second_doors = [door for door in second.get("doors", []) or [] if isinstance(door, dict)]
    paired_first, paired_second = set(), set()
    for first_index, first_door in enumerate(first_doors):
        fx = float(first.get("x", 0)) + float(first_door.get("x", 0))
        fz = float(first.get("z", 0)) + float(first_door.get("z", 0))
        for second_index, second_door in enumerate(second_doors):
            sx = float(second.get("x", 0)) + float(second_door.get("x", 0))
            sz = float(second.get("z", 0)) + float(second_door.get("z", 0))
            if abs(fx - sx) <= 0.35 and abs(fz - sz) <= 0.35:
                paired_first.add(first_index)
                paired_second.add(second_index)
    first["doors"] = [door for index, door in enumerate(first_doors) if index not in paired_first]
    second["doors"] = [door for index, door in enumerate(second_doors) if index not in paired_second]


def _absorb_removed_room_cell(rooms: List[Dict[str, Any]], removed: Dict[str, Any]) -> bool:
    """Give a deleted rectangular cell to a compatible neighbour.

    This preserves the building envelope and prevents an internal courtyard or
    room deletion from becoming an accidental open notch. A merge is accepted
    only when the exact union is still a rectangle.
    """
    tolerance = 0.2
    rx, rz = float(removed.get("x", 0)), float(removed.get("z", 0))
    rw, rl = float(removed.get("width", 0)), float(removed.get("length", 0))
    candidates = []
    preference = {
        "corridor": 0, "hallway": 0, "living_room": 1,
        "dining_room": 2, "foyer": 2, "kitchen": 3,
    }
    for room in rooms:
        if _room_floor_key(room) != _room_floor_key(removed):
            continue
        x, z = float(room.get("x", 0)), float(room.get("z", 0))
        width, length = float(room.get("width", 0)), float(room.get("length", 0))
        merged = None
        shared = 0.0
        if abs(z - rz) <= tolerance and abs(length - rl) <= tolerance:
            if abs((x + width) - rx) <= tolerance or abs((rx + rw) - x) <= tolerance:
                merged = (min(x, rx), min(z, rz), width + rw, max(length, rl))
                shared = min(length, rl)
        elif abs(x - rx) <= tolerance and abs(width - rw) <= tolerance:
            if abs((z + length) - rz) <= tolerance or abs((rz + rl) - z) <= tolerance:
                merged = (min(x, rx), min(z, rz), max(width, rw), length + rl)
                shared = min(width, rw)
        if merged:
            room_type = canonical_type(room.get("type"))
            candidates.append((preference.get(room_type, 10), -shared, room, merged))
    if not candidates:
        return False

    _, _, recipient, (new_x, new_z, new_width, new_length) = min(candidates, key=lambda item: item[:2])
    old_x, old_z = float(recipient.get("x", 0)), float(recipient.get("z", 0))
    local_dx, local_dz = old_x - new_x, old_z - new_z
    for collection in ("doors", "windows", "furniture"):
        for item in recipient.get(collection, []) or []:
            if not isinstance(item, dict):
                continue
            if "x" in item:
                item["x"] = round(float(item.get("x", 0)) + local_dx, 2)
            if "z" in item:
                item["z"] = round(float(item.get("z", 0)) + local_dz, 2)
    recipient.update({
        "x": round(new_x, 2), "z": round(new_z, 2),
        "width": round(new_width, 2), "length": round(new_length, 2),
    })
    _clamp_room_contents(recipient)
    logger.info("[EDIT HEAL] Absorbed deleted %s cell into %s", removed.get("id"), recipient.get("id"))
    return True


def reconcile_modified_rooms(
    previous_rooms: List[Dict[str, Any]], modified_rooms: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Reconcile topology after every structural edit.

    Door openings are valid only on a current finite wall. Orphan doors are
    removed, one-sided valid openings are paired, deleted-room connections are
    discarded, and furniture is kept clear of all retained door approaches.
    """
    rooms = copy.deepcopy(modified_rooms or [])
    if not rooms:
        return rooms
    from layout_engine import generate_walls_from_aabbs

    # Normalize solver/grid residue before finite-wall classification. Door
    # validation already treats seams up to 0.2 ft as coincident; using a
    # stricter wall threshold here used to delete unrelated valid openings.
    seam_tolerance = 0.2
    by_floor: Dict[Any, List[Dict[str, Any]]] = {}
    for room in rooms:
        by_floor.setdefault(_room_floor_key(room), []).append(room)
    for floor_rooms in by_floor.values():
        for index, first in enumerate(floor_rooms):
            ax, az = float(first.get("x", 0)), float(first.get("z", 0))
            aw, al = float(first.get("width", 0)), float(first.get("length", 0))
            for second in floor_rooms[index + 1:]:
                bx, bz = float(second.get("x", 0)), float(second.get("z", 0))
                bw, bl = float(second.get("width", 0)), float(second.get("length", 0))
                overlap_z = min(az + al, bz + bl) - max(az, bz)
                overlap_x = min(ax + aw, bx + bw) - max(ax, bx)
                gap = bx - (ax + aw)
                if overlap_z >= 2.0 and 0.0 < gap <= seam_tolerance:
                    first["width"] = round(aw + gap, 2); aw += gap
                gap = ax - (bx + bw)
                if overlap_z >= 2.0 and 0.0 < gap <= seam_tolerance:
                    second["width"] = round(bw + gap, 2); bw += gap
                gap = bz - (az + al)
                if overlap_x >= 2.0 and 0.0 < gap <= seam_tolerance:
                    first["length"] = round(al + gap, 2); al += gap
                gap = az - (bz + bl)
                if overlap_x >= 2.0 and 0.0 < gap <= seam_tolerance:
                    second["length"] = round(bl + gap, 2); bl += gap

    node_by_id: Dict[str, RoomNode] = {}
    nodes_by_floor: Dict[Any, List[RoomNode]] = {}
    for room in rooms:
        try:
            node = RoomNode(
                id=str(room.get("id")), type=str(room.get("type", "room")),
                name=str(room.get("name") or room.get("type") or "Room"),
                rect=Rect(float(room.get("x", 0)), float(room.get("z", 0)),
                          float(room.get("width", 0)), float(room.get("length", 0))),
            )
            node_by_id[str(room.get("id"))] = node
            nodes_by_floor.setdefault(_room_floor_key(room), []).append(node)
        except (TypeError, ValueError):
            continue
    walls = [wall for floor_nodes in nodes_by_floor.values() for wall in generate_walls_from_aabbs(floor_nodes)]
    room_by_id = {str(room.get("id")): room for room in rooms}
    valid_ids = set(room_by_id)
    valid_types = {canonical_type(room.get("type")) for room in rooms}

    def wall_contains(wall: Dict[str, Any], wx: float, wz: float) -> bool:
        return (
            min(float(wall["x1"]), float(wall["x2"])) - 0.3 <= wx <= max(float(wall["x1"]), float(wall["x2"])) + 0.3
            and min(float(wall["z1"]), float(wall["z2"])) - 0.3 <= wz <= max(float(wall["z1"]), float(wall["z2"])) + 0.3
        )

    paired_openings: List[Tuple[str, str, float, float, float]] = []
    for room in rooms:
        room_id = str(room.get("id"))
        room_walls = [wall for wall in walls if room_id in wall.get("room_ids", [])]
        surviving_doors = []
        for door in room.get("doors", []) or []:
            if not isinstance(door, dict):
                continue
            wx = float(room.get("x", 0)) + float(door.get("x", 0))
            wz = float(room.get("z", 0)) + float(door.get("z", 0))
            eligible = [
                wall for wall in room_walls if wall_contains(wall, wx, wz)
                and (bool(wall.get("is_exterior")) if door.get("is_main") else bool(wall.get("is_shared")))
            ]
            if not eligible:
                logger.info("[EDIT HEAL] Closed orphan door in %s at %.2f, %.2f", room_id, wx, wz)
                continue
            surviving_doors.append(door)
            if not door.get("is_main"):
                wall = eligible[0]
                other_id = next((value for value in wall.get("room_ids", []) if value != room_id), None)
                if other_id:
                    paired_openings.append((room_id, str(other_id), wx, wz, float(door.get("width", 3.0))))
        room["doors"] = surviving_doors
        room["connections"] = [
            connection for connection in room.get("connections", []) or []
            if isinstance(connection, dict)
            and (
                (connection.get("target_room_id") and str(connection.get("target_room_id")) in valid_ids)
                or (not connection.get("target_room_id") and canonical_type(connection.get("target_room")) in valid_types)
            )
        ]
        # Windows must still lie on an exterior wall, or face a genuine
        # open-to-sky neighbour such as a courtyard. Resizing/deleting rooms
        # otherwise leaves floating window cut-outs in newly solid walls.
        surviving_windows = []
        for window in room.get("windows", []) or []:
            if not isinstance(window, dict):
                continue
            wx = float(room.get("x", 0)) + float(window.get("x", 0))
            wz = float(room.get("z", 0)) + float(window.get("z", 0))
            eligible = False
            for wall in room_walls:
                if not wall_contains(wall, wx, wz):
                    continue
                if wall.get("is_exterior"):
                    eligible = True
                    break
                other_ids = [value for value in wall.get("room_ids", []) if value != room_id]
                if any(canonical_type(room_by_id.get(str(value), {}).get("type")) in _INTERNAL_OPEN_TYPES for value in other_ids):
                    eligible = True
                    break
            if eligible:
                surviving_windows.append(window)
            else:
                logger.info("[EDIT HEAL] Closed orphan window in %s at %.2f, %.2f", room_id, wx, wz)
        room["windows"] = surviving_windows

        # Preserve a usable approach on both sides of every retained doorway.
        # Furniture is local to the room, as are serialized door coordinates.
        doors = [door for door in room.get("doors", []) or [] if isinstance(door, dict)]
        room["furniture"] = [
            item for item in room.get("furniture", []) or []
            if not isinstance(item, dict) or not any(
                math.hypot(
                    float(item.get("x", -100)) - float(door.get("x", 0)),
                    float(item.get("z", -100)) - float(door.get("z", 0)),
                ) <= float(door.get("width", 3.0)) / 2.0
                    + max(float(item.get("width", 0)), float(item.get("length", 0))) / 2.0 + 1.25
                for door in doors
            )
        ]

    # Restore the opposite half of each valid doorway when only one room kept it.
    for source_id, other_id, wx, wz, width in paired_openings:
        other = room_by_id.get(other_id)
        if not other:
            continue
        if any(
            abs(float(other.get("x", 0)) + float(door.get("x", 0)) - wx) <= 0.3
            and abs(float(other.get("z", 0)) + float(door.get("z", 0)) - wz) <= 0.3
            for door in other.get("doors", []) or [] if isinstance(door, dict)
        ):
            continue
        ox, oz = float(other.get("x", 0)), float(other.get("z", 0))
        ow, ol = float(other.get("width", 0)), float(other.get("length", 0))
        distances = {
            "west": abs(wx - ox), "east": abs(wx - (ox + ow)),
            "north": abs(wz - oz), "south": abs(wz - (oz + ol)),
        }
        orientation = min(distances, key=distances.get)
        other.setdefault("doors", []).append({
            "x": round(wx - ox, 2), "z": round(wz - oz, 2),
            "wall_orientation": orientation, "width": width, "height": 7.0,
        })

    # Final content clamp also keeps resized/absorbed rooms render-safe.
    for room in rooms:
        _clamp_room_contents(room)
    return rooms


def rebuild_modified_door_topology(rooms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Produce a repaired edit candidate with a fresh, paired door graph.

    Reconciliation intentionally closes openings invalidated by moved/absorbed
    walls.  A removal can therefore be geometrically correct yet temporarily
    disconnect rooms.  This second candidate regenerates doors from the new
    finite walls on each floor; the transaction critic decides whether it is
    safer than the preservation-first candidate.
    """
    repaired = copy.deepcopy(rooms or [])
    floors: Dict[Any, List[Dict[str, Any]]] = {}
    for room in repaired:
        floors.setdefault(_room_floor_key(room), []).append(room)

    for floor_rooms in floors.values():
        nodes: List[RoomNode] = []
        source_by_id: Dict[str, Dict[str, Any]] = {}
        for room in floor_rooms:
            room_id = str(room.get("id") or "")
            if not room_id:
                continue
            source_by_id[room_id] = room
            main_doors = []
            for door in room.get("doors", []) or []:
                if not isinstance(door, dict) or not door.get("is_main"):
                    continue
                main_doors.append(Door(
                    x=float(door.get("x", 0)), z=float(door.get("z", 0)),
                    wall_orientation=str(door.get("wall_orientation", "south")),
                    width=float(door.get("width", 4.0)),
                    height=float(door.get("height", 7.0)), is_main=True,
                ))
            nodes.append(RoomNode(
                id=room_id,
                type=canonical_type(room.get("type")) or "room",
                name=str(room.get("name") or room.get("type") or "Room"),
                rect=Rect(
                    float(room.get("x", 0)), float(room.get("z", 0)),
                    float(room.get("width", 0)), float(room.get("length", 0)),
                ),
                doors=main_doors,
                connections=copy.deepcopy(room.get("connections", []) or []),
            ))
        if not nodes:
            continue
        AdjacencyResolver(nodes).resolve()
        for node in nodes:
            source = source_by_id[node.id]
            source["doors"] = [
                {
                    "x": round(door.x, 2), "z": round(door.z, 2),
                    "wall_orientation": door.wall_orientation,
                    "width": door.width, "height": door.height,
                    "is_main": bool(door.is_main),
                }
                for door in node.doors
            ]
            _clamp_room_contents(source)
    return repaired


def evaluate_modified_room_transaction(
    prompt: str,
    previous_rooms: List[Dict[str, Any]],
    candidate_rooms: List[Dict[str, Any]],
    plot_width: float,
    plot_length: float,
    ai_analysis: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], Any]:
    """Accept an edit only after independently proving its postconditions."""
    from edit_intelligence import compile_contract, select_best_candidate

    reconciled = reconcile_modified_rooms(previous_rooms, candidate_rooms)
    contract = compile_contract(prompt, previous_rooms, ai_analysis)
    # Multi-critic transaction: first evaluate the minimally changed topology;
    # if wall changes closed required openings, also evaluate a deterministic
    # finite-wall door rebuild. No candidate is committed unless the independent
    # contract/geometry/accessibility critic accepts it.
    repaired_topology = rebuild_modified_door_topology(reconciled)
    selected, report = select_best_candidate(
        previous_rooms, [reconciled, repaired_topology], contract,
        float(plot_width or 40), float(plot_length or 40),
    )
    if selected is None:
        logger.warning("[EDIT TRANSACTION] Rejected candidate: %s", "; ".join(report.errors))
    else:
        logger.info("[EDIT TRANSACTION] Accepted candidate (preservation score %.2f)", report.score)
    return selected, report


def edit_rejection_message(report: Any) -> str:
    errors = list(getattr(report, "errors", []) or [])
    alternatives = list(getattr(report, "alternatives", []) or [])
    message = "The layout was left unchanged because the requested edit failed validation."
    if errors:
        message += " Problems: " + "; ".join(errors[:5])
    if alternatives:
        message += " Options: " + " ".join(alternatives[:3])
    return message


def _place_room_next_to(rooms: List[Dict[str, Any]], target_index: int, anchor_index: int) -> bool:
    """Place target beside an anchor without changing room identities or styles."""
    target, anchor = rooms[target_index], rooms[anchor_index]
    tx, tz = float(target.get("x", 0)), float(target.get("z", 0))
    tw, tl = float(target.get("width", 1)), float(target.get("length", 1))
    ax, az = float(anchor.get("x", 0)), float(anchor.get("z", 0))
    aw, al = float(anchor.get("width", 1)), float(anchor.get("length", 1))
    candidates = [
        (ax + aw, az), (ax - tw, az), (ax, az + al), (ax, az - tl),
    ]
    floor_rooms = [room for room in rooms if _room_floor_key(room) == _room_floor_key(target)]
    min_x = min(float(room.get("x", 0)) for room in floor_rooms)
    min_z = min(float(room.get("z", 0)) for room in floor_rooms)
    max_x = max(float(room.get("x", 0)) + float(room.get("width", 0)) for room in floor_rooms)
    max_z = max(float(room.get("z", 0)) + float(room.get("length", 0)) for room in floor_rooms)
    for x, z in candidates:
        candidate = {**target, "x": x, "z": z}
        inside = x >= min_x and z >= min_z and x + tw <= max_x and z + tl <= max_z
        if inside and not any(
            index not in (target_index, anchor_index)
            and _room_floor_key(room) == _room_floor_key(target)
            and _overlaps_rect(candidate, room)
            for index, room in enumerate(rooms)
        ):
            target["x"], target["z"] = round(x, 2), round(z, 2)
            return True
    target["x"], target["z"] = tx, tz
    return False


def _rooms_share_boundary(a: Dict[str, Any], b: Dict[str, Any], tolerance: float = 0.15) -> bool:
    ax, az = float(a.get("x", 0)), float(a.get("z", 0))
    aw, al = float(a.get("width", 0)), float(a.get("length", 0))
    bx, bz = float(b.get("x", 0)), float(b.get("z", 0))
    bw, bl = float(b.get("width", 0)), float(b.get("length", 0))
    horizontal_span = min(ax + aw, bx + bw) - max(ax, bx)
    vertical_span = min(az + al, bz + bl) - max(az, bz)
    return (
        horizontal_span > 1.0 and (abs((az + al) - bz) <= tolerance or abs((bz + bl) - az) <= tolerance)
    ) or (
        vertical_span > 1.0 and (abs((ax + aw) - bx) <= tolerance or abs((bx + bw) - ax) <= tolerance)
    )


def _swap_room_cells(rooms: List[Dict[str, Any]], first_index: int, second_index: int) -> None:
    """Move room meanings between two existing cells without disturbing the plan tessellation."""
    geometry_keys = ("x", "z", "width", "length", "doors", "windows", "wallThicknessIn")
    first_geometry = {key: copy.deepcopy(rooms[first_index].get(key)) for key in geometry_keys}
    second_geometry = {key: copy.deepcopy(rooms[second_index].get(key)) for key in geometry_keys}
    for key in geometry_keys:
        if second_geometry[key] is not None:
            rooms[first_index][key] = second_geometry[key]
        if first_geometry[key] is not None:
            rooms[second_index][key] = first_geometry[key]
    _clamp_room_contents(rooms[first_index])
    _clamp_room_contents(rooms[second_index])


def _next_room_id(rooms: List[Dict[str, Any]], room_type: str) -> str:
    prefix = canonical_type(room_type)
    used = {str(room.get("id", "")) for room in rooms}
    number = 1
    while f"{prefix}-{number}" in used:
        number += 1
    return f"{prefix}-{number}"


def _replan_floor_with_constraint(
    rooms: List[Dict[str, Any]],
    target_index: Optional[int],
    anchor_index: int,
    plot_width: float,
    plot_length: float,
    add_room_type: str = "",
    add_room_role: str = "",
    additional_anchor_indices: Optional[List[int]] = None,
    doorway_anchor_indices: Optional[List[int]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Re-solve one floor while enforcing the requested room relationship.

    A dense generated plan has no unused rectangular cell. Moving a room into
    arbitrary coordinates creates a hole/overlap, while carving the largest
    room ignores the requested neighbour. CP-SAT is the correct fallback: it
    re-packs only the affected floor with a hard shared-wall constraint and
    keeps every room's stable ID and visual metadata.
    """
    if anchor_index < 0 or anchor_index >= len(rooms):
        return None
    floor_key = _room_floor_key(rooms[anchor_index])
    floor_rooms = [room for room in rooms if _room_floor_key(room) == floor_key]
    if not floor_rooms:
        return None

    anchor_indices = list(dict.fromkeys(
        [anchor_index] + [index for index in (additional_anchor_indices or []) if 0 <= index < len(rooms)]
    ))
    anchor_ids = [str(rooms[index].get("id") or "") for index in anchor_indices]
    if not all(anchor_ids):
        return None
    target_id = ""
    if add_room_type:
        target_id = _next_room_id(rooms, add_room_type)
    elif target_index is not None and 0 <= target_index < len(rooms):
        target_id = str(rooms[target_index].get("id") or "")
    if not target_id:
        return None

    specs: List[Dict[str, Any]] = []
    for room in floor_rooms:
        normalized = normalize_ai_room_spec({
            "id": room.get("id"),
            "type": room.get("type"),
            "name": room.get("name"),
            "roof_type": room.get("roof_type"),
            "is_outdoor": room.get("is_outdoor", False),
        })
        if normalized:
            normalized["id"] = str(room.get("id"))
            specs.append(normalized)
    if add_room_type:
        added_name = (
            "Attached Bathroom" if add_room_role == "attached"
            else "Common Bathroom" if add_room_role == "common"
            else add_room_type.replace("_", " ").title()
        )
        new_spec = normalize_ai_room_spec({
            "id": target_id,
            "type": add_room_type,
            "name": added_name,
            "bathroom_role": add_room_role,
        })
        if not new_spec:
            return None
        new_spec["id"] = target_id
        specs.append(new_spec)

    from cloud_extractor import auto_wire_topology
    wired = auto_wire_topology(specs)
    target_spec = next((spec for spec in wired if str(spec.get("id")) == target_id), None)
    anchor_specs = [next((spec for spec in wired if str(spec.get("id")) == anchor_id), None) for anchor_id in anchor_ids]
    if not target_spec or not all(anchor_specs):
        return None
    if add_room_role == "attached":
        # An ensuite is a private destination, never a common corridor room.
        # Remove generic auto-wiring and retain only the requested bedroom edge.
        target_spec["bathroom_role"] = "attached"
        target_spec["connections"] = []
    for anchor_id, anchor_spec in zip(anchor_ids, anchor_specs):
        connections = target_spec.setdefault("connections", [])
        existing = next((edge for edge in connections if edge.get("target_room_id") == anchor_id), None)
        if existing:
            existing.update({"intent": "requested_adjacency", "weight": 40})
        else:
            connections.append({
                "target_room": anchor_spec.get("type"),
                "target_room_id": anchor_id,
                "intent": "requested_adjacency",
                "weight": 40,
            })

    try:
        by_id = {str(room.get("id")): room for room in floor_rooms}
        preserve_keys = (
            "wallColor", "wallColors", "floorColor", "furnitureColor",
            "materials", "furniture", "mep_nodes",
        )
        doorway_ids = {
            str(rooms[index].get("id"))
            for index in (doorway_anchor_indices or [anchor_index])
            if 0 <= index < len(rooms)
        }
        candidates: List[Tuple[float, List[Dict[str, Any]]]] = []
        for attempt in range(2):
            try:
                variant = copy.deepcopy(wired)
                for spec in variant:
                    spec["attempt"] = attempt
                replanner = LayoutEngine(float(plot_width or 40), float(plot_length or 40))
                replanner.skip_furniture_generation = True
                nodes = replanner.generate(variant)
                apply_requested_room_names(nodes, wired)
                replanned: List[Dict[str, Any]] = []
                for node in nodes:
                    payload = node.to_dict()
                    payload["floorIndex"] = floor_key
                    payload["isFloor1"] = floor_key == 1
                    previous = by_id.get(str(node.id))
                    if previous:
                        payload["name"] = previous.get("name") or payload["name"]
                        for key in preserve_keys:
                            if key in previous:
                                payload[key] = copy.deepcopy(previous[key])
                        _clamp_room_contents(payload)
                    else:
                        payload["furniture"] = furniture_for_room(
                            payload["type"], payload["width"], payload["length"]
                        )
                    replanned.append(payload)

                target_room = next((room for room in replanned if str(room.get("id")) == target_id), None)
                anchor_rooms = [next((room for room in replanned if str(room.get("id")) == anchor_id), None) for anchor_id in anchor_ids]
                if not target_room or not all(anchor_rooms) or not all(_rooms_share_boundary(target_room, anchor) for anchor in anchor_rooms):
                    continue
                if any(
                    str(anchor_room.get("id")) in doorway_ids
                    and not _ensure_door_between_rooms(target_room, anchor_room)
                    for anchor_room in anchor_rooms
                ):
                    continue

                # Prefer the valid solution that disturbs existing geometry the
                # least. The solver seed changes dimensions/packing, providing
                # genuinely different candidates rather than cosmetic retries.
                preservation_cost = 0.0
                for room in replanned:
                    previous = by_id.get(str(room.get("id")))
                    if previous:
                        preservation_cost += sum(
                            abs(float(room.get(key, 0)) - float(previous.get(key, 0)))
                            for key in ("x", "z", "width", "length")
                        )
                candidates.append((preservation_cost, replanned))
            except Exception as attempt_exc:
                logger.warning("[EDIT] Replan candidate %s failed: %s", attempt, attempt_exc)

        if not candidates:
            logger.error("[EDIT] Replanner produced no candidate satisfying %s -> %s", target_id, anchor_ids)
            return None
        _, best = min(candidates, key=lambda item: item[0])
        unaffected = [copy.deepcopy(room) for room in rooms if _room_floor_key(room) != floor_key]
        return best + unaffected
    except Exception as exc:
        logger.exception("[EDIT] Constraint replanning failed: %s", exc)
        return None

def snapshot_layout_state(rooms: List[Dict[str, Any]]) -> str:
    """Create a hashable snapshot of layout geometry, walls, doors, and connections."""
    import json
    simplified = []
    for r in rooms or []:
        if isinstance(r, dict):
            simplified.append({
                "id": r.get("id"),
                "x": round(float(r.get("x", 0)), 2),
                "z": round(float(r.get("z", 0)), 2),
                "width": round(float(r.get("width", 0)), 2),
                "length": round(float(r.get("length", 0)), 2),
                "doors": r.get("doors", []),
                "connections": r.get("connections", []),
            })
    return json.dumps(simplified, sort_keys=True)


def build_room_changes(
    prompt: str,
    current_rooms: List[Dict[str, Any]],
    matched_intents: list,
    matched_rooms: list,
    matched_sizes: list,
    move_target: str = "",
    move_dest: str = "",
    plot_width: float = 40.0,
    plot_length: float = 40.0,
) -> Optional[List[Dict[str, Any]]]:
    """
    Apply modification intents to existing room list.
    Returns modified room list or None if no changes detected.
    """
    if not current_rooms:
        return None

    text = (prompt or "").lower()
    intents = [i.get("canonical", "") for i in matched_intents]
    room_types = [r.get("canonical", "") for r in matched_rooms]
    sizes = [s.get("canonical", "") for s in matched_sizes]

    # The AI intent extractor is best-effort.  Never discard an unambiguous
    # natural-language edit simply because the classifier omitted its intent.
    if any(word in text for word in ("increase", "bigger", "larger", "expand", "decrease", "smaller", "reduce", "shrink")) and "resize" not in intents:
        intents.append("resize")
    if any(word in text for word in ("move", "place", "put", "near", "next to", "beside", "adjacent")) and "move" not in intents:
        intents.append("move")
    if re.search(r"\b(?:remove|delete)\b", text) and not any(intent in intents for intent in ("remove", "delete")):
        intents.append("remove")
    adds_sub_element = bool(re.search(
        r"\badd\s+(?:a\s+|an\s+|the\s+)?(?:proper\s+)?(?:door|doorway|window|furniture|light|wiring|plumbing|fixture)\b",
        text,
    ))
    if re.search(r"\badd\b", text) and "add" not in intents and not adds_sub_element:
        intents.append("add")

    if not room_types:
        for room in current_rooms:
            name = str(room.get("name", "")).lower()
            room_type = str(room.get("type", "")).lower()
            if (name and name in text) or (room_type and room_type.replace("_", " ") in text):
                room_types.append(room_type)

    rooms = copy.deepcopy(current_rooms)

    # ADD_OPENING / ADD_DOOR intent
    is_door_intent = bool(re.search(
        r"\b(?:add|create|make|cut|put|place)\s+(?:a\s+|an\s+|the\s+)?(?:proper\s+|open\s+|direct\s+)?(?:door|doorway|opening|archway|passageway|entrance)\b",
        text,
    )) or any(kw in text for kw in ("doorway", "open doorway", "direct access", "opening between"))

    if is_door_intent or "add_opening" in intents or "add_door" in intents:
        matched_indices = _all_prompt_room_matches(prompt, rooms)
        if len(matched_indices) >= 2:
            src_idx, tgt_idx = matched_indices[0], matched_indices[1]
            src_room, tgt_room = rooms[src_idx], rooms[tgt_idx]
            
            logger.info(f"[EDIT PLAN] Operation: add_opening")
            logger.info(f"[EDIT PLAN] Source: {src_room.get('id')} ({src_room.get('type')})")
            logger.info(f"[EDIT PLAN] Target: {tgt_room.get('id')} ({tgt_room.get('type')})")
            
            # Check if they share a wall
            has_wall = _ensure_door_between_rooms(src_room, tgt_room)
            if not has_wall:
                logger.warning(f"[EDIT RECOVERY] {src_room.get('name')} and {tgt_room.get('name')} do not share a wall. Performing local replan...")
                repositioned = _place_room_next_to(rooms, tgt_idx, src_idx)
                if repositioned:
                    logger.info(f"[EDIT RECOVERY] Repositioned {tgt_room.get('name')} adjacent to {src_room.get('name')}")
                    has_wall = _ensure_door_between_rooms(rooms[src_idx], rooms[tgt_idx])
            
            if has_wall:
                logger.info(f"[EDIT SUCCESS] Shared wall found/created & doorway added between {src_room.get('id')} and {tgt_room.get('id')}")
                return rooms
            else:
                logger.warning(f"[EDIT FAILED] Could not create doorway between {src_room.get('id')} and {tgt_room.get('id')}")
                return None

    # ADD intent — constraint re-plan of the affected floor.
    if "add" in intents and room_types:
        REPEATABLE = {"bathroom", "bedroom", "balcony", "store_room"}
        # In relational edits the extractor can return the existing anchor
        # before the new subject (for example ["bedroom", "bathroom"] for
        # "add attached bathroom to bedroom one"). Parse the phrase following
        # ADD first so we never duplicate the anchor instead of adding the
        # requested room.
        add_subject_match = re.search(
            r"\badd\s+(?:a\s+|an\s+|the\s+)?(.+?)"
            r"(?=\s+(?:to|at|by|near|beside|next\s+to|adjacent\s+to)\b|$)",
            text,
        )
        add_subject = add_subject_match.group(1).strip() if add_subject_match else ""
        add_subject = re.sub(r"^(?:proper\s+|new\s+|attached\s+|ensuite\s+)+", "", add_subject)
        norm = canonical_type(add_subject) if add_subject else canonical_type(room_types[0])
        normalized_subject = normalize_ai_room_spec({"type": norm, "name": add_subject or norm})
        if normalized_subject:
            norm = canonical_type(normalized_subject.get("type"))
        # Keep common natural-language modifiers as relationship semantics,
        # not as separate room taxonomies.
        if norm in {"attached_bathroom", "ensuite_bathroom", "attached_bath", "ensuite"}:
            norm = "bathroom"
        if norm in _GENERIC_AI_ROOM_TYPES:
            add_match = re.search(
                r"\badd\s+(?:a|an|the)?\s*([a-z][a-z\s]+?)(?=\s+(?:at|by|near|beside|next\s+to|adjacent\s+to)\b|$)",
                text,
            )
            if add_match:
                norm = canonical_type(add_match.group(1))
        if not norm:
            return None
        existing_types = {canonical_type(room.get("type")) for room in rooms}
        if norm in existing_types and norm not in REPEATABLE:
            return None

        # Resolve the requested neighbour from the actual project. For
        # "add dining room near kitchen", kitchen is the hard anchor. If no
        # relation was stated, use an architectural default that already
        # exists in this floor.
        explicit_matches = [
            index for index in _prompt_room_matches(prompt, rooms)
            if canonical_type(rooms[index].get("type")) != norm
        ]
        anchor_index = next((
            index for index, room in enumerate(rooms)
            if move_dest and canonical_type(move_dest) in {
                canonical_type(room.get("id")), canonical_type(room.get("type")), canonical_type(room.get("name")),
            }
        ), -1)
        if anchor_index == -1:
            anchor_index = explicit_matches[0] if explicit_matches else -1
        bathroom_role = ""
        if norm == "bathroom":
            normalized_bath = normalize_ai_room_spec({"type": add_subject or norm, "name": add_subject or norm}) or {}
            bathroom_role = str(normalized_bath.get("bathroom_role") or "")
            if not bathroom_role and re.search(r"\b(?:attached|ensuite|en[- ]?suite)\b", text):
                bathroom_role = "attached"

        preferred_anchors = {
            "dining_room": ("kitchen", "living_room"),
            "pooja_room": ("living_room", "dining_room"),
            "bathroom": ("master_bedroom", "bedroom", "corridor"),
            "utility": ("kitchen",),
            "utility_area": ("kitchen",),
            "store_room": ("kitchen", "utility", "dining_room"),
            "study_room": ("bedroom", "living_room"),
            "bedroom": ("corridor", "living_room"),
        }
        if anchor_index == -1:
            anchor_order = preferred_anchors.get(norm, ("living_room", "corridor"))
            if norm == "bathroom" and bathroom_role == "common":
                anchor_order = ("corridor", "hallway", "foyer", "living_room")
            for preferred in anchor_order:
                anchor_index = next(
                    (index for index, room in enumerate(rooms) if canonical_type(room.get("type")) == preferred),
                    -1,
                )
                if anchor_index != -1:
                    break
        if anchor_index == -1:
            return None
        return _replan_floor_with_constraint(
            rooms, None, anchor_index, plot_width, plot_length, add_room_type=norm,
            add_room_role=bathroom_role,
        )

    # REMOVE intent
    if "remove" in intents or "delete" in intents:
        prompt_lower = prompt.lower()
        # If they want to remove a sub-element, DO NOT remove the entire room!
        if "door" not in prompt_lower and "window" not in prompt_lower and "furniture" not in prompt_lower:
            removed_any = False
            removed_room = None
            # Resolve the target from the room metadata actually present in
            # this plan. This supports arbitrary Gemini-generated room names
            # and prevents a broad extractor result from deleting another room.
            explicit_indices = _prompt_room_matches(prompt, rooms)
            if explicit_indices:
                candidates = [(index, rooms[index]) for index in explicit_indices]
                if "west" in prompt_lower:
                    index, _ = min(candidates, key=lambda pair: float(pair[1].get("x", 0)))
                elif "east" in prompt_lower:
                    index, _ = max(candidates, key=lambda pair: float(pair[1].get("x", 0)) + float(pair[1].get("width", 0)))
                elif "north" in prompt_lower:
                    index, _ = min(candidates, key=lambda pair: float(pair[1].get("z", 0)))
                elif "south" in prompt_lower:
                    index, _ = max(candidates, key=lambda pair: float(pair[1].get("z", 0)) + float(pair[1].get("length", 0)))
                else:
                    index, _ = candidates[0]
                removed_room = rooms.pop(index)
                removed_any = True
            
            # Second pass: remove exactly one requested room.  Gemini often
            # returns duplicate type tokens for a plural request; iterating
            # those tokens used to remove every bedroom in the project.
            if not removed_any and room_types:
                unique_types = list(dict.fromkeys(str(rtype) for rtype in room_types if rtype))
                target_type = unique_types[0] if unique_types else ""
                candidates = [
                    (i, r) for i, r in enumerate(rooms)
                    if target_type and (
                        target_type in r.get("name", "").lower()
                        or target_type in r.get("type", "").lower()
                        or target_type.replace(" ", "_") == r.get("type", "")
                    )
                ]
                if candidates:
                    # Respect an explicit compass qualifier, e.g. "remove
                    # the bedroom in the west".  Otherwise remove only the
                    # first matching instance, never all matching rooms.
                    if "west" in prompt_lower:
                        index, _ = min(candidates, key=lambda pair: float(pair[1].get("x", 0)))
                    elif "east" in prompt_lower:
                        index, _ = max(candidates, key=lambda pair: float(pair[1].get("x", 0)) + float(pair[1].get("width", 0)))
                    elif "north" in prompt_lower:
                        index, _ = min(candidates, key=lambda pair: float(pair[1].get("z", 0)))
                    elif "south" in prompt_lower:
                        index, _ = max(candidates, key=lambda pair: float(pair[1].get("z", 0)) + float(pair[1].get("length", 0)))
                    else:
                        index, _ = candidates[0]
                    removed_room = rooms.pop(index)
                    removed_any = True
            
            if len(rooms) != len(current_rooms):
                # Deleting an internal courtyard/room must not punch a hole in
                # the house. Reassign a mergeable rectangular cell, then
                # rebuild door topology so the former doorway becomes wall.
                if removed_room is not None:
                    _absorb_removed_room_cell(rooms, removed_room)
                return reconcile_modified_rooms(current_rooms, rooms)
    # RESIZE intent
    if "resize" in intents:
        size_delta = 0
        if "large" in sizes or "extra large" in sizes:
            size_delta = 4
        elif "small" in sizes:
            size_delta = -3
        elif "medium" in sizes:
            size_delta = 0

        # Also check for explicit increase/decrease in prompt
        if any(word in text for word in ["increase", "bigger", "larger", "expand", "bada"]):
            size_delta = max(size_delta, 3)
        elif any(word in text for word in ["decrease", "smaller", "reduce", "shrink", "chhota"]):
            size_delta = min(size_delta, -3)

        if size_delta != 0:
            explicit_indices = _prompt_room_matches(prompt, rooms)
            if not explicit_indices and room_types:
                requested = str(room_types[0]).lower().replace(" ", "_")
                explicit_indices = [
                    index for index, room in enumerate(rooms)
                    if requested == str(room.get("type", "")).lower().replace(" ", "_")
                ]
            # One command changes one room. Repeated types require a compass
            # qualifier; otherwise the first matching instance is selected.
            if explicit_indices:
                if "west" in text:
                    target_index = min(explicit_indices, key=lambda i: float(rooms[i].get("x", 0)))
                elif "east" in text:
                    target_index = max(explicit_indices, key=lambda i: float(rooms[i].get("x", 0)) + float(rooms[i].get("width", 0)))
                elif "north" in text:
                    target_index = min(explicit_indices, key=lambda i: float(rooms[i].get("z", 0)))
                elif "south" in text:
                    target_index = max(explicit_indices, key=lambda i: float(rooms[i].get("z", 0)) + float(rooms[i].get("length", 0)))
                else:
                    target_index = explicit_indices[0]
                if _resize_room_in_place(rooms[target_index], rooms, size_delta):
                    return rooms

    # MOVE intent
    if "move" in intents:
        mentioned_indices = _all_prompt_room_matches(prompt, rooms)
        target_index = -1
        if move_target:
            target_key = canonical_type(move_target)
            target_index = next((
                index for index, room in enumerate(rooms)
                if target_key in {
                    canonical_type(room.get("id")), canonical_type(room.get("type")), canonical_type(room.get("name")),
                }
            ), -1)
        if target_index == -1 and mentioned_indices:
            # The first existing room named after the MOVE verb is the subject.
            move_pos = text.find("move")
            target_index = next((index for index in mentioned_indices if text.find(str(rooms[index].get("name", "")).lower()) >= move_pos), mentioned_indices[0])
        if target_index == -1 and room_types:
            target_key = canonical_type(room_types[0])
            target_index = next((index for index, room in enumerate(rooms) if canonical_type(room.get("type")) == target_key), -1)
        if target_index == -1:
            return None

        # Every other explicitly named room is a required spatial anchor. This
        # is what makes a compound command atomic: "beside kitchen" AND
        # "accessible directly from living" must both be true in the result.
        anchor_indices = [index for index in mentioned_indices if index != target_index]
        if move_dest:
            destination_key = canonical_type(move_dest)
            destination_index = next((
                index for index, room in enumerate(rooms)
                if index != target_index and destination_key in {
                    canonical_type(room.get("id")), canonical_type(room.get("type")), canonical_type(room.get("name")),
                }
            ), -1)
            if destination_index != -1 and destination_index not in anchor_indices:
                anchor_indices.insert(0, destination_index)

        # Compass-only moves still use a complete cell swap so the old cell is
        # filled and the floor remains tessellated.
        direction_text = canonical_type(move_dest or text)
        if not anchor_indices and any(direction in direction_text for direction in ("north", "south", "east", "west")):
            floor_indices = [index for index, room in enumerate(rooms) if index != target_index and _room_floor_key(room) == _room_floor_key(rooms[target_index])]
            if not floor_indices:
                return None
            min_x = min(float(rooms[index].get("x", 0)) for index in floor_indices)
            max_x = max(float(rooms[index].get("x", 0)) + float(rooms[index].get("width", 1)) for index in floor_indices)
            min_z = min(float(rooms[index].get("z", 0)) for index in floor_indices)
            max_z = max(float(rooms[index].get("z", 0)) + float(rooms[index].get("length", 1)) for index in floor_indices)
            goal_x, goal_z = (min_x + max_x) / 2.0, (min_z + max_z) / 2.0
            if "east" in direction_text: goal_x = max_x
            if "west" in direction_text: goal_x = min_x
            if "south" in direction_text: goal_z = max_z
            if "north" in direction_text: goal_z = min_z
            swap_index = min(floor_indices, key=lambda index: (
                (float(rooms[index].get("x", 0)) + float(rooms[index].get("width", 1)) / 2.0 - goal_x) ** 2
                + (float(rooms[index].get("z", 0)) + float(rooms[index].get("length", 1)) / 2.0 - goal_z) ** 2
            ))
            _swap_room_cells(rooms, target_index, swap_index)
            return rooms
        if not anchor_indices:
            return None

        doorway_indices = [
            index for index in anchor_indices
            if canonical_type(rooms[index].get("type")) in {"living_room", "corridor", "hallway", "foyer"}
        ] if re.search(r"\b(?:access|accessible|entrance|door|doorway|enter|connecting)\b", text) else []
        if not doorway_indices:
            doorway_indices = [anchor_indices[0]]

        def meets_minimum(room: Dict[str, Any]) -> bool:
            minimum = ROOM_MINIMUMS.get(canonical_type(room.get("type")))
            if not minimum:
                return True
            width = float(room.get("width", 0)); length = float(room.get("length", 0))
            return min(width, length) >= float(minimum.get("min_dim", 0)) - 0.1 and width * length >= float(minimum.get("area", 0)) - 1.0

        def finalize(candidate_rooms: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
            target = candidate_rooms[target_index]
            if not all(_rooms_share_boundary(target, candidate_rooms[index]) for index in anchor_indices):
                return None
            for doorway_index in doorway_indices:
                if not _ensure_door_between_rooms(target, candidate_rooms[doorway_index]):
                    return None
            if "without passing through" in text:
                for anchor_index in anchor_indices:
                    if anchor_index not in doorway_indices:
                        _remove_door_between_rooms(target, candidate_rooms[anchor_index])
            return candidate_rooms

        already_valid = finalize(copy.deepcopy(rooms))
        if already_valid is not None:
            return already_valid

        target_area = float(rooms[target_index].get("width", 1)) * float(rooms[target_index].get("length", 1))
        protected = {"corridor", "hallway", "staircase", "stairwell"}
        swap_candidates = [
            index for index, room in enumerate(rooms)
            if index != target_index and _room_floor_key(room) == _room_floor_key(rooms[target_index])
        ]
        swap_candidates.sort(key=lambda index: (
            canonical_type(rooms[index].get("type")) in protected,
            abs(float(rooms[index].get("width", 1)) * float(rooms[index].get("length", 1)) - target_area),
        ))
        for swap_index in swap_candidates:
            candidate_rooms = copy.deepcopy(rooms)
            _swap_room_cells(candidate_rooms, target_index, swap_index)
            if not meets_minimum(candidate_rooms[target_index]) or not meets_minimum(candidate_rooms[swap_index]):
                continue
            finalized = finalize(candidate_rooms)
            if finalized is not None:
                return finalized

        return _replan_floor_with_constraint(
            rooms, target_index, anchor_indices[0], plot_width, plot_length,
            additional_anchor_indices=anchor_indices[1:],
            doorway_anchor_indices=doorway_indices,
        )

    return None


def add_storey_to_existing_rooms(
    current_rooms: List[Dict[str, Any]], prompt: str,
    ai_program: Optional[Dict[Any, List[Any]]],
    plot_width: float, plot_length: float,
) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """Add one structured upper floor while preserving existing room identity."""
    target_level, requested_specs = parse_added_floor_request(prompt, ai_program)
    if target_level is None:
        return None, ""
    if not requested_specs:
        return None, "No valid upper-floor rooms were identified. Name the rooms required on the new floor."

    working = copy.deepcopy(current_rooms)
    current_max = max((_room_floor_key(room) for room in working), default=0)
    if target_level <= current_max:
        target_level = current_max + 1
    support_level = target_level - 1
    support_indices = [index for index, room in enumerate(working) if _room_floor_key(room) == support_level]
    if not support_indices:
        return None, f"Floor {support_level} does not exist, so floor {target_level} cannot be supported."

    staircase = next((room for room in working if _room_floor_key(room) == support_level and canonical_type(room.get("type")) == "staircase"), None)
    if staircase is None:
        anchor_index = next((
            index for preferred in ("corridor", "foyer", "living_room")
            for index in support_indices
            if canonical_type(working[index].get("type")) == preferred
        ), -1)
        if anchor_index < 0:
            return None, "The supporting floor has no corridor, foyer, or living room where a staircase can open safely."
        replanned = _replan_floor_with_constraint(
            working, None, anchor_index, plot_width, plot_length,
            add_room_type="staircase",
        )
        if replanned is None:
            return None, "A safe staircase could not fit on the supporting floor without invalidating existing rooms."
        working = replanned
        staircase = next((room for room in working if _room_floor_key(room) == support_level and canonical_type(room.get("type")) == "staircase"), None)
    if staircase is None:
        return None, "Staircase creation failed."

    specs = [normalize_ai_room_spec(spec) for spec in requested_specs]
    specs = [spec for spec in specs if spec and canonical_type(spec.get("type")) not in {"staircase", "corridor", "circulation", "hallway"}]
    if not specs:
        return None, "The upper-floor instruction did not contain a usable room program."
    specs.extend([
        {"type": "corridor", "name": "Corridor", "confidence": 100},
        {"type": "staircase", "name": "Staircase", "confidence": 100},
    ])
    type_counts: Dict[str, int] = {}
    for spec in specs:
        room_type = canonical_type(spec.get("type"))
        type_counts[room_type] = type_counts.get(room_type, 0) + 1
        spec["id"] = f"{room_type}-f{target_level}-{type_counts[room_type]}"

    from cloud_extractor import auto_wire_topology
    specs = auto_wire_topology(specs)
    upper_stair_spec = next(spec for spec in specs if canonical_type(spec.get("type")) == "staircase")
    upper_stair_spec["fixed_rect"] = (
        float(staircase.get("x", 0)), float(staircase.get("z", 0)),
        float(staircase.get("width", 0)), float(staircase.get("length", 0)),
    )

    engine = LayoutEngine(float(plot_width or 40), float(plot_length or 40))
    engine.skip_furniture_generation = False
    nodes = engine.generate(specs, restrict_slots=True)
    apply_requested_room_names(nodes, specs)
    AdjacencyResolver(nodes).resolve()
    WindowPlacer(nodes, engine.plot_width, engine.plot_length,
                 setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
    for node in nodes:
        node.floorIndex = target_level
        node.doors = [door for door in node.doors if not getattr(door, "is_main", False)]

    from geometry_validator import GeometryValidator
    validation = GeometryValidator.validate_post_placement(nodes)
    if not validation.is_valid:
        return None, "The requested upper floor failed geometry/accessibility validation: " + "; ".join(validation.errors[:4])

    upper_payload = []
    for node in nodes:
        payload = node.to_dict()
        payload["floorIndex"] = target_level
        payload["isFloor1"] = target_level == 1
        upper_payload.append(payload)
    return working + upper_payload, ""


def _preserve_modified_project_rooms(rooms: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return modified rooms plus fresh finite wall metadata without relayout.

    Prompt edits must not go through the fresh-generation pipeline.  It creates
    a new BSP plan and therefore discards the user's existing room-level data.
    This helper only rebuilds the wall graph from the already-edited rectangles.
    """
    by_floor: Dict[int, List[Dict[str, Any]]] = {}
    for room in rooms:
        if not isinstance(room, dict):
            continue
        if all(math.isfinite(float(room.get(k, 0))) for k in ("x", "z", "width", "length")):
            level = _room_floor_key(room)
            room["floorIndex"] = level
            room["isFloor1"] = level == 1
            by_floor.setdefault(level, []).append(room)

    layout_data: Dict[str, Any] = {
        "floor_0": by_floor.get(0, []),
        "walls_floor_0": [],
    }
    for level, floor_rooms in by_floor.items():
        if level != 0:
            layout_data[f"floor_{level}"] = floor_rooms
            layout_data[f"walls_floor_{level}"] = []

    for level, floor_rooms in by_floor.items():
        if not floor_rooms:
            continue
        from layout_engine import AdjacencyResolver, compute_shared_walls, Door, Window
        
        nodes = []
        for r in floor_rooms:
            doors = [Door(**d) if isinstance(d, dict) else d for d in (r.get("doors") or [])]
            windows = [Window(**w) if isinstance(w, dict) else w for w in (r.get("windows") or [])]
            
            nodes.append(RoomNode(
                id=str(r.get("id")),
                type=str(r.get("type", "living_room")),
                name=str(r.get("name", r.get("type", "Room"))),
                rect=Rect(float(r["x"]), float(r["z"]), float(r["width"]), float(r["length"])),
                wallThicknessIn=float(r.get("wallThicknessIn", 6) or 6),
                floorColor=r.get("floorColor", "") or "",
                wallColor=r.get("wallColor", "") or "",
                furnitureColor=r.get("furnitureColor", "") or "",
                furniture=r.get("furniture", []) or [],
                mep_nodes=r.get("mep_nodes", []) or [],
                connections=r.get("connections", []) or [],
                doors=doors,
                windows=windows,
            ))
        
        AdjacencyResolver(nodes).resolve()
        
        walls = compute_shared_walls(nodes)
        layout_data[f"walls_floor_{level}"] = walls
        
        # Write back the resolved geometry (doors, windows, etc.)
        for r, node in zip(floor_rooms, nodes):
            r.update(node.to_dict())

    return layout_data, [room for level in sorted(by_floor) for room in by_floor[level]]


def build_edit_advisory_result(
    req: Any,
    current_rooms: List[Dict[str, Any]],
    message: str,
    report: Any = None,
    base_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a safe, non-exceptional result when an edit cannot be applied.

    A physically impossible edit is a design outcome, not an API failure. The
    existing plan remains authoritative and the UI receives actionable options
    without falsely claiming that geometry changed.
    """
    result = copy.deepcopy(base_response or {})
    layout_data, _ = _preserve_modified_project_rooms(copy.deepcopy(current_rooms))
    project = getattr(req, "currentProject", None) or {}
    plot = project.get("plot", {}) if isinstance(project, dict) else {}
    layout_params = dict(result.get("layout_params") or {})
    layout_params.setdefault("plot_width", getattr(req, "width", None) or plot.get("width", 40))
    layout_params.setdefault("plot_length", getattr(req, "length", None) or plot.get("length", 40))
    result["layout_params"] = layout_params
    result["layout_data"] = layout_data
    result["style"] = copy.deepcopy(result.get("style") or project.get("style", {}))
    result["replace_project"] = False
    result["edit_status"] = "not_applied"
    result["requested_edit"] = str(getattr(req, "prompt", "") or "")
    result["understood"] = list(result.get("understood") or []) + [
        "Analyzed the requested modification; the existing layout was preserved safely."
    ]
    issues = list(getattr(report, "errors", []) or [])
    alternatives = list(getattr(report, "alternatives", []) or [])
    result["warnings"] = list(result.get("warnings") or []) + [message] + issues[:4]
    result["recommendations"] = alternatives[:4] or [
        "Increase the plot or affected floor area.",
        "Reduce the requested room size while keeping minimum usable dimensions.",
        "Allow nearby rooms to be rearranged.",
    ]
    return result


def _project_rooms_for_edit(project: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten every floor, with the active floor first for ambiguous names."""
    if not project:
        return []
    floors = project.get("floors") or []
    if floors:
        try:
            active_index = int(project.get("current_floor_index", 0) or 0)
        except (TypeError, ValueError):
            active_index = 0
        active_index = min(max(active_index, 0), len(floors) - 1)
        ordered_indices = [active_index] + [index for index in range(len(floors)) if index != active_index]
        rooms: List[Dict[str, Any]] = []
        for index in ordered_indices:
            floor = floors[index] or {}
            try:
                level = int(floor.get("level", index))
            except (TypeError, ValueError):
                level = index
            for raw_room in floor.get("rooms", []) or []:
                if isinstance(raw_room, dict):
                    room = dict(raw_room)
                    room["floorIndex"] = level
                    room["isFloor1"] = level == 1
                    rooms.append(room)
        if rooms:
            id_counts: Dict[str, int] = {}
            for room in rooms:
                raw_id = str(room.get("id") or "")
                if raw_id:
                    id_counts[raw_id] = id_counts.get(raw_id, 0) + 1
            # Older duplexes reused IDs independently on every floor. Make
            # them globally unique for transactions, while retaining the
            # displayed/legacy ID as a natural-language alias.
            for room in rooms:
                raw_id = str(room.get("id") or "")
                if raw_id and id_counts.get(raw_id, 0) > 1:
                    level = _room_floor_key(room)
                    room["legacy_id"] = raw_id
                    room["id"] = f"{raw_id}-f{level}"
                    for connection in room.get("connections", []) or []:
                        if isinstance(connection, dict) and connection.get("target_room_id") in id_counts and id_counts[str(connection.get("target_room_id"))] > 1:
                            connection["target_room_id"] = f"{connection['target_room_id']}-f{level}"
            return rooms
    return [dict(room) for room in project.get("rooms", []) or [] if isinstance(room, dict)]


def build_ai_project_context(project: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a compact, geometry-aware state for AI modification planning.

    Sending the entire React project obscures the useful signal with materials,
    UI state, and asset payloads. This context exposes the facts needed for a
    spatial decision: stable identities, bounds, floor, and actual neighbours.
    """
    project = project or {}
    rooms = _project_rooms_for_edit(project)
    summaries: List[Dict[str, Any]] = []
    for room in rooms:
        room_id = str(room.get("id") or "")
        neighbours = [
            str(other.get("id") or other.get("type") or "")
            for other in rooms
            if other is not room
            and _room_floor_key(other) == _room_floor_key(room)
            and _rooms_share_boundary(room, other)
        ]
        summaries.append({
            "id": room_id,
            "type": canonical_type(room.get("type")),
            "name": str(room.get("name") or room.get("type") or "Room"),
            "floor": _room_floor_key(room),
            "x": float(room.get("x", 0)),
            "z": float(room.get("z", 0)),
            "width": float(room.get("width", 0)),
            "length": float(room.get("length", 0)),
            "area_sqft": round(float(room.get("width", 0)) * float(room.get("length", 0)), 2),
            "neighbours": neighbours,
            "open_to_sky": bool(room.get("is_outdoor")) or str(room.get("roof_type", "")).lower() == "open",
        })
    plot = project.get("plot") or {}
    return {
        "plot": {
            "width": float(plot.get("width", 40) or 40),
            "length": float(plot.get("length", 40) or 40),
        },
        "active_floor": int(project.get("current_floor_index", 0) or 0),
        "rooms": summaries,
        "outdoor_areas": [
            {key: area.get(key) for key in ("id", "type", "name", "x", "z", "width", "length", "floorIndex")}
            for area in project.get("outdoor_areas", []) or [] if isinstance(area, dict)
        ],
    }


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/cost-presets")
async def get_cost_presets():
    """Return available material packages for the cost engine."""
    return {
        "presets": CostEngine.get_presets(),
        "materials": CostEngine.get_materials()
    }

@app.post("/api/generate")
async def generate_plan(req: GenerateRequest):
    """
    Parse user prompt → structured layout params + style.

    Response:
        layout_params: Extracted numbers and entities
        understood: What the AI successfully parsed
        warnings: Unrecognized terms with suggestions
        style: Extracted style preferences
        rooms: Modified room list (if modifying existing project)
        physics: Cost/carbon/safety predictions (if model loaded)
    """
    try:
        request_start = time.time()
        _logs = []  # Collect backend logs to send to frontend
        def _log(level, msg):
            _logs.append({"type": level, "message": msg, "time": f"{(time.time() - request_start)*1000:.0f}ms"})
            logger.info(f"[GEN-LOG][{level.upper()}] {msg}")

        _log("info", f"Prompt received: \"{req.prompt}\"")
        logger.info("[PERF] /api/generate API endpoint hit.")
        if not req.prompt or not req.prompt.strip():
            raise HTTPException(status_code=400, detail={
                "error": True,
                "message": "Prompt cannot be empty",
                "details": {"field": "prompt"},
            })

        # Run NLP analysis / SLM Extraction
        # Dual-Path Gateway
        slm_result = None
        complexity = evaluate_complexity(req.prompt)
        _log("info", f"Complexity evaluated: {complexity}")
        ai_start = time.time()
        
        # Check if project is actually empty (initial generation)
        is_empty = True
        if req.currentProject:
            # Legacy check
            if req.currentProject.get("rooms"):
                is_empty = False
            # New schema check
            elif req.currentProject.get("floors") and req.currentProject["floors"][0].get("rooms"):
                is_empty = False

        _log("info", f"Project state: {'EMPTY (initial generation)' if is_empty else 'HAS ROOMS (modification)'}")

        if not is_empty and complexity == "HIGH":
            # Path B: High Complexity — extract with full project context.
            # The extraction result is keyword JSON, not a renderable project,
            # so it must flow through the modification pipeline below.
            _log("info", "Routing → HIGH complexity extraction (project-aware)")
            try:
                slm_result = reason_modifications_deepseek(
                    req.prompt, build_ai_project_context(req.currentProject)
                )
                elapsed = (time.time() - ai_start)*1000
                _log("success", f"Cloud extraction responded in {elapsed:.0f}ms")
            except Exception as e:
                _log("error", f"Cloud extraction failed: {e}")
                
        elif not is_empty and complexity == "LOW":
            # Path B-Low: Local CSP Matrix Solver
            _log("info", "Routing → LOW complexity modification via Gemini")
            try:
                slm_result = reason_modifications_deepseek(
                    req.prompt, build_ai_project_context(req.currentProject)
                )
                elapsed = (time.time() - ai_start)*1000
                _log("success", f"Gemini extraction done in {elapsed:.0f}ms")
            except Exception as e:
                _log("error", f"Gemini extraction failed: {e}")
                
        else:
            # Path A: Initial Generation via Gemini
            _log("info", "Routing → Initial generation via Gemini")
            try:
                slm_result = extract_keywords_groq(req.prompt, ALL_VOCABULARIES)
                elapsed = (time.time() - ai_start)*1000
                _log("success", f"Gemini extraction done in {elapsed:.0f}ms")
            except Exception as e:
                _log("error", f"Gemini extraction failed: {e}")

        if slm_result:
            slm_result = apply_spatial_analysis_defaults(slm_result)
            _log("success", f"AI extracted: intent={slm_result.get('intent', '?')}, bhk={slm_result.get('bhk', '?')}, rooms={slm_result.get('target_rooms', [])}, style={slm_result.get('style', '?')}")
            # Map SLM result to the data structures expected by the rest of the pipeline
            layout_params = {}
            if slm_result.get("bhk"):
                layout_params["bhk"] = slm_result["bhk"]
            
            if slm_result.get("target_rooms"):
                # Strict filter: only include rooms actually mentioned in the prompt
                prompt_lower = req.prompt.lower()
                valid_rooms = []
                for r in slm_result.get("target_rooms", []):
                    room_str = r.lower().replace("_", " ")
                    prompt_clean = prompt_lower.replace("_", " ")
                    
                    # 1. Check for exact match
                    is_match = room_str in prompt_clean or room_str.replace(" ", "") in prompt_clean.replace(" ", "")
                    
                    # 2. Check for AI synonym upgrades (e.g., prompt="bedroom", AI="master_bedroom")
                    if not is_match:
                        if "bedroom" in prompt_clean and "bedroom" in room_str:
                            is_match = True
                        elif "bath" in prompt_clean and "bath" in room_str:
                            is_match = True
                        elif "living" in prompt_clean and "living" in room_str:
                            is_match = True

                    if is_match:
                        valid_rooms.append(r)
                
                # Check for explicit furniture keywords in the prompt to trigger room generation or flag
                furniture_keywords = ["bed", "wardrobe", "sofa", "couch", "table", "chair", "furniture"]
                if any(kw in prompt_lower for kw in furniture_keywords):
                    # For modification, if they ask to add furniture, we add it to layout_params
                    layout_params["add_furniture"] = True

                if valid_rooms:
                    layout_params["rooms"] = [{"type": r.replace(" ", "_"), "confidence": 100} for r in valid_rooms]
            for raw_type in slm_result.get("outdoor_rooms", []) or []:
                room_type = canonical_type(raw_type)
                if room_type in _INTERNAL_OPEN_TYPES:
                    layout_params.setdefault("rooms", []).append(
                        normalize_ai_room_spec({"type": "courtyard", "name": "Courtyard"})
                    )
            
            # Roof style extraction from prompt directly
            prompt_lower = req.prompt.lower()
            if "rain" in prompt_lower or "slope" in prompt_lower or "hipped" in prompt_lower:
                layout_params["roofType"] = "hipped"
            elif "gabled" in prompt_lower or "gable" in prompt_lower or "hut" in prompt_lower:
                layout_params["roofType"] = "gabled"
            elif "flat" in prompt_lower:
                layout_params["roofType"] = "flat"
            elif "mansard" in prompt_lower:
                layout_params["roofType"] = "mansard"

            # Extract basic numbers like plot width/length using regex just in case
            numbers = extract_numbers(req.prompt)
            for k, v in numbers.items():
                # Explicit numeric facts in the user's text are authoritative.
                # This guards against model context drift such as reading 3BHK
                # as 4BHK or reusing dimensions from an earlier request.
                if k in {"bhk", "floors", "plot_width", "plot_length", "area_sqft"}:
                    layout_params[k] = v
                elif k not in layout_params:
                    layout_params[k] = v

            open_matches = re.findall(r'open\s+([a-zA-Z]+)', req.prompt.lower())
            if open_matches:
                layout_params["open_rooms"] = [m.replace(" ", "_") for m in open_matches]

            details = {
                "intents": [{"canonical": slm_result.get("intent", "").lower()}],
                "rooms": [
                    {"canonical": canonical_type(r), "confidence": 100}
                    for r in slm_result.get("target_rooms", []) or []
                    if canonical_type(r) and not is_instruction_like_room_label(r)
                ],
                "styles": [{"canonical": slm_result.get("style")}] if slm_result.get("style") else [],
                "materials": [{"canonical": m} for m in slm_result.get("materials", [])],
                "sizes": [],
                "move_target": slm_result.get("move_target_room", ""),
                "move_dest": slm_result.get("move_destination", ""),
                # NEW: Pass AI extracted colors natively
                "room_colors": slm_result.get("room_colors", []),
                "global_color": slm_result.get("global_color", "")
            }

            understood = [f"Intent: {slm_result.get('intent')}"]
            if slm_result.get("bhk"): 
                understood.append(f"Configuration: {slm_result['bhk']}BHK")
            if layout_params.get("rooms"):
                for r in layout_params["rooms"]: 
                    understood.append(f"Room: {r['type'].replace('_', ' ').title()}")
            if layout_params.get("roofType"):
                understood.append(f"Roof: {layout_params['roofType'].title()}")
            if slm_result.get("style"):
                understood.append(f"Style: {slm_result['style'].title()}")
            for m in slm_result.get("materials", []):
                understood.append(f"Material: {m.title()}")
            
            # Forward Indian feature flags from SLM extraction → layout engine
            indian_from_slm = {
                "pooja_room":    bool(slm_result.get("needs_pooja_room")),
                "utility_area":  bool(slm_result.get("utility_area")),
                "powder_room":   bool(slm_result.get("powder_room")),
                "elderly_suite": bool(slm_result.get("elderly_suite")),
                "foyer":         bool(slm_result.get("foyer")),
                "brahmasthan":   bool(slm_result.get("brahmasthan")),
                "angan":         bool(slm_result.get("angan")) or any(
                    canonical_type(value) in _INTERNAL_OPEN_TYPES
                    for value in slm_result.get("outdoor_rooms", []) or []
                ),
                "double_height": bool(slm_result.get("double_height")),
            }
            # Merge with any Indian options sent from the frontend
            fe_indian = req.indianOptions or {}
            merged_indian = {k: (indian_from_slm.get(k, False) or fe_indian.get(k, False))
                            for k in set(list(indian_from_slm) + list(fe_indian))}
            layout_params["indian_options"] = merged_indian

            warnings = []
            ai_understood, ai_warnings = spatial_analysis_messages(slm_result)
            understood.extend(ai_understood)
            warnings.extend(ai_warnings)
        else:
            _log("warn", "AI extraction returned None — falling back to rule-based NLP")
            # Fallback to Old NLP analysis
            analysis = analyze_prompt(req.prompt)
            layout_params = analysis["layout_params"]
            understood = analysis["understood"]
            warnings = analysis["warnings"]
            
            # Combine UI colors with any AI-extracted colors
            final_colors = req.colors or {}
            if slm_result and slm_result.get("color_hex"):
                final_colors["ai_color"] = slm_result.get("color_hex")
            if "vastu" in req.prompt.lower():
                final_colors["vastuColors"] = True
                
            engine = LayoutEngine(req.width or 40.0, req.length or 40.0, colors=final_colors)
            details = analysis["matched_details"]

        # Apply material packages
        colors_dict = req.colors or {}
        package_name = req.package or "Standard"
        presets = CostEngine.get_presets().get(package_name, CostEngine.get_presets()["Standard"])
        custom_mats = req.customMaterials or {}
        active_preset = {**presets}
        for k, v in custom_mats.items():
            if v: active_preset[k] = v
            
        colors_dict = dict(req.colors or {})
        # Capture an AI/prompt-extracted color so it actually themes the house.
        if slm_result and slm_result.get("color_hex"):
            colors_dict["ai_color"] = slm_result["color_hex"]
        # Extract color from prompt directly
        prompt_lower = req.prompt.lower()
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        interior_map = {
            "off_white": "#FDFBF7",
            "sage": "#9CA986",
            "terracotta": "#E2725B",
            "charcoal": "#36454F",
            "beige": "#F5F5DC"
        }
        exterior_map = {
            "ivory": "#FDF5E6",
            "cream": "#FDF5E6",
            "white": "#FFFFFF",
            "concrete": "#808080",
            "brick": "#B22222",
            "wood": "#DEB887"
        }
        roof_map = {
            "terracotta": "#8B3A3A",
            "dark_grey": "#2F4F4F",
            "brown": "#654321"
        }

        wall_finish = theme.get("wall") or active_preset.get("wall_material", "AAC Block")
        if colors_dict.get("interior") and not theme.get("wall"):
            wall_finish = interior_map.get(colors_dict["interior"], colors_dict["interior"])

        ext_color = theme.get("exterior") or colors_dict.get("exterior", "ivory")
        ext_color = exterior_map.get(ext_color, ext_color)

        roof_color = active_preset.get("roof_type", "RCC Slab")
        if colors_dict.get("roof"):
            roof_color = roof_map.get(colors_dict["roof"], colors_dict["roof"])

        style_out = {
            "wallFinish": wall_finish,
            "exteriorColor": ext_color,
            "accentColor": theme.get("accent") or "#10b981", # themed accent or default emerald
            "roofStyle": roof_color,
            "windows": active_preset.get("windows", "UPVC"),
            "doors": active_preset.get("doors", {}).get("Main", "Flush Door"),
            "kitchen_counter": active_preset.get("kitchen_counter", "Granite"),
        }
        if req.currentProject and req.currentProject.get("style"):
            style_out = {**style_out, **req.currentProject.get("style", {})}

        # Build response
        response: Dict[str, Any] = {
            "layout_params": layout_params,
            "understood": understood,
            "warnings": warnings,
            "style": style_out,
            "package_details": active_preset
        }

        # Room modifications (if existing project)
        current_rooms = _project_rooms_for_edit(req.currentProject)
        if req.currentProject:
            if current_rooms:
                _log("info", f"Loaded {len(current_rooms)} existing rooms across all project floors")
            else:
                _log("warn", "currentProject provided but NO rooms found in .rooms or .floors")
        else:
            _log("info", "No currentProject sent — will generate from scratch")

        if current_rooms:
            _log("info", f"Existing rooms: {[r.get('name', r.get('type', '?')) for r in current_rooms]}")
            if attach_requested_outdoor_areas(
                response, current_rooms, req.prompt,
                req.width or 40.0, req.length or 40.0,
                int(req.floors or 1),
            ):
                return response
            # First check for MEP modifications
            mep_adds = slm_result.get("mep_additions", []) if slm_result else []
            if mep_adds and slm_result.get("intent") == "MODIFY_MEP":
                _log("info", f"MEP modification detected: adding {len(mep_adds)} item(s)")
                updated_rooms = copy.deepcopy(current_rooms)
                for addition in mep_adds:
                    target = addition.get("room", "").lower()
                    item = addition.get("item", "")
                    if target and item:
                        # Find matching room
                        for r in updated_rooms:
                            if target in r.get("name", "").lower() or target in r.get("type", "").lower():
                                mep_nodes = r.get("mep_nodes", [])
                                # Place item loosely around center
                                cx = r.get("x", 0) + r.get("width", 10)/2
                                cz = r.get("z", 0) + r.get("length", 10)/2
                                mep_nodes.append({"type": item, "x": round(cx + 1, 2), "z": round(cz + 1, 2)})
                                r["mep_nodes"] = mep_nodes
                
                response["layout_data"], _ = _preserve_modified_project_rooms(updated_rooms)
                response["understood"].append(f"Modified MEP: added items to {len(mep_adds)} rooms")
                _log("success", f"MEP modification complete — returning updated rooms")
                response["logs"] = _logs
                logger.info(f"[PERF] /api/generate total request completed in {(time.time() - request_start)*1000:.2f} ms")
                return response


            valid_rooms_spec = [r for r in details.get("rooms", []) if r.get("canonical") not in ("door", "window", "furniture", "wiring", "plumbing")]
            _log("info", f"Room modification intent: intents={[i.get('canonical','?') for i in details.get('intents',[])]}")
            
            before_snapshot = snapshot_layout_state(current_rooms)

            modified_rooms = build_room_changes(
                req.prompt, current_rooms, details.get("intents", []),
                valid_rooms_spec, details.get("sizes", []),
                details.get("move_target", ""), details.get("move_dest", ""),
                req.width or 40.0, req.length or 40.0,
            )

            edit_report = None
            if modified_rooms is not None:
                modified_rooms, edit_report = evaluate_modified_room_transaction(
                    req.prompt, current_rooms, modified_rooms,
                    req.width or 40.0, req.length or 40.0, slm_result,
                )

            if modified_rooms is not None:
                after_snapshot = snapshot_layout_state(modified_rooms)
                if before_snapshot == after_snapshot and not details.get("room_colors"):
                    logger.warning("[EDIT FAILED] No layout property changed after edit operation.")
                    return build_edit_advisory_result(
                        req, current_rooms,
                        "No layout change was produced for your edit request. Please check room names and try again.",
                        base_response=response,
                    )
                _log("success", f"build_room_changes returned {len(modified_rooms)} modified room(s)")
            else:
                _log("warn", "build_room_changes returned None (edit could not be completed)")
                return build_edit_advisory_result(
                    req, current_rooms,
                    "The requested layout edit could not be completed cleanly; the existing plan was left unchanged to preserve project integrity.",
                    base_response=response,
                )

            # Preserve colors & furniture when modifying rooms
            if modified_rooms:
                for mr in modified_rooms:
                    for cr in current_rooms:
                        if cr.get("id") == mr.get("id"):
                            if "floorColor" in cr and not mr.get("floorColor"): mr["floorColor"] = cr["floorColor"]
                            if "wallColor" in cr and not mr.get("wallColor"): mr["wallColor"] = cr["wallColor"]
                            if "furniture" in cr and "furniture" not in mr:
                                mr["furniture"] = cr["furniture"]
                            break

                rev = int(req.currentProject.get("revision", 1)) if (req.currentProject and isinstance(req.currentProject, dict)) else 1
                response["layout_changed"] = True
                response["revision"] = rev + 1
                response["changed_rooms"] = [r.get("id") for r in modified_rooms if r.get("id")]
                response["layout_data"], _ = _preserve_modified_project_rooms(modified_rooms)
                response["updated_blueprint"] = response["layout_data"]
                if req.currentProject and isinstance(req.currentProject, dict) and req.currentProject.get("style"):
                    response["style"] = {
                        **response.get("style", {}),
                        **req.currentProject.get("style", {}),
                    }
                response["understood"].append("Applied requested structural modification and updated blueprint")
                response["logs"] = _logs
                return response
                            
            # Fix 4: Handle "add furniture" or "add door" intent safely
            # If the user prompt contains "door" or "furniture" and an intent is ADD
            prompt_lower = req.prompt.lower()
            if "door" in prompt_lower or "furniture" in prompt_lower:
                target = None
                if valid_rooms_spec:
                    target = valid_rooms_spec[0].get("canonical")
                if modified_rooms and target:
                    for mr in modified_rooms:
                        if mr["type"] == target:
                            if "door" in prompt_lower:
                                mr.setdefault("doors", []).append({"width": 3, "position": "center"})
                            if "furniture" in prompt_lower:
                                mr.setdefault("furniture", []).append({"type": "sofa", "x": mr.get("x",0)+1, "z": mr.get("z",0)+1})
                elif current_rooms and target:
                    # Modify current rooms directly if we aren't regenerating
                    for cr in current_rooms:
                        if cr["type"] == target:
                            if "door" in prompt_lower:
                                cr.setdefault("doors", []).append({"width": 3, "position": "center"})
                            if "furniture" in prompt_lower:
                                cr.setdefault("furniture", []).append({"type": "sofa", "x": cr.get("x",0)+1, "z": cr.get("z",0)+1})
                    modified_rooms = current_rooms

            if modified_rooms is None and "bhk" in layout_params:
                # The user specified a new BHK configuration without any modification verbs.
                # Assume they want to generate a new layout from scratch!
                current_rooms = []
            elif modified_rooms is None and current_rooms:
                _log("info", "No structural changes — applying AI-parsed style/color changes")
                
                painted_count = 0
                room_colors = details.get("room_colors", [])
                
                if "style" not in response:
                    response["style"] = {}
                if req.currentProject and "style" in req.currentProject:
                    response["style"].update(req.currentProject.get("style", {}))
                
                # 1. Apply specific room and surface colors parsed by the AI
                if room_colors:
                    for rc in room_colors:
                        target_r = rc.get("room", "").lower()
                        color_val = rc.get("color", "")
                        surface = rc.get("surface", "wall").lower()
                        
                        if not color_val: continue
                        
                        # Route Global Surfaces
                        if "exterior" in surface or "outside" in surface or "exterior" in target_r:
                            response["style"]["exteriorColor"] = color_val
                            painted_count += 1
                            continue
                        elif "roof" in surface or "roof" in target_r:
                            response["style"]["roofStyle"] = color_val
                            response["style"]["roofColor"] = color_val
                            painted_count += 1
                            continue
                            
                        # Route Room Surfaces
                        for r in current_rooms:
                            room_name = r.get("name", "").lower()
                            room_type = r.get("type", "").lower()
                            
                            global_aliases = ["all", "house", "every", "floor", "floors", "wall", "walls", "interior", ""]
                            
                            if target_r in global_aliases or target_r in room_name or target_r in room_type:
                                if "floor" in surface or "floor" in target_r:
                                    r["floorColor"] = color_val
                                elif "furniture" in surface or "furniture" in target_r:
                                    r["furnitureColor"] = color_val
                                else:
                                    r["wallColor"] = color_val
                                    r["wallColors"] = [color_val, color_val, color_val, color_val]
                                painted_count += 1

                # 2. Fallback: Global Intent
                if painted_count == 0:
                    target_color = details.get("global_color") or details.get("color_hex")
                    if getattr(req, "colors", None) and req.colors:
                        target_color = target_color or req.colors.get("ai_color")
                    
                    if not target_color:
                        match = re.search(r'\b(red|blue|green|yellow|orange|purple|pink|white|black|gray|grey|brown|beige|cream|light\s+[a-z]+|dark\s+[a-z]+)\b', req.prompt.lower())
                        if match:
                            target_color = match.group(1)
                        
                    if target_color:
                        target_rooms = details.get("target_rooms", [])
                        if not target_rooms and isinstance(details.get("intents"), list) and len(details["intents"]) > 0:
                            target_rooms = details["intents"][0].get("target_rooms", [])
                        
                        prompt_lower = req.prompt.lower()
                        if any(x in prompt_lower for x in ["exterior", "outside", "facade", "extrior", "exterio", "extr"]):
                            response["style"]["exteriorColor"] = target_color
                            painted_count += 1
                        elif "roof" in prompt_lower:
                            response["style"]["roofColor"] = target_color
                            response["style"]["roofStyle"] = target_color
                            painted_count += 1
                        else:
                            for r in current_rooms:
                                room_name = r.get("name", "").lower()
                                room_type = r.get("type", "").lower()
                                
                                global_aliases = ["all", "house", "every", "floor", "floors", "wall", "walls", "interior"]
                                is_global = not target_rooms or any(t.lower() in global_aliases for t in target_rooms)
                                
                                if is_global or any(t.lower() in room_name or t.lower() in room_type for t in target_rooms):
                                    if "floor" in prompt_lower:
                                        r["floorColor"] = target_color
                                    elif "furniture" in prompt_lower:
                                        r["furnitureColor"] = target_color
                                    else:
                                        r["wallColor"] = target_color
                                        r["wallColors"] = [target_color, target_color, target_color, target_color] 
                                    painted_count += 1

                if painted_count > 0:
                    response["understood"].append(f"Painted {painted_count} surface(s) via AI intent")
                else:
                    response["understood"].append("Applied style changes without modifying layout structure")
                
                # Format the data as the frontend expects
                response["layout_data"], _ = _preserve_modified_project_rooms(current_rooms)
                
                # --- THE FIX: SAFELY EXTRACT ROOMS BEFORE MAPPING COLORS ---
                layout_data = response["layout_data"]
                rooms_to_update = []
                
                # Handle both Dictionary formats (Full Project) and List formats (Flat Array)
                if isinstance(layout_data, list):
                    rooms_to_update = layout_data
                elif isinstance(layout_data, dict):
                    rooms_to_update.extend(layout_data.get("rooms", []))
                    for f in layout_data.get("floors", []):
                        rooms_to_update.extend(f.get("rooms", []))
                
                for serialized_room in rooms_to_update:
                    if not isinstance(serialized_room, dict): 
                        continue # Extra safety check
                        
                    for active_room in current_rooms:
                        # Match the rooms by ID or Name
                        if serialized_room.get("id") == active_room.get("id") or serialized_room.get("type") == active_room.get("type"):
                            if "wallColor" in active_room:
                                serialized_room["wallColor"] = active_room["wallColor"]
                                serialized_room["wallColors"] = active_room.get("wallColors")
                            if "floorColor" in active_room:
                                serialized_room["floorColor"] = active_room["floorColor"]
                            if "furnitureColor" in active_room:
                                serialized_room["furnitureColor"] = active_room["furnitureColor"]
                            break
                # ------------------------------------------------------------
                
                response["logs"] = _logs
                return response

                # NEW: Construct Master Blueprint to preserve precise carved coordinates!
                from cloud_extractor import auto_wire_topology
                wired_specs = auto_wire_topology([r["type"] for r in modified_rooms])
                
                master_bp = []
                for i, r in enumerate(modified_rooms):
                    master_bp.append({
                        "room_type": r["type"],
                        "position_x": r.get("x", 0),
                        "position_z": r.get("z", 0),
                        "width": r.get("width", 10),
                        "length": r.get("length", 10),
                        "connections": wired_specs[i].get("connections", []),
                        "floor_number": 1 if r.get("isFloor1") else 0
                    })
                layout_params["master_blueprint"] = master_bp

                # Update layout params so the downstream pipeline has the wired topology
                layout_params["rooms"] = [
                    {
                        "type": r["type"], 
                        "confidence": 100, 
                        "width": r.get("width"), 
                        "length": r.get("length"),
                        "connections": wired_specs[i].get("connections", [])
                    } 
                    for i, r in enumerate(modified_rooms)
                ]
                current_rooms = [] # This forces generation down below, but now it uses the blueprint
                _log("info", f"Triggering full layout reconstruction with {len(modified_rooms)} precisely modified room(s)")
                response["understood"].append("Reconstructed the layout precisely to accommodate changes")

        if layout_params.get("rooms") or layout_params.get("bhk"):
            _log("info", f"Generation trigger: bhk={layout_params.get('bhk', '?')}, rooms={len(layout_params.get('rooms', []))}")
            # If we aren't modifying an existing project, generate from scratch
            if not current_rooms:
                if layout_params.get("master_blueprint"):
                    # We are in precise modification mode, skip injecting base rooms
                    pass 
                else:
                    bhk_val = layout_params.get("bhk", 0)
                    requested_rooms = layout_params.get("rooms", [])
                    
                    # Check if user explicitly listed core rooms
                    core_room_types = {"kitchen", "bedroom", "bathroom", "living_room", "master_bedroom"}
                    requested_types = {r["type"] for r in requested_rooms}
                    has_core_rooms = len(core_room_types.intersection(requested_types)) >= 2
                    
                    base_rooms = []
                    if bhk_val > 0:
                        base_rooms = get_base_rooms_for_bhk(bhk_val)
                        existing_types = {r["type"] for r in base_rooms}
                        for r in requested_rooms:
                            if r["type"] in ("bedroom", "master_bedroom"):
                                continue
                            if r["type"] not in existing_types or r["type"] in ["store_room", "pooja_room", "balcony", "study_room", "laundry"]:
                                base_rooms.append(r)
                    elif has_core_rooms:
                        base_rooms = requested_rooms
                    else:
                        base_rooms = get_base_rooms_for_bhk(1)
                        existing_types = {r["type"] for r in base_rooms}
                        for r in requested_rooms:
                            if r["type"] not in existing_types:
                                base_rooms.append(r)

                    base_rooms = apply_bedroom_intelligence(base_rooms, req.prompt, requested_types=requested_types)
                    base_rooms = apply_bathroom_relationships(base_rooms, req.prompt)
                    layout_params["rooms"] = base_rooms

                    if not layout_params["rooms"]:
                        layout_params["rooms"] = get_base_rooms_for_bhk(1)
                        
                    # NEW: Auto-wire topology for from-scratch so doors generate correctly
                    from cloud_extractor import auto_wire_topology
                    layout_params["rooms"] = auto_wire_topology(
                        layout_params["rooms"], ai_categories=slm_result or {},
                    )
                    layout_params["rooms"] = apply_prompt_proximities(layout_params["rooms"], req.prompt)

                # HARD GUARD: no Pooja Room unless explicitly selected or typed.
                _prompt_l = (req.prompt or "").lower()
                _pooja_ok = bool(layout_params.get("indian_options", {}).get("pooja_room")) or \
                    any(k in _prompt_l for k in ("pooja", "puja", "mandir", "temple", "prayer", "devghar"))
                if not _pooja_ok:
                    layout_params["rooms"] = [r for r in layout_params["rooms"] if "pooja" not in r.get("type", "").lower()]

                # Prefer an explicit UI floors value (>1); else the prompt-derived count.
                floors = layout_params.get("floors", 1)
                if req.floors and req.floors > 1:
                    floors = req.floors
                layout_params["floors"] = floors
                plot_w = layout_params.get("plot_width") or req.width or 40.0
                plot_l = layout_params.get("plot_length") or req.length or 40.0

                # ── Smart validation: ensure rooms fit the plot livably ──
                validated_rooms, validation_warns, new_plot_w, new_plot_l = smart_layout_validation(
                    layout_params["rooms"], plot_w, plot_l
                )
                if validation_warns:
                    warnings.extend(validation_warns)
                layout_params["rooms"] = validated_rooms
                
                # Update layout params with final (potentially expanded) dimensions
                plot_w, plot_l = new_plot_w, new_plot_l
                layout_params["plot_width"] = plot_w
                layout_params["plot_length"] = plot_l
                layout_params["area_sqft"] = int(plot_w * plot_l)
                warnings.extend(space_recommendations(layout_params["rooms"], plot_w, plot_l))

                if "engine" not in locals():
                     engine = LayoutEngine(plot_w, plot_l, colors=colors_dict)
                     engine.furniture_prompt = req.prompt
                
                layout_data = {}
                
                # Explicit feature rooms are real rooms.  The flags authorize
                # them; they must not remove them from the BSP input.
                room_pool = list(layout_params["rooms"])
                room_pool, structural_features = strip_structural(room_pool)
                room_pool, outdoor_specs, basement_specs = split_site_specs(room_pool, req.prompt)

                first_spec = []
                if floors > 1:
                    ground_spec, first_spec = split_duplex_specs(room_pool, bhk_val)
                    floor_0_rooms = sort_spec_by_generation_order(ground_spec)
                else:
                    floor_0_rooms = sort_spec_by_generation_order(room_pool)

                # Extract floor 0 blueprint
                master_bp = layout_params.get("master_blueprint")
                bp0 = [b for b in master_bp if b.get("floor_number", 0) == 0] if master_bp else None

                eng_start = time.time()
                generated_nodes_0 = engine.generate(
                    floor_0_rooms,
                    indian_options=layout_params.get("indian_options", {}), 
                    layout_rules=req.layoutRules, 
                    restrict_slots=(floors > 1),
                    master_blueprint=bp0
                )
                logger.info(f"[PERF] Engine generation took {(time.time() - eng_start)*1000:.2f} ms")

                _req_types = requested_type_set(layout_params["rooms"], layout_params.get("indian_options", {}))
                enforce_requested_only(generated_nodes_0, _req_types)

                ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
                arch_warnings = ArchitecturalRules.validate_rules(generated_nodes_0)
                if arch_warnings:
                    warnings.extend(arch_warnings)
                
                resolver = AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", []))
                resolver.resolve()
                
                placer = WindowPlacer(generated_nodes_0, engine.plot_width, engine.plot_length,
                                     setback_x=engine.setback_x, setback_z=engine.setback_z)
                placer.place_windows()

                warnings.extend(validate_layout(generated_nodes_0))
                
                from geometry_validator import GeometryValidator
                warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_0).errors)

                shared_walls_0 = compute_shared_walls(generated_nodes_0)
                layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
                layout_data["walls_floor_0"] = shared_walls_0
                layout_data["mep_data"] = compute_mep_heuristics(generated_nodes_0)
                layout_data["setbacks"] = {
                    "x": engine.setback_x,
                    "z": engine.setback_z,
                    "buildable_width": engine.buildable_width,
                    "buildable_length": engine.buildable_length,
                }
                layout_data["indianOptions"] = layout_params.get("indian_options", req.indianOptions or {})
                
                generated_nodes_1 = []
                # Floor 1 (Spatial Inheritance)
                if floors > 1:
                    blocked_zones = []
                    staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
                    if staircase:
                        blocked_zones.append(staircase.rect)
                    living = next((n for n in generated_nodes_0 if getattr(n, "is_double_height", False)), None)
                    if living:
                        blocked_zones.append(living.rect)
                        
                    if True:
                        # SAFE FIRST SPEC FIX
                        safe_first_spec = copy.deepcopy(first_spec) if first_spec else []
                        if not safe_first_spec:
                            safe_first_spec = [copy.deepcopy(r) for r in layout_params["rooms"] if r["type"] in ("bedroom", "bathroom", "master_bedroom")]
                            if any(r["type"] == "master_bedroom" for r in floor_0_rooms):
                                for r in safe_first_spec:
                                    if r["type"] == "master_bedroom":
                                        r["type"] = "bedroom"
                        
                        # Extract floor 1 blueprint
                        bp1 = [b for b in master_bp if b.get("floor_number", 0) == 1] if master_bp else None
                        floor_1_rooms = sort_spec_by_generation_order(safe_first_spec)
                        eng_start = time.time()
                        generated_nodes_1 = engine.generate(
                            floor_1_rooms, 
                            blocked_zones=blocked_zones, 
                            restrict_slots=True,
                            master_blueprint=bp1
                        )
                        logger.info(f"[PERF] Engine generation took {(time.time() - eng_start)*1000:.2f} ms")
                        
                        _io = layout_params.get("indian_options", {})
                        align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                            make_void=bool(_io.get("double_height") or _io.get("void")))
                        enforce_requested_only(generated_nodes_1, _req_types)
                        ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                        AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                        WindowPlacer(generated_nodes_1, engine.plot_width, engine.plot_length,
                                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                                     
                        from geometry_validator import GeometryValidator
                        warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_1).errors)
                        
                        shared_walls_1 = compute_shared_walls(generated_nodes_1)
                        layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                        layout_data["walls_floor_1"] = shared_walls_1
                        layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)

                additional_floors = generate_additional_floors(
                    engine, first_spec, generated_nodes_0, 2, floors,
                    layout_params.get("indian_options", {}),
                )
                for level, nodes in additional_floors.items():
                    layout_data[f"floor_{level}"] = serialize_floor_nodes(nodes, level)
                    layout_data[f"walls_floor_{level}"] = compute_shared_walls(nodes)
                    layout_data[f"mep_data_f{level}"] = compute_mep_heuristics(nodes)
                outdoor_areas, basement_walls, basement_nodes = materialize_site_layers(
                    engine, outdoor_specs, basement_specs, generated_nodes_0,
                    plot_w, plot_l, floors, req.prompt,
                    layout_params.get("indian_options", {}),
                )
                all_nodes = list(generated_nodes_0) + (generated_nodes_1 if floors > 1 else []) + [node for nodes in additional_floors.values() for node in nodes] + list(basement_nodes)
                if basement_nodes:
                    layout_data["floor_-1"] = serialize_floor_nodes(basement_nodes, -1)
                    layout_data["walls_floor_-1"] = basement_walls
                layout_data["outdoor_areas"] = outdoor_areas
                
                # --- RESTORE PRESERVED COLORS/FURNITURE ---
                try:
                    old_rooms = []
                    if req.currentProject:
                        old_rooms = req.currentProject.get("rooms", [])
                        if not old_rooms and req.currentProject.get("floors"):
                            for floor in req.currentProject.get("floors", []):
                                old_rooms.extend(floor.get("rooms", []))
                                
                    if old_rooms and master_bp:
                        used_old = set()
                        for node in all_nodes:
                            for i, old in enumerate(old_rooms):
                                if i not in used_old and old.get("type") == node.type:
                                    used_old.add(i)
                                    if "furniture" in old: node.furniture = old["furniture"]
                                    if "wallColor" in old and old["wallColor"]: node.wallColor = old["wallColor"]
                                    if "floorColor" in old and old["floorColor"]: node.floorColor = old["floorColor"]
                                    if "wallColors" in old and old["wallColors"]: node.wallColors = old["wallColors"]
                                    break
                except Exception as e:
                    logger.warning(f"Failed to restore preserved properties: {e}")

                selected_palette = _apply_selected_palette(all_nodes, req.colors)
                layout_data["floor_0"] = serialize_floor_nodes(generated_nodes_0, 0)
                if generated_nodes_1:
                    layout_data["floor_1"] = serialize_floor_nodes(generated_nodes_1, 1)
                validation_report = final_layout_validation(
                    all_nodes,
                    indian_options=layout_params.get("indian_options", {}),
                    is_duplex=(floors > 1),
                )
                response["validation"] = validation_report
                if not validation_report["ok"]:
                    warnings.extend(validation_report["issues"])

                response["layout_data"] = layout_data

        physics = run_physics_prediction(
            room_width=layout_params.get("plot_width", 40) * 0.3,
            room_length=layout_params.get("plot_length", 40) * 0.3,
            floors=layout_params.get("floors", 1),
            ceiling_height=layout_params.get("ceiling_height_ft", 10.0),
        )
        calculated_materials = CostEngine.calculate_materials(
            layout_params.get("area_sqft", 1600),
            req.package or "Standard",
            req.customMaterials or {}
        )

        response["project"] = {
            "plot": {
                "width": layout_params.get("plot_width", 40),
                "length": layout_params.get("plot_length", 40),
                "areaSqft": layout_params.get("plot_width", 40) * layout_params.get("plot_length", 40)
            },
            "building": {
                "floors": f"Ground + {layout_params.get('floors', 1) - 1}" if layout_params.get("floors", 1) > 1 else "Ground only",
                "costTier": req.package or "Standard"
            },
            "materials": calculated_materials
        }

        cost_estimate = CostEngine.calculate_cost(
            layout_params.get("area_sqft", 1600), 
            req.package or "Standard", 
            req.customMaterials or {}, 
            {"state": req.state, "district": req.district}
        )

        if physics:
            physics["cost_inr"] = int(cost_estimate["Total"])
            response["physics"] = physics
        else:
            response["physics"] = {
                "is_safe": True,
                "safety_confidence": 95.0,
                "cost_inr": int(cost_estimate["Total"]),
                "carbon_kg": 15000,
            }

        logger.info(
            "Prompt analyzed: %d understood, %d warnings, params=%s",
            len(understood), len(warnings), list(layout_params.keys()),
        )

        total_ms = (time.time() - request_start)*1000
        _log("success", f"Total request completed in {total_ms:.0f}ms")
        if response.get("layout_data"):
            f0_count = len(response["layout_data"].get("floor_0", []))
            f1_count = len(response["layout_data"].get("floor_1", []))
            _log("success", f"Final output: {f0_count} ground-floor rooms" + (f", {f1_count} first-floor rooms" if f1_count else ""))
        response["logs"] = _logs
        logger.info(f"[PERF] /api/generate total request completed in {total_ms:.2f} ms")
        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Generate endpoint error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail={
            "error": True,
            "message": f"Internal error during prompt analysis: {str(exc)}",
            "details": {"traceback": traceback.format_exc()},
        })

@app.post("/api/template")
async def generate_from_template(req: TemplateRequest):
    """Generate layout params for a predefined template."""
    request_start = time.time()
    logger.info("[PERF] /api/template API endpoint hit.")
    try:
        template_upper = req.template.upper().replace(" ", "")

        # Template definitions
        templates: Dict[str, Dict[str, Any]] = {
            "1BHK": {
                "bhk": 1,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                ],
            },
            "2BHK": {
                "bhk": 2,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                ],
            },
            "3BHK": {
                "bhk": 3,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "dining_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "master_bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                ],
            },
            "4BHK": {
                "bhk": 4,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "dining_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "master_bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                    {"type": "store_room", "confidence": 100},
                ],
            },
            "OPENKITCHEN": {
                "bhk": 2,
                "styles": ["open concept"],
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "dining_room", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                ],
            },
            "CUSTOM": {
                "bhk": 0,
                "styles": [],
                "rooms": [{"type": r, "confidence": 100} for r in (req.customRooms or [])],
            }
        }

        if template_upper not in templates:
            available = ", ".join(sorted(templates.keys()))
            raise HTTPException(status_code=400, detail={
                "error": True,
                "message": f"Unknown template '{req.template}'. Available: {available}",
                "details": {"available_templates": sorted(templates.keys())},
            })

        template = templates[template_upper]
        area_sqft = int(req.width * req.length)

        layout_params = {
            **template,
            "plot_width": req.width,
            "plot_length": req.length,
            "area_sqft": area_sqft,
        }

        understood = [
            f"Template: {req.template}",
            f"Plot: {req.width}×{req.length} ft ({area_sqft} sq ft)",
            f"Configuration: {template['bhk']}BHK",
            f"Rooms: {len(template['rooms'])}",
        ]

        logger.info(f"Generating from template: {template} on {req.width}x{req.length} plot")
        engine = LayoutEngine(req.width, req.length, colors=req.colors or {})
        
        layout_data = {}
        
        # Floor 0
        bhk_count = template.get("bhk", 0)
        room_pool, structural_features = strip_structural(list(template["rooms"]))
        room_pool, outdoor_specs, basement_specs = split_site_specs(room_pool, "")
        first_spec = []
        if req.floors > 1:
            ground_spec, first_spec = split_duplex_specs(room_pool, bhk_count)
            floor_0_rooms = sort_spec_by_generation_order(ground_spec)
        else:
            floor_0_rooms = sort_spec_by_generation_order(room_pool)

        indian_opts = req.indianOptions or {}
        eng_start = time.time()
        generated_nodes_0 = engine.generate(floor_0_rooms, indian_options=indian_opts, restrict_slots=(req.floors > 1))
        logger.info(f"[PERF] Engine generation took {(time.time() - eng_start)*1000:.2f} ms")
        _req_types = requested_type_set(list(template["rooms"]), indian_opts)
        enforce_requested_only(generated_nodes_0, _req_types)
        ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
        AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
        WindowPlacer(generated_nodes_0, req.width, req.length,
                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
        template_warnings = validate_layout(generated_nodes_0)
        from geometry_validator import GeometryValidator
        template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_0).errors)
        template_warnings.extend(space_recommendations(template.get("rooms", []), req.width, req.length))
        
        shared_walls_0 = compute_shared_walls(generated_nodes_0)
        layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
        layout_data["walls_floor_0"] = shared_walls_0
        layout_data["mep_data"] = compute_mep_heuristics(generated_nodes_0)
        layout_data["setbacks"] = {
            "x": engine.setback_x,
            "z": engine.setback_z,
            "buildable_width": engine.buildable_width,
            "buildable_length": engine.buildable_length,
        }
        layout_data["indianOptions"] = req.indianOptions or {}
        
        # Floor 1
        generated_nodes_1 = None
        if req.floors > 1:
            staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
            if staircase:
                # SAFE FIRST SPEC FIX
                safe_first_spec = copy.deepcopy(first_spec) if first_spec else []
                if not safe_first_spec:
                    safe_first_spec = [copy.deepcopy(r) for r in tmpl["rooms"] if r["type"] in ("bedroom", "bathroom", "master_bedroom")]
                    if any(r["type"] == "master_bedroom" for r in floor_0_rooms):
                        for r in safe_first_spec:
                            if r["type"] == "master_bedroom":
                                r["type"] = "bedroom"
                                
                floor_1_rooms = sort_spec_by_generation_order(safe_first_spec)
                generated_nodes_1 = engine.generate(floor_1_rooms, blocked_zones=[staircase.rect], indian_options=indian_opts, restrict_slots=True)
                
                align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                    make_void=bool(indian_opts.get("double_height") or indian_opts.get("void")))
                enforce_requested_only(generated_nodes_1, _req_types)
                ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                WindowPlacer(generated_nodes_1, req.width, req.length,
                             setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                
                from geometry_validator import GeometryValidator
                template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_1).errors)
                
                shared_walls_1 = compute_shared_walls(generated_nodes_1)
                layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                layout_data["walls_floor_1"] = shared_walls_1
                layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)

        # Materialize site features only after indoor geometry is complete.
        additional_floors = generate_additional_floors(
            engine, first_spec, generated_nodes_0, 2, req.floors, indian_opts,
        )
        for level, nodes in additional_floors.items():
            layout_data[f"floor_{level}"] = serialize_floor_nodes(nodes, level)
            layout_data[f"walls_floor_{level}"] = compute_shared_walls(nodes)
            layout_data[f"mep_data_f{level}"] = compute_mep_heuristics(nodes)
        outdoor_areas, basement_walls, basement_nodes = materialize_site_layers(
            engine, outdoor_specs, basement_specs, generated_nodes_0,
            req.width, req.length, req.floors, "", indian_opts,
        )
        # Final layout validation (buildability gate).
        all_nodes = list(generated_nodes_0) + (generated_nodes_1 or []) + [node for nodes in additional_floors.values() for node in nodes] + list(basement_nodes)
        if basement_nodes:
            layout_data["floor_-1"] = serialize_floor_nodes(basement_nodes, -1)
            layout_data["walls_floor_-1"] = basement_walls
        layout_data["outdoor_areas"] = outdoor_areas
        template_validation = final_layout_validation(all_nodes, indian_options=indian_opts, is_duplex=(req.floors > 1))

        # Physics prediction for overall area
        physics = run_physics_prediction(
            room_width=req.width * 0.3,
            room_length=req.length * 0.3,
            floors=req.floors,
        )

        style_out = {
            "environment": "sunset",
            "lighting": "warm",
            "accentColor": "#10b981", # default emerald
            "exteriorColor": "#FDF5E6",
        }
        
        colors_dict = req.colors or {}
        # Templates do not carry a free-form prompt.
        prompt_lower = ""
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        if theme.get("accent"):
            style_out["accentColor"] = theme["accent"]
        interior_map = {
            "off_white": "#FDFBF7",
            "sage": "#9CA986",
            "terracotta": "#E2725B",
            "charcoal": "#36454F",
            "beige": "#F5F5DC"
        }
        exterior_map = {
            "ivory": "#FDF5E6",
            "cream": "#FDF5E6",
            "white": "#FFFFFF",
            "concrete": "#808080",
            "brick": "#B22222",
            "wood": "#DEB887"
        }
        roof_map = {
            "terracotta": "#8B3A3A",
            "dark_grey": "#2F4F4F",
            "brown": "#654321"
        }
        
        if colors_dict.get("interior"):
            style_out["wallFinish"] = interior_map.get(colors_dict["interior"], colors_dict["interior"])
        if colors_dict.get("exterior"):
            style_out["exteriorColor"] = exterior_map.get(colors_dict["exterior"], colors_dict["exterior"])
            style_out["accentColor"] = style_out["exteriorColor"]
        if colors_dict.get("roof"):
            style_out["roofColor"] = roof_map.get(colors_dict["roof"], colors_dict["roof"])

        if not template_validation["ok"]:
            template_warnings = list(template_warnings) + template_validation["issues"]

        response = {
            # A template is always a brand-new design session. Clients must
            # replace their project state instead of merging these rooms into
            # the previously displayed house.
            "replace_project": True,
            "layout_params": layout_params,
            "understood": understood,
            "warnings": template_warnings,
            "validation": template_validation,
            "style": {**style_out, **selected_palette},
            "layout_data": layout_data,
        }

        # Override cost with deterministic detailed cost engine
        cost_estimate = CostEngine.calculate_cost(
            area_sqft, 
            req.package or getattr(req, "package", "Standard") or "Standard", 
            req.customMaterials or getattr(req, "customMaterials", {}) or {}, 
            {"state": req.state, "district": req.district}
        )

        if physics:
            physics["cost_inr"] = int(cost_estimate["Total"])
            response["physics"] = physics
        else:
            response["physics"] = {
                "is_safe": True,
                "safety_confidence": 95.0,
                "cost_inr": int(cost_estimate["Total"]),
                "carbon_kg": 15000,
            }

        calculated_materials = CostEngine.calculate_materials(
            area_sqft,
            getattr(req, "package", "Standard") or "Standard",
            getattr(req, "customMaterials", {}) or {}
        )

        response["project"] = {
            "plot": {
                "width": req.width,
                "length": req.length,
                "areaSqft": req.width * req.length
            },
            "building": {
                "typology": req.template,
                "floors": f"Ground + {req.floors - 1}" if req.floors > 1 else "Ground only",
                "costTier": getattr(req, "package", "Standard") or "Standard"
            },
            "materials": calculated_materials
        }

        logger.info(f"[PERF] /api/template total request completed in {(time.time() - request_start)*1000:.2f} ms")
        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Template endpoint error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail={
            "error": True,
            "message": f"Template generation failed: {str(exc)}",
            "details": {"traceback": traceback.format_exc()},
        })

# ---------------------------------------------------------------------------
# SSE streaming helpers
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _noop_emit(*_args, **_kwargs):
    pass


def _stream_generate_work(req: "GenerateRequest", emit_fn: Callable) -> None:
    """Run full generate-plan logic, pushing SSE dicts to emit_fn."""
    generation_started = time.monotonic()
    generation_budget_seconds = max(10.0, float(os.getenv("GENERATION_BUDGET_SECONDS", "30")))
    generation_deadline = generation_started + generation_budget_seconds
    debug_trace: List[Dict[str, str]] = []

    def trace(message: str, level: str = "info") -> None:
        debug_trace.append({
            "type": level,
            "message": message,
            "time": f"{(time.monotonic() - generation_started):.2f}s",
        })
        getattr(logger, "warning" if level == "warn" else "info")("[GEN TRACE] %s", message)

    def type_counts(items: Iterable[Any]) -> Dict[str, int]:
        from collections import Counter
        return dict(Counter(
            _program_room_class(item.get("type") if isinstance(item, dict) else getattr(item, "type", ""))
            for item in items or []
        ))

    def node_bounds(items: Iterable[RoomNode]) -> Optional[Dict[str, float]]:
        nodes = list(items or [])
        if not nodes:
            return None
        return {
            "min_x": round(min(node.rect.x for node in nodes), 2),
            "min_z": round(min(node.rect.z for node in nodes), 2),
            "max_x": round(max(node.rect.x + node.rect.width for node in nodes), 2),
            "max_z": round(max(node.rect.z + node.rect.length for node in nodes), 2),
        }

    def require_generation_budget(stage: str, reserve_seconds: float = 0.0) -> None:
        remaining = generation_deadline - time.monotonic()
        if remaining < reserve_seconds:
            raise TimeoutError(
                f"Generation stopped at {stage} to respect the {generation_budget_seconds:.0f}-second limit. "
                "Try fewer rooms, one floor, or a larger plot."
            )

    def emit(stage: int, label: str, substage: str = ""):
        emit_fn({"stage": stage, "label": label, "substage": substage})

    try:
        emit(1, "Analyzing Requirements...", "Parsing user prompt")

        if not req.prompt or not req.prompt.strip():
            emit_fn({"error": "Prompt cannot be empty"})
            return

        # Fast, lossless path for unambiguous edits to an existing plan. Room
        # references are resolved from the project's own names/types, so this
        # works for arbitrary Gemini-generated spaces without a hardcoded room
        # catalogue. Ambiguous/add/style requests continue to AI extraction.
        prompt_lower = req.prompt.lower()
        request_mode = str(req.requestMode or "").strip().lower()
        existing_rooms = [] if request_mode == "create" else _project_rooms_for_edit(req.currentProject)
        logger.info(
            "[REQUEST MODE] mode=%s existing_rooms_visible_to_analyzer=%d",
            request_mode or "auto", len(existing_rooms),
        )
        trace(f"Request mode={request_mode or 'auto'}; existing rooms supplied to analyzer={len(existing_rooms)}")
        has_targeted_action = bool(re.search(
            r"\b(?:remove|delete|increase|bigger|larger|expand|decrease|smaller|reduce|shrink|move|place|put)\b",
            prompt_lower,
        ))
        has_combined_style_action = bool(re.search(
            r"\b(?:color|colour|paint|exterior|interior|facade|roof)\b",
            prompt_lower,
        ))
        named_room_indices = _all_prompt_room_matches(req.prompt, existing_rooms) if existing_rooms else []
        has_room_geometry_request = bool(named_room_indices) and bool(re.search(
            r"\b(?:room|near|beside|adjacent|position|behind|access|entrance|doorway|connecting)\b",
            prompt_lower,
        ))
        if existing_rooms and has_targeted_action and has_room_geometry_request and not has_combined_style_action:
            project_plot = (req.currentProject or {}).get("plot", {})
            fast_modified = build_room_changes(
                req.prompt, existing_rooms, [], [], [],
                plot_width=req.width or project_plot.get("width", 40),
                plot_length=req.length or project_plot.get("length", 40),
            )
            if fast_modified is not None:
                fast_modified, fast_report = evaluate_modified_room_transaction(
                    req.prompt, existing_rooms, fast_modified,
                    req.width or project_plot.get("width", 40),
                    req.length or project_plot.get("length", 40),
                )
            if fast_modified is not None:
                layout_data, _ = _preserve_modified_project_rooms(fast_modified)
                result = {
                    "layout_params": {
                        "plot_width": req.width or project_plot.get("width", 40),
                        "plot_length": req.length or project_plot.get("length", 40),
                    },
                    "understood": ["Applied the requested room change while preserving the existing layout"],
                    "warnings": [],
                    "style": copy.deepcopy((req.currentProject or {}).get("style", {})),
                    "layout_data": layout_data,
                }
                emit_fn({"done": True, "result": result})
                return
            elif 'fast_report' in locals():
                emit_fn({"done": True, "result": build_edit_advisory_result(
                    req, existing_rooms, edit_rejection_message(fast_report), fast_report,
                )})
                return

        slm_result = None
        if getattr(req, "analysis_id", None):
            cached_data = redis_client.get(f"analysis:{req.analysis_id}")
            if cached_data:
                slm_result = json.loads(cached_data)
                logger.info(f"[API] Restored analysis_id {req.analysis_id} from Redis.")
                
                # Apply clarifications
                if getattr(req, "clarifications", None):
                    cl = req.clarifications
                    if cl.get("road_side") and not slm_result.get("front_orientation"):
                        slm_result["front_orientation"] = str(cl["road_side"]).lower().split()[0]
                    if cl.get("coverage_preference"):
                        slm_result["coverage_preference"] = cl["coverage_preference"]
                    if cl.get("parking_count"):
                        v = str(cl["parking_count"])
                        if v.startswith("1"): slm_result.setdefault("target_rooms", []).append("parking")
                        elif v.startswith("2"): slm_result.setdefault("target_rooms", []).extend(["parking", "parking"])
                        elif v.startswith("3"): slm_result.setdefault("target_rooms", []).extend(["parking", "parking", "parking"])

        if not slm_result and USE_SLM_ENGINE:
            try:
                if existing_rooms:
                    slm_result = reason_modifications_deepseek(
                        req.prompt, build_ai_project_context(req.currentProject)
                    )
                else:
                    from cloud_extractor import extract_keywords_groq
                    slm_result = extract_keywords_groq(req.prompt, ALL_VOCABULARIES)
            except Exception as slm_e:
                logger.error("SLM Extraction Failed: %s", slm_e)
                slm_result = None

        warnings: List[str] = []

        if slm_result:
            slm_result = apply_spatial_analysis_defaults(slm_result)
            layout_params: Dict[str, Any] = {}
            if slm_result.get("bhk"):
                layout_params["bhk"] = slm_result["bhk"]
            prompt_lower = req.prompt.lower()
            valid_rooms: List[Dict] = []
            
            # Phase 1: Aggressive Deduplication
            singleton_types = {'living_room', 'dining_room', 'kitchen', 'foyer'}
            seen_types = set()
            
            for r in slm_result.get("target_rooms", []):
                if is_instruction_like_room_label(r):
                    logger.warning("[SEMANTIC GUARD] Ignored instruction-shaped target room: %s", r)
                    continue
                room_str = r.lower().replace("_", " ")
                if room_str in prompt_lower.replace("_", " ") or room_str.replace(" ", "") in prompt_lower.replace(" ", ""):
                    r_clean = r.replace(" ", "_").lower()
                    if r_clean in singleton_types:
                        has_multiple = any(f"{n} {room_str}" in prompt_lower for n in ["2", "two", "3", "three", "multiple", "double"])
                        if not has_multiple and r_clean in seen_types:
                            logger.warning(f"[PHASE 1] Stripped hallucinated duplicate room: {r_clean}")
                            continue
                        seen_types.add(r_clean)
                    valid_rooms.append(r)
            if valid_rooms:
                for key, value in slm_result.items():
                    if isinstance(value, bool) and value is True:
                        clean_room_type = key.replace("needs_", "").replace("_area", "")
                        if clean_room_type not in valid_rooms:
                            valid_rooms.append(clean_room_type)
                
                from cloud_extractor import auto_wire_topology
                layout_params["rooms"] = auto_wire_topology(
    [r.replace(" ", "_") for r in valid_rooms],
    ai_categories=slm_result
)
                for room_spec, requested_name in zip(layout_params["rooms"], valid_rooms):
                    room_spec["name"] = str(requested_name).replace("_", " ").strip()

                # --- UNIFIED AI FLAG INJECTIONS ---
                ai_outdoor_rooms = [str(r).lower().replace(" ", "_") for r in slm_result.get("outdoor_rooms", [])]
                ai_wet_rooms = [str(r).lower().replace(" ", "_") for r in slm_result.get("wet_rooms", [])]
                
                for room_dict in layout_params["rooms"]:
                    if room_dict["type"] in ai_outdoor_rooms:
                        room_dict["is_outdoor"] = True
                    if room_dict["type"] in ai_wet_rooms:
                        room_dict["is_wet"] = True
                # ----------------------------------------------------------------

            for k in ("bhk",):
                pass  # already handled above

            numbers = extract_numbers(req.prompt)
            for k, v in numbers.items():
                if k not in layout_params:
                    layout_params[k] = v

            # Explicit floor headings are the user's strongest contract.  The
            # semantic model remains primary for free-form prompts, while this
            # compiler prevents a timeout/partial response from silently
            # replacing a detailed multi-floor program with a template.
            explicit_program = {}
            written_program = extract_explicit_floor_program(req.prompt, layout_params.get("rooms", []))
            gemini_program = slm_result.get("floor_program") if isinstance(slm_result, dict) else None
            for level, raw_specs in normalize_floor_program_payload(gemini_program).items():
                normalized_specs = []
                for raw_spec in raw_specs:
                    normalized = normalize_ai_room_spec(raw_spec)
                    if normalized:
                        normalized_specs.append(normalized)
                if normalized_specs:
                    explicit_program[level] = normalized_specs
            if written_program and not explicit_program:
                explicit_program = written_program
            if explicit_program:
                source = "written floor schedule" if written_program else "Gemini floor program"
                trace(
                    f"Accepted {source}: " + "; ".join(
                        f"floor {level}={type_counts(specs)}" for level, specs in sorted(explicit_program.items())
                    )
                )
            explicit_program = ensure_internal_open_spaces(explicit_program, slm_result)
            if explicit_program:
                # Architectural completeness guard for model-produced floor
                # programs. Preserve all arbitrary rooms, while adding only
                # the universal house/circulation spaces required to make the
                # requested levels enterable and usable.
                ground_specs = explicit_program.setdefault(0, [])
                ground_types = {canonical_type(spec.get("type")) for spec in ground_specs}
                if int(layout_params.get("bhk", 0) or 0) > 0:
                    for core_type in ("living_room", "kitchen"):
                        if core_type not in ground_types:
                            ground_specs.append({"type": core_type, "name": core_type.replace("_", " "), "confidence": 100})
                            ground_types.add(core_type)
                if len(explicit_program) > 1:
                    for level in range(0, max(explicit_program) + 1):
                        level_specs = explicit_program.setdefault(level, [])
                        level_types = {canonical_type(spec.get("type")) for spec in level_specs}
                        if "staircase" not in level_types:
                            level_specs.append({"type": "staircase", "name": "Staircase", "confidence": 100})
                        circulation_types = {"corridor", "circulation", "hallway", "foyer", "lobby", "passage"}
                        if not level_types.intersection(circulation_types):
                            level_specs.append({"type": "corridor", "name": "Corridor", "confidence": 100})
                layout_params["floor_program"] = explicit_program
                layout_params["rooms"] = [spec for level in sorted(explicit_program) for spec in explicit_program[level]]
                layout_params["floors"] = max(layout_params.get("floors", 1), max(explicit_program) + 1)
            elif isinstance(slm_result, dict) and isinstance(slm_result.get("floors"), int):
                # Trust an inferred multi-floor count only when Gemini also
                # supplied a floor program or the prompt actually discusses
                # levels. This prevents an unrelated prior floor count from
                # leaking into a simple new-house request.
                has_level_language = bool(re.search(
                    r"\b(?:floor|floors|storey|storeys|story|stories|level|levels|duplex|triplex|basement|terrace|rooftop|upstairs|downstairs)\b",
                    req.prompt.lower(),
                ))
                inferred_floors = max(1, int(slm_result["floors"]))
                if inferred_floors == 1 or has_level_language:
                    layout_params["floors"] = max(layout_params.get("floors", 1), inferred_floors)

            open_matches = re.findall(r'open\s+([a-zA-Z]+)', prompt_lower)
            if open_matches:
                layout_params["open_rooms"] = [m.replace(" ", "_") for m in open_matches]

            indian_from_slm = {
                "pooja_room":    bool(slm_result.get("needs_pooja_room")),
                "utility_area":  bool(slm_result.get("utility_area")),
                "powder_room":   bool(slm_result.get("powder_room")),
                "elderly_suite": bool(slm_result.get("elderly_suite")),
                "foyer":         bool(slm_result.get("foyer")),
                "brahmasthan":   bool(slm_result.get("brahmasthan")),
                "angan":         bool(slm_result.get("angan")) or any(
                    canonical_type(value) in _INTERNAL_OPEN_TYPES
                    for value in slm_result.get("outdoor_rooms", []) or []
                ),
            }
            fe_indian = req.indianOptions or {}
            merged_indian = {k: (indian_from_slm.get(k, False) or fe_indian.get(k, False))
                             for k in set(list(indian_from_slm) + list(fe_indian))}
            layout_params["indian_options"] = merged_indian

            understood = [f"Configuration: {slm_result['bhk']}BHK"] if slm_result.get("bhk") else []
            ai_understood, ai_warnings = spatial_analysis_messages(slm_result)
            understood.extend(ai_understood)
            warnings.extend(ai_warnings)
            details = {
                "intents": [{"canonical": slm_result.get("intent", "").lower()}],
                # Edit targets must come from target_rooms, not the complete
                # floor program. Otherwise ADD dining_room is misread as ADD
                # every existing room, and a generic program type can become
                # a literal room named "room".
                "rooms": [
                    {"canonical": canonical_type(r), "confidence": 100}
                    for r in slm_result.get("target_rooms", []) or []
                    if canonical_type(r)
                ],
                "styles": [],
                "materials": [],
                "sizes": [],
                "move_target": slm_result.get("move_target_room", ""),
                "move_dest": slm_result.get("move_destination", ""),
            }
        else:
            analysis = analyze_prompt(req.prompt)
            layout_params = analysis["layout_params"]
            understood = analysis["understood"]
            warnings = list(analysis["warnings"])
            details = analysis["matched_details"]

        _merged_indian = dict(layout_params.get("indian_options", {}) or {})
        for _k, _v in (req.indianOptions or {}).items():
            if _v:
                _merged_indian[_k] = True
        layout_params["indian_options"] = _merged_indian

        emit(1, "Analyzing Requirements...", "Processing Vastu & room requirements")

        # Material / package
        colors_dict = dict(req.colors or {})
        if slm_result and slm_result.get("color_hex"):
            colors_dict["ai_color"] = slm_result["color_hex"]
        package_name = req.package or "Standard"
        presets = CostEngine.get_presets().get(package_name, CostEngine.get_presets()["Standard"])
        custom_mats = req.customMaterials or {}
        active_preset = {**presets, **{k: v for k, v in custom_mats.items() if v}}
        # Extract color from the user's prompt.
        prompt_lower = req.prompt.lower()
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        style_out = {
            "wallFinish":      active_preset.get("wall_material", "AAC Block"),
            "exteriorColor":   colors_dict.get("exterior", "ivory"),
            "accentColor":     theme.get("accent") or "#10b981",
            "roofStyle":       colors_dict.get("roof", "terracotta"),
            "windows":         active_preset.get("windows", "UPVC"),
            "doors":           (active_preset.get("doors", {}) or {}).get("Main", "Flush Door"),
            "kitchen_counter": active_preset.get("kitchen_counter", "Granite"),
        }
        if req.currentProject and req.currentProject.get("style"):
            style_out = {**style_out, **req.currentProject.get("style", {})}

        response: Dict[str, Any] = {
            "layout_params": layout_params,
            "understood": understood,
            "warnings": warnings,
            "style": style_out,
            "package_details": active_preset,
        }

        # Handle modification of existing project
        floor_extension_level, _ = parse_added_floor_request(
            req.prompt, (slm_result or {}).get("floor_program") if isinstance(slm_result, dict) else None,
        )
        is_fresh_create = request_mode == "create" or (
            str((slm_result or {}).get("intent", "")).upper() == "CREATE"
            and not (req.currentProject and floor_extension_level is not None)
        )
        current_rooms: List[Dict] = [] if is_fresh_create else _project_rooms_for_edit(req.currentProject)

        if current_rooms:
            add_floor_level = floor_extension_level
            if add_floor_level is not None:
                require_generation_budget("upper-floor extension", 9.0)
                project_plot = (req.currentProject or {}).get("plot", {})
                storey_rooms, storey_error = add_storey_to_existing_rooms(
                    current_rooms, req.prompt,
                    (slm_result or {}).get("floor_program") if isinstance(slm_result, dict) else None,
                    req.width or project_plot.get("width", 40),
                    req.length or project_plot.get("length", 40),
                )
                if storey_rooms is None:
                    emit_fn({"error": storey_error or "The requested upper floor could not be generated safely."})
                    return
                response["layout_data"], _ = _preserve_modified_project_rooms(storey_rooms)
                response["layout_params"]["floors"] = max(_room_floor_key(room) for room in storey_rooms) + 1
                response["understood"].append(
                    f"Added floor {add_floor_level} with the requested rooms and aligned staircase access"
                )
                emit_fn({"done": True, "result": response})
                return
            if attach_requested_outdoor_areas(
                response, current_rooms, req.prompt,
                req.width or 40.0, req.length or 40.0,
                int(req.floors or 1),
            ):
                emit_fn({"done": True, "result": response})
                return
            mep_adds = slm_result.get("mep_additions", []) if slm_result else []
            if mep_adds and slm_result.get("intent") == "MODIFY_MEP":
                updated_rooms = copy.deepcopy(current_rooms)
                for addition in mep_adds:
                    target = addition.get("room", "").lower()
                    item = addition.get("item", "")
                    if target and item:
                        for r in updated_rooms:
                            if target in r.get("name", "").lower() or target in r.get("type", "").lower():
                                mep_nodes = r.get("mep_nodes", [])
                                cx = r.get("x", 0) + r.get("width", 10) / 2
                                cz = r.get("z", 0) + r.get("length", 10) / 2
                                mep_nodes.append({"type": item, "x": round(cx + 1, 2), "z": round(cz + 1, 2)})
                                r["mep_nodes"] = mep_nodes
                response["layout_data"], _ = _preserve_modified_project_rooms(updated_rooms)
                emit_fn({"done": True, "result": response})
                return

            modified_rooms = build_room_changes(
                req.prompt, current_rooms,
                details.get("intents", []), details.get("rooms", []), details.get("sizes", []),
                details.get("move_target", ""), details.get("move_dest", ""),
                req.width or 40.0, req.length or 40.0,
            )
            edit_report = None
            if modified_rooms is not None:
                modified_rooms, edit_report = evaluate_modified_room_transaction(
                    req.prompt, current_rooms, modified_rooms,
                    req.width or 40.0, req.length or 40.0, slm_result,
                )
                if modified_rooms is None:
                    emit_fn({"done": True, "result": build_edit_advisory_result(
                        req, current_rooms, edit_rejection_message(edit_report), edit_report, response,
                    )})
                    return
            if modified_rooms is None and "bhk" in layout_params and str(slm_result.get("intent", "")).upper() == "CREATE" and bool(re.search(r"\b(?:generate|create|build|design|start\s+over|new\s+house)\b", req.prompt, re.I)):
                current_rooms = []
            elif modified_rooms is None and current_rooms and str((slm_result or {}).get("intent", "")).upper() in {"ADD", "MOVE", "REMOVE", "RESIZE"}:
                ai_reason = str((slm_result or {}).get("analysis_summary") or "").strip()
                blockers = (slm_result or {}).get("blocking_constraints", []) or []
                explanation = ai_reason or "; ".join(str(item) for item in blockers[:3])
                emit_fn({"done": True, "result": build_edit_advisory_result(
                    req, current_rooms,
                    (
                        "The requested layout change could not satisfy room identity, geometry, and adjacency constraints. "
                        "The existing plan was left unchanged."
                        + (f" Analysis: {explanation}" if explanation else "")
                    ),
                    base_response=response,
                )})
                return
            elif modified_rooms is None and current_rooms:
                # 1. Figure out which color the user wants
                target_color = details.get("global_color") or details.get("color_hex")
                
                # Fallback text search
                if not target_color:
                    match = re.search(r'\b(red|blue|green|yellow|orange|purple|pink|white|black|gray|grey|brown|beige|cream)\b', req.prompt.lower())
                    if match:
                        target_color = match.group(1)
                        
                if target_color:
                    target_rooms = details.get("target_rooms", [])
                    if not target_rooms and isinstance(details.get("intents"), list) and len(details["intents"]) > 0:
                        target_rooms = details["intents"][0].get("target_rooms", [])
                    
                    painted_count = 0
                    prompt_lower = req.prompt.lower()
                    if any(term in prompt_lower for term in ("exterior", "outside", "facade", "façade")):
                        response["style"]["exteriorColor"] = target_color
                        painted_count = 1
                    elif "roof" in prompt_lower:
                        response["style"]["roofColor"] = target_color
                        response["style"]["roofStyle"] = target_color
                        painted_count = 1
                    else:
                        for r in current_rooms:
                            room_name = r.get("name", "").lower()
                            room_type = r.get("type", "").lower()
                            if not target_rooms or any(t.lower() in room_name or t.lower() in room_type for t in target_rooms):
                                r["wallColor"] = target_color
                                r["wallColors"] = [target_color, target_color, target_color, target_color]
                                painted_count += 1
                    
                    if painted_count > 0:
                        response["understood"].append(f"Painted {painted_count} room(s) {target_color}")
                
                response["layout_data"], _ = _preserve_modified_project_rooms(current_rooms)
                
                # --- PREVENT SERIALIZER WIPE IN STREAM EDIT PATH ---
                layout_data = response["layout_data"]
                rooms_to_update = []
                if isinstance(layout_data, list):
                    rooms_to_update = layout_data
                elif isinstance(layout_data, dict):
                    rooms_to_update.extend(layout_data.get("rooms", []))
                    for f in layout_data.get("floors", []):
                        rooms_to_update.extend(f.get("rooms", []))
                for serialized_room in rooms_to_update:
                    if not isinstance(serialized_room, dict): continue
                    for active_room in current_rooms:
                        if serialized_room.get("id") == active_room.get("id") or serialized_room.get("type") == active_room.get("type"):
                            if "wallColor" in active_room:
                                serialized_room["wallColor"] = active_room["wallColor"]
                                serialized_room["wallColors"] = active_room.get("wallColors")
                            if "floorColor" in active_room:
                                serialized_room["floorColor"] = active_room["floorColor"]
                            if "furnitureColor" in active_room:
                                serialized_room["furnitureColor"] = active_room["furnitureColor"]
                            break
                # ----------------------------------------------------
                
                if not any("Painted" in u for u in response["understood"]):
                    response["understood"].append("Applied style changes without modifying layout structure")
                emit_fn({"done": True, "result": response})
                return
            elif modified_rooms is not None:
                # Prompt-driven move/resize operations are edits to an
                # existing plan. Re-running the full generator here used to
                # replace unrelated rooms, erase facade/interior styling and
                # break the main entry/corridor graph.
                response["layout_data"], _ = _preserve_modified_project_rooms(modified_rooms)
                response["understood"].append("Applied the requested room change while preserving the existing layout")
                emit_fn({"done": True, "result": response})
                return

                if (isinstance(details.get("intent"), str) and details.get("intent").upper() == "MOVE") or (isinstance(details.get("intents"), list) and any(isinstance(i, dict) and i.get("canonical") == "move" for i in details.get("intents", []))):
                    response["layout_data"], _ = _preserve_modified_project_rooms(modified_rooms)
                    emit_fn({"done": True, "result": response})
                    return
                    
                from cloud_extractor import auto_wire_topology
                wired_specs = auto_wire_topology([r["type"] for r in modified_rooms])
                
                master_bp = []
                for i, r in enumerate(modified_rooms):
                    master_bp.append({
                        "room_type": r["type"],
                        "position_x": r.get("x", 0),
                        "position_z": r.get("z", 0),
                        "width": r.get("width", 10),
                        "length": r.get("length", 10),
                        "connections": wired_specs[i].get("connections", []),
                        "floor_number": 1 if r.get("isFloor1") else 0
                    })
                layout_params["master_blueprint"] = master_bp
                
                layout_params["rooms"] = [
                    {
                        "type": r["type"], 
                        "confidence": 100, 
                        "width": r.get("width"), 
                        "length": r.get("length"),
                        "connections": wired_specs[i].get("connections", [])
                    } 
                    for i, r in enumerate(modified_rooms)
                ]
                current_rooms = [] 
                response["understood"].append("Reconstructed the layout to properly accommodate changes")
        if not (layout_params.get("rooms") or layout_params.get("bhk")):
            emit_fn({"done": True, "result": response})
            return

        if current_rooms:
            emit_fn({"done": True, "result": response})
            return

        # --- Fresh generation from here ---
        bhk_val = layout_params.get("bhk", 0)
        requested_rooms = layout_params.get("rooms", [])
        core_room_types = {"kitchen", "bedroom", "bathroom", "living_room", "master_bedroom"}
        requested_types = {r["type"] for r in requested_rooms}
        has_core_rooms = len(core_room_types.intersection(requested_types)) >= 2

        base_rooms: List[Dict] = []
        explicit_program = layout_params.get("floor_program") or {}
        if explicit_program:
            explicit_program = apply_floor_bathroom_roles(explicit_program, req.prompt)
            layout_params["floor_program"] = explicit_program
            base_rooms = [spec for level in sorted(explicit_program) for spec in explicit_program[level]]
        elif bhk_val > 0:
            base_rooms = get_base_rooms_for_bhk(bhk_val)
            existing_types = {r["type"] for r in base_rooms}
            for r in requested_rooms:
                if r["type"] in ("bedroom", "master_bedroom"):
                    continue
                if r["type"] not in existing_types or r["type"] in ["store_room", "pooja_room", "balcony", "study_room", "laundry"]:
                    base_rooms.append(r)
        elif has_core_rooms:
            base_rooms = requested_rooms
        else:
            base_rooms = get_base_rooms_for_bhk(1)
            existing_types = {r["type"] for r in base_rooms}
            for r in requested_rooms:
                if r["type"] not in existing_types:
                    base_rooms.append(r)

        if not explicit_program:
            base_rooms = apply_bedroom_intelligence(base_rooms, req.prompt, requested_types=requested_types)
            base_rooms = apply_bathroom_relationships(base_rooms, req.prompt)
        layout_params["rooms"] = base_rooms or get_base_rooms_for_bhk(1)

        # FIX: Missing Bathrooms Injection
        bhk_val = layout_params.get("bhk", sum(1 for r in layout_params["rooms"] if "bedroom" in r.get("type", "")))
        if bhk_val > 0 and not explicit_program:
            bath_count = sum(1 for r in layout_params["rooms"] if "bath" in r.get("type", "").lower() or "toilet" in r.get("type", "").lower())
            # BHK is a bedroom count, not a bathroom count. Preserve the
            # explicit bathroom cardinality; only supply one basic bathroom
            # when an otherwise generated program omitted bathrooms entirely.
            if bath_count == 0:
                layout_params["rooms"].append({"type": "bathroom", "confidence": 100})

        _prompt_l = (req.prompt or "").lower()
        _pooja_ok = bool(layout_params.get("indian_options", {}).get("pooja_room")) or \
            any(k in _prompt_l for k in ("pooja", "puja", "mandir", "temple", "prayer", "devghar"))
        if not _pooja_ok:
            layout_params["rooms"] = [r for r in layout_params["rooms"] if "pooja" not in r.get("type", "").lower()]

        floors = layout_params.get("floors", 1)
        if details.get("floors", 1) > 1:
            floors = details.get("floors", 1)
        if req.floors and req.floors > 1:
            floors = req.floors
            
        # Override floors if prompt explicitly asks for stairs or upper floors
        prompt_lower = req.prompt.lower()
        if any(kw in prompt_lower for kw in ["stair", "upstair", "first floor", "second floor", "duplex"]):
            floors = max(floors, 2)
        # The UI floor selector is optional; natural-language floor programs
        # must still control generation.  Infer the highest requested level
        # from phrases such as "Ground + First + Second floors".
        floor_levels = {"ground": 0, "first": 1, "second": 2, "third": 3,
                        "fourth": 4, "fifth": 5}
        mentioned_levels = [floor_levels[word] for word in floor_levels
                            if re.search(rf"\b{word}\s+floor\b", prompt_lower)]
        if mentioned_levels:
            floors = max(floors, max(mentioned_levels) + 1)
        elif re.search(r"\bground\s*\+\s*first\s*\+\s*second\b", prompt_lower):
            floors = max(floors, 3)

        # Wire topology on the final list of rooms to guarantee graph/door semantics!
        from cloud_extractor import auto_wire_topology
        bathroom_reqs = (slm_result or {}).get("bathroom_requirements")
        layout_params["rooms"] = auto_wire_topology(layout_params["rooms"], ai_categories=slm_result or {}, bathroom_requirements=bathroom_reqs)
        layout_params["rooms"] = apply_prompt_proximities(layout_params["rooms"], req.prompt)

        plot_w = layout_params.get("plot_width") or req.width or 40.0
        plot_l = layout_params.get("plot_length") or req.length or 40.0
        buildable_plot_area = plot_w * plot_l * 0.85

        from layout_engine import get_min_area
        total_required_area = sum(get_min_area(r.get("type", "room")) for r in layout_params.get("rooms", []))
        if total_required_area > buildable_plot_area:
            needed_floors = math.ceil(total_required_area / max(1.0, buildable_plot_area))
            floors = max(floors, min(3, needed_floors))
            logger.info(f"[VERTICAL ESCALATION] Total required room area ({total_required_area:.0f} sq ft) exceeds buildable plot area ({buildable_plot_area:.0f} sq ft). Auto-escalating to {floors}-story layout.")
            layout_params["floors"] = floors

        if explicit_program:
            validated_program: Dict[int, List[Dict]] = {}
            max_plot_w, max_plot_l = plot_w, plot_l
            for level, specs in explicit_program.items():
                validated, level_warns, level_w, level_l = smart_layout_validation(specs, plot_w, plot_l)
                validated_program[int(level)] = validated
                warnings.extend(level_warns)
                max_plot_w, max_plot_l = max(max_plot_w, level_w), max(max_plot_l, level_l)
            layout_params["floor_program"] = validated_program
            layout_params["rooms"] = [spec for level in sorted(validated_program) for spec in validated_program[level]]
            plot_w, plot_l = max_plot_w, max_plot_l
        else:
            validated_rooms, val_warns, plot_w, plot_l = smart_layout_validation(
                layout_params["rooms"], plot_w, plot_l
            )
            warnings.extend(val_warns)
            layout_params["rooms"] = validated_rooms
        layout_params.update({"plot_width": plot_w, "plot_length": plot_l, "area_sqft": int(plot_w * plot_l)})
        warnings.extend(space_recommendations(layout_params["rooms"], plot_w, plot_l))
        area_budget = calculate_area_budget(layout_params["rooms"], plot_w, plot_l, floors)
        layout_params["area_budget"] = area_budget
        emit_fn({"capacity": area_budget})

        emit(2, "Generating Plot Boundary...", f"Plot {int(plot_w)}×{int(plot_l)} ft · setbacks & orientation")

        engine = LayoutEngine(plot_w, plot_l, colors=colors_dict)
        engine.furniture_prompt = req.prompt

        indian_opts = layout_params.get("indian_options", {})
        # Feature flags authorize explicit rooms; they do not remove those
        # rooms from the layout program.
        room_pool = list(layout_params["rooms"])
        room_pool, structural_features = strip_structural(room_pool)
        floor_program = layout_params.get("floor_program") or {}
        floor_specs_by_level: Dict[int, List[Dict]] = {}
        if floor_program:
            outdoor_specs, basement_specs = [], []
            floor_outdoor_types: set[str] = set()
            ai_outdoor_types = {canonical_type(value) for value in (slm_result or {}).get("outdoor_rooms", []) or []}
            for level, specs in floor_program.items():
                normalized_specs = []
                level_outdoor_types = set(ai_outdoor_types)
                # A room explicitly placed inside floor_program belongs to
                # that building level even when it is open-air (balcony,
                # terrace, custom sky court, etc.). Site-only areas are those
                # listed in outdoor_rooms but absent from every floor program.
                for spec in specs:
                    normalized = normalize_ai_room_spec(spec) or dict(spec)
                    # A visible main entrance is added to the living room by
                    # the entrance placer. Do not accept an extra AI-invented
                    # entry/foyer room unless the user actually requested it.
                    normalized_type = canonical_type(normalized.get("type"))
                    if normalized_type in {"entry", "entrance", "foyer"} and not re.search(
                        r"\b(?:entry|entrance|foyer|mudroom|shoe\s*area|lobby)\b", req.prompt.lower(),
                    ):
                        logger.info("[SEMANTIC GUARD] Removed unrequested optional room: %s", normalized_type)
                        continue
                    if canonical_type(normalized.get("type")) in {"circulation", "lobby", "passage", "hallway"}:
                        normalized["type"] = "corridor"
                        normalized["name"] = "Corridor"
                    elif canonical_type(normalized.get("type")) in {"staircase", "stairwell"}:
                        normalized["type"] = "staircase"
                        normalized["name"] = "Staircase"
                    if canonical_type(normalized.get("type")) in ai_outdoor_types:
                        normalized["is_outdoor"] = True
                        normalized["roof_type"] = "open"
                    if normalized.get("is_outdoor") or str(normalized.get("roof_type", "")).lower() == "open":
                        level_outdoor_types.add(canonical_type(normalized.get("type")))
                        floor_outdoor_types.add(canonical_type(normalized.get("type")))
                    normalized_specs.append(normalized)
                bathroom_reqs = (slm_result or {}).get("bathroom_requirements")
                wired_specs = auto_wire_topology(normalized_specs, ai_categories=slm_result or {}, bathroom_requirements=bathroom_reqs)
                wired_specs = apply_floor_outdoor_connections(wired_specs, level_outdoor_types, req.prompt)
                wired_specs = apply_courtyard_and_suite_relationships(wired_specs, req.prompt)
                wired_specs = apply_prompt_proximities(wired_specs, req.prompt)
                floor_specs_by_level[int(level)] = sort_spec_by_generation_order(wired_specs)
            # Non-courtyard outdoor classifications are site layers. Keep
            # them even when Gemini's floor_program omitted them.
            existing_outdoor = {canonical_type(spec.get("type")) for spec in outdoor_specs} | floor_outdoor_types
            for raw_type in (slm_result or {}).get("outdoor_rooms", []) or []:
                room_type = canonical_type(raw_type)
                if room_type and room_type not in _INTERNAL_OPEN_TYPES and room_type not in existing_outdoor:
                    outdoor_specs.append({"type": room_type, "name": room_type.replace("_", " "), "is_outdoor": True})
                    existing_outdoor.add(room_type)

            # --- AUTOMATIC VERTICAL ESCALATION FOR OVERSIZED GROUND FLOOR ---
            all_ground_rooms = floor_specs_by_level.get(0, [])
            f0_min_area = sum(get_min_area(r.get("type", "room")) for r in all_ground_rooms)
            if floors > 1 and (f0_min_area > buildable_plot_area or len(floor_specs_by_level.get(1, [])) == 0):
                ground_spec, first_spec = split_duplex_specs(all_ground_rooms, bhk_val)
                floor_specs_by_level[0] = sort_spec_by_generation_order(ground_spec)
                floor_specs_by_level[1] = sort_spec_by_generation_order(first_spec)
                logger.info(f"[VERTICAL ESCALATION] Split oversized ground floor ({f0_min_area:.0f} sq ft) into Duplex: {len(floor_specs_by_level[0])} ground rooms, {len(floor_specs_by_level[1])} upper rooms.")

            # --- ABSOLUTE FAIL-SAFE: PYTHON FLOOR BALANCER ---
            if floors > 1 and len(floor_specs_by_level.get(1, [])) > 0:
                from layout_engine import get_min_area
                
                f0_area = sum(get_min_area(r.get("type", "room")) for r in floor_specs_by_level.get(0, []))
                f1_area = sum(get_min_area(r.get("type", "room")) for r in floor_specs_by_level.get(1, []))
                
                # If Floor 1 is heavier than Floor 0, forcefully intervene
                if f1_area > f0_area:
                    FLEXIBLE_TYPES = {'study_room', 'gym', 'children_s_play_area', 'family_lounge', 'home_office', 'bedroom'}
                    f1_specs = list(floor_specs_by_level[1])
                    
                    for spec in reversed(f1_specs):  # Iterate backwards to pull secondary rooms first
                        rtype = spec.get("type", "").lower()
                        rname = spec.get("name", "").lower()
                        if rtype in FLEXIBLE_TYPES or "bedroom" in rname:
                            # Move room to ground floor
                            floor_specs_by_level[0].append(spec)
                            floor_specs_by_level[1].remove(spec)
                            
                            # Update areas
                            shifted_area = get_min_area(rtype)
                            f0_area += shifted_area
                            f1_area -= shifted_area
                            
                            logger.info(f"[BALANCER] Overrode Gemini: Moved {spec.get('name')} to Floor 0 to fix Inverse Pyramid.")
                            
                            # Stop moving once balanced
                            if f0_area >= f1_area:
                                break

            # --- ABSOLUTE FAIL-SAFE: STRUCTURAL PADDER ---
            # If Floor 1 STILL requires more area, inject an outdoor pad to expand the foundation
            if floors > 1 and len(floor_specs_by_level.get(1, [])) > 0:
                f0_area = sum(get_min_area(r.get("type", "room")) for r in floor_specs_by_level.get(0, []))
                f1_area = sum(get_min_area(r.get("type", "room")) for r in floor_specs_by_level.get(1, []))
                if f1_area > f0_area:
                    padding_needed = f1_area - f0_area
                    pad_dim = int(math.sqrt(padding_needed)) + 1
                    floor_specs_by_level[0].append({
                        "type": "outdoor_space",
                        "name": "covered_verandah_pad",
                        "bathroom_role": "",
                        "is_outdoor": True,
                        "roof_type": "open",
                        "min_w_override": pad_dim,
                        "min_l_override": pad_dim
                    })
                    logger.info(f"[PADDER] Injected {pad_dim}x{pad_dim} structural verandah to support Floor 1.")

            floor_0_rooms = floor_specs_by_level.get(0, [])
            first_spec = floor_specs_by_level.get(1, [])
            room_pool = [spec for specs in floor_specs_by_level.values() for spec in specs]
        else:
            room_pool, outdoor_specs, basement_specs = split_site_specs(room_pool, req.prompt)
            first_spec: List[Dict] = []
            if floors > 1:
                ground_spec, first_spec = split_duplex_specs(room_pool, bhk_val)
                floor_0_rooms = sort_spec_by_generation_order(ground_spec)
            else:
                floor_0_rooms = sort_spec_by_generation_order(room_pool)

        emit(3, "Generating Room Layout...", "Preparing AI-driven architecture...")

        # --- ZERO-STATIC ENGINE: Gemini Master Blueprint ---
        master_bp = None
        gemini_result = None
        bp0 = None
        logger.info("[ZERO-STATIC] Bypassing Gemini Stage 2 coordinates (slow/redundant). Routing directly to high-speed CP Solver.")

        # ─── GEOMETRIC GENERATION & VALIDATION PIPELINE (RETRY LOOP) ───
        # One bounded geometry pass keeps end-to-end creation below the 30 s
        # product budget. CP-SAT already has a deterministic fallback; repeating
        # both floors used to multiply slow furniture/API work to 2+ minutes.
        max_attempts = 3
        generated_nodes_0 = []
        generated_nodes_1 = []
        layout_data = {}

        for attempt in range(max_attempts):
            require_generation_budget("geometry planning", 9.0 if floors > 1 else 4.5)
            logger.info(f"[PIPELINE] Attempt {attempt + 1}/{max_attempts} to generate valid layout...")
            
            # Inject attempt parameter into rooms to trigger CP-SAT seed variation
            for r in floor_0_rooms:
                r["attempt"] = attempt
            for r in first_spec:
                r["attempt"] = attempt
                
            # --- APPLY ROOM SCALING ---
            coverage_str = str(slm_result.get("coverage_preference", "")) if slm_result else ""
            coverage_ratio = 0.65 # default
            if "40" in coverage_str: coverage_ratio = 0.40
            elif "50" in coverage_str: coverage_ratio = 0.50
            elif "70" in coverage_str: coverage_ratio = 0.70
            elif "85" in coverage_str: coverage_ratio = 0.85
            
            target_footprint = (plot_w * plot_l) * coverage_ratio
            from room_planner import apply_room_scaling
            floor_0_rooms = apply_room_scaling(floor_0_rooms, target_footprint)
            if floors > 1 and first_spec:
                first_spec = apply_room_scaling(first_spec, target_footprint)

            try:
                # Calculate minimum foundation dimensions needed for Floor 1
                if floors > 1 and first_spec:
                    from layout_engine import get_min_area
                    floor_1_min_area = sum(get_min_area(r.get("type", "room")) for r in first_spec)
                    if floor_1_min_area > 0:
                        aspect = plot_w / max(1.0, plot_l)
                        min_l_needed = math.sqrt(floor_1_min_area / max(0.1, aspect))
                        min_w_needed = floor_1_min_area / max(1.0, min_l_needed)
                        engine.min_foundation_dims = (min_w_needed, min_l_needed)

                # Generate Floor 0
                generated_nodes_0 = engine.generate(
                    floor_0_rooms,
                    indian_options=indian_opts,
                    layout_rules=req.layoutRules,
                    restrict_slots=(floors > 1),
                    master_blueprint=bp0 if master_bp else None,
                    plot_info=slm_result if slm_result else None
                )
                apply_requested_room_names(generated_nodes_0, floor_0_rooms)
                _req_types = requested_type_set(layout_params["rooms"], indian_opts)
                enforce_requested_only(generated_nodes_0, _req_types)

                fidelity_0 = floor_program_fidelity_errors(generated_nodes_0, floor_0_rooms, 0)
                if fidelity_0:
                    present_ids = {str(node.id) for node in generated_nodes_0}
                    present_counts = type_counts(generated_nodes_0)
                    consumed: Dict[str, int] = {}
                    unplaced_rooms = []
                    for spec in floor_0_rooms:
                        room_class = _program_room_class(spec.get("type"))
                        consumed[room_class] = consumed.get(room_class, 0) + 1
                        if (
                            str(spec.get("id") or "") not in present_ids
                            and consumed[room_class] > present_counts.get(room_class, 0)
                        ):
                            unplaced_rooms.append({
                                "id": spec.get("id"),
                                "type": canonical_type(spec.get("type")),
                                "name": spec.get("name") or canonical_type(spec.get("type")).replace("_", " ").title(),
                                "floor": 0,
                            })
                    partial_layout = {
                        "floor_0": [node.to_dict() for node in generated_nodes_0],
                        "walls_floor_0": compute_shared_walls(generated_nodes_0),
                        "mep_data": compute_mep_heuristics(generated_nodes_0),
                        "indianOptions": indian_opts,
                    }
                    response.update({
                        "layout_data": partial_layout,
                        "area_budget": layout_params.get("area_budget"),
                        "unplaced_rooms": unplaced_rooms,
                        "generation_status": "partial",
                        "replace_project": is_fresh_create,
                        "validation": {"ok": False, "issues": fidelity_0},
                        "recommendations": [
                            "Add a second floor and move suitable private rooms upstairs.",
                            f"Increase the plot toward {layout_params['area_budget']['recommended_plot']['width']}×{layout_params['area_budget']['recommended_plot']['length']} ft.",
                            "Optimize the program by removing the lowest-priority optional spaces.",
                        ],
                        "warnings": warnings + fidelity_0,
                        "logs": debug_trace,
                    })
                    emit_fn({"done": True, "result": response})
                    return
                trace(
                    f"Floor 0 generated: requested={type_counts(floor_0_rooms)}; "
                    f"realized={type_counts(generated_nodes_0)}; bounds={node_bounds(generated_nodes_0)}"
                )

                ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
                arch_warnings = ArchitecturalRules.validate_rules(generated_nodes_0)
                AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
                WindowPlacer(generated_nodes_0, engine.plot_width, engine.plot_length,
                             setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                courtyard_window_count = place_courtyard_facing_windows(generated_nodes_0, req.prompt)
                if any(canonical_type(spec.get("type")) == "courtyard" for spec in floor_0_rooms):
                    trace(f"Courtyard-facing windows placed={courtyard_window_count}")
                
                # Post-placement validation Floor 0
                from geometry_validator import GeometryValidator
                val_0 = GeometryValidator.validate_post_placement(generated_nodes_0)

                if not val_0.is_valid:
                    logger.warning(f"[PIPELINE] Floor 0 validation failed on attempt {attempt + 1}: {val_0.errors}")
                    if attempt < max_attempts - 1:
                        logger.info(f"[PIPELINE] Attempting LOCAL REPAIR for Floor 0 (Attempt {attempt + 2})")
                        # Freeze the public core so the solver only shuffles private/wet rooms around it
                        public_core_types = {"living_room", "kitchen", "dining_room", "foyer", "corridor", "hallway", "lobby", "staircase", "stairwell", "open_kitchen", "dining_area"}
                        for spec in floor_0_rooms:
                            rt = canonical_type(spec.get("type"))
                            if rt in public_core_types:
                                # Find the generated node to get its coordinates
                                generated = next((n for n in generated_nodes_0 if n.id == spec.get("id") or canonical_type(n.type) == rt), None)
                                if generated:
                                    spec["fixed_rect"] = (generated.rect.x, generated.rect.z, generated.rect.width, generated.rect.length)
                        continue  # Retry with frozen core!
                    else:
                        raise RuntimeError(
                            "Floor 0 failed geometry/accessibility validation after repair retries: "
                            + "; ".join(val_0.errors[:8])
                        )

                # Initialize layout_data
                shared_walls_0 = compute_shared_walls(generated_nodes_0)
                layout_data = {
                    "floor_0": [n.to_dict() for n in generated_nodes_0],
                    "walls_floor_0": shared_walls_0,
                    "mep_data": compute_mep_heuristics(generated_nodes_0),
                    "setbacks": {
                        "x": engine.setback_x, "z": engine.setback_z,
                        "buildable_width": engine.buildable_width, "buildable_length": engine.buildable_length,
                    },
                    "indianOptions": indian_opts,
                }

                # Generate Floor 1 if Duplex
                if floors > 1:
                    require_generation_budget("upper-floor planning", 4.5)
                    logger.info("[PIPELINE] Generating Floor 1 (Duplex)...")
                    blocked_zones = []
                    staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
                    living_dh = next((n for n in generated_nodes_0 if getattr(n, "is_double_height", False)), None)
                    if living_dh:
                        blocked_zones.append(living_dh.rect)

                    safe_first_spec = copy.deepcopy(first_spec) if first_spec else []
                    if not safe_first_spec:
                        safe_first_spec = [copy.deepcopy(r) for r in layout_params["rooms"] if r["type"] in ("bedroom", "bathroom", "master_bedroom")]
                        if any(r["type"] == "master_bedroom" for r in floor_0_rooms):
                            for r in safe_first_spec:
                                if r["type"] == "master_bedroom":
                                    r["type"] = "bedroom"

                    floor_1_rooms = sort_spec_by_generation_order(safe_first_spec)
                    
                    if staircase:
                        # Pin the staircase on Floor 1 to the exact same coordinates as Floor 0
                        # so the CP solver builds the corridor and rooms seamlessly around it.
                        f1_stair = next((r for r in floor_1_rooms if "staircase" in r.get("type", "").lower()), None)
                        if f1_stair is None:
                            f1_stair = {"type": "staircase", "id": "staircase-f1"}
                            floor_1_rooms.append(f1_stair)
                        f1_stair["fixed_rect"] = (staircase.rect.x, staircase.rect.z, staircase.rect.width, staircase.rect.length)

                    # A double-height room is a structural absence on the
                    # upper floor, not a decoration added after placement.
                    # Reserve its exact ground-floor rectangle inside CP-SAT
                    # so bedrooms/corridors are solved around the opening.
                    if living_dh:
                        floor_1_rooms = [
                            room for room in floor_1_rooms
                            if canonical_type(room.get("type")) != "void"
                        ]
                        floor_1_rooms.append({
                            "type": "void",
                            "id": "void-f1",
                            "name": "Double Height Void",
                            "fixed_rect": (
                                living_dh.rect.x, living_dh.rect.z,
                                living_dh.rect.width, living_dh.rect.length,
                            ),
                            "is_outdoor": True,
                            "roof_type": "open",
                            "connections": [],
                        })

                    ground_indoor = [
                        node for node in generated_nodes_0
                        if not getattr(node, "is_outdoor", False) and node.roof_type != "open"
                    ]
                    slab_bounds = (
                        min(node.rect.x for node in ground_indoor),
                        min(node.rect.z for node in ground_indoor),
                        max(node.rect.x + node.rect.width for node in ground_indoor),
                        max(node.rect.z + node.rect.length for node in ground_indoor),
                    )
                    trace(f"Floor 1 allowed slab bounds={tuple(round(value, 2) for value in slab_bounds)}")

                    floor1_bp = None
                    if master_bp:
                        floor1_bp = [b for b in master_bp if b.get("floor_number", 0) == 1]
                    
                    generated_nodes_1 = engine.generate(
                        floor_1_rooms,
                        blocked_zones=blocked_zones,
                        restrict_slots=True,
                        master_blueprint=floor1_bp if floor1_bp else None,
                        plot_info={"allowed_bounds": slab_bounds},
                    )
                    apply_requested_room_names(generated_nodes_1, floor_1_rooms)

                    align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                        make_void=bool(indian_opts.get("double_height") or indian_opts.get("void")))
                    enforce_requested_only(generated_nodes_1, _req_types)
                    hard_program_errors = floor_program_fidelity_errors(generated_nodes_1, floor_1_rooms, 1)
                    hard_program_errors.extend(upper_floor_containment_errors(generated_nodes_0, generated_nodes_1, 1))
                    if hard_program_errors:
                        raise RuntimeError(" ".join(hard_program_errors))
                    trace(
                        f"Floor 1 generated: requested={type_counts(floor_1_rooms)}; "
                        f"realized={type_counts(generated_nodes_1)}; bounds={node_bounds(generated_nodes_1)}"
                    )
                    ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                    AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                    bridge_staircase_grid_seam(generated_nodes_1)
                    WindowPlacer(generated_nodes_1, engine.plot_width, engine.plot_length,
                                 setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                    for node in generated_nodes_1:
                        node.doors = [door for door in node.doors if not getattr(door, "is_main", False)]

                    val_1 = GeometryValidator.validate_post_placement(generated_nodes_1)
                    if not val_1.is_valid:
                        logger.warning(f"[PIPELINE] Floor 1 validation failed on attempt {attempt + 1}: {val_1.errors}")
                        if attempt < max_attempts - 1:
                            continue  # Retry!
                        else:
                            raise RuntimeError(
                                "Floor 1 failed geometry/accessibility validation after repair retries: "
                                + "; ".join(val_1.errors[:8])
                            )

                    shared_walls_1 = compute_shared_walls(generated_nodes_1)
                    layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                    layout_data["walls_floor_1"] = shared_walls_1
                    layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)

                # Reaching this point means every generated floor passed the
                # hard geometry and accessibility gates.
                logger.info(f"[PIPELINE] Layout generation succeeded on attempt {attempt + 1}!")
                break

            except Exception as gen_err:
                logger.error(f"[PIPELINE] Exception during generation attempt {attempt + 1}: {gen_err}")
                if attempt == max_attempts - 1:
                    raise gen_err
        else:
            emit(6, "Generating Electrical Layout...", "Switch positions · lighting · power")
            emit(7, "Generating Plumbing Layout...", "Water supply · drainage · bathroom services")

        emit(8, "Generating Materials & Structures...", "Structural analysis · cost estimation")

        additional_floors = generate_additional_floors(
            engine, first_spec, generated_nodes_0, 2, floors, indian_opts, floor_specs_by_level,
        )
        for level, nodes in additional_floors.items():
            layout_data[f"floor_{level}"] = serialize_floor_nodes(nodes, level)
            layout_data[f"walls_floor_{level}"] = compute_shared_walls(nodes)
            layout_data[f"mep_data_f{level}"] = compute_mep_heuristics(nodes)
        outdoor_areas, basement_walls, basement_nodes = materialize_site_layers(
            engine, outdoor_specs, basement_specs, generated_nodes_0,
            plot_w, plot_l, floors, req.prompt, indian_opts,
        )
        all_nodes = list(generated_nodes_0) + list(generated_nodes_1) + [node for nodes in additional_floors.values() for node in nodes] + list(basement_nodes)
        if basement_nodes:
            layout_data["floor_-1"] = serialize_floor_nodes(basement_nodes, -1)
            layout_data["walls_floor_-1"] = basement_walls
        layout_data["outdoor_areas"] = outdoor_areas
        selected_palette = _apply_selected_palette(all_nodes, req.colors)
        layout_data["floor_0"] = serialize_floor_nodes(generated_nodes_0, 0)
        if generated_nodes_1:
            layout_data["floor_1"] = serialize_floor_nodes(generated_nodes_1, 1)

        # If we reconstructed the layout from an existing project, restore preserved properties!
        try:
            mr_list = locals().get("modified_rooms")
            if getattr(req, "currentProject", None) and mr_list:
                used_mr = set()
                for node in all_nodes:
                    for i, mr in enumerate(mr_list):
                        if i not in used_mr and mr.get("type") == node.type:
                            used_mr.add(i)
                            if "furniture" in mr: node.furniture = mr["furniture"]
                            if "wallColor" in mr and mr["wallColor"]: node.wallColor = mr["wallColor"]
                            if "floorColor" in mr and mr["floorColor"]: node.floorColor = mr["floorColor"]
                            if "wallColors" in mr and mr["wallColors"]: node.wallColors = mr["wallColors"]
                            break
        except Exception as e:
            logger.warning(f"Failed to restore preserved properties in stream: {e}")

        # FIX: Color Injection
        global_color = details.get("global_color") or details.get("color_hex")
        if global_color:
            for node in all_nodes:
                node.wallColor = global_color
        
        room_colors = details.get("room_colors", [])
        for rc in room_colors:
            r_name = rc.get("room", "").lower()
            r_col = rc.get("color", "")
            if r_name and r_col:
                for node in all_nodes:
                    if r_name in node.name.lower() or r_name in getattr(node, "type", "").lower():
                        node.wallColor = r_col
        
        # Rooms on different storeys occupy the same X/Z coordinates by
        # design. Validate each floor independently; treating the combined
        # duplex as one 2-D plane produced false overlap failures everywhere.
        floor_validation_reports = [
            (0, final_layout_validation(generated_nodes_0, indian_options=indian_opts, is_duplex=(floors > 1), canonical_specs=floor_0_rooms))
        ]
        if generated_nodes_1:
            floor_validation_reports.append(
                (1, final_layout_validation(generated_nodes_1, indian_options=indian_opts, is_duplex=True, canonical_specs=first_spec))
            )
        validation_report = {
            "ok": all(report["ok"] for _, report in floor_validation_reports),
            "checks": {
                f"floor_{level}_{name}": check
                for level, report in floor_validation_reports
                for name, check in report["checks"].items()
            },
            "errors": [
                error if isinstance(error, dict) else {"code": "WARNING", "message": str(error)}
                for level, report in floor_validation_reports
                for error in report.get("errors", report.get("issues", []))
            ],
        }

        # LOCAL REPAIR PASS for repairable validation issues
        if not validation_report["ok"]:
            repaired_nodes_0 = copy.deepcopy(generated_nodes_0)
            repaired_any = False
            for error in validation_report["errors"]:
                msg = error.get("message", "")
                if "Kitchen is not adjacent to the Dining Room" in msg or "not adjacent" in msg.lower():
                    k_node = next((n for n in repaired_nodes_0 if "kitchen" in getattr(n, "type", "").lower()), None)
                    d_node = next((n for n in repaired_nodes_0 if "dining" in getattr(n, "type", "").lower()), None)
                    if k_node and d_node:
                        logger.info("[LOCAL REPAIR] Attempting local replan to bring Kitchen and Dining Room together...")
                        k_dict = k_node.to_dict()
                        d_dict = d_node.to_dict()
                        if _ensure_door_between_rooms(k_dict, d_dict):
                            k_node.doors = [Door(**d) if isinstance(d, dict) else d for d in k_dict.get("doors", [])]
                            d_node.doors = [Door(**d) if isinstance(d, dict) else d for d in d_dict.get("doors", [])]
                            k_node.connections = k_dict.get("connections", [])
                            d_node.connections = d_dict.get("connections", [])
                            repaired_any = True
                        else:
                            all_dicts = [n.to_dict() for n in repaired_nodes_0]
                            k_idx = next((i for i, r in enumerate(all_dicts) if r.get("id") == k_node.id), -1)
                            d_idx = next((i for i, r in enumerate(all_dicts) if r.get("id") == d_node.id), -1)
                            if k_idx != -1 and d_idx != -1:
                                if _place_room_next_to(all_dicts, d_idx, k_idx):
                                    _ensure_door_between_rooms(all_dicts[k_idx], all_dicts[d_idx])
                                    for idx, nd in enumerate(repaired_nodes_0):
                                        updated_r = all_dicts[idx]
                                        nd.rect.x = updated_r["x"]
                                        nd.rect.z = updated_r["z"]
                                        nd.rect.width = updated_r["width"]
                                        nd.rect.length = updated_r["length"]
                                        nd.doors = [Door(**d) if isinstance(d, dict) else d for d in updated_r.get("doors", [])]
                                        nd.connections = updated_r.get("connections", [])
                                    repaired_any = True

            if repaired_any:
                AdjacencyResolver(repaired_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
                repaired_validation_reports = [
                    (0, final_layout_validation(repaired_nodes_0, indian_options=indian_opts, is_duplex=(floors > 1)))
                ]
                if generated_nodes_1:
                    repaired_validation_reports.append(
                        (1, final_layout_validation(generated_nodes_1, indian_options=indian_opts, is_duplex=True))
                    )
                repaired_validation_report = {
                    "ok": all(report["ok"] for _, report in repaired_validation_reports),
                    "checks": {
                        f"floor_{level}_{name}": check
                        for level, report in repaired_validation_reports
                        for name, check in report["checks"].items()
                    },
                    "errors": [
                        error if isinstance(error, dict) else {"code": "WARNING", "message": str(error)}
                        for level, report in repaired_validation_reports
                        for error in report.get("errors", report.get("issues", []))
                    ],
                }
                
                # Transactional Commit: Only accept the repair if it fixed the fatal errors
                if repaired_validation_report["ok"]:
                    logger.info("[LOCAL REPAIR] Repair succeeded and passed validation! Committing changes.")
                    generated_nodes_0 = repaired_nodes_0
                    validation_report = repaired_validation_report
                    floor_validation_reports = repaired_validation_reports
                    
                    # Update layout_data to reflect the repaired nodes
                    shared_walls_0 = compute_shared_walls(generated_nodes_0)
                    layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
                    layout_data["walls_floor_0"] = shared_walls_0
                    layout_data["mep_data"] = compute_mep_heuristics(generated_nodes_0)
                else:
                    logger.warning("[LOCAL REPAIR] Repair produced an invalid layout. Rolling back.")

        response["validation"] = validation_report
        if not validation_report["ok"]:
            warnings.extend([e.get("message", str(e)) for e in validation_report["errors"]])
            error_msgs = [e.get("message", "") for e in validation_report["errors"] if e.get("code") != "WARNING"]
            logger.warning("[PIPELINE] Validation issues remaining after repair: %s", "; ".join(error_msgs))
            # Fatal Categories should correctly raise an error for now until the full Multi-Candidate loops are implemented
            raise RuntimeError("Generated layout failed final buildability validation: " + "; ".join(error_msgs))

        response["layout_data"] = layout_data
        response["warnings"] = warnings
        response["area_budget"] = layout_params.get("area_budget")
        response["replace_project"] = is_fresh_create
        trace(f"Accepted layout; replace_project={is_fresh_create}; validation_ok={validation_report['ok']}")
        response["logs"] = debug_trace

        physics = run_physics_prediction(
            room_width=plot_w * 0.3, room_length=plot_l * 0.3,
            floors=floors, ceiling_height=layout_params.get("ceiling_height_ft", 10.0),
        )
        cost_estimate = CostEngine.calculate_cost(
            layout_params.get("area_sqft", 1600), package_name, custom_mats,
            {"state": req.state, "district": req.district},
        )
        if physics:
            physics["cost_inr"] = int(cost_estimate["Total"])
            response["physics"] = physics
        else:
            response["physics"] = {"is_safe": True, "safety_confidence": 95.0,
                                    "cost_inr": int(cost_estimate["Total"]), "carbon_kg": 15000}

        response["project"] = {
            "plot": {"width": plot_w, "length": plot_l, "areaSqft": int(plot_w * plot_l)},
            "building": {"floors": f"Ground + {floors - 1}" if floors > 1 else "Ground only", "costTier": package_name},
            "materials": CostEngine.calculate_materials(layout_params.get("area_sqft", 1600), package_name, custom_mats),
        }

        # Render 2D Blueprint Image
        try:
            image_url = BlueprintRenderer.render_blueprint(all_nodes, engine.plot_width, engine.plot_length, filename="blueprint_latest.png")
            response["blueprint_url"] = image_url
        except Exception as e:
            logger.error(f"Failed to render blueprint image: {e}")

        emit(9, "Generating Engineering Blueprints...", "Construction drawings · final validation")

        emit_fn({"done": True, "result": response})

    except Exception as exc:
        logger.error("Streaming generate error: %s\n%s", exc, traceback.format_exc())
        emit_fn({"error": str(exc)})
def _stream_template_work(req: "TemplateRequest", emit_fn: Callable) -> None:
    """Run template generation in a background thread, pushing SSE dicts to pq."""
    def emit(stage: int, label: str, substage: str = ""):
        emit_fn({"stage": stage, "label": label, "substage": substage})

    try:
        details = {}  # Templates do not use prompt color extraction.
        emit(1, "Analyzing Requirements...", f"Template {req.template}")

        template_upper = req.template.upper().replace(" ", "")
        templates: Dict[str, Dict[str, Any]] = {
            "1BHK": {"bhk": 1, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "kitchen", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "foyer", "confidence": 100},
            ]},
            "2BHK": {"bhk": 2, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "kitchen", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "foyer", "confidence": 100},
            ]},
            "3BHK": {"bhk": 3, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "dining_room", "confidence": 100},
                {"type": "kitchen", "confidence": 100}, {"type": "master_bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
            ]},
            "4BHK": {"bhk": 4, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "dining_room", "confidence": 100},
                {"type": "kitchen", "confidence": 100}, {"type": "master_bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "foyer", "confidence": 100}, {"type": "store_room", "confidence": 100},
            ]},
            "OPENKITCHEN": {"bhk": 2, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "kitchen", "confidence": 100},
                {"type": "dining_room", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "foyer", "confidence": 100},
            ]},
            "CUSTOM": {"bhk": 0, "styles": [], "rooms": [{"type": r, "confidence": 100} for r in (req.customRooms or [])]},
        }

        if template_upper not in templates:
            emit_fn({"error": f"Unknown template '{req.template}'"})
            return

        tmpl = templates[template_upper]
        area_sqft = int(req.width * req.length)
        layout_params = {**tmpl, "plot_width": req.width, "plot_length": req.length, "area_sqft": area_sqft}
        understood = [f"Template: {req.template}", f"Plot: {req.width}×{req.length} ft ({area_sqft} sq ft)"]

        emit(2, "Generating Plot Boundary...", f"Plot {int(req.width)}×{int(req.length)} ft")

        colors_dict = req.colors or {}
        engine = LayoutEngine(req.width, req.length, colors=colors_dict)
        bhk_count = tmpl.get("bhk", 0)
        room_pool, _ = strip_structural(list(tmpl["rooms"]))
        room_pool, outdoor_specs, basement_specs = split_site_specs(room_pool, "")
        # HARD GUARD: a plain template (e.g. "3BHK") never includes a Pooja Room
        # unless the Pooja feature is explicitly selected.
        if not (req.indianOptions or {}).get("pooja_room"):
            room_pool = [r for r in room_pool if "pooja" not in str(r.get("type", "")).lower()]
        first_spec: List[Dict] = []
        if req.floors > 1:
            ground_spec, first_spec = split_duplex_specs(room_pool, bhk_count)
            floor_0_rooms = sort_spec_by_generation_order(ground_spec)
        else:
            floor_0_rooms = sort_spec_by_generation_order(room_pool)

        indian_opts = req.indianOptions or {}

        emit(3, "Generating Room Layout...", "BSP core room placement")

        logger.info(f"[TEMPLATE] Generating Layout Engine nodes for floor 0 (rooms: {len(floor_0_rooms)})...")
        gen_t0 = time.time()
        generated_nodes_0 = engine.generate(floor_0_rooms, indian_options=indian_opts, restrict_slots=(req.floors > 1))
        logger.info(f"[TEMPLATE] Floor 0 generation took {time.time() - gen_t0:.2f}s")
        _req_types = requested_type_set(list(tmpl["rooms"]), indian_opts)
        enforce_requested_only(generated_nodes_0, _req_types)

        emit(4, "Generating Architectural Features...", "Adjacency · windows · verandas")

        ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
        AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
        WindowPlacer(generated_nodes_0, req.width, req.length,
                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
        template_warnings = list(validate_layout(generated_nodes_0))
        from geometry_validator import GeometryValidator
        template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_0).errors)
        template_warnings.extend(space_recommendations(floor_0_rooms + first_spec, req.width, req.length))
        
        shared_walls_0 = compute_shared_walls(generated_nodes_0)
        layout_data: Dict[str, Any] = {
            "floor_0": [n.to_dict() for n in generated_nodes_0],
            "walls_floor_0": shared_walls_0,
            "mep_data": compute_mep_heuristics(generated_nodes_0),
            "setbacks": {"x": engine.setback_x, "z": engine.setback_z,
                         "buildable_width": engine.buildable_width, "buildable_length": engine.buildable_length},
            "indianOptions": indian_opts,
        }

        emit(5, "Generating Furniture...", "Room furnishing")

        generated_nodes_1: List = []
        if req.floors > 1:
            emit(6, "Generating Electrical Layout...", "First floor rooms")

            staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
            if staircase:
                floor_1_rooms = sort_spec_by_generation_order(first_spec or list(tmpl["rooms"]))
                logger.info("[TEMPLATE] Generating Layout Engine nodes for floor 1...")
                gen_t1 = time.time()
                generated_nodes_1 = engine.generate(floor_1_rooms, blocked_zones=[staircase.rect],
                                                     indian_options=indian_opts, restrict_slots=True)
                logger.info(f"[TEMPLATE] Floor 1 generation took {time.time() - gen_t1:.2f}s")

                emit(7, "Generating Plumbing Layout...", "Aligning duplex floors")

                align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                    make_void=bool(indian_opts.get("double_height") or indian_opts.get("void")))
                enforce_requested_only(generated_nodes_1, _req_types)
                ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                WindowPlacer(generated_nodes_1, req.width, req.length,
                             setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                
                from geometry_validator import GeometryValidator
                template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_1).errors)
                
                shared_walls_1 = compute_shared_walls(generated_nodes_1)
                layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                layout_data["walls_floor_1"] = shared_walls_1
                layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)
        else:
            emit(6, "Generating Electrical Layout...", "Switches · lighting · power points")
            emit(7, "Generating Plumbing Layout...", "Water lines · drainage")

        emit(8, "Generating Materials & Structures...", "Material assignment · structural check")

        additional_floors = generate_additional_floors(
            engine, first_spec, generated_nodes_0, 2, req.floors, indian_opts,
        )
        for level, nodes in additional_floors.items():
            layout_data[f"floor_{level}"] = serialize_floor_nodes(nodes, level)
            layout_data[f"walls_floor_{level}"] = compute_shared_walls(nodes)
            layout_data[f"mep_data_f{level}"] = compute_mep_heuristics(nodes)
        outdoor_areas, basement_walls, basement_nodes = materialize_site_layers(
            engine, outdoor_specs, basement_specs, generated_nodes_0,
            req.width, req.length, req.floors, "", indian_opts,
        )
        all_nodes = list(generated_nodes_0) + list(generated_nodes_1) + [node for nodes in additional_floors.values() for node in nodes] + list(basement_nodes)
        if basement_nodes:
            layout_data["floor_-1"] = serialize_floor_nodes(basement_nodes, -1)
            layout_data["walls_floor_-1"] = basement_walls
        layout_data["outdoor_areas"] = outdoor_areas
        selected_palette = _apply_selected_palette(all_nodes, req.colors)
        layout_data["floor_0"] = serialize_floor_nodes(generated_nodes_0, 0)
        if generated_nodes_1:
            layout_data["floor_1"] = serialize_floor_nodes(generated_nodes_1, 1)
        
        # FIX: Color Injection
        global_color = details.get("global_color") or details.get("color_hex")
        if global_color:
            for node in all_nodes:
                node.wallColor = global_color
        
        room_colors = details.get("room_colors", [])
        for rc in room_colors:
            r_name = rc.get("room", "").lower()
            r_col = rc.get("color", "")
            if r_name and r_col:
                for node in all_nodes:
                    if r_name in node.name.lower() or r_name in getattr(node, "type", "").lower():
                        node.wallColor = r_col
        
        template_validation = final_layout_validation(all_nodes, indian_options=indian_opts, is_duplex=(req.floors > 1))
        if not template_validation["ok"]:
            template_warnings.extend(template_validation["issues"])

        physics = run_physics_prediction(room_width=req.width * 0.3, room_length=req.length * 0.3, floors=req.floors)
        cost_estimate = CostEngine.calculate_cost(area_sqft, getattr(req, "package", "Standard") or "Standard",
                                                   getattr(req, "customMaterials", {}) or {},
                                                   {"state": req.state, "district": req.district})

        # Templates do not carry a free-form prompt.
        prompt_lower = ""
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        style_out = {"environment": "sunset", "lighting": "warm", "accentColor": theme.get("accent") or "#10b981"}

        # If we reconstructed the layout from an existing project, restore preserved properties!
        try:
            # Check if modified_rooms is in locals()
            mr_list = locals().get("modified_rooms")
            if getattr(req, "currentProject", None) and mr_list:
                used_mr = set()
                for node in all_nodes:
                    for i, mr in enumerate(mr_list):
                        if i not in used_mr and mr.get("type") == node.type:
                            used_mr.add(i)
                            if "furniture" in mr: node.furniture = mr["furniture"]
                            if "wallColor" in mr and mr["wallColor"]: node.wallColor = mr["wallColor"]
                            if "floorColor" in mr and mr["floorColor"]: node.floorColor = mr["floorColor"]
                            if "wallColors" in mr and mr["wallColors"]: node.wallColors = mr["wallColors"]
                            break
        except Exception as e:
            logger.warning(f"Failed to restore preserved properties: {e}")

        response = {
            "replace_project": True,
            "layout_params": layout_params,
            "understood": understood,
            "warnings": template_warnings,
            "validation": template_validation,
            "style": {**style_out, **selected_palette},
            "layout_data": layout_data,
            "physics": {"is_safe": True, "safety_confidence": 95.0,
                        "cost_inr": int(cost_estimate["Total"]), "carbon_kg": 15000},
            "project": {
                "plot": {"width": req.width, "length": req.length, "areaSqft": area_sqft},
                "building": {"typology": req.template,
                             "floors": f"Ground + {req.floors - 1}" if req.floors > 1 else "Ground only",
                             "costTier": getattr(req, "package", "Standard") or "Standard"},
                "materials": CostEngine.calculate_materials(area_sqft, getattr(req, "package", "Standard") or "Standard",
                                                             getattr(req, "customMaterials", {}) or {}),
            },
        }
        if physics:
            response["physics"]["cost_inr"] = int(cost_estimate["Total"])

        emit(9, "Generating Engineering Blueprints...", "Technical plans · final validation")

        emit_fn({"done": True, "result": response})

    except Exception as exc:
        logger.error("Streaming template error: %s\n%s", exc, traceback.format_exc())
        emit_fn({"error": str(exc)})


import uuid
import redis
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
redis_client = redis.Redis.from_url(REDIS_URL)

import redis.asyncio as aioredis
async_redis_client = aioredis.from_url(REDIS_URL)

QUESTION_LIBRARY = {
    "road_side": {
        "question": "Which side of the plot faces the main road?",
        "options": ["North", "South", "East", "West"],
    },
    "coverage_preference": {
        "question": "How much of the available building area should be used?",
        "options": [
            "More Garden (40%)",
            "Balanced (50%)",
            "Spacious Villa (70%)",
            "Maximum Allowed (85%)",
        ],
    },
    "parking_count": {
        "question": "How many cars should the plan accommodate?",
        "options": ["None", "1 Car", "2 Cars", "3+ Cars"],
    },
}

@app.post("/api/analyze-prompt")
async def analyze_prompt(req: GenerateRequest):
    logger.info("[API] Analyzing prompt for missing information...")
    try:
        from cloud_extractor import extract_keywords_groq
        slm_result = extract_keywords_groq(req.prompt, ALL_VOCABULARIES)
        
        analysis_id = str(uuid.uuid4())
        missing_keys = slm_result.get("missing_keys", [])
        questions = []
        for key in missing_keys:
            if key in QUESTION_LIBRARY:
                questions.append({
                    "key": key,
                    "question": QUESTION_LIBRARY[key]["question"],
                    "options": QUESTION_LIBRARY[key]["options"]
                })
                
        # Save session to Redis (expire in 1 hour)
        redis_client.set(f"analysis:{analysis_id}", json.dumps(slm_result), ex=3600)
        
        return {
            "analysis_id": analysis_id,
            "canonical_spec_preview": slm_result,
            "questions": questions
        }
    except Exception as e:
        logger.error(f"[API] Error analyzing prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/generate/stream')
async def generate_plan_stream(req: GenerateRequest):
    job_id = str(uuid.uuid4())
    logger.info(f"[API] Received architecture generation request. Queuing job {job_id}...")

    pubsub = async_redis_client.pubsub()
    await pubsub.subscribe(job_id)

    from celery_worker import generate_architecture_task
    generate_architecture_task.delay(req.dict(), job_id)

    async def _stream():
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data'].decode('utf-8')
                    msg_dict = json.loads(data)
                    yield _sse(msg_dict)
                    if msg_dict.get('done') or msg_dict.get('error'):
                        break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    return StreamingResponse(
        _stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )

@app.post('/api/template/stream')
async def generate_template_stream(req: TemplateRequest):
    job_id = str(uuid.uuid4())
    logger.info(f"[API] Received template generation request ({req.template}). Queuing job {job_id}...")

    pubsub = async_redis_client.pubsub()
    await pubsub.subscribe(job_id)

    from celery_worker import generate_template_task
    generate_template_task.delay(req.dict(), job_id)

    async def _stream():
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data'].decode('utf-8')
                    msg_dict = json.loads(data)
                    yield _sse(msg_dict)
                    if msg_dict.get('done') or msg_dict.get('error'):
                        break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    return StreamingResponse(
        _stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )

# ---------------------------------------------------------------------------
# MEP generation endpoints
# ---------------------------------------------------------------------------

@app.post("/api/generate-wiring")
async def api_generate_wiring(req: MEPRequest):
    try:
        updated_project = mep_generator.generate_wiring(req.project, req.options)
        return {"status": "success", "project": updated_project}
    except Exception as e:
        logger.error(f"Wiring generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-plumbing")
async def api_generate_plumbing(req: MEPRequest):
    try:
        updated_project = mep_generator.generate_plumbing(req.project, req.options)
        return {"status": "success", "project": updated_project}
    except Exception as e:
        logger.error(f"Plumbing generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/recalculate-cost")
async def api_recalculate_cost(req: CostRequest):
    """Cost/material recompute only — never touches geometry, rooms, or style.
    Lets the UI refresh price for a location/package without regenerating
    (and recoloring) the house."""
    try:
        project = req.project or {}
        rooms = project.get("rooms", []) or []
        # Derive built-up area from rooms; fall back to stored metrics/plot.
        area = 0.0
        for r in rooms:
            try:
                area += float(r.get("width", 0) or 0) * float(r.get("length", 0) or 0)
            except (TypeError, ValueError):
                pass
        if area <= 0:
            area = float(
                (project.get("metrics", {}) or {}).get("areaSqft")
                or (project.get("plot", {}) or {}).get("areaSqft")
                or 0
            )

        package = req.package or "Standard"
        location = req.location or project.get("location", {}) or {}
        constraints = req.constraints or project.get("engineering", {}) or {}

        cost_estimate = CostEngine.calculate_cost(area, package, {}, location, constraints)
        materials = CostEngine.calculate_materials(area, package, {}, constraints)

        return {
            "status": "success",
            "cost_inr": int(cost_estimate["Total"]),
            "breakdown": cost_estimate,
            "materials": materials,
            "factors": cost_estimate.get("factors", {}),
            "foundation_recommendation": cost_estimate.get("foundation_recommendation"),
            "corrosion_required": cost_estimate.get("corrosion_required", False),
            "area_sqft": int(round(area)),
        }
    except Exception as e:
        logger.error(f"Cost recalculation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-structural")
async def api_generate_structural(req: MEPRequest):
    try:
        updated_project = structural_generator.generate_structural(req.project, req.options)
        return {"status": "success", "project": updated_project}
    except Exception as e:
        logger.error(f"Structural generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def compute_mep_heuristics(nodes):
    electrical = []
    plumbing = []
    kitchens = []
    bathrooms = []

    for node in nodes:
        # Electrical: Ceiling Light in center
        cx = node.rect.x + node.rect.width / 2
        cz = node.rect.z + node.rect.length / 2
        electrical.append({
            "type": "ceiling_light",
            "room_id": node.id,
            "x": round(cx, 2),
            "z": round(cz, 2)
        })

        # Electrical: Switchboard near first door
        if hasattr(node, 'doors') and len(node.doors) > 0:
            door = node.doors[0]
            electrical.append({
                "type": "switchboard",
                "room_id": node.id,
                "x": round(door.x + 0.5, 2),
                "z": round(door.z + 0.5, 2)
            })

        # Plumbing identifying
        if "kitchen" in node.type.lower() or "kitchen" in getattr(node, 'name', '').lower():
            kitchens.append((cx, cz))
        elif "bath" in node.type.lower() or "bath" in getattr(node, 'name', '').lower():
            bathrooms.append((cx, cz))

    # Plumbing lines: Kitchen to Bathrooms
    if kitchens and bathrooms:
        main_kitchen = kitchens[0]
        for bath in bathrooms:
            plumbing.append({
                "type": "water_supply",
                "x1": round(main_kitchen[0], 2),
                "z1": round(main_kitchen[1], 2),
                "x2": round(bath[0], 2),
                "z2": round(bath[1], 2)
            })
    else:
        # Fallback: draw a main water line to exterior
        plumbing.append({
            "type": "water_supply",
            "x1": 0.0,
            "z1": 0.0,
            "x2": 15.0,
            "z2": 15.0
        })

    return {"electrical": electrical, "plumbing": plumbing}


@app.get("/api/health")
async def health():
    """Health check with model status."""
    return HealthResponse(
        status="ok",
        service="Home Vision AI Backend v2.0",
        nlp_matchers_loaded=True,
        physics_model_loaded=_physics_model is not None,
        nlp_adapter_found=_nlp_adapter_found,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

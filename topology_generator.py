"""Generate multiple topology candidates before any numerical geometry solve."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
from typing import Dict, Iterable, List, Sequence

from candidate_contract import LayoutCandidate, SpatialRelation, stable_relation_id
from constraint_schema import ConstraintKind, ConstraintStrength, IntentContract
from topology_grammar import (
    TopologyEdge, attach_unhandled, choose_all_by_type, choose_by_type,
    classify_rooms, dedupe_edges, edge, hub_chain, mutate_insert_short_corridor,
    mutate_move_utility_behind_kitchen, mutate_split_public_private_hub,
    private_branch, public_chain, room_zone,
)


@dataclass
class TopologyCandidate:
    name: str
    rooms: List[dict]
    edges: List[TopologyEdge]
    topology_family: str = ""
    candidate_id: str = ""
    objectives: Dict[str, float] = field(default_factory=dict)
    hard_errors: List[str] = field(default_factory=list)
    hard_user_violations: List[str] = field(default_factory=list)
    hard_code_violations: List[str] = field(default_factory=list)
    hard_topology_violations: List[str] = field(default_factory=list)

    @property
    def edge_signature(self) -> frozenset[tuple[frozenset[str], str]]:
        return frozenset(item.semantic_key for item in self.edges)

    def to_layout_candidate(self, contract: IntentContract) -> LayoutCandidate:
        rooms_by_id = {str(room["id"]): copy.deepcopy(room) for room in self.rooms}
        relations: Dict[str, SpatialRelation] = {}

        def add_relation(
            source: str, target: str | None, kind: str, strength: str,
            provenance: str, weight: float = 1.0, value: str | None = None,
            required_overlap: float = 0.0, topology_edge: bool = False,
            original_source_selector: str = "",
            original_target_selector: str = "",
        ) -> None:
            if not source or (target is not None and source == target):
                return
            relation_id = stable_relation_id(source, target, kind, provenance, value)
            relations[relation_id] = SpatialRelation(
                relation_id=relation_id,
                source_room_id=source,
                target_room_id=target,
                kind=kind,
                strength=strength,
                provenance=provenance,
                weight=weight,
                value=value,
                required_overlap_ft=required_overlap,
                topology_edge=topology_edge,
                original_source_selector=original_source_selector or source,
                original_target_selector=original_target_selector or target or "",
            )

        for item in self.edges:
            kind = {
                "direct_door": "direct_access",
                "attached": "exclusive_access",
                "open_flow": "open_flow",
                "adjacent": "adjacent",
            }.get(item.intent, item.intent)
            required_overlap = 4.0 if kind == "open_flow" else 3.0 if kind in {"direct_access", "exclusive_access"} else 0.0
            add_relation(
                item.source, item.target, kind, item.strength, item.origin,
                item.weight, required_overlap=required_overlap,
                topology_edge=True,
            )

        for constraint in contract.constraints:
            source_ids = _resolve_refs(constraint.source, self.rooms)
            target_ids = _resolve_refs(constraint.target, self.rooms) if constraint.target else []
            kind = {
                ConstraintKind.DIRECT_CONNECTION: "direct_access",
                ConstraintKind.EXCLUSIVE_ACCESS: "exclusive_access",
                ConstraintKind.SEPARATION: "separated",
            }.get(constraint.kind, constraint.kind.value)
            if not source_ids:
                continue
            pairs = [(source, None) for source in source_ids]
            if target_ids:
                pairs = [
                    (source, target_ids[min(index, len(target_ids) - 1)])
                    for index, source in enumerate(source_ids)
                ]
            for source, target in pairs:
                required_overlap = 0.0
                if kind in {"direct_access", "exclusive_access"}:
                    required_overlap = 3.0
                elif kind == "open_flow" and constraint.strength == ConstraintStrength.HARD:
                    required_overlap = 4.0
                elif kind == "adjacent" and constraint.strength == ConstraintStrength.HARD:
                    required_overlap = 1.0
                add_relation(
                    source, target, kind, constraint.strength.value,
                    constraint.origin.value, constraint.weight,
                    value=constraint.value, required_overlap=required_overlap,
                    original_source_selector=constraint.original_source_selector,
                    original_target_selector=constraint.original_target_selector,
                )

        fallback_entry = next((room_id for room_id, room in rooms_by_id.items() if room.get("main_entrance")), "")
        if not fallback_entry:
            fallback_entry = next((room_id for room_id, room in rooms_by_id.items() if str(room.get("type")) in {"foyer", "living_room", "lobby"}), "")
        candidate = LayoutCandidate(
            candidate_id=self.candidate_id,
            topology_id=self.name,
            topology_family=self.topology_family,
            rooms_by_id=rooms_by_id,
            relations_by_id=relations,
            entry_room_id=contract.entry_room_id if contract.entry_room_id in rooms_by_id else fallback_entry,
            objective_vector=dict(self.objectives),
            group_constraints_by_id={grp.id: grp for grp in contract.group_constraints},
        )
        from candidate_contract import ValidationError
        candidate.hard_user_violations = [ValidationError("HARD_USER", value) for value in self.hard_user_violations]
        candidate.hard_code_violations = [ValidationError("HARD_CODE", value) for value in self.hard_code_violations]
        candidate.hard_topology_violations = [ValidationError("HARD_TOPOLOGY", value) for value in self.hard_topology_violations]
        candidate.assert_identity_invariants()
        return candidate


def topology_family_for_name(name: str) -> str:
    if name.startswith("compact_central_hub"):
        return "compact_central_hub"
    if name.startswith("reroute__"):
        return "public_private_branch_reroute"
    if name.startswith("entry_"):
        return "entry_lobby_sequence"
    return name.split("__", 1)[0]


def _ensure_ids(rooms: Iterable[dict]) -> List[dict]:
    result = copy.deepcopy(list(rooms))
    counts: Dict[str, int] = {}
    for room in result:
        room_type = str(room.get("type") or "room").lower().replace(" ", "_")
        counts[room_type] = counts.get(room_type, 0) + 1
        room["id"] = str(room.get("id") or f"{room_type}-{counts[room_type]}")
    return result


def _resolve_refs(ref: str, rooms: Sequence[dict]) -> List[str]:
    direct = [str(room.get("id")) for room in rooms if str(room.get("id")) == ref]
    if direct:
        return direct
    canonical = str(ref or "").replace(" ", "_").lower()
    return [str(room.get("id")) for room in rooms if str(room.get("type", "")).replace(" ", "_").lower() == canonical]


def _required_edges(rooms: Sequence[dict], contract: IntentContract) -> List[TopologyEdge]:
    result: List[TopologyEdge] = []
    for constraint in contract.constraints:
        if not constraint.target or constraint.kind not in {
            ConstraintKind.DIRECT_CONNECTION, ConstraintKind.OPEN_FLOW,
            ConstraintKind.EXCLUSIVE_ACCESS,
        }:
            continue
        sources = _resolve_refs(constraint.source, rooms)
        targets = _resolve_refs(constraint.target, rooms)
        if not sources or not targets:
            continue
        # An explicit instance id is exact. A repeated type reference is paired
        # deterministically rather than connected to every duplicate.
        for source, target in zip(sources, targets if len(targets) > 1 else targets * len(sources)):
            result.append(TopologyEdge(
                source, target,
                intent="open_flow" if constraint.kind == ConstraintKind.OPEN_FLOW else "direct_door",
                origin=constraint.origin.value,
                strength=constraint.strength.value,
                weight=constraint.weight,
                reason=constraint.kind.value,
            ))
    return result


def _preferred_edges(rooms: Sequence[dict], contract: IntentContract) -> List[TopologyEdge]:
    result: List[TopologyEdge] = []
    for constraint in contract.constraints:
        if not constraint.target or constraint.kind != ConstraintKind.ADJACENT:
            continue
        sources, targets = _resolve_refs(constraint.source, rooms), _resolve_refs(constraint.target, rooms)
        if sources and targets:
            result.append(TopologyEdge(
                sources[0], targets[0], intent="adjacent", origin=constraint.origin.value,
                strength=constraint.strength.value, weight=constraint.weight,
                reason="explicit_adjacency",
            ))
    return result


def _enforce_exclusive_ownership(
    edges: Iterable[TopologyEdge], rooms: Sequence[dict], contract: IntentContract,
) -> List[TopologyEdge]:
    """Make each exclusive room a leaf connected only to its exact owner."""
    result = list(edges)
    for constraint in contract.constraints:
        if constraint.kind != ConstraintKind.EXCLUSIVE_ACCESS or not constraint.target:
            continue
        sources, targets = _resolve_refs(constraint.source, rooms), _resolve_refs(constraint.target, rooms)
        for source, target in zip(sources, targets if len(targets) > 1 else targets * len(sources)):
            result = [item for item in result if source not in (item.source, item.target)]
            result.append(TopologyEdge(
                source, target, intent="attached", origin=constraint.origin.value,
                strength="hard", weight=constraint.weight, reason="exclusive_owner",
            ))
    return dedupe_edges(result)


def _materialize(candidate: TopologyCandidate, contract: IntentContract) -> TopologyCandidate:
    rooms = copy.deepcopy(candidate.rooms)
    by_id = {str(room.get("id")): room for room in rooms}
    for room in rooms:
        room["connections"] = []
    for item in dedupe_edges(candidate.edges):
        if item.source not in by_id or item.target not in by_id:
            continue
        target = by_id[item.target]
        by_id[item.source]["connections"].append({
            "target_room": target.get("type"),
            "target_room_id": item.target,
            "intent": item.intent,
            "kind": "direct_connection" if item.intent == "direct_door" else item.intent,
            "strength": item.strength,
            "origin": item.origin,
            "weight": max(1, int(10 * item.weight)),
            "topology_edge": True,
            "reason": item.reason,
        })

    # Near/far and direction requirements remain typed geometry objectives.
    from intent_compiler import apply_contract_to_room_specs
    rooms = apply_contract_to_room_specs(rooms, contract)
    candidate.rooms = rooms
    candidate.edges = dedupe_edges(candidate.edges)
    return candidate


def generate_topology_candidates(
    rooms: Iterable[dict], contract: IntentContract, count: int = 12
) -> List[TopologyCandidate]:
    rooms = _ensure_ids(rooms)
    from intent_compiler import assert_relation_endpoints
    assert_relation_endpoints(contract, rooms, candidate_id=contract.program_id or "topology-generation")
    zones = classify_rooms(rooms)
    living = choose_by_type(rooms, "living_room", "family_lounge", "foyer")
    corridors = choose_all_by_type(rooms, "corridor", "hallway", "passage", "lobby", "entrance_lobby")
    corridor = corridors[0] if corridors else ""
    # Every corridor the program was given, not just the first. ensure_circulation
    # provisions extra hubs for a busy floor and they used to receive one stub
    # edge each and carry nothing, leaving one corridor to seat every door.
    chain = hub_chain(corridors)
    foyer = choose_by_type(rooms, "foyer", "entrance_lobby")
    entry = contract.entry_room_id if contract.entry_room_id in {str(r.get("id")) for r in rooms} else (foyer or living or corridor)
    default_hub = corridor or living or entry
    required = _required_edges(rooms, contract)
    preferred = _preferred_edges(rooms, contract)
    base_public = public_chain(rooms, contract.open_plan)

    candidates: List[tuple[str, List[TopologyEdge]]] = []

    # Public/private spine: the everyday residential grammar.
    private_hubs = corridors or ([living] if living else [])
    spine = base_public + list(chain) + private_branch(rooms, private_hubs)
    if living and corridor and living != corridor:
        item = edge(living, corridor, reason="public_private_threshold")
        if item: spine.append(item)
    candidates.append(("public_private_spine", attach_unhandled(rooms, spine, private_hubs or default_hub)))

    # Split branches: private circulation and service circulation diverge.
    split = list(base_public) + list(chain) + private_branch(rooms, private_hubs)
    candidates.append(("split_public_private_branches", attach_unhandled(rooms, split, private_hubs or default_hub)))

    # Small central lobby. Unlike a living-room hub, a lobby may branch freely.
    lobby_hubs = corridors or [h for h in (foyer, living) if h]
    lobby_edges = list(base_public) + list(chain) + private_branch(rooms, lobby_hubs)
    candidates.append(("small_central_lobby", attach_unhandled(rooms, lobby_edges, lobby_hubs)))

    # Open-plan public core is rewarded only when the request supports it.
    open_edges = public_chain(rooms, True) + list(chain) + private_branch(rooms, private_hubs)
    if living and corridor and living != corridor:
        item = edge(living, corridor, reason="open_core_private_threshold")
        if item: open_edges.append(item)
    candidates.append(("open_plan_public_core", attach_unhandled(rooms, open_edges, private_hubs or default_hub)))

    # A compact hub remains an option; adaptive privacy scoring decides when it
    # is suitable instead of a global maximum-degree rule.
    compact_hub = living or entry or corridor
    hub_edges = [TopologyEdge(compact_hub, str(room.get("id")), reason="compact_hub")
                 for room in rooms if compact_hub and str(room.get("id")) != compact_hub]
    candidates.append(("compact_central_hub", hub_edges))

    # Linear spine attaches destinations to the circulation line and public
    # service rooms in their natural sequence.
    linear = list(base_public) + list(chain) + private_branch(rooms, private_hubs)
    candidates.append(("linear_spine", attach_unhandled(rooms, linear, private_hubs or default_hub)))

    seeds = list(candidates)
    for name, edges in seeds:
        candidates.append((f"{name}__short_corridor", mutate_insert_short_corridor(edges, rooms, corridor)))
        candidates.append((f"{name}__service_tail", mutate_move_utility_behind_kitchen(edges, rooms)))
        candidates.append((f"{name}__split_hub", mutate_split_public_private_hub(edges, rooms, corridor)))

    # Structural reroutes supply genuine graph diversity for beam/Pareto
    # search. They are especially useful when the default spine is infeasible
    # geometrically: CP-SAT receives a different adjacency graph, not a new
    # random seed for the same one.
    room_by_id = {str(room.get("id")): room for room in rooms}
    alternate_hubs = list(dict.fromkeys(filter(None, [corridor, living, foyer, choose_by_type(rooms, "dining_room", "dining_area")])))
    spine_edges = candidates[0][1] if candidates else []
    for destination in zones["private"] + zones["service"] + zones["semi_public"]:
        for anchor in alternate_hubs:
            if destination == anchor:
                continue
            rerouted = [item for item in spine_edges if destination not in (item.source, item.target)]
            destination_type = room_by_id[destination].get("type", "")
            # Never reroute an assigned ensuite away from its owner.
            if room_by_id[destination].get("bathroom_role") == "attached" or room_by_id[destination].get("assigned_to"):
                continue
            rerouted.append(TopologyEdge(anchor, destination, reason="move_room_to_different_branch"))
            candidates.append((f"reroute__{destination}__to__{anchor}", dedupe_edges(rerouted)))

    if foyer and corridor and living:
        changed_entry = [item for item in spine_edges if item.key not in {
            frozenset((foyer, living)), frozenset((foyer, corridor)), frozenset((living, corridor)),
        }]
        changed_entry.extend([
            TopologyEdge(foyer, corridor, reason="change_entry_sequence"),
            TopologyEdge(corridor, living, reason="change_entry_sequence"),
        ])
        candidates.append(("entry_foyer_to_lobby_to_living", dedupe_edges(changed_entry)))

    materialized: List[TopologyCandidate] = []
    seen = set()
    for name, edges in candidates:
        combined = _enforce_exclusive_ownership(required + preferred + edges, rooms, contract)
        signature = frozenset(item.semantic_key for item in combined)
        if signature in seen:
            continue
        seen.add(signature)
        family = topology_family_for_name(name)
        signature_text = "|".join(
            sorted(f"{min(item.source, item.target)}>{max(item.source, item.target)}:{item.intent}" for item in combined)
        )
        candidate_id = "cand_" + hashlib.sha256(
            f"{family}|{name}|{signature_text}".encode("utf-8")
        ).hexdigest()[:16]
        materialized.append(_materialize(TopologyCandidate(
            name, copy.deepcopy(rooms), combined,
            topology_family=family, candidate_id=candidate_id,
        ), contract))
        if len(materialized) >= max(1, count):
            break
    return materialized

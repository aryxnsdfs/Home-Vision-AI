"""Hard filtering, adaptive multi-objective scoring, and Pareto selection."""
from __future__ import annotations

from collections import deque
import logging
from typing import Dict, Iterable, List, Sequence, Set

from constraint_schema import ConstraintKind, ConstraintStrength, IntentContract
from topology_generator import TopologyCandidate
from topology_grammar import CIRCULATION, is_bathroom, room_zone

logger = logging.getLogger("homevision")


OBJECTIVE_NAMES = (
    "user_preference_cost", "circulation_cost", "privacy_cost", "zoning_cost",
    "area_deviation", "aspect_ratio_cost", "wall_irregularity", "dead_space",
    "daylight_cost", "plumbing_cost", "aesthetic_cost",
)


def _graph(candidate: TopologyCandidate) -> Dict[str, Set[str]]:
    result = {str(room.get("id")): set() for room in candidate.rooms}
    for item in candidate.edges:
        if item.intent not in {"direct_door", "attached", "open_flow", "standard"}:
            continue
        if item.source in result and item.target in result:
            result[item.source].add(item.target)
            result[item.target].add(item.source)
    return result


def _room_map(candidate: TopologyCandidate) -> Dict[str, dict]:
    return {str(room.get("id")): room for room in candidate.rooms}


def _resolve(ref: str, candidate: TopologyCandidate) -> List[str]:
    if ref in _room_map(candidate):
        return [ref]
    key = str(ref or "").lower().replace(" ", "_")
    return [str(room.get("id")) for room in candidate.rooms
            if str(room.get("type", "")).lower().replace(" ", "_") == key]


def _shortest(graph: Dict[str, Set[str]], source: str, target: str) -> int:
    if source == target:
        return 0
    seen, queue = {source}, deque([(source, 0)])
    while queue:
        node, distance = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor == target:
                return distance + 1
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, distance + 1))
    return 10_000


def _entry(candidate: TopologyCandidate, contract: IntentContract) -> str:
    ids = _room_map(candidate)
    if contract.entry_room_id in ids:
        return contract.entry_room_id
    for room in candidate.rooms:
        if str(room.get("type", "")).lower() in {"foyer", "entrance_lobby", "living_room", "lobby"}:
            return str(room.get("id"))
    return next(iter(ids), "")


def _articulation_nodes(graph: Dict[str, Set[str]], entry: str) -> Set[str]:
    """Return nodes whose removal cuts an otherwise entry-reachable graph."""
    result: Set[str] = set()
    expected = set(graph)
    for blocked in graph:
        if blocked == entry:
            continue
        seen = {blocked, entry}
        queue = deque([entry])
        while queue:
            node = queue.popleft()
            for neighbor in graph.get(node, set()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        if expected - {blocked} - seen:
            result.add(blocked)
    return result

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


def hard_topology_errors(candidate: TopologyCandidate, contract: IntentContract) -> List[str]:
    from intent_compiler import assert_relation_endpoints
    assert_relation_endpoints(contract, candidate.rooms, candidate_id=candidate.candidate_id)
    graph, rooms = _graph(candidate), _room_map(candidate)
    entry = _entry(candidate, contract)
    errors: List[str] = []
    if graph and entry:
        unreachable = [room_id for room_id in graph if _shortest(graph, entry, room_id) >= 10_000]
        if unreachable:
            errors.append(f"Rooms unreachable from entry: {', '.join(unreachable)}")

    for constraint in contract.hard_constraints():
        sources, targets = _resolve(constraint.source, candidate), _resolve(constraint.target or "", candidate)
        if not sources:
            errors.append(f"Hard constraint references missing source room: {constraint.source}")
            continue
        if constraint.target and not targets:
            errors.append(f"Hard constraint references missing target room: {constraint.target}")
            continue
        if constraint.kind in {ConstraintKind.DIRECT_CONNECTION, ConstraintKind.OPEN_FLOW, ConstraintKind.EXCLUSIVE_ACCESS}:
            if sources and targets and not any(target in graph.get(source, set()) for source in sources for target in targets):
                errors.append(f"Missing hard {constraint.kind.value}: {constraint.source} -> {constraint.target}")
        elif constraint.kind == ConstraintKind.REACHABLE:
            if sources and targets and not any(_shortest(graph, source, target) < 10_000 for source in sources for target in targets):
                errors.append(f"Missing required route: {constraint.source} -> {constraint.target}")

    # Attached bathrooms are semantic, owner-bound terminal leaves. This does
    # not depend on their display name or on a generic bedroom/bathroom edge.
    terminal_ensuites_by_owner: Dict[str, Set[str]] = {}
    for bathroom_id, bathroom in rooms.items():
        role = str(bathroom.get("bathroom_role") or bathroom.get("role") or "").lower()
        if role != "attached":
            continue
        owner = str(bathroom.get("owner_room_id") or bathroom.get("assigned_to") or bathroom.get("attached_to_id") or "")
        if owner not in rooms or room_zone(rooms[owner].get("type", "")) != "private":
            errors.append(f"Attached bathroom {bathroom_id} references missing or invalid owner {owner}")
            continue
        if graph.get(bathroom_id, set()) != {owner}:
            errors.append(f"Attached bathroom {bathroom_id} has access outside its owner {owner}")
            continue
        terminal_ensuites_by_owner.setdefault(owner, set()).add(bathroom_id)

    # Contract-level exclusivity mirrors the same exact owner relationship.
    for constraint in contract.constraints:
        if constraint.kind != ConstraintKind.EXCLUSIVE_ACCESS:
            continue
        for bathroom in _resolve(constraint.source, candidate):
            allowed = set(_resolve(constraint.target or "", candidate))
            if graph.get(bathroom, set()) != allowed:
                errors.append(f"Exclusive bathroom {bathroom} has access outside its owner")

    for grp in contract.group_constraints:
        if grp.strength != "hard":
            continue
        if grp.kind == "group_reachability":
            for src in grp.resolved_source_room_ids:
                if src not in graph and src != entry:
                    continue
                r_set = _reachable(graph, src)
                for tgt in grp.resolved_target_room_ids:
                    if tgt not in r_set:
                        errors.append(f"Hard group reachability missing: {src} -> {tgt} (Constraint {grp.id})")
        elif grp.kind in {"group_separation", "zone_separation", "public_private_zoning"}:
            for src in grp.resolved_source_room_ids:
                for tgt in grp.resolved_target_room_ids:
                    if tgt in graph.get(src, set()):
                        errors.append(f"Hard group separation violated: {src} -> {tgt} (Constraint {grp.id})")

    # A private destination may never be the only route to unrelated rooms.
    articulations = _articulation_nodes(graph, entry)
    attached_by_owner: Dict[str, Set[str]] = {
        owner: set(bathrooms) for owner, bathrooms in terminal_ensuites_by_owner.items()
    }
    for constraint in contract.constraints:
        if constraint.kind == ConstraintKind.EXCLUSIVE_ACCESS and constraint.target:
            for owner in _resolve(constraint.target, candidate):
                attached_by_owner.setdefault(owner, set()).update(_resolve(constraint.source, candidate))
    for room_id in articulations:
        if room_zone(rooms[room_id].get("type", "")) == "private":
            seen = {room_id, entry}
            queue = deque([entry])
            while queue:
                node = queue.popleft()
                for neighbor in graph.get(node, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            stranded = set(graph) - {room_id} - seen - attached_by_owner.get(room_id, set())
            if stranded:
                errors.append(f"Private room {room_id} is used as circulation to {', '.join(sorted(stranded))}")

    hard_directions: Dict[str, Set[str]] = {}
    for constraint in contract.hard_constraints():
        if constraint.kind == ConstraintKind.DIRECTION:
            for source in _resolve(constraint.source, candidate):
                hard_directions.setdefault(source, set()).add(str(constraint.value or "").lower().replace("-", "_"))
    for room_id, directions in hard_directions.items():
        flat = "_".join(sorted(directions))
        if ("east" in flat and "west" in flat) or ("north" in flat and "south" in flat):
            errors.append(f"Contradictory hard directional requirements for {room_id}: {sorted(directions)}")
    return errors


def hub_penalty(room_id: str, candidate: TopologyCandidate, contract: IntentContract) -> float:
    graph, rooms = _graph(candidate), _room_map(candidate)
    room = rooms[room_id]
    degree = len(graph.get(room_id, set()))
    private_connections = sum(
        room_zone(rooms[neighbor].get("type", "")) == "private"
        for neighbor in graph.get(room_id, set())
    )
    penalty = float(private_connections * private_connections * 12)
    explicit_edges = 0
    for constraint in contract.constraints:
        if constraint.origin.value != "user" or not constraint.target:
            continue
        endpoints = set(_resolve(constraint.source, candidate) + _resolve(constraint.target, candidate))
        if room_id in endpoints and constraint.kind in {ConstraintKind.DIRECT_CONNECTION, ConstraintKind.ADJACENT, ConstraintKind.OPEN_FLOW}:
            explicit_edges += 1
    penalty -= explicit_edges * 10.0
    zone = room_zone(room.get("type", ""))
    # A many-branch hub remains legal, but each additional required opening
    # consumes finite wall perimeter and makes CP adjacency harder. This is a
    # contextual soft cost, not a fixed degree gate; explicit hub requests
    # and open public cores receive the reductions below.
    natural_branching = 4 if zone == "public" else 3
    penalty += max(0, degree - natural_branching) ** 2 * 4.0
    penalty -= explicit_edges * 4.0
    if zone == "circulation":
        penalty *= 0.1
    if contract.open_plan and zone == "public":
        penalty *= 0.35
    # Degree itself is never invalid; only context makes it costly.
    if degree <= 2:
        penalty *= 0.5
    return max(0.0, penalty)


def score_topology(candidate: TopologyCandidate, contract: IntentContract) -> Dict[str, float]:
    graph, rooms = _graph(candidate), _room_map(candidate)
    entry = _entry(candidate, contract)
    objectives = {name: 0.0 for name in OBJECTIVE_NAMES}

    hint = str(contract.topology_hint or "").lower().replace("-", "_")
    if hint:
        aliases = {
            "hub_and_branch": ("split", "lobby", "hub"),
            "compact_hub": ("compact", "hub"),
            "linear_spine": ("linear", "spine"),
            "courtyard_loop": ("courtyard", "loop"),
            "core_and_cluster": ("core", "cluster"),
        }
        accepted = aliases.get(hint, (hint,))
        if not any(token in candidate.name for token in accepted):
            objectives["zoning_cost"] += 2.0  # Gemini suggestion, never a hard gate.

    for constraint in contract.constraints:
        sources, targets = _resolve(constraint.source, candidate), _resolve(constraint.target or "", candidate)
        if not sources:
            objectives["user_preference_cost"] += constraint.priority_weight
            continue
        distances = [_shortest(graph, source, target) for source in sources for target in targets] if targets else []
        best = min(distances, default=0)
        if constraint.kind in {ConstraintKind.DIRECT_CONNECTION, ConstraintKind.ADJACENT, ConstraintKind.OPEN_FLOW, ConstraintKind.EXCLUSIVE_ACCESS}:
            if best != 1:
                objectives["user_preference_cost"] += constraint.priority_weight
        elif constraint.kind == ConstraintKind.NEAR:
            # One or two graph steps are both legitimately "near"; exact
            # rectangle distance is evaluated after geometry. This avoids
            # turning a proximity phrase into pressure for a direct hub edge.
            objectives["user_preference_cost"] += max(0, min(best, 8) - 2) * constraint.priority_weight * 0.1
        elif constraint.kind == ConstraintKind.BETWEEN:
            objectives["user_preference_cost"] += min(best, 8) * constraint.priority_weight * 0.1
        elif constraint.kind == ConstraintKind.SEPARATION:
            objectives["user_preference_cost"] += max(0, 3 - best) * constraint.priority_weight * 0.2
        elif constraint.kind == ConstraintKind.REACHABLE and best >= 10_000:
            objectives["user_preference_cost"] += constraint.priority_weight

    route_lengths = [_shortest(graph, entry, room_id) for room_id in graph if room_id != entry]
    objectives["circulation_cost"] = sum(min(distance, 20) for distance in route_lengths)
    objectives["circulation_cost"] += sum(
        4 for room in rooms.values() if room_zone(room.get("type", "")) == "circulation"
    )
    objectives["circulation_cost"] += sum(max(0, len(neighbors) - 1) for room_id, neighbors in graph.items()
                                                  if room_zone(rooms[room_id].get("type", "")) == "private") * 8

    articulations = _articulation_nodes(graph, entry)
    objectives["privacy_cost"] = sum(hub_penalty(room_id, candidate, contract) for room_id in graph)
    objectives["privacy_cost"] += sum(
        100 for room_id in articulations if room_zone(rooms[room_id].get("type", "")) == "private"
    )
    objectives["privacy_cost"] += sum(
        20 for neighbor in graph.get(entry, set()) if room_zone(rooms[neighbor].get("type", "")) == "private"
    )

    # Penalize public/private and public/wet mixing unless a circulation node
    # or explicit user edge mediates it.
    for source, neighbors in graph.items():
        for target in neighbors:
            if source >= target:
                continue
            zones = {room_zone(rooms[source].get("type", "")), room_zone(rooms[target].get("type", ""))}
            if zones == {"public", "private"}:
                objectives["zoning_cost"] += 8
            if zones == {"service", "private"}:
                objectives["zoning_cost"] += 5

    ids_by_type: Dict[str, List[str]] = {}
    for room_id, room in rooms.items():
        ids_by_type.setdefault(str(room.get("type", "")).lower(), []).append(room_id)
    kitchens = ids_by_type.get("kitchen", []) + ids_by_type.get("open_kitchen", [])
    dining = ids_by_type.get("dining_room", []) + ids_by_type.get("dining_area", [])
    utilities = ids_by_type.get("utility", []) + ids_by_type.get("laundry", []) + ids_by_type.get("store_room", [])
    if kitchens and dining and not any(d in graph.get(k, set()) for k in kitchens for d in dining):
        objectives["zoning_cost"] += 6
    if kitchens and utilities and not any(u in graph.get(k, set()) for k in kitchens for u in utilities):
        objectives["zoning_cost"] += 5

    # Topology-stage plumbing proxy: scattered wet rooms have separate graph
    # parents. Exact pipe distance is scored after geometry.
    wet_parents = set()
    for room_id, room in rooms.items():
        if is_bathroom(room.get("type", "")):
            wet_parents.update(graph.get(room_id, set()))
    objectives["plumbing_cost"] = max(0, len(wet_parents) - 1) * 2
    return objectives


def dominates(first: Dict[str, float], second: Dict[str, float]) -> bool:
    return all(first[name] <= second[name] for name in OBJECTIVE_NAMES) and any(
        first[name] < second[name] for name in OBJECTIVE_NAMES
    )


def pareto_front(candidates: Sequence[TopologyCandidate]) -> List[TopologyCandidate]:
    return [candidate for candidate in candidates if not any(
        other is not candidate and dominates(other.objectives, candidate.objectives)
        for other in candidates
    )]


def _ranking_key(candidate: TopologyCandidate) -> tuple:
    o = candidate.objectives
    # Lexicographic ordering preserves prompt compliance before architectural
    # defaults without pretending all objectives share one natural unit.
    return (
        o["user_preference_cost"], o["privacy_cost"], o["zoning_cost"],
        o["circulation_cost"], o["plumbing_cost"], candidate.name,
    )


def optimize_topologies(
    candidates: Iterable[TopologyCandidate], contract: IntentContract, keep: int = 4
) -> List[TopologyCandidate]:
    candidates = list(candidates)
    logger.info("[TOPOLOGY SEARCH] generated=%s", len(candidates))
    feasible: List[TopologyCandidate] = []
    for candidate in candidates:
        candidate.hard_errors = hard_topology_errors(candidate, contract)
        candidate.hard_user_violations = [
            value for value in candidate.hard_errors
            if value.startswith("Hard constraint") or "hard directional" in value.lower()
        ]
        candidate.hard_topology_violations = [
            value for value in candidate.hard_errors if value not in candidate.hard_user_violations
        ]
        candidate.objectives = score_topology(candidate, contract)
        if not candidate.hard_errors:
            feasible.append(candidate)
        else:
            logger.info(
                "[TOPOLOGY SEARCH] rejected candidate_id=%s family=%s hard_errors=%s",
                candidate.candidate_id, candidate.topology_family, candidate.hard_errors,
            )
    logger.info("[TOPOLOGY SEARCH] hard_feasible=%s", len(feasible))
    if not feasible:
        logger.error("[TOPOLOGY SEARCH] selected_for_geometry=0 (no hard-feasible candidates)")
        return []
    frontier = sorted(pareto_front(feasible), key=_ranking_key)
    logger.info("[TOPOLOGY SEARCH] pareto=%s", len(frontier))
    remainder = sorted([item for item in feasible if item not in frontier], key=_ranking_key)
    ordered = frontier + remainder
    # The residential spine is the deterministic low-complexity control
    # candidate. Try it first unless the user supplied an explicit topology
    # hint; every other Pareto family still competes in the following slots.
    # This is attempt ordering, not a hardcoded geometry template or gate.
    if not str(contract.topology_hint or "").strip():
        baseline = next((item for item in ordered if item.name == "public_private_spine"), None)
        if baseline is not None:
            ordered = [baseline] + [item for item in ordered if item is not baseline]
            logger.info(
                "[TOPOLOGY SEARCH] baseline_first candidate_id=%s topology=%s",
                baseline.candidate_id, baseline.name,
            )
    distinct_families = list(dict.fromkeys(item.topology_family for item in ordered))
    logger.info("[TOPOLOGY SEARCH] distinct_families=%s", len(distinct_families))

    # Round one selects at most one member of each family. Only after every
    # available family has competed may a second mutation from a family enter.
    selected: List[TopologyCandidate] = []
    for family in distinct_families:
        candidate = next(item for item in ordered if item.topology_family == family)
        selected.append(candidate)
        if len(selected) >= max(1, keep):
            break
    if len(selected) < max(1, keep):
        for candidate in ordered:
            if candidate in selected or any(candidate.edge_signature == prior.edge_signature for prior in selected):
                continue
            selected.append(candidate)
            if len(selected) >= max(1, keep):
                break
    logger.info("[TOPOLOGY SEARCH] selected_for_geometry=%s", len(selected))
    for candidate in selected:
        logger.info(
            "[TOPOLOGY SEARCH] selected candidate_id=%s topology=%s family=%s",
            candidate.candidate_id, candidate.name, candidate.topology_family,
        )
    return selected


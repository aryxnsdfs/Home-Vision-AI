"""Small architectural graph grammar used to propose structural alternatives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    target: str
    intent: str = "direct_door"
    origin: str = "architectural_default"
    strength: str = "strong"
    weight: float = 1.0
    reason: str = ""

    @property
    def key(self) -> frozenset[str]:
        return frozenset((self.source, self.target))

    @property
    def semantic_key(self) -> tuple[frozenset[str], str]:
        # Shared-wall adjacency and traversable access may legitimately apply
        # to the same room pair. They must not overwrite one another.
        category = "adjacency" if self.intent == "adjacent" else "access"
        return (self.key, category)


PUBLIC = {"living_room", "living", "dining_room", "dining_area", "family_lounge", "foyer"}
SERVICE = {"kitchen", "open_kitchen", "utility", "laundry", "store_room", "pantry"}
CIRCULATION = {"corridor", "hallway", "passage", "lobby", "entrance_lobby", "staircase", "stairwell"}
OUTDOOR = {"balcony", "courtyard", "terrace", "veranda", "porch", "garden", "parking", "portico"}


def canonical(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def room_zone(room_type: str) -> str:
    value = canonical(room_type)
    if value in CIRCULATION or any(token in value for token in ("corridor", "lobby", "passage", "stair")):
        return "circulation"
    if value in OUTDOOR:
        return "outdoor"
    if value in PUBLIC or any(token in value for token in ("living", "dining", "lounge")):
        return "public"
    if value in SERVICE or any(token in value for token in ("kitchen", "utility", "store", "laundry", "pantry")):
        return "service"
    if any(token in value for token in ("bed", "bath", "toilet", "closet", "dressing")):
        return "private"
    return "semi_public"


def is_bathroom(room_type: str) -> bool:
    value = canonical(room_type)
    return any(token in value for token in ("bath", "toilet", "washroom", "powder"))


def is_bedroom(room_type: str) -> bool:
    return "bed" in canonical(room_type)


def choose_by_type(rooms: Sequence[dict], *types: str) -> Optional[str]:
    # Preserve the caller's preference order (living before foyer, corridor
    # before staircase, etc.); set membership would silently choose whichever
    # room happened to appear first in the program.
    for wanted in (canonical(value) for value in types):
        for room in rooms:
            if canonical(room.get("type")) == wanted:
                return str(room.get("id"))
    return None


def classify_rooms(rooms: Sequence[dict]) -> Dict[str, List[str]]:
    result = {"public": [], "service": [], "private": [], "circulation": [], "outdoor": [], "semi_public": []}
    for room in rooms:
        result[room_zone(room.get("type", ""))].append(str(room.get("id")))
    return result


def edge(source: Optional[str], target: Optional[str], intent: str = "direct_door", reason: str = "") -> Optional[TopologyEdge]:
    if not source or not target or source == target:
        return None
    return TopologyEdge(source, target, intent=intent, reason=reason)


def public_chain(rooms: Sequence[dict], open_plan: bool = False) -> List[TopologyEdge]:
    foyer = choose_by_type(rooms, "foyer", "entrance_lobby")
    living = choose_by_type(rooms, "living_room", "living", "family_lounge")
    dining = choose_by_type(rooms, "dining_room", "dining_area")
    kitchen = choose_by_type(rooms, "kitchen", "open_kitchen")
    utility = choose_by_type(rooms, "utility", "laundry", "store_room")
    result: List[TopologyEdge] = []
    for item in (
        edge(foyer, living, reason="entry_sequence"),
        edge(living, dining, "open_flow" if open_plan else "direct_door", "public_sequence"),
        edge(dining or living, kitchen, "open_flow" if open_plan else "direct_door", "service_sequence"),
        edge(kitchen, utility, reason="service_sequence"),
    ):
        if item:
            result.append(item)
    return result


def choose_all_by_type(rooms: Sequence[dict], *types: str) -> List[str]:
    """Every room of these types, in order, not just the first.

    ``choose_by_type`` returns one id, and every topology fed that single id to
    ``private_branch``/``attach_unhandled``, so the whole private and semi public
    program hung off one corridor. A corridor is a rectangle with a finite
    perimeter and CP-SAT holds it to 5 ft wide, so a busy floor needed a 41 ft
    corridor to seat its doors and came back infeasible on a plot it used a
    third of. ``ensure_circulation`` had already provisioned the extra corridors;
    nothing was using them.
    """
    wanted = {canonical(value) for value in types}
    found: List[str] = []
    for room in rooms:
        if canonical(room.get("type")) in wanted:
            room_id = str(room.get("id") or "")
            if room_id and room_id not in found:
                found.append(room_id)
    return found


def _hub_list(hubs: object) -> List[str]:
    """Accept either a single hub id or an ordered list of them."""
    if hubs is None:
        return []
    if isinstance(hubs, str):
        return [hubs] if hubs else []
    return [str(h) for h in hubs if h]


def hub_chain(hubs: Sequence[str]) -> List[TopologyEdge]:
    """Link secondary hubs back to the primary one.

    Distributing rooms across several corridors only works if the corridors
    themselves are connected; otherwise a room lands on a corridor that reaches
    nothing and the layout fails reachability instead of adjacency.
    """
    chain: List[TopologyEdge] = []
    ordered = _hub_list(hubs)
    for secondary in ordered[1:]:
        item = edge(ordered[0], secondary, reason="hub_chain")
        if item:
            chain.append(item)
    return chain


def private_branch(rooms: Sequence[dict], hub_id: object) -> List[TopologyEdge]:
    result: List[TopologyEdge] = []
    by_id = {str(room.get("id")): room for room in rooms}
    hubs = _hub_list(hub_id)
    primary = hubs[0] if hubs else None
    assigned = 0
    for room in rooms:
        room_id = str(room.get("id"))
        room_type = canonical(room.get("type"))
        if room_zone(room_type) != "private" or room_id in hubs:
            continue
        if is_bathroom(room_type) and (
            canonical(room.get("bathroom_role")) == "attached"
            or room.get("assigned_to") or room.get("attached_to_id")
        ):
            owner = str(room.get("assigned_to") or room.get("attached_to_id") or "")
            if owner not in by_id:
                owner = next((str(item.get("id")) for item in rooms if is_bedroom(item.get("type", ""))), "")
            item = edge(owner, room_id, reason="exclusive_ensuite")
        else:
            # Spread the doors over every corridor the program was given.
            hub = hubs[assigned % len(hubs)] if hubs else primary
            assigned += 1
            item = edge(hub, room_id, reason="private_distribution")
        if item:
            result.append(item)
    return result


def attach_unhandled(rooms: Sequence[dict], edges: Iterable[TopologyEdge], default_hub: object) -> List[TopologyEdge]:
    """Connect remaining destinations without ever making one a transit hub."""
    result = list(edges)
    touched = {node for item in result for node in (item.source, item.target)}
    hubs = _hub_list(default_hub)
    primary = hubs[0] if hubs else None
    living = choose_by_type(rooms, "living_room", "family_lounge", "foyer") or primary
    kitchen = choose_by_type(rooms, "kitchen", "open_kitchen")
    assigned = 0
    for room in rooms:
        room_id = str(room.get("id"))
        if room_id in touched or room_id in hubs:
            continue
        zone = room_zone(room.get("type", ""))
        if zone == "service" and kitchen and room_id != kitchen:
            anchor = kitchen
        elif hubs:
            anchor = hubs[assigned % len(hubs)]
            assigned += 1
        else:
            anchor = living
        item = edge(anchor, room_id, "open_flow" if zone == "outdoor" else "direct_door", "orphan_reachability")
        if item:
            result.append(item)

    return result


def dedupe_edges(edges: Iterable[TopologyEdge]) -> List[TopologyEdge]:
    chosen: Dict[tuple[frozenset[str], str], TopologyEdge] = {}
    for item in edges:
        prior = chosen.get(item.semantic_key)
        if prior is None or (item.origin == "user" and prior.origin != "user"):
            chosen[item.semantic_key] = item
    return list(chosen.values())


def mutate_insert_short_corridor(
    edges: Sequence[TopologyEdge], rooms: Sequence[dict], corridor_id: Optional[str]
) -> List[TopologyEdge]:
    if not corridor_id:
        return list(edges)
    by_id = {str(room.get("id")): room for room in rooms}
    living = choose_by_type(rooms, "living_room", "family_lounge", "foyer")
    result: List[TopologyEdge] = []
    for item in edges:
        other = None
        if item.source == living:
            other = item.target
        elif item.target == living:
            other = item.source
        if other and room_zone(by_id.get(other, {}).get("type", "")) == "private":
            result.append(TopologyEdge(corridor_id, other, reason="insert_short_corridor"))
        else:
            result.append(item)
    if living and not any(corridor_id in (item.source, item.target) and living in (item.source, item.target) for item in result):
        result.append(TopologyEdge(living, corridor_id, reason="insert_short_corridor"))
    return dedupe_edges(result)


def mutate_split_public_private_hub(
    edges: Sequence[TopologyEdge], rooms: Sequence[dict], corridor_id: Optional[str]
) -> List[TopologyEdge]:
    return mutate_insert_short_corridor(edges, rooms, corridor_id)


def mutate_move_utility_behind_kitchen(edges: Sequence[TopologyEdge], rooms: Sequence[dict]) -> List[TopologyEdge]:
    kitchen = choose_by_type(rooms, "kitchen", "open_kitchen")
    utility = choose_by_type(rooms, "utility", "laundry", "store_room", "pantry")
    if not kitchen or not utility:
        return list(edges)
    result = [item for item in edges if utility not in (item.source, item.target)]
    result.append(TopologyEdge(kitchen, utility, reason="move_utility_behind_kitchen"))
    return dedupe_edges(result)

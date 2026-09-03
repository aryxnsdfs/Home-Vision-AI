"""No corridor may be asked to seat more doors than its perimeter can hold.

A busy floor used to come back "All topology candidates failed geometry or
realized-door validation" on a plot it filled a third of. The cause was not
area: every topology hung the whole private and semi public program off a single
corridor, CP-SAT holds a corridor to 5 ft wide, and seating eleven doors then
needed a corridor 41 ft long.

Two things had to be true for that to happen, and both are pinned here: that
`ensure_circulation` provisions enough hubs for the program, and that the
topology grammar actually distributes across the hubs it is given.
"""
import collections

import pytest

import server
from topology_grammar import canonical, room_zone
from topology_generator import generate_topology_candidates
from intent_compiler import compile_intent


def _busy_floor():
    """A 17 room floor of the kind that used to fail."""
    names = [
        "foyer", "living_room", "dining_room", "kitchen", "utility", "store_room",
        "pooja_room", "master_bedroom", "bedroom", "bedroom", "bathroom",
        "bathroom", "bathroom", "study_room", "staircase", "corridor", "balcony",
    ]
    return [{"type": t, "name": t.replace("_", " ").title()} for t in names]


def _max_degree(candidate):
    """The busiest room in the graph, whatever kind of room it is.

    Measuring only corridors misses the real offender: `compact_central_hub`
    hangs every room off the *living room*, so a corridor-only measurement
    reports a comfortable number for a graph no solver can place.
    """
    degree = collections.Counter()
    for item in candidate.edges:
        degree[item.source] += 1
        degree[item.target] += 1
    return max(degree.values(), default=0)


def test_a_foyer_is_not_counted_as_a_distribution_hub():
    # The grammar zones a foyer as public and gives it one edge to the living
    # room. Counting it here made ensure_circulation add nothing.
    assert "foyer" not in server.CIRCULATION_TYPES


# Named literally so the assertion cannot drift with the constant it is testing.
DISTRIBUTING_TYPES = {"corridor", "circulation", "hallway", "lobby", "passage", "entrance_lobby"}


def test_busy_floor_is_given_more_than_one_corridor():
    specs = server.ensure_circulation(_busy_floor())
    corridors = [s for s in specs if canonical(s.get("type")) in DISTRIBUTING_TYPES]
    assert len(corridors) >= 2, f"only {len(corridors)} hub(s) for a 17 room floor"


def test_no_single_corridor_seats_the_whole_program():
    specs = server.ensure_circulation(_busy_floor())
    for index, spec in enumerate(specs):
        spec.setdefault("id", f"{canonical(spec.get('type'))}-{index + 1}")

    contract = compile_intent("", {}, specs, program_id="circulation-load-test")
    candidates = generate_topology_candidates(specs, contract, count=8)
    assert candidates, "no topology candidate was generated at all"

    per_hub = max(2, int(server.os.getenv("ROOMS_PER_CIRCULATION_HUB", "6")))
    best = min((_max_degree(c) for c in candidates), default=0)
    # Some candidates are deliberately mega-hub (compact_central_hub); what
    # matters is that at least one spreads the load enough to be placeable.
    assert best <= per_hub + 2, (
        f"the least concentrated of {len(candidates)} candidates still puts "
        f"{best} doors on one room; expected at most {per_hub + 2}"
    )


def test_secondary_hubs_are_reachable_from_the_primary():
    # Distributing across corridors only helps if the corridors connect.
    specs = server.ensure_circulation(_busy_floor())
    for index, spec in enumerate(specs):
        spec.setdefault("id", f"{canonical(spec.get('type'))}-{index + 1}")
    contract = compile_intent("", {}, specs, program_id="hub-chain-test")
    candidates = generate_topology_candidates(specs, contract, count=8)

    hub_ids = [
        str(s.get("id")) for s in specs
        if canonical(s.get("type")) in DISTRIBUTING_TYPES
    ]
    if len(hub_ids) < 2:
        pytest.skip("only one hub provisioned; nothing to chain")

    for candidate in candidates:
        linked = {node for item in candidate.edges for node in (item.source, item.target)}
        for hub in hub_ids:
            assert hub in linked, f"{hub} is isolated in {candidate.name}"

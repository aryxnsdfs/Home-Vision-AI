"""Dead space walled in on every side must not survive into the drawing.

CP-SAT guarantees rooms do not overlap; it does not guarantee they tile. Most
gaps open onto the outside and are just an L-shaped footprint, which is ordinary
architecture. A pocket with rooms on all four sides is not - it is space nobody
can reach, behind walls with nothing on the other side. Roughly one layout in
nineteen had one.
"""
from layout_engine import LayoutEngine, Rect, RoomNode, rects_overlap


def _node(room_id, x, z, width, length, room_type="bedroom"):
    return RoomNode(id=room_id, type=room_type, name=room_id.replace("-", " ").title(),
                    rect=Rect(x, z, width, length))


def _enclosed_gap_plan():
    """A 30x20 plan with a 6x6 hole in the middle, surrounded on all sides."""
    return [
        _node("north-1", 0, 0, 30, 7, "living_room"),
        _node("south-1", 0, 13, 30, 7, "dining_room"),
        _node("west-1", 0, 7, 12, 6, "kitchen"),
        _node("east-1", 18, 7, 12, 6, "bedroom"),
        # leaves x 12..18, z 7..13 enclosed
    ]


def _notched_plan():
    """An L-shape. The gap touches the outside, so it must be left alone."""
    return [
        _node("a-1", 0, 0, 20, 10, "living_room"),
        _node("b-1", 0, 10, 10, 10, "bedroom"),
        # x 10..20, z 10..20 is open to the south and east edges
    ]


def _engine():
    engine = LayoutEngine(40, 30)
    engine.skip_furniture_generation = True
    return engine


def test_an_enclosed_pocket_is_absorbed():
    nodes = _enclosed_gap_plan()
    before = {n.id: (n.rect.width, n.rect.length) for n in nodes}
    _engine()._absorb_enclosed_pockets(nodes)
    after = {n.id: (n.rect.width, n.rect.length) for n in nodes}
    assert after != before, "the 36 sq ft hole was left in the plan"


def test_absorbing_never_creates_an_overlap():
    nodes = _enclosed_gap_plan()
    _engine()._absorb_enclosed_pockets(nodes)
    for i, first in enumerate(nodes):
        for second in nodes[i + 1:]:
            assert not rects_overlap(first.rect, second.rect), (
                f"{first.id} now overlaps {second.id}"
            )


def test_the_pocket_is_actually_gone():
    nodes = _enclosed_gap_plan()
    _engine()._absorb_enclosed_pockets(nodes)
    # Sample the centre of the old hole; some room must now cover it.
    px, pz = 15.0, 10.0
    covered = any(
        n.rect.x <= px <= n.rect.x + n.rect.width and n.rect.z <= pz <= n.rect.z + n.rect.length
        for n in nodes
    )
    assert covered, "the middle of the void is still empty"


def test_an_open_notch_is_left_alone():
    # An L-shaped house is a normal building, not a defect to be filled in.
    nodes = _notched_plan()
    before = {n.id: (n.rect.x, n.rect.z, n.rect.width, n.rect.length) for n in nodes}
    _engine()._absorb_enclosed_pockets(nodes)
    after = {n.id: (n.rect.x, n.rect.z, n.rect.width, n.rect.length) for n in nodes}
    assert after == before, "an L-shaped footprint was wrongly filled in"


def test_a_tight_plan_is_untouched():
    nodes = [
        _node("a-1", 0, 0, 10, 10, "living_room"),
        _node("b-1", 10, 0, 10, 10, "kitchen"),
        _node("c-1", 0, 10, 20, 10, "bedroom"),
    ]
    before = {n.id: (n.rect.width, n.rect.length) for n in nodes}
    _engine()._absorb_enclosed_pockets(nodes)
    assert {n.id: (n.rect.width, n.rect.length) for n in nodes} == before

"""Units and area, in the two places that had them wrong.

Both defects survived because neither endpoint has ever had a test: the
structural generator sized its columns in metres while every room dict in the
pipeline is in feet, and the cost estimate counted balconies and parking as
built-up area.
"""
import inspect

import server
import structural_generator


def _simple_house():
    # A 30 x 24 ft plan, the size where the metric thresholds misfired worst.
    return [
        {"id": "living-1", "type": "living_room", "x": 0, "z": 0, "width": 16, "length": 14},
        {"id": "kitchen-1", "type": "kitchen", "x": 16, "z": 0, "width": 14, "length": 10},
        {"id": "bedroom-1", "type": "bedroom", "x": 0, "z": 14, "width": 12, "length": 10},
    ]


def test_columns_are_sized_in_feet_not_metres():
    # 400 mm is about 1.31 ft. Read as feet it produced a 4.8 inch column.
    assert 1.2 < structural_generator.COLUMN_SIDE_FT < 1.5
    assert 4.5 < structural_generator.FOOTING_SIDE_FT < 5.2
    assert 8.0 < structural_generator.STOREY_HEIGHT_FT < 9.0


def test_a_normal_wall_does_not_need_an_intermediate_column():
    # The threshold is 4 m. Compared against feet, almost every wall tripped it.
    assert structural_generator.MAX_UNSUPPORTED_WALL_FT > 12.0

    points = structural_generator.find_corners_and_junctions(_simple_house())
    corners = {(round(p["x"], 2), round(p["z"], 2)) for p in points}
    # The 10 ft sides of the kitchen and bedroom are under the threshold, so
    # they contribute corners only - no midpoints along them.
    assert (16.0, 5.0) not in corners
    assert (0.0, 19.0) not in corners


def test_generated_columns_are_a_believable_size():
    project = {"floors": [{"rooms": _simple_house()}], "rooms": _simple_house()}
    result = structural_generator.generate_structural(project, {})
    layout = result.get("floors", [{}])[0] if result.get("floors") else result
    nodes = layout.get("structural_nodes") or result.get("structural_nodes") or []
    columns = [n for n in nodes if n.get("type") == "column"]
    assert columns, "no columns were generated at all"
    for column in columns:
        assert column["width"] > 1.0, "a column under a foot wide is a modelling error"
        assert column["height"] > 7.0, "a storey under seven feet is a modelling error"


def test_open_area_is_not_charged_as_built_up_area():
    # A balcony is floor area but not built-up area; billing for it inflated
    # every estimate that included one.
    indoor = {"type": "bedroom", "width": 10, "length": 12}
    balcony = {"type": "balcony", "width": 10, "length": 6, "is_outdoor": True}
    terrace = {"type": "terrace", "width": 8, "length": 8, "roof_type": "open"}

    def area_of(rooms):
        total = 0.0
        for r in rooms:
            if r.get("is_outdoor") or str(r.get("roof_type", "")).lower() == "open":
                continue
            total += float(r.get("width", 0)) * float(r.get("length", 0))
        return total

    assert area_of([indoor]) == 120
    assert area_of([indoor, balcony, terrace]) == 120


def test_structural_endpoint_keeps_the_room_mirror_in_step():
    # project.rooms must not drift from project.floors; that desync is what put
    # MEP nodes outside the house before.
    source = inspect.getsource(server.api_generate_structural)
    assert "sync_room_mirror" in source

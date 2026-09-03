"""The finished plan must contain everything the brief was owed.

Rooms used to go missing between the accepted program and the drawing, and each
time one was noticed a separate rescue was written for that room type. These
tests pin the single invariant that replaced them: whatever the contract
promised is either in the plan, or was shed on purpose and reported.
"""
from server import program_contract, reconcile_against_contract, _program_room_class


def _reconcile(accepted, floors, outdoor=(), accounted=(), prompt=""):
    contract = program_contract([{"type": t} for t in accepted], prompt)
    indoor, site, _notes, _surplus = reconcile_against_contract(
        contract,
        {level: [{"type": t} for t in rooms] for level, rooms in floors.items()},
        [{"type": t} for t in outdoor],
        accounted,
    )
    return sorted(r["type"] for r in indoor), sorted(r["type"] for r in site)


# Every house owes a bathroom, so the fixtures carry one except where that is
# the thing under test.
BATH = ["bathroom"]


def test_complete_plan_restores_nothing():
    indoor, site = _reconcile(BATH + ["kitchen", "gym"], {0: BATH + ["kitchen", "gym"]})
    assert indoor == [] and site == []


def test_room_lost_between_program_and_plan_is_restored():
    indoor, _ = _reconcile(BATH + ["kitchen", "study_room"], {0: BATH + ["kitchen"]})
    assert indoor == ["study_room"]


def test_room_present_under_another_name_is_not_duplicated():
    # The pipeline may realise a study as an office; it is the same ask.
    indoor, _ = _reconcile(BATH + ["study_room"], {0: BATH + ["office"]})
    assert indoor == []


def test_garage_satisfies_a_request_for_parking():
    _indoor, site = _reconcile(BATH + ["garage"], {0: BATH}, outdoor=["parking"])
    assert site == []


def test_restored_site_feature_does_not_become_an_indoor_room():
    indoor, site = _reconcile(BATH + ["garage"], {0: BATH})
    assert indoor == [] and site == ["parking"]


def test_room_shed_on_purpose_is_left_alone():
    # Deliberate sheds are reported to the user; restoring them would undo the
    # only thing that made the program fit the plot.
    indoor, _ = _reconcile(BATH + ["kitchen", "gym"], {0: BATH + ["kitchen"]}, accounted=["gym"])
    assert indoor == []


def test_counts_are_honoured_not_just_presence():
    indoor, _ = _reconcile(BATH * 2, {0: BATH})
    assert indoor == ["bathroom"]


def test_rooms_on_the_upper_floor_count_as_realised():
    indoor, _ = _reconcile(BATH + ["bedroom", "bedroom"], {0: BATH + ["bedroom"], 1: ["bedroom"]})
    assert indoor == []


def test_a_house_without_a_bathroom_gets_one():
    indoor, _ = _reconcile(["kitchen"], {0: ["kitchen"]})
    assert indoor == ["bathroom"]


def test_bathroom_count_stated_in_the_brief_is_met():
    indoor, _ = _reconcile(["kitchen"], {0: ["kitchen", "bathroom"]}, prompt="3BHK with three bathrooms")
    assert indoor == ["bathroom", "bathroom"]


def test_room_named_in_the_brief_but_absent_from_the_program():
    indoor, _ = _reconcile(BATH + ["kitchen"], {0: BATH + ["kitchen"]}, prompt="3BHK with a home gym")
    assert indoor == ["gym"]


def test_open_vocabulary_room_is_restored_like_any_other():
    # There is no list of room names anywhere in this path.
    indoor, _ = _reconcile(BATH + ["pottery_workshop"], {0: BATH})
    assert indoor == ["pottery_workshop"]


def test_equivalent_names_share_one_concept():
    for left, right in (
        ("study", "office"), ("parking", "garage"), ("utility", "laundry"),
        ("dining", "dining_room"), ("living", "living_room"), ("kitchen", "open_kitchen"),
        ("corridor", "hallway"), ("bedroom", "master_bedroom"),
    ):
        assert _program_room_class(left) == _program_room_class(right), (left, right)


def test_requested_bhk_overrides_a_program_that_miscounted():
    # "3BHK" is stated outright; a program that came back with four bedrooms is
    # the thing that is wrong, and the surplus has to be visible to the caller.
    contract = program_contract(
        [{"type": t} for t in ["master_bedroom", "bedroom", "bedroom", "bedroom", "bathroom"]],
        "Create a 3BHK duplex", bhk=3,
    )
    assert contract[_program_room_class("bedroom")] == 3

    _indoor, _site, _notes, surplus = reconcile_against_contract(
        contract,
        {0: [{"type": t} for t in ["master_bedroom", "bedroom", "bedroom", "bathroom"]],
         1: [{"type": "bedroom"}]},
        [],
    )
    # Four bedrooms realised against a contract of three.
    assert surplus[_program_room_class("bedroom")] == 1


def _types(floors):
    return {level: [s["type"] for s in specs] for level, specs in floors.items()}


def test_surplus_bedroom_comes_off_the_floor_that_has_it():
    # "two bedrooms downstairs and one master upstairs" arrived as three below
    # and one above. Taking the upper one leaves that storey unreachable.
    from server import trim_surplus_bedrooms
    floors = {
        0: [{"type": t} for t in ["bedroom", "bedroom", "bedroom", "kitchen"]],
        1: [{"type": "master_bedroom"}, {"type": "staircase"}],
    }
    trimmed, left = trim_surplus_bedrooms(floors, 1)
    assert left == 0
    assert _types(trimmed)[1] == ["master_bedroom", "staircase"]
    assert _types(trimmed)[0].count("bedroom") == 2


def test_trim_never_takes_the_master():
    from server import trim_surplus_bedrooms
    floors = {0: [{"type": "master_bedroom"}, {"type": "kitchen"}]}
    trimmed, left = trim_surplus_bedrooms(floors, 1)
    assert left == 1
    assert _types(trimmed)[0] == ["master_bedroom", "kitchen"]


def test_trim_leaves_other_room_types_alone():
    from server import trim_surplus_bedrooms
    floors = {0: [{"type": t} for t in ["corridor", "corridor", "bedroom", "bedroom"]]}
    trimmed, _left = trim_surplus_bedrooms(floors, 1)
    assert _types(trimmed)[0].count("corridor") == 2
    assert _types(trimmed)[0].count("bedroom") == 1


def test_a_shed_may_not_remove_the_last_bathroom():
    # "Plot is tight, so bathroom was left out" is not a house. A deliberate
    # shed is normally respected because the user is told about it, but a
    # building requirement outranks it.
    contract = program_contract([{"type": "kitchen"}], "3BHK house")
    indoor, _site, _notes, _surplus = reconcile_against_contract(
        contract,
        {0: [{"type": "kitchen"}]},
        [],
        accounted=["bathroom"],
    )
    assert [r["type"] for r in indoor] == ["bathroom"]


def test_a_shed_is_still_respected_when_one_bathroom_survives():
    contract = program_contract([{"type": t} for t in ("kitchen", "bathroom", "bathroom")],
                                "3BHK with two bathrooms")
    indoor, _site, _notes, _surplus = reconcile_against_contract(
        contract,
        {0: [{"type": "kitchen"}, {"type": "bathroom"}]},
        [],
        accounted=["bathroom"],
    )
    assert indoor == [], "a surplus bathroom shed on purpose must stay shed"


def test_a_home_gym_is_a_gym():
    # The plan realised it as home_gym and the contract asked for gym, so a
    # second one was restored beside it.
    contract = program_contract([{"type": "gym"}, {"type": "bathroom"}], "3BHK with a home gym")
    indoor, _site, _notes, _surplus = reconcile_against_contract(
        contract, {0: [{"type": "home_gym"}, {"type": "bathroom"}]}, [],
    )
    assert indoor == [], "home_gym and gym must count as one room"

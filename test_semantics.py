from semantic_evaluator import infer_semantic_profile, get_semantic_alias_predicate, evaluate_predicate
from semantic_models import SemanticProfile
from intent_compiler import compile_intent, bind_room_roles

def test_unknown_room_label_fallback():
    # Verify open-vocabulary fallback behavior for "Innovation Lab"
    # It must default to controlled access, medium privacy, destination, not transit,
    # no plumbing unless supported, no wet-area, no ownership.
    room_dict = {"id": "lab-1", "name": "Innovation Lab", "type": "innovation_lab"}
    profile = infer_semantic_profile(room_dict, "We need an Innovation Lab for testing.")
    
    assert profile.visitor_access == "controlled"
    assert profile.privacy_level == 0.5
    assert profile.circulation_role == "destination"
    assert not profile.can_be_transit
    assert not profile.requires_plumbing
    assert not profile.wet_area
    assert profile.owner_room_id is None
    assert profile.provenance == "generic_fallback"

def test_predicate_based_public_private_resolution():
    # Verify we can resolve "public_rooms" and "private_rooms" without hardcoded sets
    pub_pred = get_semantic_alias_predicate("public_rooms")
    assert pub_pred is not None
    assert pub_pred.operator == "or"
    
    priv_pred = get_semantic_alias_predicate("private_rooms")
    assert priv_pred is not None
    
    # Test a living room against public
    living_dict = {"id": "living-1", "type": "living_room"}
    living_prof = infer_semantic_profile(living_dict, "")
    assert evaluate_predicate(pub_pred, living_prof) is True
    assert evaluate_predicate(priv_pred, living_prof) is False
    
    # Test a master bedroom against private
    bed_dict = {"id": "bed-1", "type": "master_bedroom"}
    bed_prof = infer_semantic_profile(bed_dict, "")
    assert evaluate_predicate(pub_pred, bed_prof) is False
    assert evaluate_predicate(priv_pred, bed_prof) is True
    
    # Test our unknown room "Innovation Lab"
    lab_dict = {"id": "lab-1", "type": "innovation_lab"}
    lab_prof = infer_semantic_profile(lab_dict, "A place for high-tech experiments.")
    assert evaluate_predicate(pub_pred, lab_prof) is False
    assert evaluate_predicate(priv_pred, lab_prof) is False

def test_no_hardcoded_sets_in_compile_intent():
    # Verify compile_intent can handle semantic groups properly and generates GroupSpatialConstraint
    rooms = [
        {"type": "living_room", "id": "living-1"},
        {"type": "master_bedroom", "id": "bed-1"},
        {"type": "innovation_lab", "id": "lab-1"}
    ]
    rooms = bind_room_roles("", {}, rooms)
    
    extraction = {
        "typed_constraints": [
            {
                "kind": "separation",
                "source": "public_rooms",
                "target": "private_rooms",
                "strength": "hard",
                "origin": "user"
            }
        ]
    }
    
    contract = compile_intent("", extraction, rooms)
    assert len(contract.group_constraints) == 1
    
    grp = contract.group_constraints[0]
    assert grp.kind == "separation"
    assert "living-1" in grp.resolved_source_room_ids
    assert "bed-1" in grp.resolved_target_room_ids
    assert "lab-1" not in grp.resolved_source_room_ids
    assert "lab-1" not in grp.resolved_target_room_ids

if __name__ == "__main__":
    test_unknown_room_label_fallback()
    test_predicate_based_public_private_resolution()
    test_no_hardcoded_sets_in_compile_intent()
    print("All tests passed!")

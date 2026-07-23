import unittest

from candidate_contract import InternalInvariantError, LayoutCandidate, PairedDoor, SolvedRect, SpatialRelation, stable_relation_id
from constraint_schema import ArchitecturalConstraint, ConstraintKind, ConstraintOrigin, ConstraintStrength, IntentContract
from final_validator import validate_final_layout
from geometry_engine import CPSolver
from intent_compiler import (
    annotate_room_provenance, bind_room_roles, compile_intent,
    prune_optional_suggestions, reassign_bathroom_owner,
)
from layout_engine import AdjacencyResolver, Door, LayoutEngine, Rect, RoomNode
from layout_scorer import actual_door_graph, score_layout_objectives
from topology_generator import generate_topology_candidates
from topology_optimizer import optimize_topologies


def relation(source, target, kind="direct_access", strength="hard", topology=True, overlap=3.0):
    relation_id = stable_relation_id(source, target, kind, "test")
    return SpatialRelation(
        relation_id, source, target, kind, strength, "test",
        required_overlap_ft=overlap, topology_edge=topology,
    )


def candidate_for(room_defs, relations, entry="living-1", candidate_id="candidate-test"):
    rooms = {item["id"]: dict(item) for item in room_defs}
    value = LayoutCandidate(
        candidate_id, "test-topology", "test-family", rooms,
        {item.relation_id: item for item in relations}, entry_room_id=entry,
    )
    rects = {
        item["id"]: SolvedRect(item["x"], item["z"], item["width"], item["length"])
        for item in room_defs
    }
    value.set_rectangles(rects)
    return value


def nodes_for(room_defs):
    return [RoomNode(
        id=item["id"], type=item["type"], name=item["id"],
        rect=Rect(item["x"], item["z"], item["width"], item["length"]),
    ) for item in room_defs]


class AuthoritativeCandidateTests(unittest.TestCase):
    @staticmethod
    def basic_2bhk_rooms():
        return [
            {"id": "living-1", "type": "living_room"},
            {"id": "kitchen-1", "type": "kitchen"},
            {"id": "bedroom-1", "type": "bedroom"},
            {"id": "bedroom-2", "type": "master_bedroom"},
            {"id": "bathroom-common", "type": "bathroom", "bathroom_role": "common"},
            {"id": "bathroom-attached", "type": "bathroom", "bathroom_role": "attached", "assigned_to": "bedroom-2"},
            {"id": "corridor-1", "type": "corridor"},
            {"id": "utility-1", "type": "utility"},
        ]

    def test_scenario_a_generic_2bhk_is_diverse_complete_and_accessible(self):
        annotated = annotate_room_provenance(self.basic_2bhk_rooms(), "Create a basic 2BHK", {}, 2)
        utility = next(room for room in annotated if room["id"] == "utility-1")
        self.assertEqual(utility["provenance"], "gemini_suggestion")
        self.assertFalse(utility["required"])
        rooms = prune_optional_suggestions(annotated, "Create a basic 2BHK")
        self.assertEqual(sum("bedroom" in room["type"] for room in rooms), 2)
        self.assertNotIn("utility-1", {room["id"] for room in rooms})

        contract = compile_intent("Create a basic 2BHK", {}, rooms)
        selected = optimize_topologies(generate_topology_candidates(rooms, contract, 16), contract, keep=5)
        self.assertGreaterEqual(len({item.topology_family for item in selected}), 3)
        self.assertTrue(all(not item.hard_errors for item in selected))
        self.assertTrue(all(item.objectives["user_preference_cost"] == 0 for item in selected))

        candidate = selected[0].to_layout_candidate(contract)
        result = CPSolver().solve_phase_2_csp({"plot_width": 40, "plot_length": 40, "candidate": candidate})
        candidate = result["candidate"]
        nodes = [RoomNode(
            item["id"], item["type"], item["id"],
            Rect(item["x"], item["z"], item["width"], item["length"]),
        ) for item in result["resolved_rooms"]]
        AdjacencyResolver(nodes, candidate=candidate).resolve()
        report = validate_final_layout(candidate, nodes, 40, 40, contract)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(candidate.access_graph()["bathroom-attached"], {"bedroom-2"})
        self.assertIn("bathroom-common", report.door_graph["corridor-1"])
        self.assertFalse(any(error.code == "PRIVATE_TRANSIT" for error in candidate.validation_errors))
        self.assertFalse(hasattr(candidate, "blueprint_latest"))

    def test_scenario_b_direction_and_near_remain_distinct_semantics(self):
        rooms = [room for room in self.basic_2bhk_rooms() if room["id"] not in {"utility-1", "bathroom-attached"}]
        extraction = {"requested_relationships": [
            {"subject_room": "kitchen-1", "relation": "east", "required": True},
            {"subject_room": "living-1", "target_room": "bathroom-common", "relation": "near", "required": True},
        ]}
        contract = compile_intent(
            "Create a 2BHK with the kitchen east and living near the common bathroom",
            extraction, rooms,
        )
        selected = optimize_topologies(generate_topology_candidates(rooms, contract, 16), contract, keep=5)
        self.assertGreaterEqual(len({item.topology_family for item in selected}), 3)
        candidate = selected[0].to_layout_candidate(contract)
        kinds = {(item.source_room_id, item.target_room_id, item.kind) for item in candidate.relations_by_id.values()}
        self.assertIn(("living-1", "bathroom-common", "near"), kinds)
        self.assertNotIn(("living-1", "bathroom-common", "direct_access"), kinds)
        result = CPSolver().solve_phase_2_csp({"plot_width": 40, "plot_length": 40, "candidate": candidate})
        candidate = result["candidate"]
        kitchen = candidate.rectangles_by_room_id["kitchen-1"]
        self.assertGreaterEqual(kitchen.x + kitchen.width / 2, 20)
        living, bathroom = candidate.rectangles_by_room_id["living-1"], candidate.rectangles_by_room_id["bathroom-common"]
        center_distance = abs(living.x + living.width / 2 - bathroom.x - bathroom.width / 2) + abs(living.z + living.length / 2 - bathroom.z - bathroom.length / 2)
        self.assertLessEqual(center_distance, 15.0)

    def test_scenario_c_explicit_central_hub_is_allowed(self):
        rooms = [
            {"id": "living-1", "type": "living_room"}, {"id": "dining-1", "type": "dining_room"},
            {"id": "bedroom-1", "type": "bedroom"}, {"id": "bedroom-2", "type": "bedroom"},
            {"id": "corridor-1", "type": "corridor"},
        ]
        contract = IntentContract(
            constraints=[ArchitecturalConstraint(
                ConstraintKind.DIRECT_CONNECTION, "living-1", target=target,
                strength=ConstraintStrength.HARD, origin=ConstraintOrigin.USER,
            ) for target in ("bedroom-1", "bedroom-2", "dining-1")],
            open_plan=True, topology_hint="compact_hub", entry_room_id="living-1",
        )
        selected = optimize_topologies(generate_topology_candidates(rooms, contract, 16), contract, keep=5)
        self.assertEqual(selected[0].topology_family, "compact_central_hub")
        self.assertEqual(selected[0].objectives["user_preference_cost"], 0)

    def test_a_direct_connection_creates_same_id_door_and_shared_wall(self):
        rooms = [
            {"id": "living-1", "type": "living_room", "x": 0, "z": 0, "width": 10, "length": 10},
            {"id": "kitchen-1", "type": "kitchen", "x": 10, "z": 0, "width": 8, "length": 10},
        ]
        rel = relation("living-1", "kitchen-1")
        candidate = candidate_for(rooms, [rel])
        nodes = nodes_for(rooms)
        AdjacencyResolver(nodes, candidate=candidate).resolve()
        self.assertEqual(len(candidate.doors), 1)
        self.assertEqual({candidate.doors[0].room_a_id, candidate.doors[0].room_b_id}, {"living-1", "kitchen-1"})
        self.assertTrue(candidate.doors[0].wall_id.startswith("wall_"))
        self.assertEqual(actual_door_graph(candidate)["living-1"], {"kitchen-1"})

    def test_b_adjacent_means_shared_wall_without_door(self):
        rooms = [
            {"id": "living-1", "type": "living_room", "x": 0, "z": 0, "width": 10, "length": 10},
            {"id": "study-1", "type": "study_room", "x": 10, "z": 0, "width": 8, "length": 10},
        ]
        rel = relation("living-1", "study-1", kind="adjacent", topology=False, overlap=1.0)
        candidate = candidate_for(rooms, [rel])
        nodes = nodes_for(rooms)
        AdjacencyResolver(nodes, candidate=candidate).resolve()
        self.assertEqual(candidate.doors, [])
        self.assertEqual(actual_door_graph(candidate)["living-1"], set())

    def test_c_hard_direction_is_respected_by_cp_solver(self):
        rooms = [
            {"id": "foyer-1", "type": "foyer", "required": True},
            {"id": "bedroom-1", "type": "bedroom", "required": True},
        ]
        contract = IntentContract(constraints=[
            ArchitecturalConstraint(
                ConstraintKind.DIRECTION, "bedroom-1", value="east",
                strength=ConstraintStrength.HARD, origin=ConstraintOrigin.USER,
            ),
            ArchitecturalConstraint(
                ConstraintKind.DIRECT_CONNECTION, "foyer-1", target="bedroom-1",
                strength=ConstraintStrength.HARD, origin=ConstraintOrigin.USER,
            ),
        ], entry_room_id="foyer-1")
        topology = generate_topology_candidates(rooms, contract, count=4)[0]
        candidate = topology.to_layout_candidate(contract)
        result = CPSolver().solve_phase_2_csp({
            "plot_width": 30.0, "plot_length": 24.0, "candidate": candidate,
        })
        rect = result["candidate"].rectangles_by_room_id["bedroom-1"]
        self.assertGreaterEqual(rect.x + rect.width / 2, 15.0)

    def test_d_hard_infeasible_candidate_never_reaches_pareto(self):
        rooms = [{"id": "living-1", "type": "living_room"}]
        contract = IntentContract(constraints=[ArchitecturalConstraint(
            ConstraintKind.DIRECT_CONNECTION, "living-1", target="missing-room",
            strength=ConstraintStrength.HARD, origin=ConstraintOrigin.USER,
        )])
        with self.assertRaisesRegex(InternalInvariantError, "missing target"):
            generate_topology_candidates(rooms, contract, 8)

    def test_e_duplicate_types_keep_exact_identity_and_owner_door(self):
        rooms = [
            {"id": "corridor-1", "type": "corridor", "x": 0, "z": 0, "width": 4, "length": 20},
            {"id": "bedroom-1", "type": "bedroom", "x": 4, "z": 0, "width": 10, "length": 10},
            {"id": "bedroom-2", "type": "bedroom", "x": 4, "z": 10, "width": 10, "length": 10},
            {"id": "bathroom-2", "type": "bathroom", "x": 14, "z": 10, "width": 5, "length": 10},
        ]
        relations = [
            relation("corridor-1", "bedroom-1"),
            relation("corridor-1", "bedroom-2"),
            relation("bathroom-2", "bedroom-2", kind="exclusive_access"),
        ]
        candidate = candidate_for(rooms, relations, entry="corridor-1")
        nodes = nodes_for(rooms)
        AdjacencyResolver(nodes, candidate=candidate).resolve()
        graph = candidate.access_graph()
        self.assertEqual(graph["bathroom-2"], {"bedroom-2"})
        self.assertNotIn("bathroom-2", graph["bedroom-1"])

    def test_f_reachability_via_intermediary_succeeds(self):
        rooms = [
            {"id": "living-1", "type": "living_room", "x": 0, "z": 0, "width": 10, "length": 10},
            {"id": "corridor-1", "type": "corridor", "x": 10, "z": 0, "width": 4, "length": 10},
            {"id": "bedroom-1", "type": "bedroom", "x": 14, "z": 0, "width": 10, "length": 10},
        ]
        candidate = candidate_for(rooms, [
            relation("living-1", "corridor-1"), relation("corridor-1", "bedroom-1"),
            relation("living-1", "bedroom-1", kind="reachable", topology=False, overlap=0.0),
        ])
        nodes = nodes_for(rooms)
        AdjacencyResolver(nodes, candidate=candidate).resolve()
        # Deliberately inject stale one-sided metadata; final graph ignores it.
        nodes[0].doors.append(Door(5, 5, "east", target_room_id="bedroom-1"))
        report = validate_final_layout(candidate, nodes, 30, 20, IntentContract())
        self.assertTrue(report.valid, report.errors)
        self.assertNotIn("bedroom-1", report.door_graph["living-1"])

    def test_solver_master_blueprint_handoff_keeps_geometry_hash(self):
        rooms = [
            {"id": "living-1", "type": "living_room", "x": 1, "z": 2, "width": 10, "length": 9},
            {"id": "kitchen-1", "type": "kitchen", "x": 11, "z": 2, "width": 8, "length": 9},
        ]
        candidate = candidate_for(rooms, [relation("living-1", "kitchen-1")])
        before = candidate.geometry_hash()
        blueprint = [{
            "id": room["id"], "room_type": room["type"], "position_x": room["x"],
            "position_z": room["z"], "width": room["width"], "length": room["length"],
        } for room in rooms]
        engine = LayoutEngine(30, 24)
        engine.skip_furniture_generation = True
        nodes = engine.generate([], master_blueprint=blueprint,
            plot_info={"_immutable_solver_handoff": True}, layout_candidate=candidate)
        self.assertEqual(before, candidate.geometry_hash())
        self.assertEqual({node.id for node in nodes}, set(candidate.rooms_by_id))

    def test_optional_gemini_suggestion_is_not_required(self):
        rooms = [
            {"id": "living-1", "type": "living_room"},
            {"id": "kitchen-1", "type": "kitchen"},
            {"id": "bedroom-1", "type": "bedroom"},
            {"id": "bathroom-1", "type": "bathroom"},
            {"id": "utility-1", "type": "utility"},
        ]
        annotated = annotate_room_provenance(rooms, "Create a 1BHK", {}, 1)
        pruned = prune_optional_suggestions(annotated, "Create a 1BHK")
        self.assertNotIn("utility-1", {room["id"] for room in pruned})
        self.assertTrue(all(room["required"] for room in pruned))


class CanonicalRoleBindingRegressionTests(unittest.TestCase):
    EXACT_PROMPT = (
        "Design a modern 2 BHK single-story house on a 40' × 40' (1600 sq ft) plot. "
        "Include a master bedroom with an attached bathroom, one additional bedroom, "
        "one common bathroom, a spacious living room, dining room, kitchen, foyer, and "
        "utility area. Maintain proper zoning between public and private spaces with "
        "smooth circulation and minimal corridor space."
    )

    @staticmethod
    def wired(values):
        from cloud_extractor import auto_wire_topology
        return auto_wire_topology(values)

    def test_exact_failing_prompt_reaches_fully_validated_layout(self):
        from server import automatically_repair_program

        extraction = automatically_repair_program(self.EXACT_PROMPT, {
            "bhk": 2,
            "floor_program": {"0": [
                "foyer", "living_room", "dining_room", "kitchen",
                "master_bedroom", "bedroom", "bathroom", "bathroom",
                "utility", "utility", "corridor", "corridor",
            ]},
            "typed_constraints": [
                {"kind": "exclusive_access", "source": "master_bathroom", "target": "master_bedroom",
                 "strength": "hard", "origin": "user"},
                {"kind": "reachable", "source": "foyer", "target": "utility",
                 "strength": "hard", "origin": "user"},
                {"kind": "reachable", "source": "foyer", "target": "corridor",
                 "strength": "hard", "origin": "user"},
            ],
        }, 1)
        self.assertEqual(extraction["floors"], 1)
        self.assertEqual(extraction["target_rooms"].count("utility"), 1)
        self.assertEqual(extraction["target_rooms"].count("corridor"), 0)

        rooms = bind_room_roles(
            self.EXACT_PROMPT, extraction,
            self.wired(extraction["floor_program"]["0"]),
            bhk=2, program_id="exact-failing-prompt",
        )
        by_id = {room["id"]: room for room in rooms}
        bedrooms = [room for room in rooms if "bedroom" in room["type"]]
        bathrooms = [room for room in rooms if room["type"] == "bathroom"]
        utilities = [room for room in rooms if room["type"] == "utility"]
        corridors = [room for room in rooms if room["type"] == "corridor"]
        self.assertEqual(len(bedrooms), 2)
        self.assertEqual(sum(room["role"] == "master" for room in bedrooms), 1)
        self.assertEqual(sum(room["role"] == "standard" for room in bedrooms), 1)
        self.assertEqual(len(bathrooms), 2)
        self.assertEqual(sum(room["role"] == "attached" for room in bathrooms), 1)
        self.assertEqual(sum(room["role"] == "common" for room in bathrooms), 1)
        self.assertEqual(len(utilities), 1)
        self.assertEqual(len(corridors), 1)
        self.assertFalse(corridors[0]["required_by_user"])
        attached = next(room for room in bathrooms if room["role"] == "attached")
        common = next(room for room in bathrooms if room["role"] == "common")
        self.assertEqual(attached["owner_room_id"], "master_bedroom-1")

        contract = compile_intent(
            self.EXACT_PROMPT, extraction, rooms, program_id="exact-failing-prompt",
        )
        self.assertTrue(any(
            item.kind == "circulation_area" and item.goal == "minimize"
            for item in contract.optimization_preferences
        ))
        self.assertTrue(all(
            constraint.source in by_id and (not constraint.target or constraint.target in by_id)
            for constraint in contract.constraints
        ))
        self.assertFalse(any(
            "master_bathroom" in {constraint.source, constraint.target}
            for constraint in contract.constraints
        ))
        self.assertTrue(any(
            constraint.original_source_selector == "master_bathroom"
            and constraint.source == attached["id"]
            for constraint in contract.constraints
        ))

        generated = generate_topology_candidates(rooms, contract, 16)
        selected = optimize_topologies(generated, contract, keep=5)
        feasible = [candidate for candidate in generated if not candidate.hard_errors]
        self.assertGreater(len(generated), 0)
        self.assertGreater(len(feasible), 0)
        self.assertGreater(len({candidate.topology_family for candidate in selected}), 1)
        baseline = next(candidate for candidate in feasible if candidate.name == "public_private_spine")
        baseline_graph = {room_id: set() for room_id in by_id}
        for edge in baseline.edges:
            baseline_graph[edge.source].add(edge.target)
            baseline_graph[edge.target].add(edge.source)
        self.assertEqual(baseline_graph[attached["id"]], {"master_bedroom-1"})
        self.assertIn(attached["id"], baseline_graph["master_bedroom-1"])

        candidate = baseline.to_layout_candidate(contract)
        solved = CPSolver().solve_phase_2_csp({
            "plot_width": 40, "plot_length": 40, "candidate": candidate,
        })
        candidate = solved["candidate"]
        nodes = [RoomNode(
            item["id"], item["type"], item["id"],
            Rect(item["x"], item["z"], item["width"], item["length"]),
        ) for item in solved["resolved_rooms"]]
        AdjacencyResolver(nodes, candidate=candidate).resolve()
        report = validate_final_layout(candidate, nodes, 40, 40, contract)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(candidate.access_graph()[attached["id"]], {"master_bedroom-1"})
        self.assertNotIn(common["id"], candidate.access_graph()["master_bedroom-1"])
        self.assertIn(utilities[0]["id"], candidate.access_graph()["kitchen-1"])
        self.assertEqual(set(candidate.rooms_by_id), set(by_id))
        scores = score_layout_objectives(nodes, 40, 40, contract, candidate)
        corridor_node = next(node for node in nodes if node.id == corridors[0]["id"])
        self.assertGreaterEqual(
            scores["circulation_cost"], corridor_node.rect.width * corridor_node.rect.length * 2,
        )

    def test_no_attached_bathroom_is_not_invented(self):
        prompt = "Create a 2BHK single-story house with two bedrooms and one common bathroom. Do not include any attached bathrooms."
        rooms = bind_room_roles(prompt, {}, self.wired([
            "living_room", "kitchen", "bedroom", "bedroom", "bathroom",
        ]), bhk=2)
        self.assertFalse(any(room.get("bathroom_role") == "attached" for room in rooms))
        contract = compile_intent(prompt, {}, rooms)
        self.assertFalse(any("master_bathroom" in constraint.original_source_selector for constraint in contract.constraints))
        selected = optimize_topologies(generate_topology_candidates(rooms, contract, 12), contract, 4)
        self.assertTrue(selected)
        graph = {room["id"]: set() for room in rooms}
        for edge in selected[0].edges:
            graph[edge.source].add(edge.target)
            graph[edge.target].add(edge.source)
        bath = next(room["id"] for room in rooms if room["type"] == "bathroom")
        self.assertFalse(any("bedroom" in neighbor for neighbor in graph[bath]))

    def test_two_attached_bathrooms_bind_distinct_owners_and_common_powder(self):
        prompt = "Create a 2BHK house where both bedrooms have their own attached bathrooms, plus one common powder room near the living room."
        rooms = bind_room_roles(prompt, {}, self.wired([
            "living_room", "kitchen", "master_bedroom", "bedroom",
            "bathroom", "bathroom", "powder_room",
        ]), bhk=2)
        attached = [room for room in rooms if room.get("bathroom_role") == "attached"]
        common = [room for room in rooms if room.get("bathroom_role") == "common"]
        self.assertEqual(len(attached), 2)
        self.assertEqual(len({room["owner_room_id"] for room in attached}), 2)
        self.assertEqual(len(common), 1)
        contract = compile_intent(prompt, {}, rooms)
        selected = optimize_topologies(generate_topology_candidates(rooms, contract, 16), contract, 5)
        self.assertTrue(selected)
        for bath in attached:
            neighbors = {other for edge in selected[0].edges for other in (
                [edge.target] if edge.source == bath["id"] else [edge.source] if edge.target == bath["id"] else []
            )}
            self.assertEqual(neighbors, {bath["owner_room_id"]})

    def test_minimal_corridor_and_explicit_utility_counts(self):
        compact = "Create a compact 3BHK single-floor house with one attached master bathroom, one common bathroom, and very little corridor space."
        rooms = bind_room_roles(compact, {}, self.wired([
            "living_room", "kitchen", "master_bedroom", "bedroom", "bedroom",
            "bathroom", "bathroom", "corridor", "corridor",
        ]), bhk=3)
        self.assertEqual(sum(room["type"] == "corridor" for room in rooms), 1)
        contract = compile_intent(compact, {}, rooms)
        self.assertEqual(contract.optimization_preferences[0].kind, "circulation_area")
        self.assertEqual(contract.optimization_preferences[0].strength.value, "strong")

        two_utilities = (
            "Create a 3BHK with one laundry utility beside the kitchen and a separate "
            "storage utility near the rear entrance."
        )
        utility_rooms = bind_room_roles(two_utilities, {}, self.wired([
            "living_room", "kitchen", "bedroom", "bedroom", "bedroom",
            {"type": "utility", "name": "Laundry Utility"},
            {"type": "utility", "name": "Storage Utility"},
        ]), bhk=3)
        utilities = [room for room in utility_rooms if room["type"] == "utility"]
        self.assertEqual(len(utilities), 2)
        self.assertEqual({room.get("utility_purpose") for room in utilities}, {"laundry", "storage"})
        self.assertTrue(all(room["provenance"] == "explicit_user" for room in utilities))

    def test_near_common_bathroom_does_not_create_living_room_door(self):
        prompt = "Create a 2BHK with the living room near the common bathroom, but the bathroom should open only into the corridor."
        rooms = bind_room_roles(prompt, {}, self.wired([
            "living_room", "kitchen", "bedroom", "bedroom", "bathroom", "corridor",
        ]), bhk=2)
        contract = compile_intent(prompt, {"requested_relationships": [
            {"subject_room": "living_room", "target_room": "common_bathroom", "relation": "near"},
            {"subject_room": "common_bathroom", "target_room": "corridor", "relation": "direct_door"},
        ]}, rooms)
        selected = optimize_topologies(generate_topology_candidates(rooms, contract, 16), contract, 5)
        self.assertTrue(selected)
        common_id = next(room["id"] for room in rooms if room.get("bathroom_role") == "common")
        self.assertFalse(any(edge.key == frozenset(("living_room-1", common_id)) for edge in selected[0].edges))
        self.assertTrue(any(edge.key == frozenset(("corridor-1", common_id)) for edge in selected[0].edges))

    def test_duplex_role_resolution_stays_within_each_floor(self):
        prompt = "Create a duplex with one master bedroom and attached bathroom on the ground floor, and two bedrooms with one common bathroom on the first floor."
        ground = bind_room_roles(prompt, {}, self.wired([
            {"type": "living_room"}, {"type": "master_bedroom"},
            {"type": "bathroom", "bathroom_role": "attached", "bathroom_role_provenance": "floor_schedule"},
        ]), bhk=1, floor_index=0, program_id="duplex-ground")
        upper = bind_room_roles(prompt, {}, self.wired([
            {"type": "bedroom"}, {"type": "bedroom"},
            {"type": "bathroom", "bathroom_role": "common", "bathroom_role_provenance": "floor_schedule"},
        ]), bhk=2, floor_index=1, program_id="duplex-upper")
        ground_bath = next(room for room in ground if room["type"] == "bathroom")
        upper_bath = next(room for room in upper if room["type"] == "bathroom")
        self.assertEqual(ground_bath["owner_room_id"], "master_bedroom-1")
        self.assertEqual(upper_bath["bathroom_role"], "common")
        self.assertIsNone(upper_bath["owner_room_id"])
        ground_contract = compile_intent(prompt, {"typed_constraints": [{
            "kind": "exclusive_access", "source": "master_bathroom", "target": "master_bedroom",
            "strength": "hard", "origin": "user",
        }]}, ground, program_id="duplex-ground")
        self.assertEqual(ground_contract.constraints[0].source, ground_bath["id"])
        self.assertTrue(all(room["floor_index"] == 1 for room in upper))

    def test_modification_rebinds_bathroom_atomically_and_preserves_ids(self):
        original = bind_room_roles(
            "Create a 2BHK with two bedrooms and one common bathroom.", {},
            self.wired(["living_room", "bedroom", "bedroom", "bathroom", "corridor"]), bhk=2,
        )
        original_ids = {room["id"] for room in original}
        bath_id = next(room["id"] for room in original if room["type"] == "bathroom")
        changed = reassign_bathroom_owner(original, bath_id, "bedroom-2")
        self.assertEqual({room["id"] for room in changed}, original_ids)
        bath = next(room for room in changed if room["id"] == bath_id)
        self.assertEqual(bath["bathroom_role"], "attached")
        self.assertEqual(bath["owner_room_id"], "bedroom-2")
        contract = compile_intent("Change the common bathroom into an attached bathroom for bedroom 2.", {}, changed)
        exclusive = [item for item in contract.constraints if item.kind == ConstraintKind.EXCLUSIVE_ACCESS]
        self.assertEqual([(item.source, item.target) for item in exclusive], [(bath_id, "bedroom-2")])


if __name__ == "__main__":
    unittest.main()

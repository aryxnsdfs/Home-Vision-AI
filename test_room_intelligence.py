import cloud_extractor
import edit_intelligence
import server
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from collections import Counter


def _base_rooms():
    return [
        {"id": "living_room-1", "type": "living_room", "name": "Living Room", "x": 2, "z": 20, "width": 14, "length": 16, "floorIndex": 0, "wallColor": "pink"},
        {"id": "kitchen-1", "type": "kitchen", "name": "Kitchen", "x": 2, "z": 8, "width": 10, "length": 12, "floorIndex": 0},
        {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom", "x": 20, "z": 2, "width": 16, "length": 16, "floorIndex": 0},
        {"id": "bathroom-1", "type": "bathroom", "name": "Bathroom", "x": 12, "z": 8, "width": 8, "length": 8, "floorIndex": 0},
        {"id": "pooja_room-1", "type": "pooja_room", "name": "Pooja Room", "x": 12, "z": 16, "width": 8, "length": 8, "floorIndex": 0},
        {"id": "corridor-1", "type": "corridor", "name": "Corridor", "x": 16, "z": 20, "width": 4, "length": 16, "floorIndex": 0},
    ]


class RoomIntelligenceTests(unittest.TestCase):
    def test_hub_spoke_wiring_does_not_chain_destination_rooms(self):
        specs = cloud_extractor.auto_wire_topology([
            {"type": "living_room"}, {"type": "dining_area"},
            {"type": "home_office"}, {"type": "home_theater"},
            {"type": "prayer_room"}, {"type": "corridor"},
        ])
        by_type = {spec["type"]: spec for spec in specs}
        corridor_id = by_type["corridor"]["id"]
        for room_type in ("dining_area", "home_office", "home_theater", "prayer_room"):
            self.assertTrue(any(
                edge.get("target_room_id") == corridor_id
                for edge in by_type[room_type]["connections"]
            ))
        self.assertFalse(any(
            edge.get("target_room_id") == by_type["home_theater"]["id"]
            for edge in by_type["home_office"]["connections"]
        ))

    def test_explicit_connected_open_room_survives_hub_spoke_wiring(self):
        specs = cloud_extractor.auto_wire_topology([
            {"type": "living_room"}, {"type": "kitchen"},
            {"type": "dining_area"}, {"type": "corridor"},
        ])
        specs = server.apply_prompt_proximities(
            specs, "an open kitchen connected to the dining area",
        )
        kitchen = next(spec for spec in specs if spec["type"] == "kitchen")
        dining = next(spec for spec in specs if spec["type"] == "dining_area")
        self.assertTrue(any(
            edge.get("target_room_id") == dining["id"] and edge.get("intent") == "open_flow"
            for edge in kitchen["connections"]
        ))

    def test_kitchen_dining_validator_contract_is_wired_before_solving(self):
        specs = cloud_extractor.auto_wire_topology([
            {"type": "living_room"}, {"type": "kitchen"},
            {"type": "dining_room"}, {"type": "gym"}, {"type": "corridor"},
        ])
        kitchen = next(spec for spec in specs if spec["type"] == "kitchen")
        dining = next(spec for spec in specs if spec["type"] == "dining_room")
        self.assertTrue(any(
            edge.get("target_room_id") == dining["id"]
            for edge in kitchen["connections"]
        ))

    def test_shared_public_wall_does_not_create_unrequested_door(self):
        from layout_engine import AdjacencyResolver, Rect, RoomNode

        foyer = RoomNode("foyer-1", "foyer", "Foyer", Rect(0, 0, 8, 8), connections=[])
        gym = RoomNode("gym-1", "gym", "Gym", Rect(8, 0, 10, 8), connections=[])
        AdjacencyResolver([foyer, gym]).resolve()
        self.assertEqual(foyer.doors, [])
        self.assertEqual(gym.doors, [])

    def test_large_plot_expansion_preserves_structural_room_spans(self):
        from layout_engine import LayoutEngine, Rect, RoomNode

        engine = LayoutEngine(100, 100)
        nodes = [
            RoomNode("living_room-1", "living_room", "Living Room", Rect(0, 0, 11, 20)),
            RoomNode("master_bedroom-1", "master_bedroom", "Master Bedroom", Rect(11, 0, 14, 18)),
            RoomNode("corridor-1", "corridor", "Corridor", Rect(25, 0, 4, 30)),
        ]
        engine._expand_to_target_coverage(nodes)
        enclosed = [node for node in nodes if node.type != "corridor"]
        self.assertLessEqual(max(max(node.rect.width, node.rect.length) for node in enclosed), 36.01)
        self.assertLess(max(node.rect.x + node.rect.width for node in nodes), 100)

    def test_area_budget_reports_overflow_and_tradeoffs(self):
        rooms = [{"type": "bedroom"} for _ in range(10)]
        budget = server.calculate_area_budget(rooms, 30, 40, floors=1)
        self.assertFalse(budget["fits"])
        self.assertGreater(budget["usage_percent"], 100)
        self.assertEqual(
            [action["id"] for action in budget["actions"]],
            ["add_floor", "increase_plot", "optimize"],
        )

    def test_infeasible_edit_returns_advisory_result_not_transport_error(self):
        rooms = _base_rooms()
        request = SimpleNamespace(
            prompt="add ten bedrooms",
            width=40,
            length=40,
            currentProject={"plot": {"width": 40, "length": 40}, "style": {"exteriorColor": "pink"}},
        )
        report = edit_intelligence.CandidateReport(
            valid=False,
            errors=["The requested rooms do not fit inside the plot."],
            alternatives=["Increase the plot size."],
        )
        result = server.build_edit_advisory_result(
            request, rooms, "The edit was analyzed but could not be applied.", report,
        )

        self.assertEqual(result["edit_status"], "not_applied")
        self.assertFalse(result["replace_project"])
        self.assertEqual(result["recommendations"], ["Increase the plot size."])
        self.assertEqual(len(result["layout_data"]["floor_0"]), len(rooms))
        self.assertEqual(result["style"]["exteriorColor"], "pink")

    def test_dense_custom_program_survives_solver_fallback_without_room_loss(self):
        from cloud_extractor import auto_wire_topology
        from layout_engine import AdjacencyResolver, LayoutEngine

        raw_specs = [
            {"type": "living_room", "name": "living_room"},
            {"type": "kitchen", "name": "kitchen"},
            {"type": "dining_area", "name": "dining_area"},
            {"type": "home_office", "name": "home_office"},
            {"type": "home_theater", "name": "home_theater"},
            {"type": "prayer_room", "name": "prayer_room"},
            {"type": "bedroom", "name": "bedroom_1"},
            {"type": "bathroom", "name": "bathroom_1", "bathroom_role": "attached"},
            {"type": "bedroom", "name": "bedroom_2"},
            {"type": "bathroom", "name": "bathroom_2", "bathroom_role": "attached"},
            {"type": "bedroom", "name": "bedroom_3"},
            {"type": "bathroom", "name": "bathroom_3", "bathroom_role": "attached"},
            {"type": "staircase", "name": "staircase_to_first_floor"},
            {"type": "corridor", "name": "ground_floor_corridor"},
        ]
        specs = auto_wire_topology([server.normalize_ai_room_spec(spec) for spec in raw_specs])
        engine = LayoutEngine(40, 60)
        engine.skip_furniture_generation = True
        with patch("geometry_engine.LayoutGeometryEngine.solve_phase_2_csp", return_value={}):
            nodes = engine.generate(
                specs,
                indian_options={"pooja_room": True, "double_height": True},
            )
        AdjacencyResolver(nodes).resolve()

        self.assertEqual(server.floor_program_fidelity_errors(nodes, specs, 0), [])
        counts = Counter(server._program_room_class(node.type) for node in nodes)
        self.assertEqual(counts["bathroom"], 3)
        self.assertEqual(counts["home_theater"], 1)
        self.assertEqual(counts["prayer_room"], 1)
        self.assertNotIn("pooja_room", counts)
        # A last-resort slot fallback must preserve the complete program. Door
        # topology is intentionally not fabricated when the fallback geometry
        # does not provide the requested shared wall; the streaming layer will
        # expose that result as partial instead of punching privacy-breaking
        # doors between unrelated destinations.
        self.assertTrue(all(node.rect.width > 0 and node.rect.length > 0 for node in nodes))

    def test_duplex_fallback_keeps_two_private_balconies_on_owning_bedrooms(self):
        from cloud_extractor import auto_wire_topology
        from layout_engine import AdjacencyResolver, LayoutEngine

        prompt = "Each upstairs bedroom should have a private balcony."
        raw_specs = [
            {"type": "staircase", "name": "staircase_landing"},
            {"type": "corridor", "name": "corridor"},
            {"type": "home_theater", "name": "home_theater"},
            {"type": "bedroom", "name": "bedroom_4"},
            {"type": "bathroom", "name": "bathroom_4", "bathroom_role": "attached"},
            {"type": "private_balcony", "name": "private_balcony_1", "is_outdoor": True, "roof_type": "open"},
            {"type": "bedroom", "name": "bedroom_5"},
            {"type": "bathroom", "name": "bathroom_5", "bathroom_role": "attached"},
            {"type": "private_balcony", "name": "private_balcony_2", "is_outdoor": True, "roof_type": "open"},
        ]
        specs = auto_wire_topology(
            [server.normalize_ai_room_spec(spec) for spec in raw_specs],
            {"outdoor_rooms": ["private_balcony"]},
        )
        specs = server.apply_floor_outdoor_connections(specs, {"private_balcony"}, prompt)
        engine = LayoutEngine(40, 60)
        engine.skip_furniture_generation = True
        with patch("geometry_engine.LayoutGeometryEngine.solve_phase_2_csp", return_value={}):
            nodes = engine.generate(
                specs,
                restrict_slots=True,
                plot_info={"allowed_bounds": (1.0, 1.5, 39.0, 58.5)},
            )
        AdjacencyResolver(nodes).resolve()

        self.assertEqual(server.floor_program_fidelity_errors(nodes, specs, 1), [])
        by_id = {node.id: node for node in nodes}
        bedrooms = [spec for spec in specs if spec["type"] == "bedroom"]
        for bedroom in bedrooms:
            balcony_edge = next(edge for edge in bedroom["connections"] if "balcony" in edge.get("target_room", ""))
            balcony = by_id[balcony_edge["target_room_id"]]
            self.assertTrue(balcony.is_outdoor)
            self.assertEqual(balcony.roof_type, "open")
            self.assertTrue(
                server._rooms_share_boundary(by_id[bedroom["id"]].to_dict(), balcony.to_dict()),
                (by_id[bedroom["id"]].to_dict(), balcony.to_dict()),
            )
        self.assertTrue(server.final_layout_validation(nodes, is_duplex=True)["ok"])

    def test_generic_ai_room_type_uses_precise_name(self):
        spec = server.normalize_ai_room_spec({"type": "room", "name": "dining_room"})
        self.assertEqual(spec["type"], "dining_room")

    def test_ai_context_contains_real_geometry_and_neighbours(self):
        context = server.build_ai_project_context({
            "plot": {"width": 40, "length": 40},
            "floors": [{"level": 0, "rooms": _base_rooms()}],
            "current_floor_index": 0,
        })
        kitchen = next(room for room in context["rooms"] if room["id"] == "kitchen-1")
        self.assertEqual(kitchen["area_sqft"], 120)
        self.assertIn("living_room-1", kitchen["neighbours"])

    def test_ai_relationship_plan_drives_legacy_move_fields(self):
        extraction = server.apply_spatial_analysis_defaults({
            "intent": "MOVE",
            "move_target_room": "",
            "move_destination": "",
            "requested_relationships": [{
                "subject_room": "pooja_room-1",
                "target_room": "master_bedroom-1",
                "relation": "adjacent",
                "required": True,
            }],
        })
        self.assertEqual(extraction["move_target_room"], "pooja_room-1")
        self.assertEqual(extraction["move_destination"], "master_bedroom-1")

    def test_create_materializes_internal_courtyard_and_requested_adjacency(self):
        extraction = {
        "intent": "CREATE",
        "bhk": 1,
        "floors": 1,
        "floor_program": {
            "0": [
                {"type": "living_room", "name": "living_room"},
                {"type": "kitchen", "name": "kitchen"},
                {"type": "bedroom", "name": "master_bedroom"},
                {"type": "bathroom", "name": "master_bathroom"},
                {"type": "pooja_room", "name": "pooja_room"},
                {"type": "circulation", "name": "hallway"},
            ]
        },
        "target_rooms": ["pooja_room"],
        "outdoor_rooms": ["courtyard"],
        "needs_pooja_room": True,
        "angan": False,
        "materials": [],
        "style": "",
        "color_hex": "",
        "move_target_room": "",
        "move_destination": "",
        "negative_constraints": [],
        }
        events = []
        request = server.GenerateRequest(
            prompt="generate one bedroom with pooja room at courtyard",
            width=40,
            length=40,
            floors=1,
        )
        with patch.object(server, "extract_keywords_to_json", return_value=extraction), patch.object(
            cloud_extractor, "generate_furniture_manifest", return_value={}
        ):
            server._stream_generate_work(request, events.append)

        self.assertTrue(events[-1].get("done"))
        rooms = events[-1]["result"]["layout_data"]["floor_0"]
        courtyard = next(room for room in rooms if room["type"] == "courtyard")
        pooja = next(room for room in rooms if room["type"] == "pooja_room")
        self.assertEqual(courtyard["roof_type"], "open")
        self.assertTrue(courtyard["is_outdoor"])
        self.assertTrue(server._rooms_share_boundary(pooja, courtyard))

    def test_add_dining_room_replans_next_to_kitchen_and_preserves_identity(self):
        rooms = _base_rooms()
        result = server.build_room_changes(
            "add dining room near kitchen",
            rooms,
            [{"canonical": "add"}],
            [{"canonical": "dining_room"}],
            [],
            plot_width=40,
            plot_length=40,
        )

        self.assertIsNotNone(result)
        dining = next(room for room in result if room["type"] == "dining_room")
        kitchen = next(room for room in result if room["id"] == "kitchen-1")
        living = next(room for room in result if room["id"] == "living_room-1")
        self.assertTrue(dining["id"].startswith("dining_room-"))
        self.assertTrue(server._rooms_share_boundary(dining, kitchen))
        self.assertEqual(living["wallColor"], "pink")
        self.assertFalse(any(room["type"] == "room" for room in result))

    def test_move_room_swaps_into_cell_beside_requested_anchor(self):
        rooms = [
            {"id": "pooja_room-1", "type": "pooja_room", "name": "Pooja Room", "x": 0, "z": 0, "width": 8, "length": 8, "floorIndex": 0},
            {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom", "x": 10, "z": 0, "width": 10, "length": 10, "floorIndex": 0},
            {"id": "master_bedroom-1", "type": "master_bedroom", "name": "Master Bedroom", "x": 20, "z": 0, "width": 10, "length": 10, "floorIndex": 0},
            {"id": "living_room-1", "type": "living_room", "name": "Living Room", "x": 0, "z": 10, "width": 20, "length": 10, "floorIndex": 0},
        ]
        result = server.build_room_changes(
            "move the pooja room near the master bedroom", rooms, [], [], [],
        )

        self.assertIsNotNone(result)
        pooja = next(room for room in result if room["id"] == "pooja_room-1")
        master = next(room for room in result if room["id"] == "master_bedroom-1")
        self.assertTrue(server._rooms_share_boundary(pooja, master))

    def test_compound_pooja_move_satisfies_kitchen_and_direct_living_access_without_ai(self):
        rooms = [
            {"id": "pooja_room-1", "type": "pooja_room", "name": "Pooja Room", "x": 0, "z": 0, "width": 8, "length": 10, "floorIndex": 0, "doors": [], "furniture": []},
            {"id": "kitchen-1", "type": "kitchen", "name": "Kitchen", "x": 8, "z": 0, "width": 10, "length": 10, "floorIndex": 0, "doors": [], "furniture": []},
            {"id": "living_room-1", "type": "living_room", "name": "Living Room", "x": 18, "z": 0, "width": 14, "length": 20, "floorIndex": 0, "doors": [], "furniture": []},
            {"id": "dining_room-1", "type": "dining_room", "name": "Dining Room", "x": 0, "z": 10, "width": 18, "length": 10, "floorIndex": 0, "doors": [], "furniture": []},
        ]
        prompt = (
            "Move the pooja room so that it is positioned directly beside the kitchen instead of behind it. "
            "The entrance to the pooja room must be accessible directly from the living room. "
            "Add a proper doorway connecting the living room to the pooja room without passing through the kitchen."
        )
        result = server.build_room_changes(prompt, rooms, [], [], [])

        self.assertIsNotNone(result)
        pooja = next(room for room in result if room["id"] == "pooja_room-1")
        kitchen = next(room for room in result if room["id"] == "kitchen-1")
        living = next(room for room in result if room["id"] == "living_room-1")
        self.assertEqual(pooja["x"], 8)
        self.assertTrue(server._rooms_share_boundary(pooja, kitchen))
        self.assertTrue(server._rooms_share_boundary(pooja, living))

        def shared_door(first, second):
            first_points = {
                (round(first["x"] + door["x"], 2), round(first["z"] + door["z"], 2))
                for door in first.get("doors", [])
            }
            second_points = {
                (round(second["x"] + door["x"], 2), round(second["z"] + door["z"], 2))
                for door in second.get("doors", [])
            }
            return bool(first_points.intersection(second_points))

        self.assertTrue(shared_door(pooja, living))
        self.assertFalse(shared_door(pooja, kitchen))

        project = {
            "plot": {"width": 40, "length": 40},
            "floors": [{"level": 0, "rooms": rooms}],
            "current_floor_index": 0,
            "style": {},
        }
        events = []
        with patch.object(server, "reason_modifications_deepseek", side_effect=AssertionError("AI path should not run")):
            server._stream_generate_work(
                server.GenerateRequest(prompt=prompt, width=40, length=40, currentProject=project),
                events.append,
            )
        self.assertTrue(events[-1].get("done"))

    def test_remove_courtyard_fills_cell_and_closes_orphan_doorway(self):
        rooms = [
            {
                "id": "corridor-1", "type": "corridor", "name": "Corridor",
                "x": 0, "z": 0, "width": 10, "length": 10, "floorIndex": 0,
                "doors": [{"x": 5, "z": 10, "wall_orientation": "south", "width": 3}],
                "connections": [{"target_room": "courtyard", "target_room_id": "courtyard-1", "intent": "standard"}],
            },
            {
                "id": "courtyard-1", "type": "courtyard", "name": "Courtyard",
                "x": 0, "z": 10, "width": 10, "length": 10, "floorIndex": 0,
                "doors": [{"x": 5, "z": 0, "wall_orientation": "north", "width": 3}],
                "roof_type": "open", "is_outdoor": True,
            },
            {
                "id": "living_room-1", "type": "living_room", "name": "Living Room",
                "x": 10, "z": 0, "width": 10, "length": 20, "floorIndex": 0,
                "doors": [],
            },
        ]

        result = server.build_room_changes("remove the courtyard", rooms, [], [], [])

        self.assertIsNotNone(result)
        self.assertFalse(any(room["id"] == "courtyard-1" for room in result))
        corridor = next(room for room in result if room["id"] == "corridor-1")
        self.assertEqual((corridor["x"], corridor["z"], corridor["width"], corridor["length"]), (0, 0, 10, 20))
        self.assertEqual(corridor.get("doors"), [])
        self.assertEqual(corridor.get("connections"), [])

    def test_transaction_rejects_false_success_when_required_room_is_missing(self):
        rooms = _base_rooms()
        contract = edit_intelligence.compile_contract("add dining room near kitchen", rooms)
        report = edit_intelligence.evaluate_candidate(rooms, rooms, contract, 40, 40)
        self.assertFalse(report.valid)
        self.assertTrue(any("was not added" in error for error in report.errors))

    def test_transaction_rejects_overlap_and_unrequested_room_loss(self):
        rooms = _base_rooms()
        candidate = [dict(room) for room in rooms if room["id"] != "bathroom-1"]
        pooja = next(room for room in candidate if room["id"] == "pooja_room-1")
        pooja.update({"x": 2, "z": 8})
        contract = edit_intelligence.compile_contract("move pooja room near kitchen", rooms)
        report = edit_intelligence.evaluate_candidate(rooms, candidate, contract, 40, 40)
        self.assertFalse(report.valid)
        self.assertTrue(any("overlaps" in error for error in report.errors))
        self.assertTrue(any("Unrequested rooms disappeared" in error for error in report.errors))

    def test_transaction_requires_real_paired_direct_door(self):
        rooms = [
            {"id": "pooja_room-1", "type": "pooja_room", "name": "Pooja Room", "x": 0, "z": 0, "width": 8, "length": 10, "doors": []},
            {"id": "living_room-1", "type": "living_room", "name": "Living Room", "x": 8, "z": 0, "width": 12, "length": 10, "doors": []},
        ]
        contract = edit_intelligence.compile_contract(
            "move pooja room beside living room with direct access from living room", rooms,
        )
        report = edit_intelligence.evaluate_candidate(rooms, rooms, contract, 40, 40)
        self.assertFalse(report.valid)
        self.assertTrue(any("direct paired doorway" in error for error in report.errors))

    def test_ambiguous_duplicate_room_edit_is_rejected(self):
        rooms = [
            {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom 1", "x": 0, "z": 0, "width": 10, "length": 10},
            {"id": "bedroom-2", "type": "bedroom", "name": "Bedroom 2", "x": 10, "z": 0, "width": 10, "length": 10},
        ]
        contract = edit_intelligence.compile_contract("remove the bedroom", rooms)
        self.assertTrue(contract.ambiguity)
        report = edit_intelligence.evaluate_candidate(rooms, rooms[1:], contract, 40, 40)
        self.assertFalse(report.valid)

    def test_candidate_selector_chooses_valid_layout_not_first_layout(self):
        rooms = [
            {"id": "kitchen-1", "type": "kitchen", "name": "Kitchen", "x": 0, "z": 0, "width": 10, "length": 10, "doors": []},
        ]
        invalid = rooms + [{"id": "dining_room-1", "type": "dining_room", "name": "Dining Room", "x": 5, "z": 0, "width": 8, "length": 8, "doors": []}]
        valid = rooms + [{"id": "dining_room-1", "type": "dining_room", "name": "Dining Room", "x": 10, "z": 0, "width": 8, "length": 8, "doors": []}]
        contract = edit_intelligence.compile_contract("add dining room near kitchen", rooms)
        selected, report = edit_intelligence.select_best_candidate(rooms, [invalid, valid], contract, 40, 40)
        self.assertTrue(report.valid)
        self.assertEqual(next(room for room in selected if room["id"] == "dining_room-1")["x"], 10)

    def test_compass_contract_rejects_wrong_plot_zone(self):
        rooms = [
            {"id": "kitchen-1", "type": "kitchen", "name": "Kitchen", "x": 0, "z": 0, "width": 10, "length": 10},
        ]
        contract = edit_intelligence.compile_contract("move kitchen to the south east", rooms)
        report = edit_intelligence.evaluate_candidate(rooms, rooms, contract, 40, 40)
        self.assertFalse(report.valid)
        self.assertTrue(any("requested east south zone" in error for error in report.errors))

    def test_without_passing_through_compiles_forbidden_door(self):
        rooms = [
            {"id": "pooja_room-1", "type": "pooja_room", "name": "Pooja Room", "x": 0, "z": 0, "width": 8, "length": 8},
            {"id": "kitchen-1", "type": "kitchen", "name": "Kitchen", "x": 8, "z": 0, "width": 8, "length": 8},
            {"id": "living_room-1", "type": "living_room", "name": "Living Room", "x": 0, "z": 8, "width": 16, "length": 8},
        ]
        contract = edit_intelligence.compile_contract(
            "move pooja room beside kitchen with direct access from living room without passing through kitchen", rooms,
        )
        self.assertTrue(any(item.kind == "no_direct_door" and "kitchen" in item.target for item in contract.relationships))

    def test_reconciliation_removes_stale_window_and_door_obstruction(self):
        rooms = [
            {
                "id": "living_room-1", "type": "living_room", "name": "Living Room",
                "x": 0, "z": 0, "width": 10, "length": 10,
                "doors": [{"x": 10, "z": 5, "width": 3, "wall_orientation": "east"}],
                "windows": [
                    {"x": 0, "z": 5, "width": 3, "wall_orientation": "west"},
                    {"x": 10, "z": 8, "width": 3, "wall_orientation": "east"},
                ],
                "furniture": [{"type": "chair", "x": 9, "z": 5, "width": 2, "length": 2}],
            },
            {
                "id": "kitchen-1", "type": "kitchen", "name": "Kitchen",
                "x": 10, "z": 0, "width": 10, "length": 10,
                "doors": [{"x": 0, "z": 5, "width": 3, "wall_orientation": "west"}],
                "windows": [], "furniture": [],
            },
        ]
        result = server.reconcile_modified_rooms(rooms, rooms)
        living = next(room for room in result if room["id"] == "living_room-1")
        self.assertEqual(len(living["windows"]), 1)
        self.assertEqual(living["windows"][0]["x"], 0)
        self.assertEqual(living["furniture"], [])

    def test_space_recommendations_explain_dense_programs(self):
        rooms = ([{"type": "living_room"}, {"type": "kitchen"}] +
                 [{"type": "bedroom"} for _ in range(10)])
        recommendations = server.space_recommendations(rooms, 40, 40)
        self.assertTrue(recommendations)
        self.assertIn("Space recommendation:", recommendations[0])
        self.assertIn("Reduce room count", recommendations[0])

    def test_fixed_staircase_survives_cp_master_blueprint_transform(self):
        from layout_engine import LayoutEngine

        engine = LayoutEngine(40, 40)
        nodes = engine.generate([
            {"id": "staircase-f1", "type": "staircase", "fixed_rect": (5, 7, 4, 8),
             "connections": [{"target_room": "corridor", "target_room_id": "corridor-1"}]},
            {"id": "corridor-1", "type": "corridor",
             "connections": [{"target_room": "staircase", "target_room_id": "staircase-f1"}]},
            {"id": "bedroom-1", "type": "bedroom",
             "connections": [{"target_room": "corridor", "target_room_id": "corridor-1"}]},
        ])
        staircase = next(node for node in nodes if node.id == "staircase-f1")
        self.assertEqual(
            (staircase.rect.x, staircase.rect.z, staircase.rect.width, staircase.rect.length),
            (5, 7, 4, 8),
        )

    def test_upper_solver_uses_global_plot_frame_for_east_edge_staircase(self):
        from layout_engine import LayoutEngine

        engine = LayoutEngine(40, 60)
        with patch(
            "geometry_engine.LayoutGeometryEngine.solve_phase_2_csp",
            return_value={},
        ) as solve:
            engine.generate([
                {"id": "staircase-1", "type": "staircase", "fixed_rect": (35, 4.5, 4, 12),
                 "connections": [{"target_room": "corridor", "target_room_id": "corridor-1"}]},
                {"id": "corridor-1", "type": "corridor"},
                {"id": "bedroom-1", "type": "bedroom"},
            ], plot_info={"allowed_bounds": (1, 4.5, 39, 55.5)}, restrict_slots=True)

        floor_data = solve.call_args.args[0]
        self.assertEqual(floor_data["plot_width"], 40)
        self.assertEqual(floor_data["plot_length"], 60)
        self.assertEqual(floor_data["allowed_bounds"], (1, 4.5, 39, 55.5))

    def test_duplex_alignment_bridges_grid_seam_without_mutating_rooms(self):
        from layout_engine import AdjacencyResolver, Rect, RoomNode, align_duplex_floors

        ground = [
            RoomNode(id="staircase-1", type="staircase", name="Staircase",
                     rect=Rect(6.41, 5.0, 4.2, 8.0)),
        ]
        upper = [
            # CP's enclosing grid reservation ends at 13.1, while the exact
            # staircase restored by alignment ends at 13.0.
            RoomNode(id="staircase-1", type="staircase", name="Staircase",
                     rect=Rect(6.4, 5.0, 4.3, 8.1)),
            RoomNode(id="corridor-1", type="corridor", name="Corridor",
                     rect=Rect(5.0, 13.1, 8.0, 4.0)),
        ]

        align_duplex_floors(ground, upper)
        AdjacencyResolver(upper).resolve()
        server.bridge_staircase_grid_seam(upper)

        staircase = next(node for node in upper if node.type == "staircase")
        corridor = next(node for node in upper if node.type == "corridor")
        self.assertAlmostEqual(corridor.rect.z, 13.1)
        self.assertTrue(staircase.doors)
        self.assertTrue(corridor.doors)

    def test_furniture_generation_uses_fast_local_path_by_default(self):
        from layout_engine import RoomNode, Rect, place_furniture

        node = RoomNode(id="bedroom-1", type="bedroom", name="Bedroom", rect=Rect(0, 0, 12, 12))
        with patch.object(cloud_extractor, "generate_furniture_manifest", side_effect=AssertionError("network furniture called")):
            place_furniture([node], {}, "")
        self.assertTrue(node.furniture)

    def test_add_attached_bathroom_uses_bath_as_subject_and_bedroom_as_anchor(self):
        rooms = [
            {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom 1",
             "x": 2, "z": 2, "width": 14, "length": 14, "floorIndex": 0},
            {"id": "corridor-1", "type": "corridor", "name": "Corridor",
             "x": 16, "z": 2, "width": 4, "length": 14, "floorIndex": 0},
        ]
        sentinel = rooms + [{"id": "bathroom-1", "type": "bathroom"}]
        with patch.object(server, "_replan_floor_with_constraint", return_value=sentinel) as replan:
            result = server.build_room_changes(
                "add attached bathroom to bedroom one",
                rooms,
                [{"canonical": "add"}],
                # Gemini may return the existing anchor first.
                [{"canonical": "bedroom"}, {"canonical": "bathroom"}],
                [],
            )
        self.assertIs(result, sentinel)
        self.assertEqual(replan.call_args.kwargs["add_room_type"], "bathroom")
        self.assertEqual(replan.call_args.kwargs["add_room_role"], "attached")
        self.assertEqual(replan.call_args.args[2], 0)

    def test_add_misspelled_general_bathroom_normalizes_and_uses_corridor(self):
        rooms = _base_rooms()
        with patch.object(server, "_replan_floor_with_constraint", return_value=rooms) as replan:
            result = server.build_room_changes(
                "add genral bathroom",
                rooms,
                [{"canonical": "add"}],
                [{"canonical": "genral_bathroom"}],
                [],
                plot_width=40,
                plot_length=40,
            )

        self.assertIs(result, rooms)
        self.assertEqual(rooms[replan.call_args.args[2]]["type"], "corridor")
        self.assertEqual(replan.call_args.kwargs["add_room_type"], "bathroom")
        self.assertEqual(replan.call_args.kwargs["add_room_role"], "common")
        normalized = server.normalize_ai_room_spec({"type": "genral_bathroom"})
        self.assertEqual(normalized["type"], "bathroom")
        self.assertEqual(normalized["bathroom_role"], "common")
        contract = edit_intelligence.compile_contract(
            "add genral bathroom", rooms,
            {"intent": "ADD", "target_rooms": ["genral_bathroom"]},
        )
        self.assertEqual(contract.subject, "bathroom")

    def test_duplex_floor_program_preserves_attached_and_common_bath_roles(self):
        program = {
            0: [
                {"type": "bedroom", "name": "bedroom_1"},
                {"type": "bedroom", "name": "bedroom_2"},
                {"type": "bathroom", "name": "bathroom_1"},
            ],
            1: [
                {"type": "bedroom", "name": "bedroom_3"},
                {"type": "bathroom", "name": "bathroom_2"},
            ],
        }
        prompt = (
            "Ground floor consists of two bedrooms, a gym and one attached bathroom. "
            "First floor contains one bedroom and one general bathroom."
        )
        result = server.apply_floor_bathroom_roles(program, prompt)
        ground_bath = next(room for room in result[0] if room["type"] == "bathroom")
        first_bath = next(room for room in result[1] if room["type"] == "bathroom")
        self.assertEqual(ground_bath["bathroom_role"], "attached")
        self.assertEqual(ground_bath["name"], "Attached Bathroom 1")
        self.assertEqual(first_bath["bathroom_role"], "common")
        self.assertEqual(first_bath["name"], "Common Bathroom")

    def test_attached_bath_contract_requires_private_bedroom_door(self):
        rooms = [
            {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom 1", "x": 0, "z": 0, "width": 10, "length": 10},
            {"id": "corridor-1", "type": "corridor", "name": "Corridor", "x": 10, "z": 0, "width": 4, "length": 10},
        ]
        contract = edit_intelligence.compile_contract(
            "add attached bathroom to bedroom one", rooms,
            {"intent": "ADD", "target_rooms": ["bedroom", "bathroom"]},
        )
        self.assertEqual(contract.subject, "bathroom")
        self.assertTrue(any(rel.kind == "direct_door" and rel.target == "bedroom_1" for rel in contract.relationships))
        self.assertTrue(any(rel.kind == "no_direct_door" and rel.target == "corridor_1" for rel in contract.relationships))

    def test_remove_dining_rebuilds_door_graph_after_cell_absorption(self):
        rooms = [
            {"id": "living_room-1", "type": "living_room", "name": "Living Room",
             "x": 0, "z": 0, "width": 10, "length": 10, "floorIndex": 0,
             "doors": [
                 {"x": 0, "z": 5, "wall_orientation": "west", "width": 4, "is_main": True},
                 {"x": 10, "z": 5, "wall_orientation": "east", "width": 3},
             ]},
            {"id": "dining_area-1", "type": "dining_area", "name": "Dining Area",
             "x": 10, "z": 0, "width": 10, "length": 10, "floorIndex": 0,
             "doors": [
                 {"x": 0, "z": 5, "wall_orientation": "west", "width": 3},
                 {"x": 10, "z": 5, "wall_orientation": "east", "width": 3},
             ]},
            {"id": "kitchen-1", "type": "kitchen", "name": "Kitchen",
             "x": 20, "z": 0, "width": 10, "length": 10, "floorIndex": 0,
             "doors": [{"x": 0, "z": 5, "wall_orientation": "west", "width": 3}]},
        ]
        candidate = server.build_room_changes("remove the dining area", rooms, [], [], [])
        selected, report = server.evaluate_modified_room_transaction(
            "remove the dining area", rooms, candidate, 40, 40,
        )
        self.assertTrue(report.valid, report.errors)
        self.assertFalse(any(room["id"] == "dining_area-1" for room in selected))
        living = next(room for room in selected if room["id"] == "living_room-1")
        kitchen = next(room for room in selected if room["id"] == "kitchen-1")
        self.assertTrue(edit_intelligence.paired_door(living, kitchen))

    def test_add_floor_instruction_compiles_to_individual_room_specs(self):
        level, specs = server.parse_added_floor_request(
            "add 1st floor and in the 1st floor add1 bedroom with a general bathroom and study room",
            {},
        )
        self.assertEqual(level, 1)
        self.assertEqual([spec["type"] for spec in specs], ["bedroom", "bathroom", "study_room"])
        self.assertEqual(specs[1]["bathroom_role"], "common")

    def test_instruction_clause_can_never_be_normalized_as_room_type(self):
        malformed = "1st_floor_and_in_the_ist_floor_add1_bedroom_with_a_general_bathroom_and_study_room"
        self.assertIsNone(server.normalize_ai_room_spec({"type": malformed, "name": malformed}))

    def test_gemini_modification_schema_supports_floor_program(self):
        request = cloud_extractor.HouseDesignRequest(
            intent="ADD",
            floor_program=[{"floor_number": 1, "rooms": [
                    {"type": "bedroom", "name": "Bedroom"},
                    {"type": "bathroom", "name": "Common Bathroom", "bathroom_role": "common"},
                    {"type": "study_room", "name": "Study Room"},
                ]}],
        )
        self.assertEqual(len(request.floor_program[0].rooms), 3)
        self.assertEqual(request.floor_program[0].rooms[1].bathroom_role, "common")
        normalized = server.normalize_floor_program_payload(request.model_dump()["floor_program"])
        self.assertEqual([room["type"] for room in normalized[1]], ["bedroom", "bathroom", "study_room"])

    def test_named_duplicate_room_id_removes_only_active_floor_instance(self):
        project = {
            "current_floor_index": 1,
            "floors": [
                {"level": 0, "rooms": [
                    {"id": "living_room-1", "type": "living_room", "name": "Living Room",
                     "x": 0, "z": 0, "width": 10, "length": 10,
                     "doors": [{"x": 0, "z": 5, "wall_orientation": "west", "width": 4, "is_main": True}]},
                    {"id": "bathroom-1", "type": "bathroom", "name": "Bathroom 1",
                     "x": 10, "z": 0, "width": 6, "length": 10, "doors": []},
                ]},
                {"level": 1, "rooms": [
                    {"id": "corridor-1", "type": "corridor", "name": "Corridor",
                     "x": 0, "z": 0, "width": 10, "length": 10, "doors": []},
                    {"id": "bathroom-1", "type": "bathroom", "name": "Bathroom 1",
                     "x": 10, "z": 0, "width": 6, "length": 10, "doors": []},
                ]},
            ],
        }
        rooms = server._project_rooms_for_edit(project)
        candidate = server.build_room_changes("remove bathroom_1", rooms, [], [], [])
        selected, report = server.evaluate_modified_room_transaction(
            "remove bathroom_1", rooms, candidate, 40, 40,
        )
        self.assertTrue(report.valid, report.errors)
        remaining_baths = [room for room in selected if room["type"] == "bathroom"]
        self.assertEqual(len(remaining_baths), 1)
        self.assertEqual(remaining_baths[0]["floorIndex"], 0)

    def test_structural_floor_names_are_not_mistaken_for_instruction_sentences(self):
        staircase = server.normalize_ai_room_spec({"type": "staircase", "name": "staircase_to_first_floor"})
        circulation = server.normalize_ai_room_spec({"type": "circulation", "name": "ground_floor_circulation"})
        landing = server.normalize_ai_room_spec({"type": "staircase_landing", "name": "first_floor_staircase_landing"})
        self.assertEqual(staircase["type"], "staircase")
        self.assertEqual(circulation["type"], "circulation")
        self.assertEqual(landing["type"], "staircase")

    def test_repeated_ground_floor_mentions_preserve_explicit_bath_count(self):
        prompt = (
            "Ground floor having three bedrooms and two attached bathrooms. "
            "Place kitchen and dining on the ground floor. Then make two bedrooms upstairs with balconies outside."
        )
        program = {
            0: [
                {"type": "bedroom", "name": "bedroom_1"},
                {"type": "bathroom", "name": "bathroom_1"},
                {"type": "bedroom", "name": "bedroom_2"},
                {"type": "bathroom", "name": "bathroom_2"},
            ],
            1: [
                {"type": "bedroom", "name": "bedroom_4"},
                {"type": "bathroom", "name": "hallucinated_bathroom_4"},
                {"type": "bedroom", "name": "bedroom_5"},
                {"type": "bathroom", "name": "hallucinated_bathroom_5"},
            ],
        }
        result = server.apply_floor_bathroom_roles(program, prompt)
        self.assertEqual([room["bathroom_role"] for room in result[0] if room["type"] == "bathroom"], ["attached", "attached"])
        self.assertFalse(any(room["type"] == "bathroom" for room in result[1]))

    def test_two_floor_outdoor_spaces_are_paired_to_two_bedrooms(self):
        from cloud_extractor import auto_wire_topology

        specs = auto_wire_topology([
            {"type": "bedroom", "name": "bedroom_4"},
            {"type": "balcony", "name": "balcony_1", "is_outdoor": True},
            {"type": "bedroom", "name": "bedroom_5"},
            {"type": "balcony", "name": "balcony_2", "is_outdoor": True},
            {"type": "corridor", "name": "corridor"},
        ], ai_categories={"outdoor_rooms": ["balcony"]})
        wired = server.apply_floor_outdoor_connections(
            specs, {"balcony"}, "make two rooms with the balcony outside",
        )
        beds = [room for room in wired if room["type"] == "bedroom"]
        balconies = [room for room in wired if room["type"] == "balcony"]
        self.assertEqual(
            [connection["target_room_id"] for bed in beds for connection in bed["connections"] if connection["target_room"] == "balcony"],
            [balconies[0]["id"], balconies[1]["id"]],
        )

    def test_markdown_duplex_schedule_is_compiled_with_exact_counts(self):
        prompt = """**Ground Floor**
3 bedrooms.
2 attached bathrooms, each connected to one of the two primary bedrooms.
1 study room.
A spacious living room connected to the main entrance.
A well-planned kitchen with an adjacent dining area.
A common powder room or guest bathroom.
Proper corridors and circulation so every room is easily accessible.
Adequate natural lighting and ventilation.

**First Floor**
2 bedrooms.
Each bedroom should have direct access to a spacious private balcony.
1 common bathroom (or attached bathrooms if space permits).
An open family lounge or sitting area connected to the staircase.
The staircase should connect naturally from the living room below."""
        program = server.extract_explicit_floor_program(prompt, [])
        ground = [server._program_room_class(spec["type"]) for spec in program[0]]
        upper = [server._program_room_class(spec["type"]) for spec in program[1]]
        self.assertEqual(ground.count("bedroom"), 3)
        self.assertEqual(ground.count("bathroom"), 2)
        self.assertEqual(ground.count("study_room"), 1)
        self.assertEqual(ground.count("powder_room"), 1)
        self.assertEqual(upper.count("bedroom"), 2)
        self.assertEqual(upper.count("balcony"), 1)
        self.assertEqual(upper.count("bathroom"), 1)
        self.assertEqual(upper.count("family_lounge"), 1)

    def test_floor_contract_rejects_an_extra_bedroom(self):
        nodes = [
            server.RoomNode("bedroom-1", "bedroom", "Bedroom 1", server.Rect(0, 0, 10, 10)),
            server.RoomNode("bedroom-2", "bedroom", "Bedroom 2", server.Rect(10, 0, 10, 10)),
            server.RoomNode("bedroom-3", "bedroom", "Bedroom 3", server.Rect(20, 0, 10, 10)),
        ]
        errors = server.floor_program_fidelity_errors(
            nodes, [{"type": "bedroom"}, {"type": "bedroom"}], 1,
        )
        self.assertTrue(any("requested 2" in error and "generated 3" in error for error in errors))

    def test_upper_indoor_room_must_remain_on_ground_slab(self):
        ground = [server.RoomNode("living-1", "living_room", "Living", server.Rect(2, 2, 20, 20))]
        upper = [server.RoomNode("bedroom-1", "bedroom", "Bedroom", server.Rect(18, 2, 10, 10))]
        self.assertTrue(server.upper_floor_containment_errors(ground, upper))

    def test_singular_balcony_with_each_bedroom_connects_both_rooms(self):
        from cloud_extractor import auto_wire_topology

        prompt = "Each bedroom should have direct access to a spacious private balcony."
        specs = auto_wire_topology([
            {"type": "bedroom", "name": "Bedroom 1"},
            {"type": "bedroom", "name": "Bedroom 2"},
            {"type": "balcony", "name": "Private Balcony", "is_outdoor": True, "roof_type": "open"},
            {"type": "corridor", "name": "Corridor"},
        ], ai_categories={"outdoor_rooms": ["balcony"]})
        wired = server.apply_floor_outdoor_connections(specs, {"balcony"}, prompt)
        balcony_id = next(spec["id"] for spec in wired if spec["type"] == "balcony")
        bedroom_targets = [
            connection.get("target_room_id")
            for spec in wired if spec["type"] == "bedroom"
            for connection in spec.get("connections", [])
            if connection.get("target_room") == "balcony"
        ]
        self.assertEqual(bedroom_targets, [balcony_id, balcony_id])

    def test_bedroom_subject_sets_exact_attached_bathroom_count(self):
        program = {0: [
            {"type": "master_bedroom"}, {"type": "bedroom"},
            {"type": "bedroom"}, {"type": "bedroom"},
            {"type": "bathroom", "bathroom_role": "attached"},
            {"type": "bathroom", "bathroom_role": "attached"},
            {"type": "bathroom", "bathroom_role": "attached"},
            {"type": "bathroom", "bathroom_role": "attached"},
        ]}
        result = server.apply_floor_bathroom_roles(
            program, "Two bedrooms should have attached bathrooms.",
        )
        bathrooms = [spec for spec in result[0] if spec["type"] == "bathroom"]
        self.assertEqual(len(bathrooms), 2)
        self.assertTrue(all(spec["bathroom_role"] == "attached" for spec in bathrooms))

    def test_courtyard_overlook_compiles_three_view_edges_and_master_closet(self):
        from cloud_extractor import auto_wire_topology

        prompt = (
            "The house should be centered around the courtyard. The living room, "
            "dining room, and kitchen should overlook the courtyard. "
            "The master bedroom should include a walk-in closet."
        )
        specs = auto_wire_topology([
            {"type": "living_room"}, {"type": "dining_room"}, {"type": "kitchen"},
            {"type": "master_bedroom"}, {"type": "walk_in_closet"},
            {"type": "courtyard", "is_outdoor": True, "roof_type": "open"},
            {"type": "corridor"},
        ], {"outdoor_rooms": ["courtyard"]})
        wired = server.apply_courtyard_and_suite_relationships(specs, prompt)
        courtyard_id = next(spec["id"] for spec in wired if spec["type"] == "courtyard")
        courtyard = next(spec for spec in wired if spec["type"] == "courtyard")
        self.assertEqual(courtyard.get("preferred_location"), "center")
        self.assertNotIn("fixed_rect", courtyard)
        for room_type in ("living_room", "dining_room", "kitchen"):
            room = next(spec for spec in wired if spec["type"] == room_type)
            self.assertTrue(any(
                edge.get("target_room_id") == courtyard_id and edge.get("intent") == "courtyard_view"
                for edge in room["connections"]
            ))
        master = next(spec for spec in wired if spec["type"] == "master_bedroom")
        closet = next(spec for spec in wired if spec["type"] == "walk_in_closet")
        self.assertTrue(any(edge.get("target_room_id") == closet["id"] for edge in master["connections"]))


if __name__ == "__main__":
    unittest.main()

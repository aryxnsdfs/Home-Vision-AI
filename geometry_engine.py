import logging
import math
import os
import contextvars
import time
import copy
from typing import List, Any, Dict, Optional
from ortools.sat.python import cp_model
from collections import deque
from layout_engine import ROOM_MINIMUMS, _DEFAULT_MIN
from candidate_contract import CandidateStatus, InternalInvariantError, LayoutCandidate, SolvedRect

logger = logging.getLogger(__name__)

# ─── DEFAULT HYGIENE PREFERENCES ───
# These pairs are quality penalties, not universal building-code facts.  A
# caller may promote a pair to a hard rule through ``hard_forbidden_pairs``.
FORBIDDEN_PAIRS = {
    frozenset({'kitchen', 'bathroom'}),
    frozenset({'kitchen', 'toilet'}),
    frozenset({'dining_room', 'bathroom'}),
    frozenset({'dining_room', 'toilet'}),
}

# Minimum shared wall length (feet) required to place a door
MIN_DOOR_WALL_FT = 4.0

COORD_SCALE = 4  # quarter-foot precision

# Geometry search is the dominant cost of a generation: a single request could
# burn 47s across 13 solves, each allowed its full 8s, because every envelope
# and every retry paid the worst-case price. The server sets a deadline for the
# whole geometry phase and the search trims itself to fit.
SOLVE_DEADLINE: contextvars.ContextVar = contextvars.ContextVar("cp_solve_deadline", default=None)


def solve_time_remaining() -> Optional[float]:
    deadline = SOLVE_DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def solve_deadline_passed() -> bool:
    remaining = solve_time_remaining()
    return remaining is not None and remaining <= 0

def to_cp(value: float) -> int:
    return int(round(value * COORD_SCALE))

def from_cp(value: int) -> float:
    return value / COORD_SCALE

class CPSolver:
    """
    Graph-First Geometry Solver.

    Pipeline:
        Required Door Graph (connections)
            ↓
        CP-SAT with HARD adjacency constraints
            ↓
        Post-solve BFS validation
            ↓
        Retry on failure (up to 3 attempts)
    """

    def _generate_candidate_envelopes(self, plot_w_ft: float, plot_l_ft: float, target_area: float) -> list:
        import math
        envelopes = []
        seen = set()
        aspects = [1.0, 1.2, 0.8, 1.5, 0.66]
        plot_area = max(1.0, plot_w_ft * plot_l_ft)

        # An envelope sized to the bare sum of room minimums leaves no width
        # for walls or circulation, so a roomy plot could still come back
        # infeasible. Try the snug envelopes first (they give tighter, better
        # layouts when they fit), then progressively roomier ones before
        # falling back to the whole plot.
        for scale in (1.0, 1.3, 1.6):
            area = min(target_area * scale, plot_area)
            for aspect in aspects:
                w = math.sqrt(area * aspect)
                l = area / max(1.0, w)
                if w <= plot_w_ft and l <= plot_l_ft:
                    min_x = (plot_w_ft - w) / 2
                    max_x = min_x + w
                    min_z = (plot_l_ft - l) / 2
                    max_z = min_z + l
                    key = (round(min_x, 2), round(min_z, 2), round(max_x, 2), round(max_z, 2))
                    if key not in seen:
                        seen.add(key)
                        envelopes.append((min_x, min_z, max_x, max_z))

        # Add full plot fallback
        envelopes.append((0.0, 0.0, plot_w_ft, plot_l_ft))
        return envelopes

    def solve_phase_2_csp(self, floor_data: dict, attempt: int = 0) -> dict:
        """
        Places rooms on a grid such that:
        1. Connected rooms share >= 4ft wall (HARD)
        2. Forbidden pairs never touch (HARD)
        3. Minimum room dimensions and areas (HARD)
        4. Zonal clustering and walking distance (SOFT)
        """
        plot_w_ft = floor_data.get('plot_width', 30.0)
        plot_l_ft = floor_data.get('plot_length', 40.0)
        candidate: LayoutCandidate | None = floor_data.get('candidate')
        if candidate is not None:
            candidate.assert_identity_invariants()
        rooms_spec = list(candidate.rooms_by_id.values()) if candidate is not None else floor_data.get('rooms', [])
        allowed_bounds = floor_data.get('allowed_bounds')

        if not rooms_spec:
            return floor_data
            
        total_min_area = sum(r.get('target_area') or ROOM_MINIMUMS.get(r.get('type', 'room'), _DEFAULT_MIN).get('area', 64) for r in rooms_spec if not r.get('is_outdoor'))
        if allowed_bounds:
            slab_area = max(0.1, float(allowed_bounds[2]) - float(allowed_bounds[0])) * max(0.1, float(allowed_bounds[3]) - float(allowed_bounds[1]))
        else:
            slab_area = plot_w_ft * plot_l_ft * 0.85
            
        if total_min_area > slab_area:
            logger.error(f"[PRE-CHECK] Infeasible layout: needs {int(total_min_area)} sq ft, but only {int(slab_area)} sq ft available.")
            raise RuntimeError(f"Requested rooms require at least {int(total_min_area)} sq ft, but the available footprint is only {int(slab_area)} sq ft.")

        # Multi-Envelope Loop for Coverage Optimization
        envelopes = self._generate_candidate_envelopes(plot_w_ft, plot_l_ft, total_min_area) if not allowed_bounds else [allowed_bounds]
        
        last_exception = None
        index = 0
        while index < len(envelopes):
            # Past the first envelope, stop spending the user's wall clock on
            # near-identical alternatives once the budget is gone — but never
            # skip the whole-plot envelope at the end. That is the one that
            # rescues a room-heavy program, so jump to it rather than give up.
            if index and index < len(envelopes) - 1 and solve_deadline_passed():
                logger.info(
                    "[BUDGET] Skipping envelopes %d-%d; trying the full-plot fallback.",
                    index, len(envelopes) - 2,
                )
                index = len(envelopes) - 1
            env = envelopes[index]
            index += 1
            floor_data_env = dict(floor_data)
            floor_data_env['allowed_bounds'] = env
            try:
                result = self._solve_single_topology(floor_data_env, attempt, "compact_hub")
                if 'resolved_rooms' in result:
                    return result
            except Exception as e:
                if isinstance(e, InternalInvariantError):
                    raise
                last_exception = e
                continue
                
        # If all envelopes failed, return the floor_data with errors or raise the last exception
        if last_exception:
            raise last_exception
        return floor_data

    def _solve_single_topology(self, floor_data: dict, attempt: int, topology_type: str) -> dict:
        plot_w_ft = floor_data.get('plot_width', 30.0)
        plot_l_ft = floor_data.get('plot_length', 40.0)
        candidate: LayoutCandidate | None = floor_data.get('candidate')
        rooms_spec = list(candidate.rooms_by_id.values()) if candidate is not None else floor_data.get('rooms', [])
        allowed_bounds = floor_data.get('allowed_bounds')

        if not rooms_spec:
            return floor_data

        # Grid uses quarter-foot precision via COORD_SCALE
        plot_w = to_cp(plot_w_ft)
        plot_l = to_cp(plot_l_ft)
        door_w = to_cp(MIN_DOOR_WALL_FT)

        model = cp_model.CpModel()

        if attempt == 0:
            attempt = floor_data.get('attempt', 0)

        # ────────────────────────────────────────────
        # PHASE 1 — Room Variables
        # ────────────────────────────────────────────
        room_vars = {}
        for idx, room in enumerate(rooms_spec):
            r_type = room.get("type", "room")
            r_id = room.get("id", f"{r_type}_{idx}")

            base_min_dim = room.get("target_min_dim") or ROOM_MINIMUMS.get(r_type, _DEFAULT_MIN).get("min_dim", 8)
            min_dim = to_cp(base_min_dim)
            # Enforce architectural minimums
            if "master" in r_type:
                min_dim = max(min_dim, to_cp(11.0))
            elif "bedroom" in r_type:
                min_dim = max(min_dim, to_cp(10.0))
            elif "living" in r_type:
                min_dim = max(min_dim, to_cp(11.0))
            elif "dining" in r_type:
                min_dim = max(min_dim, to_cp(9.0))
            elif "kitchen" in r_type:
                min_dim = max(min_dim, to_cp(8.0))
            elif "bath" in r_type or "toilet" in r_type:
                min_dim = max(min_dim, to_cp(5.0))

            # Check for explicit dimension overrides (e.g. structural padder)
            if room.get("min_w_override") and room.get("min_l_override"):
                override_dim = to_cp(min(room["min_w_override"], room["min_l_override"]))
                min_dim = max(min_dim, override_dim)

            # Never construct an invalid CP-SAT domain when a previous
            # request supplied an oversized/custom minimum or the plot is
            # compact. Infeasible geometry may fall back, but MODEL_INVALID
            # must never occur.
            min_dim = max(1, min(min_dim, plot_w, plot_l))

            base_area = room.get("target_area") or ROOM_MINIMUMS.get(r_type, _DEFAULT_MIN).get("area", 64)
            min_area_ft = max(1.0, float(base_area))
            if room.get("min_w_override") and room.get("min_l_override"):
                min_area_ft = max(min_area_ft, float(room["min_w_override"] * room["min_l_override"]))

            if "fixed_rect" in room:
                fx, fz, fw, fl = room["fixed_rect"]
                fixed_x = math.floor(fx * COORD_SCALE)
                fixed_z = math.floor(fz * COORD_SCALE)
                fixed_x_end = math.ceil((fx + fw) * COORD_SCALE)
                fixed_z_end = math.ceil((fz + fl) * COORD_SCALE)
                fw_cp = fixed_x_end - fixed_x
                fl_cp = fixed_z_end - fixed_z
                
                # Directly bound the variables to prevent 'min_dim' contradictions
                x = model.NewIntVar(fixed_x, fixed_x, f'x_{r_id}')
                z = model.NewIntVar(fixed_z, fixed_z, f'z_{r_id}')
                w = model.NewIntVar(fw_cp, fw_cp, f'w_{r_id}')
                l = model.NewIntVar(fl_cp, fl_cp, f'l_{r_id}')
            else:
                x = model.NewIntVar(0, max(0, plot_w - min_dim), f'x_{r_id}')
                z = model.NewIntVar(0, max(0, plot_l - min_dim), f'z_{r_id}')
                w = model.NewIntVar(min_dim, plot_w, f'w_{r_id}')
                l = model.NewIntVar(min_dim, plot_l, f'l_{r_id}')

                # Aspect ratio (no super-elongated rooms)
                if "corridor" not in r_type and "hallway" not in r_type:
                    model.Add(100 * w >= 50 * l)   # w/l >= 0.5
                    model.Add(100 * w <= 200 * l)   # w/l <= 2.0
                else:
                    # Corridor: at least one dimension must be narrow (<= 5 ft)
                    b1 = model.NewBoolVar(f'w_{r_id}_limit')
                    b2 = model.NewBoolVar(f'l_{r_id}_limit')
                    model.Add(w <= to_cp(5.0)).OnlyEnforceIf(b1)
                    model.Add(l <= to_cp(5.0)).OnlyEnforceIf(b2)
                    model.AddBoolOr([b1, b2])

            x_end = model.NewIntVar(0, max(plot_w, 2000), f'xe_{r_id}')
            z_end = model.NewIntVar(0, max(plot_l, 2000), f'ze_{r_id}')
            model.Add(x_end == x + w)
            model.Add(z_end == z + l)

            # Upper indoor rooms require structural support from the lower
            # slab. Balconies/open-air projections may extend beyond it, but
            # still remain inside the plot domains above.
            if allowed_bounds and "fixed_rect" not in room and not room.get("is_outdoor") and str(room.get("roof_type", "")).lower() != "open":
                bx0 = math.ceil(float(allowed_bounds[0]) * COORD_SCALE)
                bz0 = math.ceil(float(allowed_bounds[1]) * COORD_SCALE)
                bx1 = math.floor(float(allowed_bounds[2]) * COORD_SCALE)
                bz1 = math.floor(float(allowed_bounds[3]) * COORD_SCALE)
                model.Add(x >= max(0, bx0))
                model.Add(z >= max(0, bz0))
                model.Add(x_end <= min(plot_w, bx1))
                model.Add(z_end <= min(plot_l, bz1))

            x_iv = model.NewIntervalVar(x, w, x_end, f'xi_{r_id}')
            z_iv = model.NewIntervalVar(z, l, z_end, f'zi_{r_id}')

            # Minimum area (HARD)
            area = model.NewIntVar(0, max(plot_w * plot_l, 1000000), f'area_{r_id}')
            model.AddMultiplicationEquality(area, [w, l])
            if min_area_ft > 0:
                model.Add(area >= int(min_area_ft * COORD_SCALE * COORD_SCALE))

            loc_pref = room.get('location_pref') or room.get('preferred_location') or (
                'front' if r_type in {'foyer', 'porch', 'verandah', 'portico', 'entrance_lobby'} else ''
            )
            room_vars[r_id] = {
                'type': r_type,
                'connections': room.get('connections', []),
                'location_pref': loc_pref,
                'preferred_location': loc_pref,
                'location_weight': room.get('location_weight', 50 if r_type in {'foyer', 'porch', 'portico'} else 8),
                'direction_constraints': room.get('direction_constraints', []),
                'min_dim': min_dim,
                'x': x, 'z': z, 'w': w, 'l': l,
                'x_end': x_end, 'z_end': z_end,
                'x_iv': x_iv, 'z_iv': z_iv,
            }

        # ────────────────────────────────────────────
        # PHASE 2 — Non-Overlap
        # ────────────────────────────────────────────
        model.AddNoOverlap2D(
            [rv['x_iv'] for rv in room_vars.values()],
            [rv['z_iv'] for rv in room_vars.values()],
        )

        # Linear foundation sizing constraint for Floor 0
        if room_vars:
            global_z0 = model.NewIntVar(0, plot_l, 'gz0')
            for rv in room_vars.values():
                model.Add(global_z0 <= rv['z'])
            
            foyer_rvs = [rv for rv in room_vars.values() if rv['type'] in {'foyer', 'entrance_lobby'}]
            if foyer_rvs:
                for f_rv in foyer_rvs:
                    model.Add(f_rv['z'] == global_z0)

        min_dims = floor_data.get('min_foundation_dims')
        if min_dims and room_vars:
            min_fw = to_cp(min_dims[0])
            min_fl = to_cp(min_dims[1])
            global_x0 = model.NewIntVar(0, plot_w, 'gx0')
            global_x1 = model.NewIntVar(0, plot_w, 'gx1')
            global_z1 = model.NewIntVar(0, plot_l, 'gz1')
            for rv in room_vars.values():
                model.Add(global_x0 <= rv['x'])
                model.Add(global_x1 >= rv['x_end'])
                model.Add(global_z1 >= rv['z_end'])
            model.Add(global_x1 - global_x0 >= min(plot_w, min_fw))
            model.Add(global_z1 - global_z0 >= min(plot_l, min_fl))

        # Master bedroom area ≥ any regular bedroom
        masters = [rv for rv in room_vars.values() if rv['type'] == 'master_bedroom']
        beds = [rv for rv in room_vars.values() if rv['type'] == 'bedroom']
        if masters and beds:
            m = masters[0]
            ma = model.NewIntVar(0, plot_w * plot_l, 'master_area')
            model.AddMultiplicationEquality(ma, [m['w'], m['l']])
            for i, b in enumerate(beds):
                ba = model.NewIntVar(0, plot_w * plot_l, f'bed_area_{i}')
                model.AddMultiplicationEquality(ba, [b['w'], b['l']])
                model.Add(ma >= ba)
        # Default zoning is deliberately soft. Only explicit hard directional
        # constraints are promoted to CP-SAT feasibility rules.
        pub_count, srv_count, priv_count = 0, 0, 0
        hard_direction_count = 0
        for rv in room_vars.values():
            rt = rv['type'].lower()
            if rt in {"foyer", "living_room", "dining_room", "living", "dining"}:
                pub_count += 1
            elif rt in {"kitchen", "open_kitchen", "store_room", "utility"}:
                srv_count += 1
            elif "bedroom" in rt or "bath" in rt or "toilet" in rt:
                priv_count += 1

            for constraint in rv.get('direction_constraints', []):
                if str(constraint.get('strength', '')).lower() != 'hard':
                    continue
                hard_direction_count += 1
                # A compass pin confines a room's centre to one half of the
                # plot. Combined with the door graph that can be infeasible on
                # a busy program, and a house with the gym on the wrong side
                # beats no house at all, so a recovery pass drops these.
                if floor_data.get('relax_directional'):
                    continue
                direction = str(constraint.get('value', '')).lower().replace('-', '_')
                center_x2 = 2 * rv['x'] + rv['w']
                center_z2 = 2 * rv['z'] + rv['l']
                if 'east' in direction:
                    model.Add(center_x2 >= plot_w)
                if 'west' in direction:
                    model.Add(center_x2 <= plot_w)
                if 'north' in direction:
                    model.Add(center_z2 <= plot_l)
                if 'south' in direction:
                    model.Add(center_z2 >= plot_l)

        logger.info(f"[ZONE] Public rooms={pub_count} | Service rooms={srv_count} | Private rooms={priv_count} (soft defaults)")
        floor_data['_hard_direction_count'] = hard_direction_count
        # ────────────────────────────────────────────
        # PHASE 3 — Required Door Graph (HARD vs SOFT)
        # Only strictly required structural adjacencies (attached bath ↔ bedroom,
        # open flow kitchen ↔ dining, stair ↔ landing) are encoded as HARD shared walls.
        # Access relationships (room ↔ corridor/lobby/foyer) are solved via soft walking-distance objectives.
        # ────────────────────────────────────────────
        relation_counts = {
            "structural_touch": 0, "direct_access": 0, "open_flow": 0,
            "adjacent_hard": 0, "adjacent_soft": 0, "near": 0,
            "reachable": 0, "separated": 0, "directional": 0,
        }
        encoded_relation_ids = set()
        if candidate is not None:
            relations = list(candidate.relations_by_id.values())
            for relation in relations:
                kind = relation.kind
                if kind == "direction":
                    relation_counts["directional"] += 1
                    continue
                if kind == "near":
                    relation_counts["near"] += 1
                    continue
                if kind == "reachable":
                    relation_counts["reachable"] += 1
                    continue
                if kind == "separated":
                    relation_counts["separated"] += 1
                    if relation.is_hard and relation.target_room_id:
                        if relation.source_room_id not in room_vars or relation.target_room_id not in room_vars:
                            raise RuntimeError(
                                f"candidate={candidate.candidate_id} relation={relation.relation_id} references missing CP room IDs"
                            )
                        a, b = room_vars[relation.source_room_id], room_vars[relation.target_room_id]
                        gap = to_cp(max(1.0, relation.required_overlap_ft))
                        sides = [model.NewBoolVar(f"sep_{relation.relation_id}_{index}") for index in range(4)]
                        model.Add(a['x_end'] + gap <= b['x']).OnlyEnforceIf(sides[0])
                        model.Add(b['x_end'] + gap <= a['x']).OnlyEnforceIf(sides[1])
                        model.Add(a['z_end'] + gap <= b['z']).OnlyEnforceIf(sides[2])
                        model.Add(b['z_end'] + gap <= a['z']).OnlyEnforceIf(sides[3])
                        model.AddBoolOr(sides)
                        encoded_relation_ids.add(relation.relation_id)
                    continue
                if kind == "adjacent" and not relation.is_hard:
                    relation_counts["adjacent_soft"] += 1
                    continue
                if kind == "open_flow" and not relation.is_hard:
                    relation_counts["open_flow"] += 1
                    continue
                if not relation.target_room_id:
                    continue
                if relation.source_room_id not in room_vars or relation.target_room_id not in room_vars:
                    raise RuntimeError(
                        f"candidate={candidate.candidate_id} relation={relation.relation_id} references missing CP room IDs"
                    )
                if relation.source_room_id == relation.target_room_id:
                    raise RuntimeError(
                        f"candidate={candidate.candidate_id} relation={relation.relation_id} is self-referential"
                    )
                if not relation.needs_shared_wall:
                    continue
                required_overlap = max(0.1, relation.required_overlap_ft)
                self._add_touch_constraint(
                    model,
                    room_vars[relation.source_room_id], room_vars[relation.target_room_id],
                    relation.source_room_id, relation.target_room_id,
                    min_overlap_ft=required_overlap,
                )
                encoded_relation_ids.add(relation.relation_id)
                if kind in {"direct_access", "exclusive_access"}:
                    relation_counts["direct_access"] += 1
                    if kind == "exclusive_access":
                        relation_counts["structural_touch"] += 1
                elif kind == "open_flow":
                    relation_counts["open_flow"] += 1
                elif kind == "adjacent":
                    relation_counts["adjacent_hard"] += 1
            candidate.encoded_relation_ids = encoded_relation_ids
            required_encoded = {
                relation.relation_id for relation in candidate.relations_by_id.values()
                if relation.needs_shared_wall or (relation.is_hard and relation.kind == "separated")
            }
            if required_encoded != encoded_relation_ids:
                raise InternalInvariantError(
                    f"candidate={candidate.candidate_id}: hard relation encoding mismatch "
                    f"missing={sorted(required_encoded - encoded_relation_ids)} "
                    f"unexpected={sorted(encoded_relation_ids - required_encoded)}"
                )
        else:
            # Backwards-compatible compilation for edit/template paths. Fresh
            # generation always supplies the authoritative candidate contract.
            processed_edges = set()
            for r_id, rv in room_vars.items():
                for conn in rv['connections']:
                    intent = conn.get('intent', 'standard')
                    key = {
                        "proximity": "near", "separation": "separated",
                        "open_flow": "open_flow", "adjacent": "adjacent_soft",
                    }.get(intent)
                    if key:
                        relation_counts[key] += 1
                        continue
                    target_id = conn.get('target_room_id')
                    if target_id not in room_vars or target_id == r_id:
                        continue
                    edge = frozenset((r_id, target_id))
                    if edge in processed_edges:
                        continue
                    processed_edges.add(edge)
                    self._add_touch_constraint(model, rv, room_vars[target_id], r_id, target_id, min_overlap_ft=3.0)
                    relation_counts["direct_access"] += 1

        logger.info("[RELATION COMPILE]")
        for key, value in relation_counts.items():
            logger.info("[RELATION COMPILE] %s=%s", key, value)
        logger.info(
            "[CP-SAT EDGE AUDIT] requested=%s encoded=%s",
            sum(1 for relation in candidate.relations_by_id.values() if relation.needs_shared_wall) if candidate else relation_counts["direct_access"],
            len(encoded_relation_ids) if candidate else relation_counts["direct_access"],
        )

        # ────────────────────────────────────────────
        # PHASE 4 — Forbidden Adjacencies (HARD)
        # ────────────────────────────────────────────
        all_ids = list(room_vars.keys())
        hard_forbidden_pairs = {
            frozenset(str(value).lower() for value in pair)
            for pair in floor_data.get('hard_forbidden_pairs', [])
            if isinstance(pair, (list, tuple, set)) and len(pair) == 2
        }
        forbidden_count = 0
        for i in range(len(all_ids)):
            for j in range(i + 1, len(all_ids)):
                id_a, id_b = all_ids[i], all_ids[j]
                t_a = room_vars[id_a]['type']
                t_b = room_vars[id_b]['type']
                pair_set = frozenset({t_a, t_b})

                if pair_set in hard_forbidden_pairs:
                    a, b = room_vars[id_a], room_vars[id_b]
                    # Force ≥ 1 grid-unit gap in at least one direction
                    s1 = model.NewBoolVar(f'fsep_l_{id_a}_{id_b}')
                    s2 = model.NewBoolVar(f'fsep_r_{id_a}_{id_b}')
                    s3 = model.NewBoolVar(f'fsep_a_{id_a}_{id_b}')
                    s4 = model.NewBoolVar(f'fsep_b_{id_a}_{id_b}')

                    model.Add(a['x'] + a['w'] + 1 <= b['x']).OnlyEnforceIf(s1)
                    model.Add(b['x'] + b['w'] + 1 <= a['x']).OnlyEnforceIf(s2)
                    model.Add(a['z'] + a['l'] + 1 <= b['z']).OnlyEnforceIf(s3)
                    model.Add(b['z'] + b['l'] + 1 <= a['z']).OnlyEnforceIf(s4)

                    model.AddBoolOr([s1, s2, s3, s4])
                    forbidden_count += 1

        logger.info(f"[CP-SAT] {forbidden_count} forbidden adjacency pairs encoded as HARD constraints.")

        # ────────────────────────────────────────────
        # PHASE 5 — Soft Objectives
        # ────────────────────────────────────────────
        obj_terms = []

        # Plot coverage is intentionally NOT solved by inflating individual
        # room variables. A single oversized bathroom/service room can satisfy
        # an area target while producing a terrible plan. LayoutEngine expands
        # the finished connected floor plan proportionally instead, so every
        # room shares the available plot area according to its normal program.

        # 5a. Zonal clustering
        zones = {"public": [], "private": []}
        for rv in room_vars.values():
            t = rv['type'].lower()
            if t in ('living_room', 'foyer', 'dining_room', 'kitchen', 'porch', 'entrance'):
                zones["public"].append(rv)
            elif t in ('master_bedroom', 'bedroom', 'closet'):
                zones["private"].append(rv)

        for z_name, z_rooms in zones.items():
            if not z_rooms:
                continue
            zx0 = model.NewIntVar(0, plot_w, f'{z_name}_x0')
            zx1 = model.NewIntVar(0, plot_w, f'{z_name}_x1')
            zz0 = model.NewIntVar(0, plot_l, f'{z_name}_z0')
            zz1 = model.NewIntVar(0, plot_l, f'{z_name}_z1')
            for rv in z_rooms:
                model.Add(zx0 <= rv['x'])
                model.Add(zx1 >= rv['x'] + rv['w'])
                model.Add(zz0 <= rv['z'])
                model.Add(zz1 >= rv['z'] + rv['l'])
            # Cluster tightly
            obj_terms.append((zx1 - zx0) * 3)
            obj_terms.append((zz1 - zz0) * 3)
            # Public at front (South / Z=max), private at rear (North / Z=0)
            if z_name == "public":
                obj_terms.append(-zz1 * 15)
            elif z_name == "private":
                obj_terms.append(zz0 * 15)

        # 5b. Global Compactness
        global_x0 = model.NewIntVar(0, plot_w, 'global_x0')
        global_x1 = model.NewIntVar(0, plot_w, 'global_x1')
        global_z0 = model.NewIntVar(0, plot_l, 'global_z0')
        global_z1 = model.NewIntVar(0, plot_l, 'global_z1')
        for rv in room_vars.values():
            model.Add(global_x0 <= rv['x'])
            model.Add(global_x1 >= rv['x'] + rv['w'])
            model.Add(global_z0 <= rv['z'])
            model.Add(global_z1 >= rv['z'] + rv['l'])

        # The living room is the public entry room and must own a real facade
        # segment. Without this constraint another public room can surround it,
        # leaving the "main door" on an internal wall.
        entry_room = next((rv for rv in room_vars.values() if rv['type'] == 'living_room'), None)
        if entry_room is not None:
            model.Add(entry_room['z_end'] == global_z1)
        obj_terms.append((global_x1 - global_x0) * 10)
        obj_terms.append((global_z1 - global_z0) * 10)

        # 5c. Room expansion weights
        for rv in room_vars.values():
            t = rv['type']
            ew = 1
            if 'living' in t:    ew = 10
            elif 'master' in t:  ew = 9
            elif 'dining' in t:  ew = 8
            elif 'kitchen' in t: ew = 7
            elif 'bed' in t:     ew = 6
            elif 'bath' in t or 'toilet' in t: ew = 3
            elif 'corridor' in t or 'hallway' in t: ew = 12

            # Slightly perturb weights on retries to find alternate topologies
            if attempt > 0:
                # Deterministic perturbation based on room id and attempt
                ew = max(1, ew + (hash(rv['type']) + attempt) % 3 - 1)

            obj_terms.append(-ew * rv['w'])
            obj_terms.append(-ew * rv['l'])
            # Mild packing to origin
            obj_terms.append(rv['x'])
            obj_terms.append(rv['z'])

        # 5c. Walking-distance minimisation
        for r_id, rv in room_vars.items():
            cx = model.NewIntVar(0, plot_w * 2, f'cx_{r_id}')
            cz = model.NewIntVar(0, plot_l * 2, f'cz_{r_id}')
            model.Add(cx == 2 * rv['x'] + rv['w'])
            model.Add(cz == 2 * rv['z'] + rv['l'])

            if rv.get('location_pref') == 'front':
                obj_terms.append(10 * cz)
            elif rv.get('location_pref') == 'rear':
                obj_terms.append(10 * (plot_l * 2 - cz))
            elif rv.get('location_pref') == 'center':
                # Minimise distance between room centre and buildable-plot
                # centre. This remains soft so hard access, adjacency and
                # non-overlap constraints can select a nearby feasible spot.
                center_dx = model.NewIntVar(0, plot_w * 2, f'center_dx_{r_id}')
                center_dz = model.NewIntVar(0, plot_l * 2, f'center_dz_{r_id}')
                model.AddAbsEquality(center_dx, cx - plot_w)
                model.AddAbsEquality(center_dz, cz - plot_l)
                center_weight = max(1, min(50, int(rv.get('location_weight', 8))))
                obj_terms.append(center_weight * center_dx)
                obj_terms.append(center_weight * center_dz)

                # A hint guides the first feasible search without constraining
                # the final room dimensions or topology.
                model.AddHint(rv['x'], max(0, plot_w // 2 - rv['min_dim'] // 2))
                model.AddHint(rv['z'], max(0, plot_l // 2 - rv['min_dim'] // 2))

            for constraint in rv.get('direction_constraints', []):
                direction = str(constraint.get('value', '')).lower().replace('-', '_')
                strength = str(constraint.get('strength', 'preference')).lower()
                origin = str(constraint.get('origin', 'architectural_default')).lower()
                base_weight = {'hard': 80, 'strong': 25, 'preference': 8}.get(strength, 8)
                if origin == 'user':
                    base_weight *= 3
                if 'east' in direction:
                    obj_terms.append(base_weight * (plot_w * 2 - cx))
                if 'west' in direction:
                    obj_terms.append(base_weight * cx)
                if 'north' in direction:
                    obj_terms.append(base_weight * cz)
                if 'south' in direction:
                    obj_terms.append(base_weight * (plot_l * 2 - cz))

            for conn in rv['connections']:
                tt = conn.get('target_room', '')
                wt = conn.get('weight', 5)
                target_room_id = conn.get('target_room_id', '')
                if not tt and not target_room_id:
                    continue
                tid = target_room_id if target_room_id in room_vars and target_room_id != r_id else None
                if tid is None:
                    for k, v in room_vars.items():
                        if v['type'] == tt and k != r_id:
                            tid = k
                            break
                if not tid:
                    continue
                tv = room_vars[tid]
                cx2 = model.NewIntVar(0, plot_w * 2, f'cx2_{r_id}_{tid}')
                cz2 = model.NewIntVar(0, plot_l * 2, f'cz2_{r_id}_{tid}')
                model.Add(cx2 == 2 * tv['x'] + tv['w'])
                model.Add(cz2 == 2 * tv['z'] + tv['l'])

                dx = model.NewIntVar(0, plot_w * 2, f'dx_{r_id}_{tid}')
                dz = model.NewIntVar(0, plot_l * 2, f'dz_{r_id}_{tid}')
                model.AddAbsEquality(dx, cx - cx2)
                model.AddAbsEquality(dz, cz - cz2)
                if conn.get('intent') == 'separation':
                    obj_terms.append(-wt * dx)
                    obj_terms.append(-wt * dz)
                else:
                    obj_terms.append(wt * dx)
                    obj_terms.append(wt * dz)

        # Recovery asks only for the first valid complete plan. Optimizing all
        # pairwise walking distances can consume the whole deadline without
        # ever publishing a feasible incumbent on dense programs.
        if not floor_data.get('relaxed_recovery'):
            model.Minimize(sum(obj_terms))

        # ────────────────────────────────────────────
        # PHASE 6 — Solve
        # ────────────────────────────────────────────
        solver = cp_model.CpSolver()
        # Complex courtyard/suite graphs routinely have 12–16 hard shared-wall
        # edges. Four seconds returned UNKNOWN on otherwise feasible 40x80
        # plots and forced a lossy legacy fallback. Eight seconds still keeps
        # the complete request inside the 30-second product budget.
        # A feasible layout is normally found in well under a second; the long
        # solves are the ones that were going to fail anyway. Capping each
        # solve lets the search try more envelopes inside the same budget.
        configured_limit = float(os.getenv("CP_SOLVER_TIMEOUT_SECONDS", "4"))
        if floor_data.get('relaxed_recovery'):
            configured_limit = float(os.getenv("CP_RECOVERY_TIMEOUT_SECONDS", "4"))
        solver_limit = max(1.0, min(8.0, configured_limit))
        remaining = solve_time_remaining()
        if remaining is not None:
            # Past the budget this is a last-chance rescue solve, so it still
            # gets a workable allowance — starving it to a fraction of a second
            # just converts a slow success into a fast failure.
            rescue = float(os.getenv("CP_SOLVER_RESCUE_SECONDS", "3"))
            solver_limit = max(rescue, min(solver_limit, remaining)) if remaining <= 0 else \
                max(1.0, min(solver_limit, remaining))
        solver.parameters.max_time_in_seconds = solver_limit
        # Set random seed to explore different search spaces
        solver.parameters.random_seed = attempt
        # Complex adjacency graphs were previously solved on one search
        # worker during attempt zero. Use bounded parallel CP search from the
        # start; the Celery process itself runs in solo mode.
        solver.parameters.num_workers = max(1, min(8, int(os.getenv("CP_SOLVER_WORKERS", "8"))))

        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            quality = "optimal" if status == cp_model.OPTIMAL else "feasible"
            logger.info(f"CP Solver found a {quality} solution (attempt {attempt})!")

            resolved = []
            for r_id, rv in room_vars.items():
                resolved.append({
                    "id": r_id,
                    "type": rv['type'],
                    "connections": rv['connections'],
                    "x": from_cp(solver.Value(rv['x'])),
                    "z": from_cp(solver.Value(rv['z'])),
                    "width": from_cp(solver.Value(rv['w'])),
                    "length": from_cp(solver.Value(rv['l'])),
                })

            # ── PHASE 7 — Post-Solve Validation ──
            if candidate is not None:
                candidate.set_rectangles({
                    room["id"]: SolvedRect(room["x"], room["z"], room["width"], room["length"])
                    for room in resolved
                })
                candidate.assert_encoded_edges_realized()
                logger.info("[SOLVER INVARIANT] all encoded hard edges realized candidate_id=%s", candidate.candidate_id)
            validation = self._validate(resolved, hard_forbidden_pairs, validate_reachability=candidate is None)
            floor_data['resolved_rooms'] = resolved
            if candidate is not None:
                floor_data['candidate'] = candidate
            floor_data['validation'] = validation

            if not validation['passed']:
                for e in validation['errors']:
                    logger.info(f"[POST-VALIDATE] {e}")
                max_attempts = max(1, int(os.getenv("CP_SOLVER_MAX_ATTEMPTS", "1")))
                if attempt + 1 < max_attempts:
                    logger.info(f"[RETRY] Re-solving (attempt {attempt + 1})…")
                    floor_data.pop('resolved_rooms', None)
                    return self._solve_single_topology(floor_data, attempt + 1, topology_type)
                else:
                    logger.info("[DISCARD] Post-validation failed and retries exhausted; discarding invalid candidate layout.")
                    floor_data.pop('resolved_rooms', None)
                    floor_data['validation'] = {'passed': False, 'errors': validation['errors']}
        else:
            if status == cp_model.MODEL_INVALID:
                logger.info(f"[FALLBACK] CP model was invalid; Details: {model.Validate()}")
            logger.info(f"CP Solver did not return a solution (status {status}, attempt {attempt}).")
            floor_data['validation'] = {'passed': False, 'errors': ['Solver infeasible']}
            
            # First recovery: keep every room, drop the compass pins. These are
            # placement preferences, not access rules, and they were turning
            # solvable programs infeasible on plots less than half full.
            if (
                'resolved_rooms' not in floor_data
                and floor_data.get('_hard_direction_count')
                and not floor_data.get('relax_directional')
            ):
                logger.info(
                    "[RELAXATION] Dropping %d hard directional constraint(s) and retrying...",
                    floor_data['_hard_direction_count'],
                )
                floor_data_free = dict(floor_data)
                floor_data_free['relax_directional'] = True
                try:
                    result = self._solve_single_topology(floor_data_free, attempt, topology_type)
                    if 'resolved_rooms' in result:
                        return result
                except Exception:  # noqa: BLE001 - fall through to the next recovery
                    pass

            # Relaxation pass for slab-constrained upper floors or complex programs
            if 'resolved_rooms' not in floor_data and attempt < 2:
                relaxed_specs = self._relax_optional_rooms(rooms_spec)
                if relaxed_specs is not None:
                    logger.info("[RELAXATION] Shrinking rooms to architectural minimums and retrying...")
                    floor_data_relaxed = dict(floor_data)
                    floor_data_relaxed['rooms'] = relaxed_specs
                    if candidate is not None:
                        relaxed_candidate = copy.deepcopy(candidate)
                        relaxed_candidate.rooms_by_id = {
                            str(room['id']): room for room in relaxed_specs
                        }
                        floor_data_relaxed['candidate'] = relaxed_candidate
                    floor_data_relaxed['relaxed_recovery'] = True
                    result = self._solve_single_topology(floor_data_relaxed, attempt + 1, topology_type)
                    if 'resolved_rooms' in result:
                        return result

            max_attempts = max(1, int(os.getenv("CP_SOLVER_MAX_ATTEMPTS", "3")))
            if attempt + 1 < max_attempts and not solve_deadline_passed():
                logger.info(f"[RETRY] Re-solving with perturbed weights (attempt {attempt + 1})…")
                return self._solve_single_topology(floor_data, attempt + 1, topology_type)

            # Final diagnostic if all attempts fail
            failing_stage = self._diagnose_model_stages(floor_data)
            logger.info(f"[MODEL DIAGNOSTIC] Final failing stage identified: {failing_stage}")
            if allowed_bounds:
                total_min = sum(ROOM_MINIMUMS.get(r.get('type', 'room'), _DEFAULT_MIN)['area'] 
                                for r in rooms_spec if not r.get('is_outdoor'))
                slab_w = max(0.1, float(allowed_bounds[2]) - float(allowed_bounds[0]))
                slab_l = max(0.1, float(allowed_bounds[3]) - float(allowed_bounds[1]))
                slab_area = slab_w * slab_l
                if total_min > slab_area:
                    raise RuntimeError(
                        f"Requested layout requires a minimum of {int(total_min)} sq ft, "
                        f"but available buildable footprint is only {int(slab_area)} sq ft."
                    )
                else:
                    raise RuntimeError(
                        f"The requested layout ({len(rooms_spec)} rooms, {int(total_min)} sq ft min) "
                        f"could not satisfy all spatial or door adjacency rules within the buildable footprint ({int(slab_area)} sq ft available) "
                        f"[Failing stage: {failing_stage}]. Please simplify room count or relax layout rules."
                    )

        return floor_data

    def _diagnose_model_stages(self, floor_data: dict) -> str:
        """Staged CP-SAT diagnostic tool to pinpoint constraint conflicts."""
        plot_w_ft = floor_data.get('plot_width', 30.0)
        plot_l_ft = floor_data.get('plot_length', 40.0)
        rooms_spec = floor_data.get('rooms', [])
        allowed_bounds = floor_data.get('allowed_bounds')
        
        stages = [
            "variables_only",
            "inside_boundary",
            "room_dimensions",
            "no_overlap",
            "fixed_rooms",
            "structural_adjacency",
            "forbidden_adjacency",
        ]
        
        first_failing_stage = "none"
        for final_stage in stages:
            model = cp_model.CpModel()
            plot_w = to_cp(plot_w_ft)
            plot_l = to_cp(plot_l_ft)
            room_vars = {}
            for idx, room in enumerate(rooms_spec):
                r_type = room.get("type", "room")
                r_id = room.get("id", f"{r_type}_{idx}")
                base_min_dim = room.get("target_min_dim") or ROOM_MINIMUMS.get(r_type, _DEFAULT_MIN).get("min_dim", 8)
                min_dim = to_cp(base_min_dim)
                min_dim = max(1, min(min_dim, plot_w, plot_l))
                
                if "fixed_rect" in room and final_stage in {"fixed_rooms", "structural_adjacency", "forbidden_adjacency"}:
                    fx, fz, fw, fl = room["fixed_rect"]
                    fixed_x = math.floor(fx * COORD_SCALE)
                    fixed_z = math.floor(fz * COORD_SCALE)
                    fixed_x_end = math.ceil((fx + fw) * COORD_SCALE)
                    fixed_z_end = math.ceil((fz + fl) * COORD_SCALE)
                    x = model.NewIntVar(fixed_x, fixed_x, f'x_{r_id}')
                    z = model.NewIntVar(fixed_z, fixed_z, f'z_{r_id}')
                    w = model.NewIntVar(fixed_x_end - fixed_x, fixed_x_end - fixed_x, f'w_{r_id}')
                    l = model.NewIntVar(fixed_z_end - fixed_z, fixed_z_end - fixed_z, f'l_{r_id}')
                else:
                    x = model.NewIntVar(0, max(0, plot_w - min_dim), f'x_{r_id}')
                    z = model.NewIntVar(0, max(0, plot_l - min_dim), f'z_{r_id}')
                    w = model.NewIntVar(min_dim, plot_w, f'w_{r_id}')
                    l = model.NewIntVar(min_dim, plot_l, f'l_{r_id}')

                x_end = model.NewIntVar(0, max(plot_w, 2000), f'xe_{r_id}')
                z_end = model.NewIntVar(0, max(plot_l, 2000), f'ze_{r_id}')
                model.Add(x_end == x + w)
                model.Add(z_end == z + l)

                if final_stage != "variables_only" and allowed_bounds and "fixed_rect" not in room and not room.get("is_outdoor") and str(room.get("roof_type", "")).lower() != "open":
                    bx0 = math.ceil(float(allowed_bounds[0]) * COORD_SCALE)
                    bz0 = math.ceil(float(allowed_bounds[1]) * COORD_SCALE)
                    bx1 = math.floor(float(allowed_bounds[2]) * COORD_SCALE)
                    bz1 = math.floor(float(allowed_bounds[3]) * COORD_SCALE)
                    model.Add(x >= max(0, bx0))
                    model.Add(z >= max(0, bz0))
                    model.Add(x_end <= min(plot_w, bx1))
                    model.Add(z_end <= min(plot_l, bz1))

                x_iv = model.NewIntervalVar(x, w, x_end, f'xi_{r_id}')
                z_iv = model.NewIntervalVar(z, l, z_end, f'zi_{r_id}')

                if final_stage in {"room_dimensions", "no_overlap", "fixed_rooms", "structural_adjacency", "forbidden_adjacency"}:
                    base_area = room.get("target_area") or ROOM_MINIMUMS.get(r_type, _DEFAULT_MIN).get("area", 64)
                    min_area_ft = max(1.0, float(base_area))
                    area = model.NewIntVar(0, max(plot_w * plot_l, 1000000), f'area_{r_id}')
                    model.AddMultiplicationEquality(area, [w, l])
                    model.Add(area >= int(min_area_ft * COORD_SCALE * COORD_SCALE))

                room_vars[r_id] = {'type': r_type, 'connections': room.get('connections', []), 'x': x, 'z': z, 'w': w, 'l': l, 'x_end': x_end, 'z_end': z_end, 'x_iv': x_iv, 'z_iv': z_iv}

            if final_stage in {"no_overlap", "fixed_rooms", "structural_adjacency", "forbidden_adjacency"}:
                model.AddNoOverlap2D([rv['x_iv'] for rv in room_vars.values()], [rv['z_iv'] for rv in room_vars.values()])

            if final_stage == "structural_adjacency":
                for r_id, rv in room_vars.items():
                    for conn in rv['connections']:
                        intent = conn.get('intent', 'standard')
                        target_type = conn.get('target_room', '')
                        target_room_id = conn.get('target_room_id', '')
                        target_id = target_room_id if target_room_id in room_vars and target_room_id != r_id else None
                        if target_id and (intent == "attached" or intent == "open_flow" or "stair" in rv['type']):
                            self._add_touch_constraint(model, rv, room_vars[target_id], r_id, target_id)

            if final_stage == "forbidden_adjacency":
                all_ids = list(room_vars.keys())
                for i in range(len(all_ids)):
                    for j in range(i + 1, len(all_ids)):
                        id_a, id_b = all_ids[i], all_ids[j]
                        if frozenset({room_vars[id_a]['type'], room_vars[id_b]['type']}) in FORBIDDEN_PAIRS:
                            a, b = room_vars[id_a], room_vars[id_b]
                            s1, s2, s3, s4 = model.NewBoolVar('s1'), model.NewBoolVar('s2'), model.NewBoolVar('s3'), model.NewBoolVar('s4')
                            model.Add(a['x'] + a['w'] + 1 <= b['x']).OnlyEnforceIf(s1)
                            model.Add(b['x'] + b['w'] + 1 <= a['x']).OnlyEnforceIf(s2)
                            model.Add(a['z'] + a['l'] + 1 <= b['z']).OnlyEnforceIf(s3)
                            model.Add(b['z'] + b['l'] + 1 <= a['z']).OnlyEnforceIf(s4)
                            model.AddBoolOr([s1, s2, s3, s4])

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 1.0
            status = solver.Solve(model)
            status_name = solver.StatusName(status)
            logger.info(f"[MODEL DIAGNOSTIC] stage={final_stage} status={status_name}")
            if status == cp_model.INFEASIBLE and first_failing_stage == "none":
                first_failing_stage = final_stage

        return first_failing_stage

        return floor_data

    def _relax_optional_rooms(self, rooms_spec: list) -> list | None:
        """Shrink rooms to their absolute architectural minimums for fallback recovery."""
        import copy
        relaxed = copy.deepcopy(rooms_spec)
        changed = False
        for room in relaxed:
            rtype = room.get('type', '')
            if rtype not in {'staircase', 'stairwell', 'void'}:
                mins = ROOM_MINIMUMS.get(rtype, _DEFAULT_MIN)
                min_side = float(mins.get('min_dim', 6.0))
                min_area = float(mins.get('area', 36.0))
                if room.get('target_min_dim') != min_side or room.get('target_area') != min_area:
                    room['target_min_dim'] = min_side
                    room['target_area'] = min_area
                    room['min_w_override'] = min_side
                    room['min_l_override'] = min_side
                    changed = True
        return relaxed if changed else None

    # ── helpers ──────────────────────────────────────

    @staticmethod
    def _add_touch_constraint(model, a, b, a_id, b_id, min_overlap_ft=1.0):
        """
        HARD: rooms *a* and *b* must share a wall with ≥ min_overlap overlap (adjacency).

        Encodes four directional cases; at least one must be true.
        """
        min_overlap = to_cp(min_overlap_ft)
        tag = f'{a_id}__{b_id}'

        # Case 1 — A left of B  (A.x_end == B.x, Z overlap ≥ min_overlap)
        c1 = model.NewBoolVar(f'L_{tag}')
        model.Add(a['x'] + a['w'] == b['x']).OnlyEnforceIf(c1)
        model.Add(a['z'] + min_overlap <= b['z'] + b['l']).OnlyEnforceIf(c1)
        model.Add(b['z'] + min_overlap <= a['z'] + a['l']).OnlyEnforceIf(c1)

        # Case 2 — A right of B  (B.x_end == A.x)
        c2 = model.NewBoolVar(f'R_{tag}')
        model.Add(b['x'] + b['w'] == a['x']).OnlyEnforceIf(c2)
        model.Add(a['z'] + min_overlap <= b['z'] + b['l']).OnlyEnforceIf(c2)
        model.Add(b['z'] + min_overlap <= a['z'] + a['l']).OnlyEnforceIf(c2)

        # Case 3 — A above B  (A.z_end == B.z, X overlap ≥ min_overlap)
        c3 = model.NewBoolVar(f'A_{tag}')
        model.Add(a['z'] + a['l'] == b['z']).OnlyEnforceIf(c3)
        model.Add(a['x'] + min_overlap <= b['x'] + b['w']).OnlyEnforceIf(c3)
        model.Add(b['x'] + min_overlap <= a['x'] + a['w']).OnlyEnforceIf(c3)

        # Case 4 — A below B  (B.z_end == A.z)
        c4 = model.NewBoolVar(f'B_{tag}')
        model.Add(b['z'] + b['l'] == a['z']).OnlyEnforceIf(c4)
        model.Add(a['x'] + min_overlap <= b['x'] + b['w']).OnlyEnforceIf(c4)
        model.Add(b['x'] + min_overlap <= a['x'] + a['w']).OnlyEnforceIf(c4)

        model.AddBoolOr([c1, c2, c3, c4])

    def _validate(self, rooms, hard_forbidden_pairs=None, validate_reachability=True):
        """BFS reachability + forbidden-adjacency + door-feasibility post-check."""
        errors = []

        # Build geometric adjacency graph (shared wall ≥ MIN_DOOR_WALL_FT)
        adj = {r['id']: set() for r in rooms}
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                sw = self._shared_wall(a, b)
                if sw > 0.01:
                    # Check forbidden
                    if frozenset({a['type'], b['type']}) in (hard_forbidden_pairs or set()):
                        errors.append(f"FORBIDDEN: {a['id']} ({a['type']}) touches {b['id']} ({b['type']})")
                    # Standard doors can downscale on a 2–4 ft shared segment;
                    # open-flow and structural edges retain the wider check
                    # below. Using 4 ft for graph reachability rejected valid
                    # bathroom/corridor doors after CP had guaranteed 3 ft.
                    if sw >= 2.0:
                        adj[a['id']].add(b['id'])
                        adj[b['id']].add(a['id'])

        # Verify structural topology edges (attached, open_flow, stair) have enough shared wall
        for r in rooms:
            for conn in r.get('connections', []):
                intent = conn.get('intent', 'standard')
                if intent not in {'attached', 'open_flow'} and "stair" not in r.get('type', ''):
                    continue
                tt = conn.get('target_room', '')
                target_room_id = conn.get('target_room_id', '')
                if not tt and not target_room_id:
                    continue
                target = next((t for t in rooms if t['id'] == target_room_id and t['id'] != r['id']), None)
                if target is None:
                    target = next((t for t in rooms if t['type'] == tt and t['id'] != r['id']), None)
                if not target:
                    continue
                sw = self._shared_wall(r, target)
                if sw < MIN_DOOR_WALL_FT:
                    errors.append(
                        f"DOOR INFEASIBLE: {r['id']}↔{target['id']} "
                        f"shared wall {sw:.1f}ft < {MIN_DOOR_WALL_FT}ft"
                    )

        # BFS from living_room — every room must be reachable
        starts = [r['id'] for r in rooms if r['type'] == 'living_room']
        if validate_reachability and starts:
            visited = set(starts)
            q = deque(starts)
            while q:
                cur = q.popleft()
                for nb in adj.get(cur, []):
                    if nb not in visited:
                        visited.add(nb)
                        q.append(nb)
            unreachable = [r['id'] for r in rooms if r['id'] not in visited]
            if unreachable:
                errors.append(f"UNREACHABLE from living room: {unreachable}")

        return {'passed': len(errors) == 0, 'errors': errors}

    @staticmethod
    def _shared_wall(a, b):
        """Length of shared wall between two AABBs (0 if they don't touch)."""
        TOL = 0.05
        ax1, az1 = a['x'], a['z']
        ax2, az2 = ax1 + a['width'], az1 + a['length']
        bx1, bz1 = b['x'], b['z']
        bx2, bz2 = bx1 + b['width'], bz1 + b['length']

        # Vertical wall (left/right touch)
        if abs(ax2 - bx1) < TOL or abs(bx2 - ax1) < TOL:
            s, e = max(az1, bz1), min(az2, bz2)
            return max(0.0, e - s)

        # Horizontal wall (top/bottom touch)
        if abs(az2 - bz1) < TOL or abs(bz2 - az1) < TOL:
            s, e = max(ax1, bx1), min(ax2, bx2)
            return max(0.0, e - s)

        return 0.0


def score_layout(nodes: List[Any], plot_w: float, plot_l: float) -> float:
    """Calculate architectural quality score (0.0 to 100.0) for a solved floor layout."""
    if not nodes:
        return 0.0

    total_area = sum(n.rect.width * n.rect.length for n in nodes)
    plot_area = plot_w * plot_l

    # 1. Compactness (ratio of building footprint to plot area)
    compactness = min(1.0, total_area / max(1.0, plot_area * 0.75))

    # 2. Corridor Area Penalty
    corridor_area = sum(n.rect.width * n.rect.length for n in nodes if "corridor" in getattr(n, "type", "").lower() or "passage" in getattr(n, "type", "").lower())
    corridor_ratio = corridor_area / max(1.0, total_area)
    corridor_penalty = max(0.0, (corridor_ratio - 0.15) * 2.0)

    # 3. Zoning Quality (Public rooms at front z <= 0.6*l, Private rooms at rear)
    zoning_score = 0.0
    for n in nodes:
        t = getattr(n, "type", "").lower()
        if t in {"foyer", "living_room", "dining_room"}:
            if n.rect.z + n.rect.length <= plot_l * 0.60:
                zoning_score += 1.0
        elif "bedroom" in t or "bath" in t:
            if n.rect.z >= plot_l * 0.30:
                zoning_score += 1.0
    zoning_quality = min(1.0, zoning_score / max(1.0, float(len(nodes))))

    # Total score formula (0 to 100)
    score = (
        compactness * 30.0 +
        zoning_quality * 40.0 +
        (1.0 - min(1.0, corridor_penalty)) * 30.0
    )
    return round(max(0.0, min(100.0, score)), 2)


# ───────────────────────────────────────────────
# Public API consumed by layout_engine.py
# ───────────────────────────────────────────────
class LayoutGeometryEngine:
    def __init__(self):
        self.solver = CPSolver()

    def solve_phase_1_local(self, prompt: str, floor_data: dict) -> dict:
        return floor_data

    def solve_phase_2_csp(self, floor_data: dict) -> dict:
        return self.solver.solve_phase_2_csp(floor_data)

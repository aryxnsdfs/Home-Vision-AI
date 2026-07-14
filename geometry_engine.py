import logging
from ortools.sat.python import cp_model
from layout_engine import ROOM_MINIMUMS, _DEFAULT_MIN

logger = logging.getLogger(__name__)

class CPSolver:
    def __init__(self):
        pass

    def solve_phase_2_csp(self, floor_data: dict) -> dict:
        """
        Phase 2: Continuous Constraint Satisfaction Problem Solver
        Uses Google OR-Tools to solve the precise coordinates and dimensions 
        of rooms based on the Room Graph, packing them without overlap.
        """
        # We will parse the floor_data which is a dictionary of room specs
        # Return a dictionary of modified floor_data
        
        plot_w_ft = floor_data.get('plot_width', 30.0)
        plot_l_ft = floor_data.get('plot_length', 40.0)
        
        rooms_spec = floor_data.get('rooms', [])
        
        # Grid Resolution: 1 unit = 0.5 feet
        scale = 2
        plot_w = int(plot_w_ft * scale)
        plot_l = int(plot_l_ft * scale)
        
        model = cp_model.CpModel()
        
        # Variables for each room
        room_vars = {}
        for idx, room in enumerate(rooms_spec):
            r_type = room.get("type", "room")
            r_id = room.get("id", f"{r_type}_{idx}")
            
            min_dim = int(ROOM_MINIMUMS.get(r_type, _DEFAULT_MIN)["min_dim"] * scale)
            # Enforce strict minimum widths per the architectural spec
            if "bedroom" in r_type:
                min_dim = max(min_dim, int(9.0 * scale))
            elif "kitchen" in r_type:
                min_dim = max(min_dim, int(8.0 * scale))
            elif "bath" in r_type or "toilet" in r_type:
                min_dim = max(min_dim, int(5.0 * scale))
                
            min_area_ft = ROOM_MINIMUMS.get(r_type, _DEFAULT_MIN)["area"]
            
            x = model.NewIntVar(0, plot_w - min_dim, f'x_{r_id}')
            z = model.NewIntVar(0, plot_l - min_dim, f'z_{r_id}')
            
            w = model.NewIntVar(min_dim, plot_w, f'w_{r_id}')
            l = model.NewIntVar(min_dim, plot_l, f'l_{r_id}')
            
            # Aspect ratio constraint: 0.65 <= w/l <= 1.6
            if "corridor" not in r_type:
                model.Add(100 * w >= 65 * l)
                model.Add(10 * w <= 16 * l)
            else:
                # Corridor must be narrow (width <= 5 OR length <= 5)
                b1 = model.NewBoolVar(f'corr_w_narrow_{r_id}')
                b2 = model.NewBoolVar(f'corr_l_narrow_{r_id}')
                model.Add(w <= int(5.0 * scale)).OnlyEnforceIf(b1)
                model.Add(l <= int(5.0 * scale)).OnlyEnforceIf(b2)
                model.AddBoolOr([b1, b2])
            
            x_end = model.NewIntVar(min_dim, plot_w, f'x_end_{r_id}')
            z_end = model.NewIntVar(min_dim, plot_l, f'z_end_{r_id}')
            
            # x + w = x_end
            model.Add(x_end == x + w)
            # z + l = z_end
            model.Add(z_end == z + l)
            
            x_interval = model.NewIntervalVar(x, w, x_end, f'x_interval_{r_id}')
            z_interval = model.NewIntervalVar(z, l, z_end, f'z_interval_{r_id}')
            
            room_vars[r_id] = {
                'type': r_type,
                'connections': room.get('connections', []),
                'x': x, 'z': z, 'w': w, 'l': l,
                'x_interval': x_interval, 'z_interval': z_interval,
                'min_area_ft': min_area_ft
            }
            
        # Constraint: Non-overlap
        if room_vars:
            x_intervals = [rv['x_interval'] for rv in room_vars.values()]
            z_intervals = [rv['z_interval'] for rv in room_vars.values()]
            model.AddNoOverlap2D(x_intervals, z_intervals)
            
            # Constraint: Master Bedroom Area >= Other Bedrooms Area
            master_rvs = [rv for rv in room_vars.values() if rv['type'] == 'master_bedroom']
            bed_rvs = [rv for rv in room_vars.values() if rv['type'] == 'bedroom']
            if master_rvs and bed_rvs:
                mrv = master_rvs[0]
                m_area = model.NewIntVar(0, plot_w * plot_l, 'm_area')
                model.AddMultiplicationEquality(m_area, [mrv['w'], mrv['l']])
                for i, brv in enumerate(bed_rvs):
                    b_area = model.NewIntVar(0, plot_w * plot_l, f'b_area_{i}')
                    model.AddMultiplicationEquality(b_area, [brv['w'], brv['l']])
                    model.Add(m_area >= b_area)
            
        # Constraint: Adjacency (Rooms must touch if connected)
        # We also want to ensure the graph is fully connected (all rooms touch the main cluster)
        # For simplicity, we just enforce explicit connections from the room graph.
        for r_id, rv in room_vars.items():
            for conn in rv['connections']:
                target_type = conn.get('target_room', '')
                if not target_type: continue
                # Find target room id
                target_id = None
                for tid, trv in room_vars.items():
                    if trv['type'] == target_type:
                        target_id = tid
                        break
                if not target_id: continue
                
                trv = room_vars[target_id]
                
                # A and B touch if:
                # (A.x_end == B.x AND A.z_interval overlaps B.z_interval) OR ...
                # We can define boolean variables for each of the 4 touching cases:
                
                # Case 1: A is to the left of B (A.x_end == B.x)
                left_of = model.NewBoolVar(f'{r_id}_left_{target_id}')
                model.Add(rv['x'] + rv['w'] == trv['x']).OnlyEnforceIf(left_of)
                
                # Case 2: A is to the right of B (B.x_end == A.x)
                right_of = model.NewBoolVar(f'{r_id}_right_{target_id}')
                model.Add(trv['x'] + trv['w'] == rv['x']).OnlyEnforceIf(right_of)
                
                # For left/right, Z intervals must overlap.
                # Overlap means: max(A.z, B.z) < min(A.z_end, B.z_end)
                z_overlap = model.NewBoolVar(f'{r_id}_z_overlap_{target_id}')
                # A.z < B.z_end AND B.z < A.z_end
                model.Add(rv['z'] < trv['z'] + trv['l']).OnlyEnforceIf(z_overlap)
                model.Add(trv['z'] < rv['z'] + rv['l']).OnlyEnforceIf(z_overlap)
                
                # Case 3: A is above B (A.z_end == B.z)
                above = model.NewBoolVar(f'{r_id}_above_{target_id}')
                model.Add(rv['z'] + rv['l'] == trv['z']).OnlyEnforceIf(above)
                
                # Case 4: A is below B (B.z_end == A.z)
                below = model.NewBoolVar(f'{r_id}_below_{target_id}')
                model.Add(trv['z'] + trv['l'] == rv['z']).OnlyEnforceIf(below)
                
                # For above/below, X intervals must overlap.
                x_overlap = model.NewBoolVar(f'{r_id}_x_overlap_{target_id}')
                model.Add(rv['x'] < trv['x'] + trv['w']).OnlyEnforceIf(x_overlap)
                model.Add(trv['x'] < rv['x'] + rv['w']).OnlyEnforceIf(x_overlap)
                
                # Exactly one directional touch must be true, AND its corresponding overlap must be true
                touch_left = model.NewBoolVar(f'{r_id}_touch_left_{target_id}')
                model.AddImplication(touch_left, left_of)
                model.AddImplication(touch_left, z_overlap)
                model.AddBoolAnd([left_of, z_overlap]).OnlyEnforceIf(touch_left)
                
                touch_right = model.NewBoolVar(f'{r_id}_touch_right_{target_id}')
                model.AddImplication(touch_right, right_of)
                model.AddImplication(touch_right, z_overlap)
                model.AddBoolAnd([right_of, z_overlap]).OnlyEnforceIf(touch_right)
                
                touch_above = model.NewBoolVar(f'{r_id}_touch_above_{target_id}')
                model.AddImplication(touch_above, above)
                model.AddImplication(touch_above, x_overlap)
                model.AddBoolAnd([above, x_overlap]).OnlyEnforceIf(touch_above)
                
                touch_below = model.NewBoolVar(f'{r_id}_touch_below_{target_id}')
                model.AddImplication(touch_below, below)
                model.AddImplication(touch_below, x_overlap)
                model.AddBoolAnd([below, x_overlap]).OnlyEnforceIf(touch_below)
                
                model.AddBoolOr([touch_left, touch_right, touch_above, touch_below])
            
        # Architectural Objectives: Zoning, Clustering, and Packing
        if room_vars:
            obj_vars = []
            
            # 1. Soft Structural Alignment & Packing + Room Expansion
            # We pull x and z to origin to pack rooms tightly.
            # We subtract a heavily weighted width and length to maximize room utility instead of shrinking them.
            for rv in room_vars.values():
                t = rv['type']
                weight = 1
                if 'living' in t: weight = 10
                elif 'master' in t: weight = 9
                elif 'dining' in t: weight = 8
                elif 'kitchen' in t: weight = 7
                elif 'bed' in t: weight = 6
                elif 'bath' in t or 'toilet' in t: weight = 3
                elif 'corridor' in t: weight = -5 # Heavily penalize corridor size
                
                # Pull to origin
                obj_vars.append(rv['x'])
                obj_vars.append(rv['z'])
                # Expand size
                obj_vars.append(-weight * rv['w'])
                obj_vars.append(-weight * rv['l'])
                
            # 2. Strict Front/Middle/Rear Structural Zoning
            front_types = ['living_room', 'foyer', 'dining_room', 'kitchen', 'porch', 'entrance']
            # middle_types = ['corridor', 'hallway', 'powder_room'] float naturally
            rear_types = ['bedroom', 'master_bedroom']
            
            for rv in room_vars.values():
                t = rv['type']
                if t in front_types:
                    obj_vars.append(rv['z'] * 15) # Extreme pull to front (z=0)
                elif t in rear_types:
                    obj_vars.append(-rv['z'] * 15) # Extreme push to rear (z=max)
                    
            # 3. Wet Zone Clustering
            wet_rooms = [rv for rv in room_vars.values() if rv['type'] in ['kitchen', 'bathroom', 'laundry', 'toilet']]
            if len(wet_rooms) > 1:
                wet_min_x = model.NewIntVar(0, plot_w, 'wet_min_x')
                wet_max_x = model.NewIntVar(0, plot_w, 'wet_max_x')
                wet_min_z = model.NewIntVar(0, plot_l, 'wet_min_z')
                wet_max_z = model.NewIntVar(0, plot_l, 'wet_max_z')
                
                for wrv in wet_rooms:
                    model.Add(wet_min_x <= wrv['x'])
                    model.Add(wet_max_x >= wrv['x'] + wrv['w'])
                    model.Add(wet_min_z <= wrv['z'])
                    model.Add(wet_max_z >= wrv['z'] + wrv['l'])
                    
                # Penalize the size of the wet bounding box (clusters them tightly)
                obj_vars.append((wet_max_x - wet_min_x) * 3)
                obj_vars.append((wet_max_z - wet_min_z) * 3)
                
            model.Minimize(sum(obj_vars))
        
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 2.0
        
        status = solver.Solve(model)
        
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            logger.info("CP Solver found a solution!")
            resolved_rooms = []
            for r_id, rv in room_vars.items():
                resolved_rooms.append({
                    "id": r_id,
                    "type": rv['type'],
                    "connections": rv['connections'],
                    "x": solver.Value(rv['x']) / scale,
                    "z": solver.Value(rv['z']) / scale,
                    "width": solver.Value(rv['w']) / scale,
                    "length": solver.Value(rv['l']) / scale,
                })
            floor_data['resolved_rooms'] = resolved_rooms
        else:
            logger.warning("CP Solver failed to find a valid layout within the time limit.")
            
        return floor_data

class LayoutGeometryEngine:
    def __init__(self):
        self.solver = CPSolver()
        
    def solve_phase_1_local(self, prompt: str, floor_data: dict) -> dict:
        return floor_data

    def solve_phase_2_csp(self, floor_data: dict) -> dict:
        return self.solver.solve_phase_2_csp(floor_data)

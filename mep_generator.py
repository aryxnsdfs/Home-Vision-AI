import math
import random
import logging
from typing import Dict, Any, List

from mep_rules import get_wiring_for_room, get_plumbing_for_room, HIGH_LOAD_APPLIANCES, ELECTRICAL_METADATA, PLUMBING_METADATA

logger = logging.getLogger(__name__)

def check_aabb_collision(x, z, width, height, existing_nodes):
    """
    Returns True if the proposed box (x, z, w, h) collides with any existing node in the room.
    Existing nodes are assumed to have a basic size (e.g. 1x1 for appliances).
    """
    for node in existing_nodes:
        # Give existing nodes a generic 1.5x1.5 bounding box for safety
        nx1, nz1 = node["x"] - 0.75, node["z"] - 0.75
        nx2, nz2 = node["x"] + 0.75, node["z"] + 0.75
        
        px1, pz1 = x - width/2, z - height/2
        px2, pz2 = x + width/2, z + height/2
        
        # AABB intersection
        if not (px2 < nx1 or px1 > nx2 or pz2 < nz1 or pz1 > nz2):
            return True
    return False

def find_safe_wall_position(room, fixture_type, existing_nodes):
    """Finds a safe coordinate for a plumbing fixture on a wall using AABB."""
    rx, rz, rw, rl = room["x"], room["z"], room["width"], room["length"]
    # Try multiple random wall positions
    for _ in range(50):
        wall = random.choice(["north", "south", "east", "west"])
        if wall == "north":
            px, pz = rx + random.uniform(1, rw-1), rz + 0.5
        elif wall == "south":
            px, pz = rx + random.uniform(1, rw-1), rz + rl - 0.5
        elif wall == "east":
            px, pz = rx + rw - 0.5, rz + random.uniform(1, rl-1)
        else:
            px, pz = rx + 0.5, rz + random.uniform(1, rl-1)
            
        if not check_aabb_collision(px, pz, 1.5, 1.5, existing_nodes):
            return round(px, 2), round(pz, 2)
            
    # Fallback if no safe space
    return round(rx + rw/2, 2), round(rz + rl/2, 2)

def generate_plumbing(layout: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    package = options.get("package", "Basic")
    water_source = options.get("waterSource", "Municipal")
    storage = options.get("storage", "Overhead Tank")
    hot_water = options.get("hotWater", "None")
    
    rooms = []
    if "floors" in layout:
        for f in layout.get("floors", []):
            rooms.extend(f.get("rooms", []))
    if not rooms:
        rooms = layout.get("rooms", [])
    if not rooms: return layout
    
    # Find Utility Room for Manifold chain
    p_room = next((r for r in rooms if "util" in r.get("type", "").lower() or "garage" in r.get("type", "").lower()), rooms[0])
    px, pz = p_room["x"] + 0.2, p_room["z"] + 0.2
    
    chain = ["water_source", "ug_tank", "water_pump", "oh_tank", "plumbing_manifold"]
    manifold_node = None
    
    # Replace (never stack): drop every existing plumbing node — both the
    # flagged ones from a previous run and the default unflagged fixtures — so
    # only one water-supply system exists for a room at a time.
    _plumb_types = ("water_sink", "geyser", "shower", "tap", "wc", "toilet",
                    "washbasin", "floor_drain", "water_inlet", "water_source",
                    "ug_tank", "water_pump", "oh_tank", "plumbing_manifold")
    for r in rooms:
        r["mep_nodes"] = [n for n in r.get("mep_nodes", [])
                          if not n.get("is_plumbing") and n.get("type", "") not in _plumb_types]
        r["plumbing_paths"] = []
    
    for i, ntype in enumerate(chain):
        node = {"type": ntype, "x": px + i*0.5, "z": pz, "y": 0.5 if "tank" in ntype else 0.2, "is_plumbing": True, "circuit": "water_main"}
        p_room["mep_nodes"].append(node)
        if ntype == "plumbing_manifold": manifold_node = node
        # Basic pipe visual connecting chain
        if i > 0:
            p_room["plumbing_paths"].append({
                "from": {"x": px + (i-1)*0.5, "y": 0.2, "z": pz},
                "to": {"x": px + i*0.5, "y": 0.2, "z": pz},
                "circuit_type": "water_main"
            })
    
    for r in rooms:
        mode = options.get("mode", "Auto")
        if mode == "Manual":
            manual_fixtures = options.get("manualFixtures", {})
            plumbing_fixtures = []
            for k, count in manual_fixtures.items():
                for _ in range(int(count)):
                    plumbing_fixtures.append(k)
        else:
            plumbing_fixtures = get_plumbing_for_room(r["type"], package)
        
        for fixture in plumbing_fixtures:
            ftype = fixture
            if fixture in ["wc", "indian_toilet", "western_toilet"]: ftype = "toilet"
            elif fixture in ["wash_basin", "water_sink"]: ftype = "water_sink"
            elif fixture == "shower": ftype = "shower"
            elif fixture == "geyser": ftype = "geyser"
                
            fx, fz = find_safe_wall_position(r, ftype, r["mep_nodes"])
            meta = PLUMBING_METADATA.get(fixture, {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"})
            
            node = {"type": ftype, "x": fx, "z": fz, "y": 1.0, "is_plumbing": True}
            node.update(meta)
            r["mep_nodes"].append(node)
            
            # Route CW, HW, Drain from a local shaft drop in the room
            shaft_x = r["x"] + 0.5
            shaft_z = r["z"] + 0.5
            if meta.get("cold_water"):
                r["plumbing_paths"].append({
                    "from": {"x": shaft_x, "y": 3.0, "z": shaft_z},
                    "to": {"x": fx, "y": 0.2, "z": fz},
                    "circuit_type": "cold_water"
                })
            if meta.get("hot_water"):
                r["plumbing_paths"].append({
                    "from": {"x": shaft_x, "y": 3.0, "z": shaft_z},
                    "to": {"x": fx, "y": 0.3, "z": fz},
                    "circuit_type": "hot_water"
                })
            if meta.get("drain"):
                r["plumbing_paths"].append({
                    "from": {"x": fx, "y": 0.1, "z": fz},
                    "to": {"x": shaft_x, "y": -0.5, "z": shaft_z},
                    "circuit_type": "drainage"
                })
        
        logger.info(f"Generated plumbing nodes and paths for room {r.get('type', 'unknown')}")
            
    return layout

def get_manhattan_distance(x1, z1, x2, z2):
    return abs(x2 - x1) + abs(z2 - z1)

def route_rmst(nodes, switchboard):
    edges = []
    
    # Sort nodes by circuit type
    # Lighting (Star Topology)
    lighting_nodes = [n for n in nodes if n != switchboard and n.get("circuit") == "lighting"]
    for ln in lighting_nodes:
        edges.append({
            "from": {"x": switchboard["x"], "y": switchboard.get("y", 1.2), "z": switchboard["z"]},
            "to": {"x": ln["x"], "y": ln.get("y", 3.0), "z": ln["z"]},
            "circuit_type": "lighting"
        })
        
    # Heavy Power (Home Run)
    heavy_nodes = [n for n in nodes if n != switchboard and n.get("circuit") == "heavy_power"]
    for hn in heavy_nodes:
        edges.append({
            "from": {"x": switchboard["x"], "y": switchboard.get("y", 1.2), "z": switchboard["z"]},
            "to": {"x": hn["x"], "y": hn.get("y", 0.3), "z": hn["z"]},
            "circuit_type": "heavy_power"
        })
        
    # RMST for General Power, Data, Smart
    for circuit_name in ["general_power", "data", "smart"]:
        circuit_nodes = [n for n in nodes if n != switchboard and n.get("circuit") == circuit_name]
        if not circuit_nodes: continue
        
        unconnected = list(circuit_nodes)
        connected = [switchboard]
        
        while unconnected:
            min_dist = float('inf')
            best_pair = None
            for c in connected:
                for u in unconnected:
                    dist = get_manhattan_distance(c["x"], c["z"], u["x"], u["z"])
                    if dist < min_dist:
                        min_dist = dist
                        best_pair = (c, u)
            
            if best_pair:
                c, u = best_pair
                edges.append({
                    "from": {"x": c["x"], "y": c.get("y", 1.2), "z": c["z"]},
                    "to": {"x": u["x"], "y": u.get("y", 0.3), "z": u["z"]},
                    "circuit_type": circuit_name
                })
                connected.append(u)
                unconnected.remove(u)
                
    return edges

def generate_wiring(layout: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    package = options.get("package", "Basic")
    rooms = []
    if "floors" in layout:
        for f in layout.get("floors", []):
            rooms.extend(f.get("rooms", []))
    if not rooms:
        rooms = layout.get("rooms", [])
    if not rooms: return layout
    # 1. Place Main DB
    db_room = None
    for p in ["utility", "garage", "foyer", "corridor", "living"]:
        for r in rooms:
            if p in r.get("type", "").lower():
                db_room = r
                break
        if db_room: break
    if not db_room: db_room = rooms[0]
    
    main_db = {"type": "main_db", "label": "Main DB", "x": db_room["x"] + 0.5, "z": db_room["z"] + 0.5, "y": 1.5, "is_wiring": True, "circuit": "main"}
    
    # Replace (never stack): drop every existing electrical node — flagged old
    # wiring AND default unflagged fixtures — so changing the wiring package
    # swaps the system instead of layering a new one on top.
    _elec_types = ("ceiling_light", "chandelier", "switch", "socket", "fan",
                   "light", "main_db", "switchboard", "tv_point", "wifi_point",
                   "ac_point", "geyser_point", "exhaust_fan")
    for r in rooms:
        r["mep_nodes"] = [n for n in r.get("mep_nodes", [])
                          if not n.get("is_wiring") and n.get("type", "") not in _elec_types]
        r["wiring_paths"] = []

        if r == db_room:
            r["mep_nodes"].append(main_db)
        
        mode = options.get("mode", "Auto")
        if mode == "Manual":
            manual_fixtures = options.get("manualFixtures", {})
            wiring_fixtures = []
            for k, count in manual_fixtures.items():
                for _ in range(int(count)):
                    wiring_fixtures.append(k)
        else:
            wiring_fixtures = get_wiring_for_room(r["type"], package)
        
        rx, rz_val, rw, rl = r["x"], r["z"], r["width"], r["length"]
        doors = r.get("doors", [])
        sb_x, sb_z = rx + 0.5, rz_val + 0.5
        
        if doors:
            d = doors[0]
            door_x_abs = d["x"]
            door_z_abs = d["z"]
            if d["wall_orientation"] in ["north", "south"]:
                sb_x = door_x_abs + d["width"] + 0.3
                if sb_x > rx + rw: sb_x = door_x_abs - 0.3
                sb_z = door_z_abs
            else:
                sb_z = door_z_abs + d["width"] + 0.3
                if sb_z > rz_val + rl: sb_z = door_z_abs - 0.3
                sb_x = door_x_abs
        
        sb_name = f"SB-{(r.get('type', 'room')[:3]).upper()}"
        switchboard = {"type": "switchboard", "label": sb_name, "x": round(sb_x, 2), "z": round(sb_z, 2), "y": 1.2, "is_wiring": True, "circuit": "sub_main"}
        r["mep_nodes"].append(switchboard)
        
        # Connect Switchboard to Main DB
        r["wiring_paths"].append({
            "from": {"x": main_db["x"], "y": main_db["y"], "z": main_db["z"]},
            "to": {"x": switchboard["x"], "y": switchboard["y"], "z": switchboard["z"]},
            "circuit_type": "sub_main"
        })
        
        new_wiring_nodes = [switchboard]
        
        for fixture in wiring_fixtures:
            meta = ELECTRICAL_METADATA.get(fixture, {"load_watts": 100, "circuit": "general_power", "wire_size": "1.5mm²"})
            y_val = 3.0 if meta.get("circuit") == "lighting" else (0.3 if "socket" in fixture else 1.2)
            
            if "ceiling" in fixture or "fan" in fixture or "chandelier" in fixture:
                px = rx + rw/2 + random.uniform(-1, 1)
                pz = rz_val + rl/2 + random.uniform(-1, 1)
                node = {"type": fixture, "x": round(px, 2), "z": round(pz, 2), "y": y_val, "is_wiring": True}
            else:
                px, pz = find_safe_wall_position(r, fixture, r["mep_nodes"])
                node = {"type": fixture, "x": px, "z": pz, "y": y_val, "is_wiring": True}
                
            node.update(meta)
            r["mep_nodes"].append(node)
            new_wiring_nodes.append(node)
            
        # Run new circuit routing
        paths = route_rmst(new_wiring_nodes, switchboard)
        r["wiring_paths"].extend(paths)
        logger.info(f"Generated {len(new_wiring_nodes)} wiring nodes and {len(paths)} paths for room {r.get('type', 'unknown')}")
        
    return layout

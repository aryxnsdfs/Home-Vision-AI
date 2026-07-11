import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

def find_corners_and_junctions(rooms: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    # We collect all distinct grid corners used by the rooms
    points = []
    # Use a small tolerance for floating point merging
    TOL = 0.1
    
    def add_point(px, pz):
        for pt in points:
            if abs(pt["x"] - px) < TOL and abs(pt["z"] - pz) < TOL:
                return
        points.append({"x": px, "z": pz})

    for r in rooms:
        rx, rz, rw, rl = r["x"], r["z"], r["width"], r["length"]
        # Corners
        add_point(rx, rz)
        add_point(rx + rw, rz)
        add_point(rx, rz + rl)
        add_point(rx + rw, rz + rl)
        
        # If wall > 4m, add midpoints
        if rw > 4.0:
            add_point(rx + rw/2, rz)
            add_point(rx + rw/2, rz + rl)
        if rl > 4.0:
            add_point(rx, rz + rl/2)
            add_point(rx + rw, rz + rl/2)

    return points

def generate_structural(layout: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates Structural Nodes (Columns, Beams, Footings) and paths (Beams).
    """
    rooms = layout.get("rooms", [])
    if not rooms:
        return layout

    # 1. Gather all load-bearing points (Corners + Intersections + Midpoints of long spans)
    ground_rooms = [r for r in rooms if not r.get("isFloor1")]
    columns_pts = find_corners_and_junctions(ground_rooms)
    
    structural_nodes = []
    structural_paths = []
    
    # 2. Spawn Columns & Footings
    for i, pt in enumerate(columns_pts):
        px, pz = pt["x"], pt["z"]
        
        # Column
        structural_nodes.append({
            "id": f"col_{i}",
            "type": "column",
            "x": px,
            "y": 0,
            "z": pz,
            "width": 0.4, # 400x400mm
            "length": 0.4,
            "height": 2.6 # Standard floor height
        })
        
        # Footing
        structural_nodes.append({
            "id": f"footing_{i}",
            "type": "footing",
            "x": px,
            "y": -0.5, # 0.5m below ground
            "z": pz,
            "width": 1.5, # 1.5x1.5m pad
            "length": 1.5,
            "height": 0.3
        })
        
    # 3. Connect Beams (Plinth + Roof)
    # Very basic routing: connect adjacent columns along horizontal/vertical lines
    for i in range(len(columns_pts)):
        for j in range(i + 1, len(columns_pts)):
            c1 = columns_pts[i]
            c2 = columns_pts[j]
            
            # Check if they share an axis
            same_x = abs(c1["x"] - c2["x"]) < 0.1
            same_z = abs(c1["z"] - c2["z"]) < 0.1
            
            if same_x or same_z:
                dist = abs(c1["z"] - c2["z"]) if same_x else abs(c1["x"] - c2["x"])
                if dist < 6.0: # Only connect if within a reasonable beam span
                    # We might get overlapping segments, but visually it works for now
                    
                    # Plinth Beam
                    structural_paths.append({
                        "from": {"x": c1["x"], "y": 0, "z": c1["z"]},
                        "to": {"x": c2["x"], "y": 0, "z": c2["z"]},
                        "type": "plinth_beam"
                    })
                    
                    # Roof Beam
                    structural_paths.append({
                        "from": {"x": c1["x"], "y": 2.6, "z": c1["z"]},
                        "to": {"x": c2["x"], "y": 2.6, "z": c2["z"]},
                        "type": "roof_beam"
                    })

    # Store at layout root level
    layout["structural_nodes"] = structural_nodes
    layout["structural_paths"] = structural_paths
    
    logger.info(f"Generated {len(structural_nodes)} structural nodes and {len(structural_paths)} beams.")
    return layout

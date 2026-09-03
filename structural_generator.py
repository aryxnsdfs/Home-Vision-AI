import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# EVERYTHING HERE IS IN FEET, because that is what RoomNode.rect and every room
# dict in this pipeline carry. The sizes below were written as metres - a 400 mm
# column, a 1.5 m footing pad, a 2.6 m storey, a 4 m wall before it needs an
# intermediate column - and then compared against feet, so an intermediate
# column was added to virtually every wall and the columns came out 0.4 ft
# (under five inches) square. Keep the metric intent in the comment and the
# value in feet.
FT_PER_M = 3.28084

COLUMN_SIDE_FT = round(0.4 * FT_PER_M, 3)        # 400 x 400 mm
FOOTING_SIDE_FT = round(1.5 * FT_PER_M, 3)       # 1.5 x 1.5 m pad
FOOTING_DEPTH_FT = round(0.3 * FT_PER_M, 3)
FOOTING_LEVEL_FT = round(-0.5 * FT_PER_M, 3)     # 0.5 m below ground
STOREY_HEIGHT_FT = round(2.6 * FT_PER_M, 3)      # standard floor height
MAX_UNSUPPORTED_WALL_FT = round(4.0 * FT_PER_M, 3)   # add a column past this
MAX_BEAM_SPAN_FT = round(6.0 * FT_PER_M, 3)


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
        
        # A wall longer than this needs an intermediate column.
        if rw > MAX_UNSUPPORTED_WALL_FT:
            add_point(rx + rw/2, rz)
            add_point(rx + rw/2, rz + rl)
        if rl > MAX_UNSUPPORTED_WALL_FT:
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
            "width": COLUMN_SIDE_FT,
            "length": COLUMN_SIDE_FT,
            "height": STOREY_HEIGHT_FT
        })
        
        # Footing
        structural_nodes.append({
            "id": f"footing_{i}",
            "type": "footing",
            "x": px,
            "y": FOOTING_LEVEL_FT,
            "z": pz,
            "width": FOOTING_SIDE_FT,
            "length": FOOTING_SIDE_FT,
            "height": FOOTING_DEPTH_FT
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
                if dist < MAX_BEAM_SPAN_FT:
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

def infer_structural_elements(floor_data: dict) -> list:
    """
    Algorithmic Structural Inference: 
    Systematically places load-bearing walls, structural concrete columns, 
    and span support beams right after a room layout is compiled.
    """
    elements = []
    
    # 1. Place corner columns for the main bounding box
    if not floor_data.get("rooms"):
        return elements
        
    # Example logic: place a 1x1 foot RCC column at the corners of every major room
    for room in floor_data["rooms"]:
        # We need a unique ID for each column
        import uuid
        room_bounds = room.get("bounding_box", {})
        if room_bounds:
            # Place at min_x, min_y
            col_id = f"uuid-str-col-{uuid.uuid4().hex[:6]}"
            elements.append({
                "element_id": col_id,
                "type": "column",
                "geometry": {
                    "min_x": room_bounds.get("min_x", 0),
                    "min_y": room_bounds.get("min_y", 0),
                    "min_z": 0,
                    "max_x": room_bounds.get("min_x", 0) + 1,
                    "max_y": room_bounds.get("min_y", 0) + 1,
                    "max_z": floor_data.get("height", 10)
                },
                "material": "reinforced_concrete",
                "load_bearing": True
            })
            
    return elements

def relocate_furniture(room_data: dict, old_bounds: dict, new_bounds: dict):
    """
    Intelligent Furniture Relocation: 
    When a room expands or shifts, scales, centers, and transforms the assets 
    relative to the room matrices rather than throwing them outside.
    """
    if "furniture" not in room_data:
        return
        
    old_width = old_bounds.get("max_x", 0) - old_bounds.get("min_x", 0)
    old_length = old_bounds.get("max_y", 0) - old_bounds.get("min_y", 0)
    
    new_width = new_bounds.get("max_x", 0) - new_bounds.get("min_x", 0)
    new_length = new_bounds.get("max_y", 0) - new_bounds.get("min_y", 0)
    
    if old_width == 0 or old_length == 0:
        return
        
    scale_x = new_width / old_width
    scale_y = new_length / old_length
    
    for item in room_data["furniture"]:
        if item.get("mobility_status") == "fixed":
            # Just shift it relative to the new min_x / min_y
            rel_x = item["position"]["x"] - old_bounds.get("min_x", 0)
            rel_y = item["position"]["y"] - old_bounds.get("min_y", 0)
            
            item["position"]["x"] = new_bounds.get("min_x", 0) + (rel_x * scale_x)
            item["position"]["y"] = new_bounds.get("min_y", 0) + (rel_y * scale_y)

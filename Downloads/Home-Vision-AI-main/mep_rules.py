# mep_rules.py
# Contains O(1) Hash Maps for MEP Auto generation

WIRING_RULES = {
    "living_room": {
        "Basic": ["ceiling_light", "fan", "socket_6a", "socket_6a"],
        "Standard": ["ceiling_light", "ceiling_light", "fan", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "tv_point", "wifi_point"],
        "Premium": ["ceiling_light"]*4 + ["fan"] + ["socket_6a"]*6 + ["tv_point", "wifi_point", "ac_point", "decorative_light"],
        "Smart Home": ["ceiling_light"]*4 + ["fan"] + ["socket_6a"]*6 + ["tv_point", "wifi_point", "ac_point", "decorative_light", "smart_switch", "motion_sensor", "cctv_point", "smart_speaker"]
    },
    "master_bedroom": {
        "Basic": ["ceiling_light", "fan", "socket_6a", "socket_6a"],
        "Standard": ["ceiling_light", "ceiling_light", "fan", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "tv_point"],
        "Premium": ["ceiling_light", "ceiling_light", "fan", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "tv_point", "ac_point", "bedside_light"],
        "Smart Home": ["ceiling_light", "ceiling_light", "fan", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "tv_point", "ac_point", "bedside_light", "smart_switch", "motion_sensor", "smart_curtain"]
    },
    "bedroom": {
        "Basic": ["ceiling_light", "fan", "socket_6a", "socket_6a"],
        "Standard": ["ceiling_light", "ceiling_light", "socket_6a", "socket_6a", "socket_6a", "socket_6a"],
        "Premium": ["ceiling_light", "ceiling_light", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "ac_point", "tv_point", "socket_6a", "socket_6a"],
        "Smart Home": ["ceiling_light", "ceiling_light", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "ac_point", "tv_point", "socket_6a", "socket_6a", "smart_switch"]
    },
    "kitchen": {
        "Basic": ["ceiling_light", "exhaust_fan", "refrigerator_point"],
        "Standard": ["ceiling_light", "exhaust_fan", "refrigerator_point", "microwave_point", "chimney_point"],
        "Premium": ["refrigerator_point", "microwave_point", "chimney_point", "oven_point", "dishwasher_point", "water_purifier_point", "under_cabinet_light"],
        "Smart Home": ["refrigerator_point", "microwave_point", "chimney_point", "oven_point", "dishwasher_point", "water_purifier_point", "under_cabinet_light", "smart_switch", "motion_sensor"]
    },
    "bathroom": {
        "Basic": ["ceiling_light", "exhaust_fan"],
        "Standard": ["ceiling_light", "exhaust_fan", "geyser_point"],
        "Premium": ["ceiling_light", "mirror_light", "exhaust_fan", "geyser_point", "shaver_socket"],
        "Smart Home": ["ceiling_light", "mirror_light", "exhaust_fan", "geyser_point", "shaver_socket", "smart_switch", "motion_sensor"]
    },
    "dining_room": {
        "Basic": ["ceiling_light", "socket_6a", "socket_6a"],
        "Standard": ["chandelier_point", "socket_6a", "socket_6a", "socket_6a", "socket_6a"],
        "Premium": ["decorative_light", "chandelier_point", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a"],
        "Smart Home": ["decorative_light", "chandelier_point", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "socket_6a", "smart_switch"]
    },
    "corridor": {
        "Basic": ["ceiling_light"],
        "Standard": ["ceiling_light", "ceiling_light"],
        "Premium": ["decorative_light", "motion_sensor"],
        "Smart Home": ["decorative_light", "motion_sensor"]
    },
    "staircase": {
        "Basic": ["stair_light"],
        "Standard": ["stair_light", "stair_light"],
        "Premium": ["step_light", "motion_sensor"],
        "Smart Home": ["step_light", "motion_sensor"]
    },
    "balcony": {
        "Basic": ["outdoor_light"],
        "Standard": ["outdoor_light", "socket_6a"],
        "Premium": ["decorative_light", "socket_6a", "fan"],
        "Smart Home": ["decorative_light", "socket_6a", "fan", "smart_switch"]
    },
    "foyer": {
        "Basic": ["ceiling_light"],
        "Standard": ["decorative_light"],
        "Premium": ["decorative_light", "wall_light"],
        "Smart Home": ["decorative_light", "wall_light", "smart_switch"]
    },
    "pooja_room": {
        "Basic": ["ceiling_light"],
        "Standard": ["ceiling_light", "decorative_lamp_point"],
        "Premium": ["decorative_light", "backlit_temple_light"],
        "Smart Home": ["decorative_light", "backlit_temple_light"]
    },
    "store_room": {
        "Basic": ["ceiling_light"],
        "Standard": ["ceiling_light", "socket_6a"],
        "Premium": ["ceiling_light", "ceiling_light", "socket_6a", "socket_6a"],
        "Smart Home": ["ceiling_light", "ceiling_light", "socket_6a", "socket_6a"]
    },
    "utility": {
        "Basic": ["ceiling_light", "washing_machine_point"],
        "Standard": ["ceiling_light", "washing_machine_point", "socket_6a"],
        "Premium": ["ceiling_light", "washing_machine_point", "socket_6a", "socket_6a"],
        "Smart Home": ["ceiling_light", "washing_machine_point", "socket_6a", "socket_6a"]
    }
}

# Fallback for unknown rooms
WIRING_RULES["default"] = {
    "Basic": ["ceiling_light", "socket_6a"],
    "Standard": ["ceiling_light", "socket_6a", "socket_6a"],
    "Premium": ["ceiling_light", "socket_6a", "socket_6a", "socket_6a"],
    "Smart Home": ["ceiling_light", "socket_6a", "socket_6a", "socket_6a", "smart_switch"]
}

# Define High-Load appliances that need a Home Run (no daisy-chaining)
HIGH_LOAD_APPLIANCES = {"geyser_point", "ac_point", "microwave_point", "oven_point", "washing_machine_point", "refrigerator_point", "dishwasher_point"}

ELECTRICAL_METADATA = {
    "ceiling_light": {"load_watts": 12, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "chandelier_point": {"load_watts": 60, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "decorative_light": {"load_watts": 20, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "wall_light": {"load_watts": 12, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "bedside_light": {"load_watts": 10, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "stair_light": {"load_watts": 5, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "step_light": {"load_watts": 5, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "outdoor_light": {"load_watts": 15, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "mirror_light": {"load_watts": 12, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "under_cabinet_light": {"load_watts": 18, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "fan": {"load_watts": 75, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "exhaust_fan": {"load_watts": 35, "circuit": "lighting", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "socket_6a": {"load_watts": 100, "circuit": "general_power", "wire_size": "2.5mm²", "breaker": "MCB-Power"},
    "socket_16a": {"load_watts": 1000, "circuit": "general_power", "wire_size": "2.5mm²", "breaker": "MCB-Power"},
    "shaver_socket": {"load_watts": 20, "circuit": "general_power", "wire_size": "1.5mm²", "breaker": "MCB-Power"},
    "ac_point": {"load_watts": 1500, "circuit": "heavy_power", "wire_size": "4.0mm²", "breaker": "MCB-AC"},
    "geyser_point": {"load_watts": 2000, "circuit": "heavy_power", "wire_size": "4.0mm²", "breaker": "MCB-Geyser"},
    "refrigerator_point": {"load_watts": 300, "circuit": "general_power", "wire_size": "2.5mm²", "breaker": "MCB-Kitchen"},
    "microwave_point": {"load_watts": 1200, "circuit": "heavy_power", "wire_size": "4.0mm²", "breaker": "MCB-Kitchen"},
    "oven_point": {"load_watts": 2000, "circuit": "heavy_power", "wire_size": "4.0mm²", "breaker": "MCB-Kitchen"},
    "chimney_point": {"load_watts": 250, "circuit": "general_power", "wire_size": "2.5mm²", "breaker": "MCB-Kitchen"},
    "water_purifier_point": {"load_watts": 100, "circuit": "general_power", "wire_size": "1.5mm²", "breaker": "MCB-Kitchen"},
    "dishwasher_point": {"load_watts": 1800, "circuit": "heavy_power", "wire_size": "4.0mm²", "breaker": "MCB-Kitchen"},
    "washing_machine_point": {"load_watts": 1500, "circuit": "heavy_power", "wire_size": "4.0mm²", "breaker": "MCB-Utility"},
    "tv_point": {"load_watts": 0, "circuit": "data", "wire_size": "Cat6", "breaker": "None"},
    "wifi_point": {"load_watts": 15, "circuit": "data", "wire_size": "Cat6", "breaker": "None"},
    "smart_switch": {"load_watts": 5, "circuit": "smart", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"},
    "motion_sensor": {"load_watts": 5, "circuit": "smart", "wire_size": "1.5mm²", "breaker": "MCB-Lighting"}
}

PLUMBING_METADATA = {
    "wc": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "indian_toilet": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "western_toilet": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "wash_basin": {"cold_water": True, "hot_water": True, "drain": True, "pipe_size": "15mm"},
    "double_basin": {"cold_water": True, "hot_water": True, "drain": True, "pipe_size": "20mm"},
    "water_sink": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "shower": {"cold_water": True, "hot_water": True, "drain": True, "pipe_size": "20mm"},
    "geyser": {"cold_water": True, "hot_water": True, "drain": False, "pipe_size": "15mm"},
    "bathtub": {"cold_water": True, "hot_water": True, "drain": True, "pipe_size": "20mm"},
    "water_purifier": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "dishwasher_point": {"cold_water": True, "hot_water": True, "drain": True, "pipe_size": "15mm"},
    "washing_machine_water": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "washing_machine_drain": {"cold_water": False, "hot_water": False, "drain": True, "pipe_size": "15mm"},
    "tap_point": {"cold_water": True, "hot_water": False, "drain": True, "pipe_size": "15mm"}
}

PLUMBING_RULES = {
    "kitchen": {
        "Basic": ["water_sink", "cold_water", "drain_line"],
        "Standard": ["water_sink", "cold_water", "drain_line", "water_purifier"],
        "Premium": ["water_sink", "cold_water", "drain_line", "water_purifier", "dishwasher_point", "refrigerator_water_line"],
        "Smart Home": ["water_sink", "cold_water", "drain_line", "water_purifier", "dishwasher_point", "refrigerator_water_line"]
    },
    "bathroom": {
        "Basic": ["wc", "wash_basin", "shower"],
        "Standard": ["wc", "wash_basin", "shower", "geyser"],
        "Premium": ["wc", "wash_basin", "shower", "geyser", "bathtub"],
        "Smart Home": ["wc", "wash_basin", "shower", "geyser", "bathtub"]
    },
    "master_bedroom": { # Since master bathroom is merged
        "Premium": ["double_basin", "shower", "wc", "bathtub"],
        "Smart Home": ["double_basin", "shower", "wc", "bathtub"]
    },
    "utility": {
        "Basic": ["washing_machine_water", "washing_machine_drain"],
        "Standard": ["washing_machine_water", "washing_machine_drain"],
        "Premium": ["washing_machine_water", "washing_machine_drain"],
        "Smart Home": ["washing_machine_water", "washing_machine_drain"]
    },
    "balcony": {
        "Basic": [],
        "Standard": [],
        "Premium": ["tap_point"],
        "Smart Home": ["tap_point"]
    }
}

PLUMBING_RULES["default"] = {
    "Basic": [],
    "Standard": [],
    "Premium": [],
    "Smart Home": []
}

def get_wiring_for_room(room_type: str, package: str) -> list:
    rt = room_type.lower()
    # Normalize some names
    if "bath" in rt or "toilet" in rt:
        rt = "bathroom"
    elif "master" in rt:
        rt = "master_bedroom"
    elif "bed" in rt:
        rt = "bedroom"
    elif "liv" in rt:
        rt = "living_room"
    elif "din" in rt:
        rt = "dining_room"
    elif "kit" in rt:
        rt = "kitchen"
    elif "pooja" in rt:
        rt = "pooja_room"
    elif "store" in rt:
        rt = "store_room"
    elif "foy" in rt:
        rt = "foyer"
    elif "stair" in rt:
        rt = "staircase"
    elif "cor" in rt:
        rt = "corridor"
    elif "bal" in rt:
        rt = "balcony"
    elif "util" in rt or "wash" in rt:
        rt = "utility"
        
    return WIRING_RULES.get(rt, WIRING_RULES["default"]).get(package, WIRING_RULES["default"]["Basic"])

def get_plumbing_for_room(room_type: str, package: str) -> list:
    rt = room_type.lower()
    if "bath" in rt or "toilet" in rt:
        rt = "bathroom"
    elif "master" in rt: # Sometimes master bathroom is special but we'll map to bathroom if not found
        if package in ["Premium", "Smart Home"]:
            rt = "master_bedroom" # use specific rule
        else:
            rt = "bathroom"
    elif "bed" in rt:
        rt = "default"
    elif "kit" in rt:
        rt = "kitchen"
    elif "util" in rt or "wash" in rt:
        rt = "utility"
    elif "bal" in rt:
        rt = "balcony"
    else:
        rt = "default"
        
    return PLUMBING_RULES.get(rt, PLUMBING_RULES["default"]).get(package, PLUMBING_RULES["default"]["Basic"])

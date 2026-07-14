import json
import logging
import requests
import re
import os
import google.generativeai as genai
from typing import Dict, Any

logger = logging.getLogger("homevision")

def _default_extraction_result() -> Dict[str, Any]:
    """Return a minimal default extraction result when LLM is unavailable."""
    return {
        "intent": "CREATE",
        "bhk": 0,
        "style": "",
        "materials": [],
        "target_rooms": [],
        "color_hex": "",
        "theme_description": "",
    }

INDIAN_KEYWORD_MAP = {
    "needs_pooja_room": ["pooja", "puja", "mandir", "temple", "prayer room"],
    "angan":            ["angan", "courtyard", "aangan"],
    "powder_room":      ["powder room", "guest toilet", "half bath", "guest bathroom"],
    "utility_area":     ["utility", "wash area", "laundry", "dhulai", "wet kitchen", "washing area"],
    "foyer":            ["foyer", "entry hall", "entrance hall", "shoe area", "mudroom"],
    "bhandar_ghar":     ["bhandar", "store room", "storage", "pantry"],
    "elderly_suite":    ["elderly", "senior", "grandparent", "ground floor suite", "parents", "in-laws", "dadi"],
    "brahmasthan":      ["brahmasthan", "brahmasthana", "center open", "vastu", "sacred center"],
    "double_height":    ["double height", "double-height", "high ceiling", "tall ceiling"],
    "diwan":            ["diwan", "divan", "daybed", "built-in seating", "baithak"],
    "otta":             ["otta", "thinnai", "sit-out", "front veranda", "porch seating"],
    "portico":          ["portico", "car porch", "car parking", "parking shade"],
    "jharokha":         ["jharokha", "bay window", "oriel", "enclosed balcony", "protruding window"],
    "jali":             ["jali", "jalli", "lattice screen", "breeze blocks", "perforated screen", "mesh wall"],
    "chhajja":          ["chhajja", "chajja", "sunshade", "canopy", "window overhang"],
    "stack_vent":       ["stack ventilation", "stack vent", "wind tower", "roof vent", "wind catcher"],
    "flat_terrace":     ["flat terrace", "open terrace", "terrace garden", "flat roof"],
    "sump_tank":        ["sump", "underground tank", "water sump"],
    "overhead_tank":    ["overhead tank", "sintex", "water tank on roof"],
    "maliya":           ["maliya", "loft", "attic", "overhead storage"],
    "mumty":            ["mumty", "mumti", "staircase cover", "staircase headroom", "stair tower"],
    "parapet":          ["parapet", "parapet wall", "boundary wall on roof"],
}

def scan_for_indian_features(prompt: str) -> Dict[str, bool]:
    """
    Scans the prompt for Indian feature keywords while avoiding negated ones.
    (e.g., 'with a pooja room' -> True, 'but absolutely NO pooja room' -> False)
    """
    features = {}
    lower_prompt = prompt.lower()
    
    for feature_key, keywords in INDIAN_KEYWORD_MAP.items():
        features[feature_key] = False
        for kw in keywords:
            # Look for the keyword
            for match in re.finditer(re.escape(kw), lower_prompt):
                # Grab a snippet of text right before the keyword
                start_idx = max(0, match.start() - 35) # check up to ~35 characters before
                pre_context = lower_prompt[start_idx:match.start()]
                
                # Check for negative words immediately before (the 'Deaf Listener' fix)
                negated = False
                negative_words = ["no ", "without ", "exclude ", "not ", "dont ", "don't ", "except ", "never "]
                for nw in negative_words:
                    # If the negative word is in the immediate prefix, assume negated
                    # Example: "absolutely no pooja room" -> 'no ' is in pre_context
                    # We do a split to ensure the negative word is close.
                    if nw in pre_context:
                        # Make sure it's not too far (e.g., "no bedrooms, but I want a pooja room")
                        # By splitting on punctuation, we keep it in the same clause
                        clause = re.split(r'[.,;!_]|\bbut\b', pre_context)[-1]
                        if nw in clause:
                            negated = True
                            break
                            
                if not negated:
                    features[feature_key] = True
                    break # Found one positive mention, no need to check other keywords for this feature
    return features


def extract_keywords_to_json(user_prompt: str, vocabulary: dict) -> Dict[str, Any]:
    """
    Passes the prompt to Gemini to extract parameters as JSON.
    Falls back to a default result dict when the model is not available.
    """
    # Extract known keys for injection into system prompt
    known_rooms = list(vocabulary.get("rooms", {}).keys())
    known_styles = list(vocabulary.get("styles", {}).keys())
    known_materials = list(vocabulary.get("materials", {}).keys())
    
    system_prompt = f"""You are a strict JSON translation engine.
Read the user's architectural request.
You MUST map their requested styles, materials, and rooms ONLY to the exact words provided in these lists:
ROOMS: {known_rooms}
STYLES: {known_styles}
MATERIALS: {known_materials}

Schema: {{"intent": "CREATE" | "ADD" | "REMOVE" | "RESIZE" | "COLOR" | "MODIFY_MEP" | "MOVE", "bhk": int, "style": str, "materials": [str], "target_rooms": [str], "color_hex": str, "theme_description": str, "move_target_room": str, "move_destination": str, "vastu_specifics": [{{"room": str, "location": str}}], "negative_constraints": [str], "mep_additions": [{{"room": str, "item": str}}], "needs_pooja_room": bool, "utility_area": bool, "powder_room": bool, "elderly_suite": bool, "foyer": bool, "brahmasthan": bool, "angan": bool, "bhandar_ghar": bool, "maliya": bool, "sump_tank": bool, "overhead_tank": bool, "diwan": bool, "otta": bool, "portico": bool, "flat_terrace": bool, "parapet": bool, "mumty": bool, "double_height": bool, "jali": bool, "chhajja": bool, "jharokha": bool, "stack_vent": bool, "facing": "North" | "South" | "East" | "West" | ""}}

Indian feature detection rules (set to true if user mentions):
- needs_pooja_room: "pooja room", "prayer room", "mandir", "temple", "puja", "devghar"
- utility_area: "utility", "wash area", "wet kitchen", "washing area"
- powder_room: "powder room", "guest toilet", "half bath", "guest bathroom"
- elderly_suite: "parents", "elderly", "joint family", "in-laws", "dadi"
- foyer: "foyer", "entry area", "shoe area", "entrance lobby", "mudroom"
- brahmasthan: "brahmasthan", "vastu", "sacred center", "open center"
- angan: "angan", "courtyard", "open courtyard", "internal courtyard"
- bhandar_ghar: "bhandar", "store room", "storage attached to kitchen"
- maliya: "maliya", "loft", "overhead storage", "storage above"
- sump_tank: "sump", "underground tank", "water sump"
- overhead_tank: "sintex", "overhead tank", "water tank on roof"
- diwan: "diwan", "built-in seating", "baithak"
- otta: "otta", "thinnai", "front veranda", "veranda", "porch seating"
- portico: "portico", "car porch", "parking shade", "car parking"
- flat_terrace: "flat terrace", "flat roof", "terrace"
- parapet: "parapet", "boundary wall on roof"
- mumty: "mumty", "staircase headroom", "stair tower"
- double_height: "double height", "tall ceiling", "two story ceiling"
- jali: "jali", "breeze blocks", "perforated screen", "mesh wall"
- chhajja: "chhajja", "sunshade", "window overhang"
- jharokha: "jharokha", "enclosed balcony", "protruding window"
- stack_vent: "stack ventilation", "roof vent", "wind catcher"
- facing: extract from "north facing", "south facing", etc.

Intent rules:
- CREATE: generating a new house (e.g. "Build a 3BHK")
- ADD: adding a room (e.g. "Add a balcony")
- REMOVE: removing a room (e.g. "Remove the study")
- RESIZE: changing room size (e.g. "Make the living room bigger")
- COLOR: changing color or theme (e.g. "Make it ocean blue", "Paint the walls terracotta")
- MODIFY_MEP: adding a specific appliance or fixture to a room (e.g. "Add a geyser to the bathroom", "put a TV in the bedroom", "add rainwater sump"). Set mep_additions with the item and room. Treat any specific object/noun as an MEP item!
- MOVE: moving a room or changing its location (e.g. "move the kitchen to south east", "put the dining next to kitchen"). Set move_target_room and move_destination.

COLOUR VIBE MAP (use these hex codes for the color_hex field):
- ocean blue = #0077be
- neon green = #39ff14
- warm wood = #c8845c
- concrete grey = #8a8f97
- pearl white → #f8f6f0
- midnight black → #1a1a2e
- terracotta → #c85c3a
- sage green → #87a87d
- royal blue → #4169e1
- blush pink → #ffb6c1
- sunflower → #ffd700
- lavender → #e6e6fa
- burnt orange → #cc5500
- teal → #008080
- cream → #fffdd0
- charcoal → #36454f
- rose gold → #b76e79
- mint → #98ff98
- indigo → #4b0082
- copper → #b87333
- ivory → #fffff0
- slate → #708090
- dusty rose → #dcae96
- forest green → #228b22
- cobalt → #0047ab
- warm beige → #f5f5dc
- gunmetal → #2a3439
- marigold → #eaa221
- plum → #dda0dd
- sandstone → #c2b280

If intent is COLOR, set color_hex to the matching hex code from the COLOUR VIBE MAP above, or your best estimation of the hex for the described colour. Set theme_description to a 2-3 word poetic name for the colour.
For non-COLOR intents, set color_hex and theme_description to empty strings "".

Output NOTHING but valid JSON matching this schema. Do not include markdown code blocks.
"""

    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment")
            
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = system_prompt + "\n\nUser Request: " + user_prompt
        
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            )
        )
        content = response.text
        
        # Sometimes models output markdown code blocks despite instructions
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
            
        parsed = json.loads(content.strip())
        
        # Merge the manually scanned features (Deaf Listener fix)
        # This forces features on if mentioned positively, but safely ignores them if negated.
        scanned_features = scan_for_indian_features(user_prompt)
        for feature_key, is_present in scanned_features.items():
            if is_present:
                parsed[feature_key] = True
                
        return parsed
    except Exception as e:
        logger.error(f"Failed to extract via Gemini: {e}")
        return _default_extraction_result()

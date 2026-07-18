import os
import json
import logging
import time
import requests
from typing import Dict, Any, List, Optional, Callable
from pydantic import BaseModel, Field

logger = logging.getLogger("homevision")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))

from geometry_validator import GeometryValidator

# ---------------------------------------------------------------------------
# 1. Pydantic Schemas
# ---------------------------------------------------------------------------
class RoomColor(BaseModel):
    room: str = Field(description="Room name (e.g. 'bedroom') or 'all'")
    color: str = Field(description="The requested color or material")
    surface: str = Field(default="wall", description="One of: wall, floor, furniture, exterior, roof")

class VastuSpecific(BaseModel):
    room: str = Field(description="e.g. entrance, kitchen, master_bedroom")
    location: str = Field(description="e.g. north, east, south, west, north-east")

class Connection(BaseModel):
    target_room: str = Field(description="The room_type this room connects to")
    intent: str = Field(description="Connection style: 'standard' (door) or 'open_flow' (no wall)")

class MepAddition(BaseModel):
    room: str
    item: str
MODIFICATION_SYSTEM_PROMPT = """You are a Spatial Engineer modifying an architectural floor plan.

You will receive the CURRENT state of the layout and a modification request.

## CRITICAL RULES
1. PRESERVE INTENT: Keep existing rooms roughly in their relative positions.
2. TOPOLOGY OVER MATH: Focus on the `connections` array. If adding a new room, you MUST add a connection between the new room and the `corridor` or `living_room` so it is accessible.
3. ROUGH COORDINATES: Provide approximate position_x and position_z for the new room. The downstream CP-Solver will snap everything perfectly flush, so you do NOT need to worry about exact decimal precision or minor overlaps.

Output the COMPLETE updated master_blueprint array in the JSON response matching the BlueprintOnlyResponse schema.
"""

def modify_validated_blueprint(
    prompt: str,
    current_blueprint: list,
    plot_width: float,
    plot_length: float,
) -> Dict[str, Any]:
    try:
        from google import genai
    except ImportError:
        logger.error("google-genai not installed.")
        return {}

    # This uses the new SDK, clearing your deprecation warning for this call
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    user_content = (
        f"Request: {prompt}\n"
        f"CURRENT BLUEPRINT:\n{json.dumps(current_blueprint, indent=2)}\n"
        f"Plot bounds: {plot_width}ft x {plot_length}ft\n"
        f"Update the room roster and connections. Provide approximate coordinates for any new rooms."
    )

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=user_content,
        config=genai.types.GenerateContentConfig(
            system_instruction=MODIFICATION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=BlueprintOnlyResponse,
            temperature=0.1,
        ),
    )

    result = json.loads(response.text)
    logger.info("[GEMINI] Extracted topological blueprint in a single fast pass.")
    return result


class BlueprintRoom(BaseModel):
    room_type: str = Field(description="e.g., master_bedroom, living_room, kitchen, bathroom, corridor")
    floor_number: int = Field(default=0, description="0 for ground floor, 1 for first floor")
    width: float = Field(description="Width of the room in feet (X-axis extent)")
    length: float = Field(description="Length/depth of the room in feet (Z-axis extent)")
    position_x: float = Field(description="Top-left X coordinate of the room in feet")
    position_z: float = Field(description="Top-left Z coordinate of the room in feet")
    min_width: float = Field(default=0.0, description="Minimum acceptable width in feet (e.g. 3.5 for corridors)")
    min_length: float = Field(default=0.0, description="Minimum acceptable length in feet")
    connections: List[Connection] = Field(default_factory=list, description="List of rooms this room flows into")
    color_hex: str = ""
    materials: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)

class HouseDesignRequest(BaseModel):
    intent: str = Field(description="The core action or intent inferred from the user prompt...")
    bhk: int = 0
    floors: int = 1
    style: str = ""
    materials: List[str] = Field(default_factory=list)
    target_rooms: List[str] = Field(default_factory=list)
    
    # --- ZERO HARDCODING: FULL AI ROOM CLASSIFICATION ---
    outdoor_rooms: List[str] = Field(default_factory=list, description="Rooms open to the sky (e.g., courtyard, angan).")
    wet_rooms: List[str] = Field(default_factory=list, description="Rooms requiring plumbing (e.g., bath, toilet, kitchen).")
    circulation_rooms: List[str] = Field(default_factory=list, description="Rooms used for movement (e.g., corridor, hallway, foyer).")
    private_rooms: List[str] = Field(default_factory=list, description="Private personal spaces (e.g., bedrooms).")
    public_rooms: List[str] = Field(default_factory=list, description="Shared communal spaces (e.g., living room, dining room, pooja room).")
    # ----------------------------------------------------
    
    global_color: str = ""
    room_colors: List[RoomColor] = Field(default_factory=list)
    color_hex: str = ""
    theme_description: str = ""
    move_target_room: str = ""
    move_destination: str = ""
    vastu_specifics: List[VastuSpecific] = Field(default_factory=list)
    negative_constraints: List[str] = Field(default_factory=list)
    mep_additions: List[MepAddition] = Field(default_factory=list)
    primary_entry_room_id: str = Field(default="", description="The room_type of the main entrance room")
    front_orientation: str = Field(default="north", description="The plot's street-facing direction")
    facing: str = Field(default="", description="North, South, East, West or empty")

class ProgramRoom(BaseModel):
    room_type: str = Field(description="e.g. master_bedroom, living_room, kitchen, corridor, bathroom")
    min_width: float = Field(description="Minimum width in feet")
    min_length: float = Field(description="Minimum length in feet")
    connections: List[Connection] = Field(default_factory=list, description="Rooms this flows into")

class ProgramResponse(BaseModel):
    program_rationale: str = Field(description="Cultural and architectural reasoning for this specific room mix")
    rooms: List[ProgramRoom] = Field(description="The list of rooms to be built")

class BlueprintOnlyResponse(BaseModel):
    master_blueprint: List[BlueprintRoom] = Field(default_factory=list)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 1.5 Cultural & Architectural Planner Prompt
# ---------------------------------------------------------------------------
CULTURAL_PLANNER_PROMPT = """You are an elite, culturally sensitive international architect.
Analyze the user's text to infer their cultural background, family structure, and architectural style using your vast world knowledge.
Output a strict JSON 'Program' listing the exact rooms to build, their minimum viable sizes (in feet), and their connectivity.

## CRITICAL RULES
1. Do NOT calculate absolute coordinates. You are only defining the roster of rooms.
2. EXACT COUNT HONOURING: You MUST strictly behave as a constraint extractor. If the user explicitly asks for "3 bathrooms", you MUST output exactly 3 bathroom nodes. If they ask for a "dining room", you MUST include one. Do NOT omit requested rooms under any circumstance.
3. ENTRANCE & CIRCULATION: Every home MUST have a 'living_room' acting as the main entrance, or a dedicated 'foyer' that connects to the living_room. ALWAYS include a 'corridor' if there are more than 3 rooms.
4. ZONING & PRIVACY: Bedrooms MUST NOT connect directly to the living_room or dining_room. Bedrooms must connect to a 'corridor'.
5. WET ZONES: Ensure every bedroom connects to a bathroom. Do not connect bathrooms directly to living rooms or dining rooms (use a corridor).
6. VERTICAL CIRCULATION: If the prompt implies multiple floors (e.g. "duplex", "stairs", "two-story"), you MUST include a 'staircase' room.
7. For connections, use intent 'open_flow' for open-plan areas (e.g., living to dining), and 'standard' for doors.
"""

def generate_cultural_program(prompt: str, emit_fn: Callable = None) -> dict:
    """Stage 1: Dynamic Cultural & Architectural Planner."""
    try:
        from google import genai
    except ImportError:
        return {}

    client = genai.Client(api_key=GEMINI_API_KEY)
    if emit_fn:
        emit_fn({"stage": 1, "label": "AI Planning Requirements...", "substage": "Inferring cultural context and room program..."})
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=f"User request: {prompt}",
        config=genai.types.GenerateContentConfig(
            system_instruction=CULTURAL_PLANNER_PROMPT,
            response_mime_type="application/json",
            response_schema=ProgramResponse,
            temperature=0.3,
        ),
    )
    import json
    program = json.loads(response.text)
    
    # Post-process to strictly enforce deduplication (LLMs often hallucinate extras)
    prompt_lower = prompt.lower()
    singleton_types = ['living_room', 'dining_room', 'kitchen', 'foyer']
    seen_types = set()
    deduped_rooms = []
    
    for r in program.get('rooms', []):
        rtype = r.get('type')
        if rtype in singleton_types:
            # Check if user explicitly asked for multiple of this room type
            rtype_clean = rtype.replace('_', ' ')
            has_multiple = any(f"{n} {rtype_clean}" in prompt_lower for n in ["2", "two", "3", "three", "multiple", "double"])
            if not has_multiple and rtype in seen_types:
                continue # Skip duplicate
            seen_types.add(rtype)
        deduped_rooms.append(r)
        
    program['rooms'] = deduped_rooms
    return program

# 2. Chain-of-Thought Architect Prompt
# ---------------------------------------------------------------------------
ARCHITECT_SYSTEM_PROMPT = """You are a spatial engineer and geometric layout generator.

## CRITICAL RULES
1. You will be provided a locked-in "Program" of rooms. DO NOT alter the program or add/remove rooms.
2. Your ONLY job is to calculate the precise min_x, max_x, min_z, max_z (position_x, position_z, width, length) for those specific rooms.
3. All rooms must fit exactly within plot_width and plot_length.
4. If placing horizontally, Room.position_x = previous_room.position_x + previous_room.width.
5. If placing vertically, Room.position_z = previous_room.position_z + previous_room.length.
6. PREVENT GAPS: Adjacent rooms MUST be perfectly flush. If Room A ends at x=10, Room B must start EXACTLY at x=10. Do not leave 0.5ft gaps, otherwise the physics engine will create double walls.

## ROOM SIZE GUIDELINES (in feet)
- Master Bedroom: 14x12 to 16x14
- Bedroom: 12x10 to 14x12
- Living Room: 16x14 to 20x16
- Kitchen: 10x10 to 12x12
- Bathroom: 5x5 to 8x8
- Dining Room: 12x10 to 14x12
- Study Room: 8x8 to 10x10
- Pooja Room: 5x5 to 6x6
- Corridor: 4xN (N = buildable length)
- Staircase: 8x10 to 10x12

## COLOR HANDLING
- If the user specifies a room color (e.g., "blue bedroom"), set that room's color_hex to the appropriate hex code.
- If the user specifies a global house color, set ALL rooms' color_hex to that hex.
- Common hex codes: red=#ef4444, blue=#3b82f6, green=#22c55e, yellow=#eab308, pink=#ec4899, white=#ffffff, black=#1e1e1e, orange=#f97316, purple=#a855f7

## OUTPUT FORMAT
You MUST output valid JSON matching the BlueprintOnlyResponse schema. The master_blueprint array must contain every room with exact position_x, position_z, width, length, doors[], and windows[].
"""

CORRECTION_SYSTEM_PROMPT = """You are a Master Architect AI correcting a flawed floor plan.

Your PREVIOUS output had the following GEOMETRY ERRORS detected by the AABB Collision Detection system:

{errors}

## CORRECTION INSTRUCTIONS
1. Read each error carefully. It tells you EXACTLY which rooms overlap, which doors are misaligned, or which rooms are unreachable.
2. Use the arithmetic scratchpad to recalculate the coordinates.
3. Fix ONLY the problematic coordinates. Do not change rooms that passed validation.
4. Ensure ALL rooms still fit within the plot boundary (0,0) to ({plot_width},{plot_length}).
5. Verify: For every pair of adjacent rooms A and B:
   - A.position_x + A.width == B.position_x (horizontal adjacency) OR
   - A.position_z + A.length == B.position_z (vertical adjacency)
6. Verify: Every door sits exactly on a shared wall boundary.

Output the COMPLETE corrected master_blueprint JSON. Do not omit any rooms.
"""

# ---------------------------------------------------------------------------
# 3. Gemini Master Blueprint Generator
# ---------------------------------------------------------------------------
def generate_master_blueprint(
    prompt: str,
    program: dict,
    plot_width: float,
    plot_length: float,
    floors: int = 1,
    facing: str = "",
    corrections: Optional[List[str]] = None,
    emit_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Call Gemini to generate a complete master blueprint with exact coordinates."""
    try:
        from google import genai
    except ImportError:
        logger.error("google-genai not installed. Cannot generate master blueprint.")
        return {}

    client = genai.Client(api_key=GEMINI_API_KEY)

    if corrections:
        # Self-correction pass
        error_text = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(corrections))
        sys_prompt = CORRECTION_SYSTEM_PROMPT.format(
            errors=error_text,
            plot_width=plot_width,
            plot_length=plot_length,
        )
        user_content = (
            f"Original request: {prompt}\n"
            f"Plot: {plot_width}ft x {plot_length}ft, Floors: {floors}, Facing: {facing or 'Any'}\n\n"
            f"Please fix the errors listed in the system prompt and output the corrected master_blueprint."
        )
        if emit_fn:
            emit_fn({"stage": 3, "label": "Generating Room Layout...",
                      "substage": f"Correction needed — asking Gemini to fix {len(corrections)} error(s)..."})
    else:
        # First-pass generation
        sys_prompt = ARCHITECT_SYSTEM_PROMPT
        user_content = (
            f"Design a house floor plan for the following request:\n"
            f"  \"{prompt}\"\n\n"
            f"LOCKED PROGRAM:\n{json.dumps(program)}\n\n"
            f"Plot dimensions: {plot_width}ft wide x {plot_length}ft deep\n"
            f"Number of floors: {floors}\n"
            f"Facing direction: {facing or 'Any'}\n\n"
            f"Calculate exact coordinates before outputting JSON. "
            f"All rooms must fit within (0,0) to ({plot_width},{plot_length}). "
            f"Output the full BlueprintOnlyResponse JSON with a populated master_blueprint array."
        )
        if emit_fn:
            emit_fn({"stage": 3, "label": "Generating Room Layout...",
                      "substage": "Gemini is calculating room coordinates..."})

    t0 = time.time()
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=user_content,
        config=genai.types.GenerateContentConfig(
            system_instruction=sys_prompt,
            response_mime_type="application/json",
            response_schema=BlueprintOnlyResponse,
            temperature=0.2,
        ),
    )
    elapsed = time.time() - t0
    logger.info(f"[GEMINI] Blueprint generated in {elapsed:.2f}s")

    result = json.loads(response.text)
    logger.info(f"[GEMINI] Received {len(result.get('master_blueprint', []))} rooms in blueprint")
    return result


# ---------------------------------------------------------------------------
# 4. Validated Blueprint Generator (Self-Correction Loop)
# ---------------------------------------------------------------------------

def generate_validated_blueprint(
    prompt: str,
    plot_width: float,
    plot_length: float,
    floors: int = 1,
    facing: str = "",
    max_retries: int = 0,
    emit_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    
    # STAGE 1: Cultural Program
    program = generate_cultural_program(prompt, emit_fn=emit_fn)
    logger.info(f"[PLANNER] Program generated with {len(program.get('rooms', []))} rooms.")
    
    corrections = None

    for attempt in range(max_retries + 1):
        logger.info(f"[ARCHITECT] Attempt {attempt + 1}/{max_retries + 1}")

        # Call Gemini (STAGE 2)
        result = generate_master_blueprint(
            prompt, program, plot_width, plot_length, floors, facing,
            corrections=corrections,
            emit_fn=emit_fn,
        )

        blueprint = result.get("master_blueprint", [])
        if not blueprint:
            logger.warning("[ARCHITECT] Gemini returned empty master_blueprint. Retrying...")
            corrections = ["You returned an EMPTY master_blueprint array. You MUST populate it with room coordinates."]
            continue

        # Convert Pydantic-style dicts for the validator
        bp_dicts = []
        for room in blueprint:
            if isinstance(room, dict):
                bp_dicts.append(room)
            else:
                bp_dicts.append(room.dict() if hasattr(room, 'dict') else dict(room))

        # Validate
        if emit_fn:
            emit_fn({"stage": 3, "label": "Generating Room Layout...",
                      "substage": f"Validating geometry (attempt {attempt + 1})..."})

        validation = GeometryValidator.validate(bp_dicts, plot_width, plot_length)

        if validation.is_valid:
            logger.info(f"[ARCHITECT] Blueprint PASSED validation on attempt {attempt + 1}!")
            if emit_fn:
                emit_fn({"stage": 3, "label": "Generating Room Layout...",
                          "substage": "Geometry validated! All rooms perfectly placed."})
            return result

        # Validation failed — prepare correction prompt
        logger.warning(
            f"[ARCHITECT] Validation FAILED on attempt {attempt + 1}: "
            f"{len(validation.errors)} error(s). Errors: {validation.errors[:5]}"
        )
        corrections = validation.errors

    # Exhausted all retries
    logger.error(f"[ARCHITECT] Exhausted {max_retries + 1} attempts. Last errors: {corrections}")
    raise RuntimeError(
        f"Gemini blueprint failed geometry validation after {max_retries + 1} attempts. "
        f"Last errors: {corrections}"
    )


# ---------------------------------------------------------------------------
# 5. QueryRouter (Fast Lane / Heavy Lane) — retained for modifications
# ---------------------------------------------------------------------------
class QueryRouter:
    @staticmethod
    def _is_heavy_reasoning(prompt: str, current_floorplan: Optional[dict]) -> bool:
        heavy_keywords = ["redesign", "optimize", "rearrange", "remodel", "evaluate", "complex"]
        prompt_lower = prompt.lower()
        if any(kw in prompt_lower for kw in heavy_keywords):
            return True
        if current_floorplan and len(str(current_floorplan)) > 1000:
            return True
        return False

    @staticmethod
    def _heavy_lane_gemini(user_prompt: str, vocabulary: dict, current_floorplan: Optional[dict] = None) -> Dict[str, Any]:
        """Gemini 1.5 Flash Heavy Reasoning Lane with Pydantic JSON Schema enforcement"""
        logger.info("[ROUTER] Routing to Gemini Heavy Lane (1M+ context & Native Schema)")
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            sys_prompt = f"You are a strict JSON translation engine for architectural layouts. ROOMS: {list(vocabulary.get('rooms', {}).keys())}"
            if current_floorplan:
                user_content = f"Current State: {json.dumps(current_floorplan)}\nRequest: {user_prompt}"
            else:
                user_content = user_prompt
                
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_content,
                config=genai.types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    response_mime_type="application/json",
                    response_schema=HouseDesignRequest,
                    temperature=0.1
                )
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Gemini Extraction Failed: {e}")
            return {"intent": "CREATE", "target_rooms": [], "bhk": 0, "style": "", "materials": []}

    @staticmethod
    def _fast_lane_groq(user_prompt: str, vocabulary: dict) -> Dict[str, Any]:
        """Groq LLaMA 3.1 8B Fast Lane for sub-100ms response"""
        known_rooms = list(vocabulary.get("rooms", {}).keys())
        known_styles = list(vocabulary.get("styles", {}).keys())
        known_materials = list(vocabulary.get("materials", {}).keys())
        
        system_prompt = f"""You are a strict JSON translation engine for architectural layouts.
Read the user's architectural request.
Map styles, materials, and rooms ONLY to these exact words:
ROOMS: {known_rooms}
STYLES: {known_styles}
MATERIALS: {known_materials}
"room_colors": [{"room": str, "color": str, "surface": "wall" | "floor" | "furniture" | "exterior" | "roof"}],
Schema: {{"intent": "CREATE" | "ADD" | "REMOVE" | "RESIZE" | "COLOR" | "MODIFY_MEP" | "MOVE", "bhk": int, "floors": int, "style": str, "materials": [str], "target_rooms": [str], "global_color": str, "room_colors": [{{"room": str, "color": str}}], "color_hex": str, "theme_description": str, "move_target_room": str, "move_destination": str, "vastu_specifics": [{{"room": str, "location": str}}], "negative_constraints": [str], "mep_additions": [{{"room": str, "item": str}}], "needs_pooja_room": bool, "utility_area": bool, "powder_room": bool, "elderly_suite": bool, "foyer": bool, "brahmasthan": bool, "angan": bool, "bhandar_ghar": bool, "maliya": bool, "sump_tank": bool, "overhead_tank": bool, "diwan": bool, "otta": bool, "portico": bool, "flat_terrace": bool, "parapet": bool, "mumty": bool, "double_height": bool, "jali": bool, "chhajja": bool, "jharokha": bool, "stack_vent": bool, "facing": "North" | "South" | "East" | "West" | ""}}

If 'duplex' or 'two story' is mentioned, interpret floors as 2.
Output ONLY valid JSON. No markdown code blocks."""
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=5)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    @classmethod
    def route(cls, user_prompt: str, vocabulary: dict, current_floorplan: Optional[dict] = None) -> Dict[str, Any]:
        """Traffic cop routing logic."""
        logger.info("[ROUTER] Routing directly to Gemini Heavy Lane (Groq disabled)")
        return cls._heavy_lane_gemini(user_prompt, vocabulary, current_floorplan)

# -------------------------------------------------------------
# Backwards Compatible Wrappers for server.py
# -------------------------------------------------------------
def extract_keywords_groq(user_prompt: str, vocabulary: dict) -> Dict[str, Any]:
    result = QueryRouter.route(user_prompt, vocabulary)
    
    # Post-process to rigorously enforce deduplication and logic rules
    prompt_lower = user_prompt.lower()
    singleton_types = {'living_room', 'dining_room', 'kitchen', 'foyer'}
    seen_types = set()
    deduped_rooms = []
    
    target_rooms = result.get('target_rooms', [])
    if isinstance(target_rooms, list):
        for rtype in target_rooms:
            if rtype in singleton_types:
                # Allow duplicates only if explicitly requested
                rtype_clean = rtype.replace('_', ' ')
                has_multiple = any(f"{n} {rtype_clean}" in prompt_lower for n in ["2", "two", "3", "three", "multiple", "double"])
                if not has_multiple and rtype in seen_types:
                    continue # Strip hallucinated duplicate
                seen_types.add(rtype)
            deduped_rooms.append(rtype)
            
        # Ensure a master bedroom exists if bedrooms are present
        if 'master_bedroom' not in deduped_rooms and 'bedroom' in deduped_rooms:
            bed_idx = deduped_rooms.index('bedroom')
            deduped_rooms[bed_idx] = 'master_bedroom'
            
        result['target_rooms'] = deduped_rooms
        
    return result

def reason_modifications_deepseek(user_prompt: str, current_floorplan: dict) -> dict:
    return QueryRouter.route(user_prompt, {}, current_floorplan)

def auto_wire_topology(room_types: list, ai_categories: dict = None) -> list:
    """Takes a list of room strings and wires connections using AI-provided categories."""
    if not room_types:
        return []
        
    if not ai_categories:
        # Intelligent fallback categorization
        ai_categories = {
            "outdoor_rooms": [r for r in room_types if any(kw in r.lower() for kw in ("courtyard", "parking", "garden", "pool", "deck", "patio", "balcony", "porch", "veranda"))],
            "wet_rooms": [r for r in room_types if any(kw in r.lower() for kw in ("bath", "toilet", "powder", "laundry", "wash"))],
            "circulation_rooms": [r for r in room_types if any(kw in r.lower() for kw in ("corridor", "hallway", "staircase", "foyer"))],
            "private_rooms": [r for r in room_types if any(kw in r.lower() for kw in ("bed", "master", "closet", "study", "office"))],
            "public_rooms": [r for r in room_types if any(kw in r.lower() for kw in ("living", "kitchen", "dining", "lounge", "family", "drawing"))],
        }
    
    # Normalize AI sets for fast lookup
    outdoor_set = {r.replace(" ", "_").lower() for r in ai_categories.get("outdoor_rooms", [])}
    wet_set = {r.replace(" ", "_").lower() for r in ai_categories.get("wet_rooms", [])}
    circ_set = {r.replace(" ", "_").lower() for r in ai_categories.get("circulation_rooms", [])}
    private_set = {r.replace(" ", "_").lower() for r in ai_categories.get("private_rooms", [])}
    public_set = {r.replace(" ", "_").lower() for r in ai_categories.get("public_rooms", [])}

    room_specs = [{"type": r, "connections": []} for r in room_types]
    
    circulation_idx, outdoor_idx, wet_idx, private_idx, public_idx = [], [], [], [], []
    
    # Phase 1: Pure AI Classification & Role Assignment
    for i, r in enumerate(room_specs):
        rt = r['type'].lower()
        
        if rt in outdoor_set:
            outdoor_idx.append(i)
            r['role'] = {'traffic': 'high', 'can_be_passage': True}
        elif rt in circ_set:
            circulation_idx.append(i)
            r['role'] = {'traffic': 'high', 'can_be_passage': True}
        elif rt in private_set:
            private_idx.append(i)
            r['role'] = {'traffic': 'low', 'can_be_passage': False}
        elif rt in wet_set:
            wet_idx.append(i)
            r['role'] = {'traffic': 'low', 'can_be_passage': False}
        else:
            # Default to public zone if it isn't private, wet, or outdoor
            public_idx.append(i)
            r['role'] = {'traffic': 'medium', 'can_be_passage': True}

    def add_conn(src_idx, target_idx, intent, weight):
        room_specs[src_idx]['connections'].append({
            "target_room": room_specs[target_idx]['type'],
            "intent": intent,
            "weight": weight
        })

    # Phase 2: Dynamic Topology Wiring based on AI Bins
    # 1. Chain Public Zones together (Open Concept Flow)
    for i in range(len(public_idx) - 1):
        add_conn(public_idx[i], public_idx[i+1], "open_flow", 10)

    # 2. Determine Primary Hub for Circulation
    hub_idx = circulation_idx[0] if circulation_idx else (public_idx[0] if public_idx else 0)

    # Connect the primary public room to the hub if they are distinct
    if public_idx and hub_idx not in public_idx:
        add_conn(public_idx[0], hub_idx, "open_flow", 10)

    # 3. Connect Outdoor Spaces to the Hub
    for oi in outdoor_idx:
        if hub_idx != oi:
            add_conn(hub_idx, oi, "open_flow", 10)

    # 4. Connect Private Zones to the Hub
    for pi in private_idx:
        if hub_idx != pi:
            add_conn(pi, hub_idx, "standard", 10)

    # 5. Distribute Wet Zones (Bathrooms)
    available_baths = list(wet_idx)
    
    # En-suite priority: Give a bath to each private room first
    for pi in private_idx:
        if available_baths:
            bath_i = available_baths.pop(0)
            add_conn(pi, bath_i, "standard", 10)

    # Remaining wet zones act as common baths connected to the hub
    for bath_i in available_baths:
        if hub_idx != bath_i:
            add_conn(hub_idx, bath_i, "standard", 6)

    return room_specs
    def add_conn(src_idx, target_idx, intent, weight):
        room_specs[src_idx]['connections'].append({
            "target_room": room_specs[target_idx]['type'],
            "intent": intent,
            "weight": weight
        })

    # Phase 2: Dynamic Topology Wiring

    # 1. Chain Public Zones together (Open Concept Flow)
    for i in range(len(public_idx) - 1):
        add_conn(public_idx[i], public_idx[i+1], "open_flow", 10)

    # 2. Determine Primary Hub for Circulation
    hub_idx = circulation_idx[0] if circulation_idx else (public_idx[0] if public_idx else 0)

    # 3. Connect Outdoor Spaces to the Hub
    for oi in outdoor_idx:
        if hub_idx != oi:
            add_conn(hub_idx, oi, "open_flow", 10)

    # 4. Connect Private Zones to the Hub
    for pi in private_idx:
        if hub_idx != pi:
            add_conn(pi, hub_idx, "standard", 10)

    # 5. Distribute Wet Zones (Bathrooms)
    available_baths = list(wet_idx)
    
    # En-suite priority: Give a bath to each private room first
    for pi in private_idx:
        if available_baths:
            bath_i = available_baths.pop(0)
            add_conn(pi, bath_i, "standard", 10)

    # Remaining wet zones act as common baths connected to the hub
    for bath_i in available_baths:
        if hub_idx != bath_i:
            add_conn(hub_idx, bath_i, "standard", 6)

    return room_specs
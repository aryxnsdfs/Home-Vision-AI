import os
import json
import logging
import requests
from typing import Dict, Any

logger = logging.getLogger("homevision")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "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")

def extract_keywords_groq(user_prompt: str, vocabulary: dict) -> Dict[str, Any]:
    """
    Path A (Initial Generation): Routed to Groq (Llama 3.1 8B Instant) 
    for sub-300ms structured text-to-JSON parsing.
    """
    known_rooms = list(vocabulary.get("rooms", {}).keys())
    known_styles = list(vocabulary.get("styles", {}).keys())
    known_materials = list(vocabulary.get("materials", {}).keys())
    
    system_prompt = f"""You are a strict JSON translation engine for architectural layouts.
Read the user's architectural request.
Map styles, materials, and rooms ONLY to these exact words:
ROOMS: {known_rooms}
STYLES: {known_styles}
MATERIALS: {known_materials}

Schema: {{"intent": "CREATE" | "ADD" | "REMOVE" | "RESIZE" | "COLOR" | "MODIFY_MEP" | "MOVE", "bhk": int, "style": str, "materials": [str], "target_rooms": [str], "color_hex": str, "theme_description": str, "move_target_room": str, "move_destination": str, "vastu_specifics": [{{"room": str, "location": str}}], "negative_constraints": [str], "mep_additions": [{{"room": str, "item": str}}], "needs_pooja_room": bool, "utility_area": bool, "powder_room": bool, "elderly_suite": bool, "foyer": bool, "brahmasthan": bool, "angan": bool, "bhandar_ghar": bool, "maliya": bool, "sump_tank": bool, "overhead_tank": bool, "diwan": bool, "otta": bool, "portico": bool, "flat_terrace": bool, "parapet": bool, "mumty": bool, "double_height": bool, "jali": bool, "chhajja": bool, "jharokha": bool, "stack_vent": bool, "facing": "North" | "South" | "East" | "West" | ""}}

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
    
    try:
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.error(f"Groq Extraction Failed: {e}")
        return {"intent": "CREATE", "target_rooms": [], "bhk": 0, "style": "", "materials": []}

def reason_modifications_deepseek(user_prompt: str, current_floorplan: dict) -> dict:
    """
    Path B (Complex Modifications): Routed to OpenRouter (DeepSeek-R1)
    for advanced spatial reasoning, modification logic, and design suggestions.
    """
    system_prompt = """You are an advanced architectural layout reasoning engine.
The user wants to modify their floorplan.
You will be given the current JSON layout state (FloorPlan JSON) and the user's prompt.
Your job is to return the MODIFIED layout structure reflecting the user's request. 
Preserve the FloorPlan JSON schema structure strictly.
Output ONLY valid JSON."""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek/deepseek-r1",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Current Layout State:\n{json.dumps(current_floorplan)}\n\nUser Request: {user_prompt}"}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }
    
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.error(f"DeepSeek Reasoning Failed: {e}")
        return current_floorplan

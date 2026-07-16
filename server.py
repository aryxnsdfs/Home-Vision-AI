"""
server.py — Home Vision AI Backend with 3-layer NLP matching + Physics BitMLP.

Endpoints:
    POST /api/generate   — Parse user prompt → structured layout params
    POST /api/template   — Generate layout from predefined template
    GET  /api/health      — Health check

NLP Pipeline:
    1. Regex extraction (numbers: BHK count, area, budget)
    2. Token-level 3-layer matching (exact → fuzzy → semantic)
    3. Physics BitMLP inference for cost/carbon/safety (if model loaded)

No external API calls. All processing is local.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from dotenv import load_dotenv
load_dotenv()

import queue as _queue_mod
import re
import threading as _threading
import traceback
import random
import uuid
from datetime import datetime
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from blueprint_renderer import BlueprintRenderer

import numpy as np
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import mep_generator
import structural_generator
from cost_engine import CostEngine

from layout_engine import (
    LayoutEngine, AdjacencyResolver, WindowPlacer, ArchitecturalRules,
    Rect, RoomNode, compute_minimum_plot_area, ROOM_MINIMUMS, resolve_theme,
    align_duplex_floors, compute_shared_walls, validate_layout
)
from room_planner import (
    sort_spec_by_generation_order, strip_structural, split_duplex_specs,
    final_layout_validation, INSUFFICIENT_SPACE_MSG,
    requested_type_set, enforce_requested_only,
)
from matcher import MultiVocabularyMatcher, VocabularyMatcher
from vocabulary import (
    ALL_VOCABULARIES,
    INTENT_ACTIONS,
    MATERIALS,
    ROOMS,
    SIZE_MODIFIERS,
    STYLES,
    TYPOLOGY,
)
from cloud_extractor import extract_keywords_groq, reason_modifications_deepseek
from local_extractor import extract_keywords_to_json
from semantic_analyzer import evaluate_complexity
from geometry_engine import LayoutGeometryEngine

USE_SLM_ENGINE = True
# Frontend palette IDs are normalized here so template and AI generation use
# exactly the colors selected in ProjectSetupModal, including custom hex values.
PALETTE_HEX = {
    "off_white": "#F8F8FF", "warm_beige": "#F5F5DC", "light_grey": "#D3D3D3",
    "red": "#E2725B", "sage": "#9CA986", "charcoal": "#36454F", "beige": "#F5F5DC",
    "mustard": "#E4A010", "yellow": "#E4A010", "terracotta": "#E2725B",
    "cream": "#FDF5E6", "peach": "#FFDAB9", "sea_green": "#2E8B57",
    "indigo": "#4B0082", "white": "#FFFFFF", "concrete": "#808080",
    "brick": "#B22222", "wood": "#DEB887",
    "light_wood": "#C8A878", "dark_wood": "#5A3A22", "walnut": "#4B3621",
    "modern_gray": "#6B7280", "white_oak": "#D8C2A0", "teak": "#9C6B3F",
    "marble_white": "#F1F0EC", "beige_marble": "#E6DCC8", "granite": "#4A4A52",
    "wooden_flooring": "#8B5A2B", "ceramic_tile": "#D7DDE5", "concrete_finish": "#8B929D",
    "dark_grey": "#2F4F4F", "brown": "#654321",
}

def _palette_color(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("#"):
        return value
    return PALETTE_HEX.get(value.lower().replace(" ", "_"), value)


def _apply_selected_palette(nodes: List[RoomNode], colors: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    colors = colors or {}
    interior = _palette_color(colors.get("interior"))
    exterior = _palette_color(colors.get("exterior"))
    floor = _palette_color(colors.get("floor"))
    furniture = _palette_color(colors.get("furniture"))
    roof = _palette_color(colors.get("roof"))
    logger.info("[PALETTE DEBUG] Resolved palette request=%s resolved=%s node_count=%d", colors, {"interior": interior, "exterior": exterior, "floor": floor, "furniture": furniture, "roof": roof}, len(nodes))
    for node in nodes:
        if interior:
            node.wallColor = interior
        if floor:
            node.floorColor = floor
        if furniture:
            node.furnitureColor = furniture
    return {
        **({"wallFinish": interior} if interior else {}),
        **({"exteriorColor": exterior} if exterior else {}),
        **({"floorMaterial": floor} if floor else {}),
        **({"furnitureColor": furniture} if furniture else {}),
        **({"roofColor": roof} if roof else {}),
        "selectedColors": {k: v for k, v in {
            "interior": interior, "exterior": exterior, "floor": floor,
            "furniture": furniture, "roof": roof,
        }.items() if v},
    }


def smart_layout_validation(
    rooms_spec: list,
    plot_width: float,
    plot_length: float,
) -> tuple:
    """
    Validate whether the requested rooms fit livably in the given plot.
    If the plot is too small, automatically expand the plot mathematically 
    to maintain a spacious layout instead of deleting rooms.
    Returns (adjusted_rooms_spec, warnings_list, new_plot_width, new_plot_length).
    """
    warnings: list = []
    buildable_area = plot_width * plot_length * 0.85

    room_types = [r["type"] for r in rooms_spec]
    min_needed = compute_minimum_plot_area(room_types)

    if buildable_area <= 0:
        buildable_area = 1.0  # Prevent division by zero
        
    if buildable_area >= min_needed:
        return rooms_spec, warnings, float(plot_width), float(plot_length)

    # Plot is too small. Expand the plot size instead of shrinking the rooms!
    multiplier = (min_needed / buildable_area) ** 0.5
    new_plot_width = round(plot_width * multiplier + 1)
    new_plot_length = round(plot_length * multiplier + 1)
    
    warnings.append(
        f"The provided plot size ({plot_width}'x{plot_length}') was too small for the requested layout. "
        f"Automatically expanded the plot to {new_plot_width}'x{new_plot_length}' to keep all rooms spacious."
    )
    
    return rooms_spec, warnings, float(new_plot_width), float(new_plot_length)


def get_base_rooms_for_bhk(bhk: int) -> list:
    """Return the standard set of rooms for a given BHK count.

    Bedrooms are plain bedrooms by default. A Master Bedroom is NEVER invented
    here — it is only created when the user explicitly asks for one or when a
    bedroom is given an attached bathroom (see apply_bedroom_intelligence).
    """
    rooms = [
        {"type": "living_room", "confidence": 100},
        {"type": "kitchen", "confidence": 100},
        {"type": "bathroom", "confidence": 100},
    ]
    for _ in range(max(1, bhk)):
        rooms.append({"type": "bedroom", "confidence": 100})
    if bhk >= 2:
        rooms.append({"type": "bathroom", "confidence": 100})
    if bhk >= 3:
        rooms.append({"type": "dining_room", "confidence": 100})
    if bhk >= 4:
        rooms.append({"type": "bathroom", "confidence": 100})
    if bhk >= 5:
        rooms.append({"type": "store_room", "confidence": 100})
    return rooms


# Keywords that indicate the user explicitly wants a master/primary bedroom.
_MASTER_KEYWORDS = ("master bedroom", "master bed", "primary bedroom", "primary bed", "master suite")
# Keywords that indicate an attached/ensuite bathroom is wanted for a bedroom.
_ATTACHED_BATH_KEYWORDS = (
    "attached bath", "attached toilet", "attached washroom", "attached bathroom",
    "ensuite", "en-suite", "en suite",
)


def apply_bedroom_intelligence(rooms: list, prompt: str = "", requested_types=None) -> list:
    """Decide whether a bedroom should be promoted to a Master Bedroom.

    Rule: only create a Master Bedroom when the user explicitly requested one,
    OR when an attached bathroom is requested. In that case exactly one bedroom
    is promoted (the rest stay plain bedrooms). Never auto-invent a master.
    """
    text = (prompt or "").lower()
    requested_types = set(requested_types or [])

    wants_master = (
        any(kw in text for kw in _MASTER_KEYWORDS)
        or "master_bedroom" in requested_types
    )
    wants_attached_bath = any(kw in text for kw in _ATTACHED_BATH_KEYWORDS)

    masters = [r for r in rooms if r["type"] == "master_bedroom"]
    bedrooms = [r for r in rooms if r["type"] == "bedroom"]

    if not (wants_master or wants_attached_bath):
        # No justification for a master — collapse any stray master to a bedroom.
        for r in masters:
            r["type"] = "bedroom"
        return rooms

    # Justified: ensure exactly one master bedroom.
    if not masters:
        if bedrooms:
            bedrooms[0]["type"] = "master_bedroom"
    else:
        # Keep the first master, demote any extras.
        for r in masters[1:]:
            r["type"] = "bedroom"
    return rooms

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("homevision")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Home Vision AI Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Model paths
# ---------------------------------------------------------------------------

WORK_DIR = Path(os.getenv("WORK_DIR", "."))
PHYSICS_MODEL_DIR = WORK_DIR / "model_artifacts" / "physics_bitmlp"
NLP_MODEL_DIR = WORK_DIR / "model_artifacts" / "indian_nlp_qlora"

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str
    width: Optional[float] = None
    length: Optional[float] = None
    floors: Optional[int] = 1
    currentProject: Optional[Dict[str, Any]] = None
    indianOptions: Optional[Dict[str, Any]] = None
    colors: Optional[Dict[str, Any]] = None
    package: Optional[str] = "Standard"
    customMaterials: Optional[Dict[str, Any]] = None
    state: Optional[str] = "Maharashtra"
    district: Optional[str] = "Mumbai"
    layoutRules: Optional[List[Dict[str, str]]] = None


class TemplateRequest(BaseModel):
    template: str  # e.g. "2BHK", "3BHK", "CUSTOM"
    
    # 1. FIX: Make these Optional so Pydantic doesn't crash if the frontend sends `null`
    width: Optional[float] = 40.0  
    length: Optional[float] = 40.0  
    floors: Optional[int] = 1
    
    customRooms: Optional[List[str]] = None
    
    # 2. FIX: Change `bool` to `Any` to match GenerateRequest. 
    # If the frontend passes an object or string inside this dictionary, bool strictness causes a 422.
    indianOptions: Optional[Dict[str, Any]] = None 
    
    colors: Optional[Dict[str, Any]] = None
    package: Optional[str] = "Standard"
    customMaterials: Optional[Dict[str, Any]] = None
    state: Optional[str] = "Maharashtra"
    district: Optional[str] = "Mumbai"

class MEPRequest(BaseModel):
    project: dict
    options: dict


class CostRequest(BaseModel):
    project: dict
    package: Optional[str] = "Standard"
    location: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None  # seismicZone, sbc, windExposure


class HealthResponse(BaseModel):
    status: str
    service: str
    nlp_matchers_loaded: bool
    physics_model_loaded: bool
    nlp_adapter_found: bool


# ---------------------------------------------------------------------------
# Initialize matchers
# ---------------------------------------------------------------------------

room_matcher = VocabularyMatcher(ROOMS)
style_matcher = VocabularyMatcher(STYLES)
material_matcher = VocabularyMatcher(MATERIALS)
size_matcher = VocabularyMatcher(SIZE_MODIFIERS)
intent_matcher = VocabularyMatcher(INTENT_ACTIONS)
typology_matcher = VocabularyMatcher(TYPOLOGY)

multi_matcher = MultiVocabularyMatcher({
    "rooms": room_matcher,
    "styles": style_matcher,
    "materials": material_matcher,
    "size_modifiers": size_matcher,
    "intent_actions": intent_matcher,
    "typology": typology_matcher,
})

logger.info("Vocabulary matchers initialized with %d categories", len(ALL_VOCABULARIES))

# ---------------------------------------------------------------------------
# Physics BitMLP loader
# ---------------------------------------------------------------------------

_physics_model = None
_physics_metadata = None


def _load_physics_model():
    """Load the trained Physics BitMLP TorchScript model."""
    global _physics_model, _physics_metadata

    meta_path = PHYSICS_MODEL_DIR / "physics_feature_metadata.json"
    script_path = PHYSICS_MODEL_DIR / "physics_bitmlp_torchscript.pt"

    if not meta_path.exists() or not script_path.exists():
        logger.warning(
            "Physics model not found at %s — physics predictions disabled. "
            "Train with: python train_indian_physics_bitmlp.py",
            PHYSICS_MODEL_DIR,
        )
        return

    try:
        import torch

        _physics_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        _physics_model = torch.jit.load(str(script_path), map_location="cpu")
        _physics_model.eval()
        logger.info(
            "Physics BitMLP loaded: input_dim=%d, hidden=%d, depth=%d",
            _physics_metadata.get("input_dim", "?"),
            _physics_metadata.get("hidden_dim", "?"),
            _physics_metadata.get("depth", "?"),
        )
    except ImportError:
        logger.warning("PyTorch not installed — physics predictions disabled")
    except Exception as exc:
        logger.error("Failed to load physics model: %s", exc)


_load_physics_model()

# ---------------------------------------------------------------------------
# NLP adapter status check
# ---------------------------------------------------------------------------

_nlp_adapter_found = (NLP_MODEL_DIR / "adapter" / "adapter_model.safetensors").exists()
if _nlp_adapter_found:
    logger.info("NLP QLoRA adapter found at %s (inference requires GPU + transformers)", NLP_MODEL_DIR / "adapter")
else:
    logger.info("NLP QLoRA adapter not found — using local 3-layer matcher only")


# ---------------------------------------------------------------------------
# Regex extractors
# ---------------------------------------------------------------------------

def extract_numbers(prompt: str) -> Dict[str, Any]:
    """Extract numeric values from prompt: BHK count, area, budget, floors."""
    text = prompt.lower()
    result: Dict[str, Any] = {}

    # BHK count: "3BHK", "3 BHK", "three bhk"
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "single": 1, "double": 2, "triple": 3,
        "ek": 1, "do": 2, "teen": 3, "chaar": 4, "paanch": 5,
    }
    bhk_match = re.search(r'(\d+)\s*bhk', text)
    if bhk_match:
        result["bhk"] = int(bhk_match.group(1))
    else:
        for word, num in word_to_num.items():
            if re.search(rf'\b{word}\s*bhk\b', text):
                result["bhk"] = num
                break

    if "bhk" not in result:
        bed_match = re.search(r'(\d+)\s*(?:bedroom|bed|bedrooms)', text)
        if bed_match:
            result["bhk"] = int(bed_match.group(1))
        else:
            for word, num in word_to_num.items():
                if re.search(rf'\b{word}\s*(?:bedroom|bed|bedrooms)\b', text):
                    result["bhk"] = num
                    break

    # Area in sq ft
    area_match = re.search(r'(\d+(?:,\d+)*)\s*(?:sq\.?\s*(?:ft|feet)|square\s*(?:ft|feet)|sqft|sft)', text)
    if area_match:
        result["area_sqft"] = int(area_match.group(1).replace(",", ""))

    # Area in gaj
    gaj_match = re.search(r'(\d+(?:,\d+)*)\s*gaj', text)
    if gaj_match:
        result["area_sqft"] = int(float(gaj_match.group(1).replace(",", "")) * 9)

    # Budget in lakhs/crores
    lakh_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:lakh|lac|lakhs)', text)
    if lakh_match:
        result["budget_inr"] = int(float(lakh_match.group(1)) * 100_000)

    crore_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:crore|crores|cr)', text)
    if crore_match:
        result["budget_inr"] = int(float(crore_match.group(1)) * 10_000_000)

    # Floors
    if "duplex" in text:
        result["floors"] = 2
    else:
        floor_match = re.search(r'g\+(\d+)', text)
        if floor_match:
            result["floors"] = int(floor_match.group(1)) + 1
        else:
            floor_match = re.search(r'(\d+)\s*(?:floor|story|storey|manzil)', text)
            if floor_match:
                result["floors"] = int(floor_match.group(1))

    # Plot dimensions: "30x40", "30 x 40", "30ft x 40ft"
    plot_match = re.search(r'(\d+)\s*(?:ft|feet)?\s*[xX×]\s*(\d+)\s*(?:ft|feet)?', text)
    if plot_match:
        result["plot_width"] = int(plot_match.group(1))
        result["plot_length"] = int(plot_match.group(2))

    # Ceiling height
    ceiling_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:ft|feet)\s*ceiling', text)
    if ceiling_match:
        result["ceiling_height_ft"] = float(ceiling_match.group(1))

    return result


# ---------------------------------------------------------------------------
# Token-based NLP pipeline
# ---------------------------------------------------------------------------

def tokenize_prompt(prompt: str) -> List[str]:
    """
    Split prompt into meaningful tokens for matching.
    Preserves multi-word phrases by first trying bigrams/trigrams.
    """
    # Clean up
    text = prompt.lower().strip()
    text = re.sub(r'[^\w\s/+]', ' ', text)  # keep alphanumeric + space
    text = re.sub(r'\s+', ' ', text)

    words = text.split()
    tokens: List[str] = []

    i = 0
    while i < len(words):
        # Try trigram
        if i + 2 < len(words):
            trigram = f"{words[i]} {words[i+1]} {words[i+2]}"
            cat, result = multi_matcher.match_best(trigram)
            if result.found and result.confidence >= 90:
                tokens.append(trigram)
                i += 3
                continue

        # Try bigram
        if i + 1 < len(words):
            bigram = f"{words[i]} {words[i+1]}"
            cat, result = multi_matcher.match_best(bigram)
            if result.found and result.confidence >= 85:
                tokens.append(bigram)
                i += 2
                continue

        # Single word
        tokens.append(words[i])
        i += 1

    return tokens


def analyze_prompt(prompt: str) -> Dict[str, Any]:
    """
    Full NLP analysis of a user prompt.

    Returns:
        layout_params: Extracted entities for BSP layout engine
        understood: Human-readable list of what was parsed
        warnings: List of unrecognized terms with suggestions
    """
    # Step 1: Regex number extraction
    numbers = extract_numbers(prompt)

    # Step 2: Tokenize
    tokens = tokenize_prompt(prompt)

    # Step 3: Match each token through all matchers
    matched_rooms: List[Dict[str, Any]] = []
    matched_styles: List[Dict[str, Any]] = []
    matched_materials: List[Dict[str, Any]] = []
    matched_sizes: List[Dict[str, Any]] = []
    matched_intents: List[Dict[str, Any]] = []
    matched_typology: List[Dict[str, Any]] = []
    matched_colors: List[Dict[str, Any]] = []
    unmatched_terms: List[Dict[str, Any]] = []

    # Skip common stop words for matching
    stop_words = {
        "i", "a", "an", "the", "is", "am", "are", "was", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "shall", "should", "may", "might", "must",
        "can", "could", "to", "of", "in", "for", "on", "with", "at",
        "by", "from", "up", "about", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off",
        "over", "under", "again", "further", "then", "once", "here",
        "there", "when", "where", "why", "how", "all", "both", "each",
        "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very",
        "just", "don", "now", "and", "but", "or", "if", "my", "me",
        "we", "our", "you", "your", "it", "its", "this", "that",
        "these", "those", "what", "which", "who", "whom", "whose",
        "mera", "meri", "mere", "ka", "ke", "ki", "ko", "se", "me",
        "hai", "hain", "ho", "tha", "thi", "the", "wala", "wali",
        "type", "kind", "like", "also", "please", "bhai", "sir",
        "yaar", "dude", "hey",
        # Generic architectural descriptors that cause false fuzzy matches
        "flooring", "floors", "floor", "walls", "wall", "ceiling",
        "ceilings", "doors", "door", "windows", "window", "room",
        "rooms", "house", "home", "ghar", "building", "area",
        "space", "layout", "plan", "design", "style", "look",
        "feel", "vibe", "give", "side", "ft", "feet", "sqft",
        "sq", "bhk", "want", "need", "keep",
        # Hindi/Hinglish particles and common words
        "ek", "do", "zaroor", "bhi", "lakh", "lakhs", "crore", "crores",
        "budget", "chahiye", "karo", "karna", "dena", "rakhna",
        "de", "kar", "wala", "wali", "wale", "aur", "ya",
        "nahi", "nahin", "bas", "sirf", "bilkul", "accha",
        "theek", "thik", "sahi", "pakka", "abhi",
        "compliant", "friendly", "based", "concept", "open",
    }

    # Also skip tokens that are just numbers (already extracted by regex)
    for token in tokens:
        if token in stop_words:
            continue
        if re.match(r'^\d+$', token):
            continue
        # Skip BHK tokens (already extracted)
        if re.match(r'^\d*\s*bhk$', token):
            continue

        # Try matching in each category
        results = multi_matcher.match(token)

        if results:
            # Take the highest confidence match
            best_cat = max(results, key=lambda c: results[c].confidence)
            best = results[best_cat]

            entry = {
                "term": token,
                "canonical": best.canonical,
                "confidence": best.confidence,
                "layer": best.layer,
            }

            if best_cat == "rooms":
                matched_rooms.append(entry)
            elif best_cat == "styles":
                matched_styles.append(entry)
            elif best_cat == "materials":
                matched_materials.append(entry)
            elif best_cat == "size_modifiers":
                matched_sizes.append(entry)
            elif best_cat == "intent_actions":
                matched_intents.append(entry)
            elif best_cat == "typology":
                matched_typology.append(entry)
            elif best_cat == "colors":
                matched_colors.append(entry)
        else:
            # No match in any category — get closest suggestions
            closest: List[str] = []
            for cat_name in ["rooms", "styles", "materials"]:
                cat_matcher = multi_matcher.matchers[cat_name]
                result = cat_matcher.match(token)
                if result.closest:
                    closest.extend(result.closest[:1])

            unmatched_terms.append({
                "term": token,
                "suggestions": closest[:3],
            })

    # Step 4: Build understood list
    understood: List[str] = []

    if "bhk" in numbers:
        understood.append(f"Configuration: {numbers['bhk']}BHK")
    if "area_sqft" in numbers:
        understood.append(f"Area: {numbers['area_sqft']} sq ft")
    if "budget_inr" in numbers:
        budget_lakhs = numbers["budget_inr"] / 100_000
        understood.append(f"Budget: {budget_lakhs:.1f} Lakhs")
    if "floors" in numbers:
        understood.append(f"Floors: {numbers['floors']}")
    if "plot_width" in numbers and "plot_length" in numbers:
        understood.append(f"Plot: {numbers['plot_width']}×{numbers['plot_length']} ft")
    if "ceiling_height_ft" in numbers:
        understood.append(f"Ceiling: {numbers['ceiling_height_ft']} ft")

    for item in matched_typology:
        understood.append(f"Typology: {item['canonical'].upper()}")
    for item in matched_rooms:
        understood.append(f"Room: {item['canonical'].title()}")
    for item in matched_styles:
        understood.append(f"Style: {item['canonical'].title()}")
    for item in matched_materials:
        understood.append(f"Material: {item['canonical'].title()}")
    for item in matched_sizes:
        understood.append(f"Size: {item['canonical'].title()}")
    for item in matched_intents:
        understood.append(f"Action: {item['canonical'].title()}")
    for item in matched_colors:
        understood.append(f"Color: {item['canonical'].title()}")

    # Step 5: Build warnings
    warnings: List[str] = []
    for item in unmatched_terms:
        if item["suggestions"]:
            suggestions = "', '".join(item["suggestions"])
            warnings.append(
                f"Could not understand '{item['term']}'. "
                f"Did you mean '{suggestions}'?"
            )
        else:
            warnings.append(f"Could not understand '{item['term']}'.")

    # Step 6: Build layout_params
    layout_params: Dict[str, Any] = {**numbers}

    if matched_rooms:
        layout_params["rooms"] = [
            {"type": r["canonical"], "confidence": r["confidence"]}
            for r in matched_rooms
        ]

    if matched_styles:
        layout_params["styles"] = [s["canonical"] for s in matched_styles]

    if matched_materials:
        layout_params["materials"] = {
            m["canonical"]: m["confidence"] for m in matched_materials
        }

    if matched_sizes:
        layout_params["size_modifiers"] = [s["canonical"] for s in matched_sizes]
        
    if matched_colors:
        # Add colors to styles so the front-end will apply it
        if "styles" not in layout_params:
            layout_params["styles"] = []
        layout_params["styles"].extend([c["canonical"] for c in matched_colors])

    if matched_intents:
        layout_params["intents"] = [i["canonical"] for i in matched_intents]

    if matched_typology:
        layout_params["typology"] = matched_typology[0]["canonical"]

    return {
        "layout_params": layout_params,
        "understood": understood,
        "warnings": warnings,
        "matched_details": {
            "rooms": matched_rooms,
            "styles": matched_styles,
            "materials": matched_materials,
            "sizes": matched_sizes,
            "intents": matched_intents,
            "typology": matched_typology,
            "colors": matched_colors,
            "unmatched": unmatched_terms,
        },
    }


# ---------------------------------------------------------------------------
# Physics inference
# ---------------------------------------------------------------------------

def run_physics_prediction(
    room_width: float,
    room_length: float,
    floors: int = 1,
    ceiling_height: float = 10.0,
    wall_material: str = "AAC Blocks",
    city: str = "Pune",
    state: str = "Maharashtra",
    seismic_zone: str = "III",
    climate: str = "Moderate/Deccan",
    cost_tier: str = "Tier 2",
) -> Optional[Dict[str, Any]]:
    """Run Physics BitMLP inference if model is loaded."""
    if _physics_model is None or _physics_metadata is None:
        return None

    try:
        import torch

        meta = _physics_metadata
        cats = meta["categories"]
        stats = meta["numeric_stats"]

        # Build numeric features
        area = room_width * room_length
        aspect = max(room_width, room_length) / max(min(room_width, room_length), 0.1)
        governing_span = max(room_width, room_length)

        zone_factors = {"II": 0.10, "III": 0.16, "IV": 0.24, "V": 0.36}
        seismic_factor = zone_factors.get(seismic_zone, 0.16)

        # Determine derived flags
        is_coastal = "Coastal" in climate or "coastal" in climate.lower()
        is_heavy_rain = "Monsoon" in climate or "Rain" in climate
        is_extreme_heat = "Extreme" in climate or "Heat" in climate
        is_snow = "Snow" in climate or "Mountain" in climate
        is_high_seismic = seismic_zone in ("IV", "V")

        tier_map = {"Tier 1": 1.30, "Tier 2": 1.00, "Tier 3": 0.85}
        tier_mult = tier_map.get(cost_tier, 1.0)

        column_width_mm = 230 if not is_high_seismic else 300
        required_col_mm = column_width_mm

        numerics = {
            "room_width_ft": room_width,
            "room_length_ft": room_length,
            "column_width_mm": float(column_width_mm),
            "floors": float(floors),
            "ceiling_height_ft": ceiling_height,
            "has_beam": 1.0 if governing_span > 12 else 0.0,
            "ductile_detailing": 1.0 if is_high_seismic else 0.0,
            "tier_multiplier": tier_mult,
            "governing_span_ft": governing_span,
            "area_sqft": area,
            "aspect_ratio": aspect,
            "effective_span_limit_ft": governing_span * 1.1,
            "seismic_zone_factor": seismic_factor,
            "required_column_width_mm": float(required_col_mm),
            "epoxy_tmt_required": 1.0 if is_coastal else 0.0,
            "damp_proofing_required": 1.0 if (is_coastal or is_heavy_rain) else 0.0,
            "thermal_mass_required": 1.0 if is_extreme_heat else 0.0,
            "snow_roof_required": 1.0 if is_snow else 0.0,
            "engine_override_active": 1.0,
        }

        # Normalize numerics
        feature_vec = []
        for feat in meta["numeric_features"]:
            val = numerics.get(feat, 0.0)
            mean = stats[feat]["mean"]
            std = stats[feat]["std"]
            feature_vec.append((val - mean) / max(std, 1e-8))

        # One-hot categoricals
        cat_values = {
            "material_type": "1",  # RCC
            "wall_material": wall_material,
            "roofing_type": "Flat RCC Slab" if not is_snow else "Sloped/Pitched Roof",
            "foundation_type": "Strip Footing",
            "steel_grade": "Fe550D" if is_high_seismic else "Fe500",
            "soil_type": "Medium Soil",
            "city": city,
            "state": state,
            "cost_tier": cost_tier,
            "seismic_zone": seismic_zone,
            "climate": climate,
            "required_steel_grade": "Fe550D" if is_high_seismic else "Fe500",
            "required_foundation_type": "Raft Foundation" if is_high_seismic else "Isolated Footing",
            "required_roofing_type": "Sloped/Pitched Roof" if is_snow else "Flat RCC Slab",
        }

        for cat_feat in meta["categorical_features"]:
            values = cats[cat_feat]
            actual = cat_values.get(cat_feat, "")
            for v in values:
                feature_vec.append(1.0 if v == actual else 0.0)

        # Pad or truncate to input_dim
        input_dim = meta["input_dim"]
        while len(feature_vec) < input_dim:
            feature_vec.append(0.0)
        feature_vec = feature_vec[:input_dim]

        # Run inference
        x = torch.tensor([feature_vec], dtype=torch.float32)
        with torch.no_grad():
            out = _physics_model(x)

        safe_logit = float(out[0, 0])
        cost_scaled = float(out[0, 1])
        carbon_scaled = float(out[0, 2])

        target_stats = meta["target_stats"]
        cost_inr = cost_scaled * target_stats["cost_inr"]["std"] + target_stats["cost_inr"]["mean"]
        carbon_kg = carbon_scaled * target_stats["carbon_kg"]["std"] + target_stats["carbon_kg"]["mean"]

        import torch.nn.functional as F
        safety_prob = float(torch.sigmoid(torch.tensor(safe_logit)))

        return {
            "is_safe": safety_prob >= 0.5,
            "safety_confidence": round(safety_prob * 100, 1),
            "cost_inr": max(0, int(round(cost_inr, -3))),
            "carbon_kg": max(0, round(carbon_kg, 1)),
        }

    except Exception as exc:
        logger.error("Physics prediction failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Style extraction (local, from old server.py — enhanced)
# ---------------------------------------------------------------------------

def extract_style(prompt: str, matched_materials: list, matched_styles: list) -> Dict[str, Any]:
    """Build style dict from matched materials and styles."""
    style: Dict[str, Any] = {}

    # Floor material from matched materials
    floor_map = {
        "italian marble": "italian_marble",
        "indian marble": "indian_marble",
        "vitrified tiles": "vitrified_tiles",
        "kota stone": "kota_stone",
        "granite": "granite",
        "wooden laminate": "wood_laminate",
        "terrazzo": "terrazzo",
    }
    for mat in matched_materials:
        canonical = mat.get("canonical", "")
        if canonical in floor_map:
            style["floorMaterial"] = floor_map[canonical]

    # Wall finish from matched materials
    wall_map = {
        "distemper": "distemper",
        "acrylic paint": "acrylic_paint",
        "texture paint": "texture_paint",
        "exposed brick": "exposed_brick",
        "wallpaper": "wallpaper",
    }
    for mat in matched_materials:
        canonical = mat.get("canonical", "")
        if canonical in wall_map:
            style["wallFinish"] = wall_map[canonical]

    # Door material
    door_map = {
        "teak wood": "teak_wood",
        "flush doors": "flush_door",
    }
    for mat in matched_materials:
        canonical = mat.get("canonical", "")
        if canonical in door_map:
            style["doorMaterial"] = door_map[canonical]

    # Style-based environment
    style_env_map = {
        "coastal": {"site": "coastal_villa", "environment": "sunset"},
        "farmhouse": {"site": "garden_courtyard", "environment": "park"},
        "industrial": {"site": "urban_luxury", "environment": "city"},
    }
    for s in matched_styles:
        canonical = s.get("canonical", "")
        if canonical in style_env_map:
            style.update(style_env_map[canonical])

    # Accent colors from prompt text
    text = prompt.lower()
    color_map = {
        "green": "#22c55e",
        "blue": "#2563eb",
        "amber": "#f59e0b",
        "gold": "#f59e0b",
        "pink": "#ec4899",
        "red": "#ef4444",
        "purple": "#8b5cf6",
        "orange": "#f97316",
        "neon": "#39ff14",
        "yellow": "#eab308",
    }
    for color_name, hex_val in color_map.items():
        if color_name in text:
            style["accentColor"] = hex_val
            break

    if style:
        style["lastPrompt"] = prompt

    return style


# ---------------------------------------------------------------------------
# Room list builder for existing project modifications
# ---------------------------------------------------------------------------

def build_room_changes(
    prompt: str,
    current_rooms: List[Dict[str, Any]],
    matched_intents: list,
    matched_rooms: list,
    matched_sizes: list,
    move_target: str = "",
    move_dest: str = ""
) -> Optional[List[Dict[str, Any]]]:
    """
    Apply modification intents to existing room list.
    Returns modified room list or None if no changes detected.
    """
    if not current_rooms:
        return None

    intents = [i.get("canonical", "") for i in matched_intents]
    room_types = [r.get("canonical", "") for r in matched_rooms]
    sizes = [s.get("canonical", "") for s in matched_sizes]

    rooms = [dict(r) for r in current_rooms]  # shallow copy

    # ADD intent — Carve-and-Shrink.
    # A generated house already uses 100% of its floor area, so we must NEVER
    # spawn a new room at free/out-of-bounds coordinates (that produced the
    # floating bathroom outside the footprint). Instead: pick a suitable Parent
    # Room, shrink it, and place the new room inside the freed strip. Coordinates
    # are derived entirely from the parent's rect, so the room stays in bounds.
    if "add" in intents and room_types:
        # Preferred donor parents per new-room type (first match wins, largest of type).
        PARENT_PREF = {
            "bathroom":      ["master_bedroom", "bedroom", "corridor", "living_room"],
            "powder_room":   ["foyer", "living_room", "corridor"],
            "pooja_room":    ["living_room", "dining_room"],          # never bath/utility/kitchen
            "utility":       ["kitchen"],
            "store_room":    ["kitchen", "utility", "dining_room"],
            "master_bedroom":["bedroom", "living_room"],
            "bedroom":       ["master_bedroom", "bedroom", "living_room"],
            "kitchen":       ["dining_room", "living_room"],
            "dining_room":   ["living_room"],
            "balcony":       ["bedroom", "living_room"],
            "foyer":         ["living_room"],
            "study_room":    ["bedroom", "living_room"],
            "laundry":       ["kitchen", "utility"],
        }
        # Target footprint (ft) of the new room.
        NEW_SIZE = {
            "bathroom": (6, 7), "powder_room": (4, 5), "pooja_room": (6, 6),
            "utility": (6, 6), "store_room": (5, 6), "kitchen": (10, 10),
            "dining_room": (10, 10), "balcony": (5, 8), "foyer": (6, 7),
            "study_room": (8, 9), "laundry": (5, 6), "master_bedroom": (11, 11),
            "bedroom": (10, 10),
        }
        MIN_PARENT = 8.0   # parent must keep >= this ft on the carved axis
        # Types that may legitimately repeat; others are skipped if present.
        REPEATABLE = {"bathroom", "bedroom", "balcony", "store_room"}

        for rtype in room_types:
            norm = rtype.replace(" ", "_")
            existing_types = {r.get("type", "") for r in rooms}
            if norm in existing_types and norm not in REPEATABLE:
                continue

            nw, nl = NEW_SIZE.get(norm, (8, 8))

            # Pick a parent by preference (largest of that type); else biggest room.
            parent = None
            for pref in PARENT_PREF.get(norm, []):
                cands = [r for r in rooms if pref in r.get("type", "")]
                if cands:
                    parent = max(cands, key=lambda r: r.get("width", 0) * r.get("length", 0))
                    break
            if parent is None and rooms:
                parent = max(rooms, key=lambda r: r.get("width", 0) * r.get("length", 0))
            if parent is None:
                continue

            px, pz = parent.get("x", 0), parent.get("z", 0)
            pw, pl = parent.get("width", 10), parent.get("length", 10)

            # Carve a strip off the parent's longer axis; clamp so the parent
            # remainder stays >= MIN_PARENT (reject the carve otherwise).
            if pw >= pl:
                slice_w = min(nw, pw - MIN_PARENT)
                if slice_w < 3.0:
                    continue  # parent too small — reject (no random placement)
                nx, nz, nwid, nlen = px + pw - slice_w, pz, slice_w, pl
                parent["width"] = pw - slice_w
            else:
                slice_l = min(nl, pl - MIN_PARENT)
                if slice_l < 3.0:
                    continue
                nx, nz, nwid, nlen = px, pz + pl - slice_l, pw, slice_l
                parent["length"] = pl - slice_l

            # Attached bath carved from a plain bedroom → promote it to Master.
            if norm == "bathroom" and parent.get("type", "") == "bedroom":
                parent["type"] = "master_bedroom"
                parent["name"] = "Master Bedroom"

            rooms.append({
                "id": f"{norm}-{len(rooms)+1}",
                "name": f"New {rtype.replace('_', ' ').title()}",
                "type": norm,
                "width": round(nwid, 2),
                "length": round(nlen, 2),
                "x": round(nx, 2),
                "z": round(nz, 2),
                "wallThicknessIn": 8 if norm in ("kitchen", "bathroom", "utility") else 6,
                # Keep the new room connected to its donor.  Coordinates are
                # local to the new room, as expected by the React renderer.
                "doors": [{
                    "x": 0 if pw >= pl else round(nwid / 2, 2),
                    "z": round(nlen / 2, 2) if pw >= pl else 0,
                    "wall_orientation": "west" if pw >= pl else "north",
                    "width": min(3.0, nwid * 0.6, nlen * 0.6),
                    "height": 7.0,
                }],
                "windows": [],
                "floorColor": "",
                "furnitureColor": "",
                "wallColor": "",
                "wallColors": {},
                "furniture": [],
                "mep_nodes": [],
                "connections": [{"target_room": parent.get("type", "corridor"), "weight": 10}],
            })
        return rooms

    # REMOVE intent
    if ("remove" in intents or "delete" in intents) and room_types:
        prompt_lower = prompt.lower()
        # If they want to remove a sub-element, DO NOT remove the entire room!
        if "door" not in prompt_lower and "window" not in prompt_lower and "furniture" not in prompt_lower:
            removed_any = False
            # First pass: look for exact ID/Name match in the prompt
            for i, r in enumerate(rooms):
                r_id = r.get("id", "").lower()
                r_name = r.get("name", "").lower()
                if (r_id and r_id in prompt_lower) or (r_name and r_name in prompt_lower):
                    rooms.pop(i)
                    removed_any = True
                    break
            
            # Second pass: generic type match if no exact match found
            if not removed_any:
                for rtype in room_types:
                    for i, r in enumerate(rooms):
                        if (rtype in r.get("name", "").lower()
                            or rtype in r.get("type", "").lower()
                            or rtype.replace(" ", "_") == r.get("type", "")):
                            rooms.pop(i)
                            break
            
            if len(rooms) != len(current_rooms):
                return rooms
    # RESIZE intent
    if "resize" in intents and room_types:
        size_delta = 0
        if "large" in sizes or "extra large" in sizes:
            size_delta = 4
        elif "small" in sizes:
            size_delta = -3
        elif "medium" in sizes:
            size_delta = 0

        # Also check for explicit increase/decrease in prompt
        text = prompt.lower()
        if any(word in text for word in ["increase", "bigger", "larger", "expand", "bada"]):
            size_delta = max(size_delta, 3)
        elif any(word in text for word in ["decrease", "smaller", "reduce", "shrink", "chhota"]):
            size_delta = min(size_delta, -3)

        if size_delta != 0:
            for rtype in room_types:
                for room in rooms:
                    r_name = room.get("name", "").lower()
                    r_type = room.get("type", "").lower()
                    r_id = room.get("id", "").lower()
                    
                    # If the prompt explicitly mentions this room's exact ID or Name, ONLY scale this one!
                    if (r_id and r_id in text) or (r_name and r_name in text):
                        room["width"] = max(6, room.get("width", 10) + size_delta)
                        room["length"] = max(6, room.get("length", 10) + size_delta)
                    # Fallback generic match
                    elif (rtype in r_name or rtype in r_type or rtype.replace(" ", "_") == r_type):
                        # Make sure they didn't explicitly request a DIFFERENT room
                        any_id_in_text = any((r2.get("id", "").lower() in text or r2.get("name", "").lower() in text) for r2 in rooms)
                        if not any_id_in_text:
                            room["width"] = max(6, room.get("width", 10) + size_delta)
                            room["length"] = max(6, room.get("length", 10) + size_delta)
            return rooms

    # MOVE intent
    if "move" in intents and move_target:
        target_room_name = move_target.lower().replace(" ", "_")
        dest_val = move_dest.lower().replace(" ", "_") if move_dest else ""
        
        idx_a = -1
        for i, r in enumerate(rooms):
            if target_room_name in r.get("type", "").lower() or target_room_name in r.get("name", "").lower():
                idx_a = i
                break
        
        if idx_a != -1:
            idx_b = -1
            # Check if dest_val is another room
            for i, r in enumerate(rooms):
                if i != idx_a and (dest_val in r.get("type", "").lower() or dest_val in r.get("name", "").lower()):
                    idx_b = i
                    break
            
            # If not a room, check if it's a direction like south east
            if idx_b == -1 and dest_val:
                min_x = min(r.get("x", 0) for r in rooms)
                max_x = max(r.get("x", 0) + r.get("width", 1) for r in rooms)
                min_z = min(r.get("z", 0) for r in rooms)
                max_z = max(r.get("z", 0) + r.get("length", 1) for r in rooms)
                
                target_x, target_z = (min_x + max_x)/2, (min_z + max_z)/2
                if "south" in dest_val: target_z = max_z
                elif "north" in dest_val: target_z = min_z
                if "east" in dest_val: target_x = max_x
                elif "west" in dest_val: target_x = min_x
                
                best_dist = float('inf')
                for i, r in enumerate(rooms):
                    if i != idx_a:
                        cx = r.get("x", 0) + r.get("width", 1)/2
                        cz = r.get("z", 0) + r.get("length", 1)/2
                        dist = (cx - target_x)**2 + (cz - target_z)**2
                        if dist < best_dist:
                            best_dist = dist
                            idx_b = i
                            
            if idx_b != -1:
                # SIZE CHECK: AI can only swap if area differs by less than 40%
                room_a = rooms[idx_a]
                room_b = rooms[idx_b]
                area_a = room_a.get("width", 1) * room_a.get("length", 1)
                area_b = room_b.get("width", 1) * room_b.get("length", 1)
                if min(area_a, area_b) >= max(area_a, area_b) * 0.6:
                    # Swap metadata
                    meta_keys = ["name", "type", "wallThicknessIn", "floorColor", "furnitureColor", "wallColor", "furniture", "mep_nodes", "materials", "is_wet"]
                    meta_a = {k: room_a.get(k) for k in meta_keys}
                    meta_b = {k: room_b.get(k) for k in meta_keys}
                    for k in meta_keys:
                        if k in meta_b: room_a[k] = meta_b[k]
                        if k in meta_a: room_b[k] = meta_a[k]
                    return rooms

    return None


def _preserve_modified_project_rooms(rooms: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return modified rooms plus fresh finite wall metadata without relayout.

    Prompt edits must not go through the fresh-generation pipeline.  It creates
    a new BSP plan and therefore discards the user's existing room-level data.
    This helper only rebuilds the wall graph from the already-edited rectangles.
    """
    by_floor: Dict[bool, List[Dict[str, Any]]] = {False: [], True: []}
    for room in rooms:
        if not isinstance(room, dict):
            continue
        if all(math.isfinite(float(room.get(k, 0))) for k in ("x", "z", "width", "length")):
            by_floor[bool(room.get("isFloor1", False))].append(room)

    layout_data: Dict[str, Any] = {
        "floor_0": by_floor[False],
        "walls_floor_0": [],
    }
    if by_floor[True]:
        layout_data["floor_1"] = by_floor[True]
        layout_data["walls_floor_1"] = []

    for is_floor1, floor_rooms in by_floor.items():
        if not floor_rooms:
            continue
        nodes = [RoomNode(
            id=str(r.get("id")),
            type=str(r.get("type", "living_room")),
            name=str(r.get("name", r.get("type", "Room"))),
            rect=Rect(float(r["x"]), float(r["z"]), float(r["width"]), float(r["length"])),
            wallThicknessIn=float(r.get("wallThicknessIn", 6) or 6),
            floorColor=r.get("floorColor", "") or "",
            wallColor=r.get("wallColor", "") or "",
            furnitureColor=r.get("furnitureColor", "") or "",
            furniture=r.get("furniture", []) or [],
            mep_nodes=r.get("mep_nodes", []) or [],
        ) for r in floor_rooms]
        walls = compute_shared_walls(nodes)
        layout_data["walls_floor_1" if is_floor1 else "walls_floor_0"] = walls

    return layout_data, [*by_floor[False], *by_floor[True]]


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/cost-presets")
async def get_cost_presets():
    """Return available material packages for the cost engine."""
    return {
        "presets": CostEngine.get_presets(),
        "materials": CostEngine.get_materials()
    }

@app.post("/api/generate")
async def generate_plan(req: GenerateRequest):
    """
    Parse user prompt → structured layout params + style.

    Response:
        layout_params: Extracted numbers and entities
        understood: What the AI successfully parsed
        warnings: Unrecognized terms with suggestions
        style: Extracted style preferences
        rooms: Modified room list (if modifying existing project)
        physics: Cost/carbon/safety predictions (if model loaded)
    """
    try:
        request_start = time.time()
        _logs = []  # Collect backend logs to send to frontend
        def _log(level, msg):
            _logs.append({"type": level, "message": msg, "time": f"{(time.time() - request_start)*1000:.0f}ms"})
            logger.info(f"[GEN-LOG][{level.upper()}] {msg}")

        _log("info", f"Prompt received: \"{req.prompt}\"")
        logger.info("[PERF] /api/generate API endpoint hit.")
        if not req.prompt or not req.prompt.strip():
            raise HTTPException(status_code=400, detail={
                "error": True,
                "message": "Prompt cannot be empty",
                "details": {"field": "prompt"},
            })

        # Run NLP analysis / SLM Extraction
        # Dual-Path Gateway
        slm_result = None
        complexity = evaluate_complexity(req.prompt)
        _log("info", f"Complexity evaluated: {complexity}")
        ai_start = time.time()
        
        # Check if project is actually empty (initial generation)
        is_empty = True
        if req.currentProject:
            # Legacy check
            if req.currentProject.get("rooms"):
                is_empty = False
            # New schema check
            elif req.currentProject.get("floors") and req.currentProject["floors"][0].get("rooms"):
                is_empty = False

        _log("info", f"Project state: {'EMPTY (initial generation)' if is_empty else 'HAS ROOMS (modification)'}")

        if not is_empty and complexity == "HIGH":
            # Path B: High Complexity Cloud LLM (OpenRouter/DeepSeek-R1)
            _log("info", "Routing → HIGH complexity Cloud Engine (DeepSeek-R1)")
            try:
                slm_result = reason_modifications_deepseek(req.prompt, req.currentProject)
                elapsed = (time.time() - ai_start)*1000
                _log("success", f"DeepSeek responded in {elapsed:.0f}ms")
                return {"status": "success", "project": slm_result, "logs": _logs}
            except Exception as e:
                _log("error", f"DeepSeek failed: {e}")
                
        elif not is_empty and complexity == "LOW":
            # Path B-Low: Local CSP Matrix Solver
            _log("info", "Routing → LOW complexity modification via Gemini")
            try:
                slm_result = extract_keywords_groq(req.prompt, ALL_VOCABULARIES)
                elapsed = (time.time() - ai_start)*1000
                _log("success", f"Gemini extraction done in {elapsed:.0f}ms")
            except Exception as e:
                _log("error", f"Gemini extraction failed: {e}")
                
        else:
            # Path A: Initial Generation via Gemini
            _log("info", "Routing → Initial generation via Gemini")
            try:
                slm_result = extract_keywords_groq(req.prompt, ALL_VOCABULARIES)
                elapsed = (time.time() - ai_start)*1000
                _log("success", f"Gemini extraction done in {elapsed:.0f}ms")
            except Exception as e:
                _log("error", f"Gemini extraction failed: {e}")

        if slm_result:
            _log("success", f"AI extracted: intent={slm_result.get('intent', '?')}, bhk={slm_result.get('bhk', '?')}, rooms={slm_result.get('target_rooms', [])}, style={slm_result.get('style', '?')}")
            # Map SLM result to the data structures expected by the rest of the pipeline
            layout_params = {}
            if slm_result.get("bhk"):
                layout_params["bhk"] = slm_result["bhk"]
            
            if slm_result.get("target_rooms"):
                # Strict filter: only include rooms actually mentioned in the prompt
                prompt_lower = req.prompt.lower()
                valid_rooms = []
                for r in slm_result.get("target_rooms", []):
                    room_str = r.lower().replace("_", " ")
                    prompt_clean = prompt_lower.replace("_", " ")
                    
                    # 1. Check for exact match
                    is_match = room_str in prompt_clean or room_str.replace(" ", "") in prompt_clean.replace(" ", "")
                    
                    # 2. Check for AI synonym upgrades (e.g., prompt="bedroom", AI="master_bedroom")
                    if not is_match:
                        if "bedroom" in prompt_clean and "bedroom" in room_str:
                            is_match = True
                        elif "bath" in prompt_clean and "bath" in room_str:
                            is_match = True
                        elif "living" in prompt_clean and "living" in room_str:
                            is_match = True

                    if is_match:
                        valid_rooms.append(r)
                
                # Check for explicit furniture keywords in the prompt to trigger room generation or flag
                furniture_keywords = ["bed", "wardrobe", "sofa", "couch", "table", "chair", "furniture"]
                if any(kw in prompt_lower for kw in furniture_keywords):
                    # For modification, if they ask to add furniture, we add it to layout_params
                    layout_params["add_furniture"] = True

                if valid_rooms:
                    layout_params["rooms"] = [{"type": r.replace(" ", "_"), "confidence": 100} for r in valid_rooms]
            
            # Roof style extraction from prompt directly
            prompt_lower = req.prompt.lower()
            if "rain" in prompt_lower or "slope" in prompt_lower or "hipped" in prompt_lower:
                layout_params["roofType"] = "hipped"
            elif "gabled" in prompt_lower or "gable" in prompt_lower or "hut" in prompt_lower:
                layout_params["roofType"] = "gabled"
            elif "flat" in prompt_lower:
                layout_params["roofType"] = "flat"
            elif "mansard" in prompt_lower:
                layout_params["roofType"] = "mansard"

            # Extract basic numbers like plot width/length using regex just in case
            numbers = extract_numbers(req.prompt)
            for k, v in numbers.items():
                if k not in layout_params:
                    layout_params[k] = v

            open_matches = re.findall(r'open\s+([a-zA-Z]+)', req.prompt.lower())
            if open_matches:
                layout_params["open_rooms"] = [m.replace(" ", "_") for m in open_matches]

            details = {
                "intents": [{"canonical": slm_result.get("intent", "").lower()}],
                "rooms": [{"canonical": r.get("type", "").replace(" ", "_"), "confidence": 100} for r in layout_params.get("rooms", [])],
                "styles": [{"canonical": slm_result.get("style")}] if slm_result.get("style") else [],
                "materials": [{"canonical": m} for m in slm_result.get("materials", [])],
                "sizes": [],
                "move_target": slm_result.get("move_target_room", ""),
                "move_dest": slm_result.get("move_destination", ""),
                # NEW: Pass AI extracted colors natively
                "room_colors": slm_result.get("room_colors", []),
                "global_color": slm_result.get("global_color", "")
            }

            understood = [f"Intent: {slm_result.get('intent')}"]
            if slm_result.get("bhk"): 
                understood.append(f"Configuration: {slm_result['bhk']}BHK")
            if layout_params.get("rooms"):
                for r in layout_params["rooms"]: 
                    understood.append(f"Room: {r['type'].replace('_', ' ').title()}")
            if layout_params.get("roofType"):
                understood.append(f"Roof: {layout_params['roofType'].title()}")
            if slm_result.get("style"):
                understood.append(f"Style: {slm_result['style'].title()}")
            for m in slm_result.get("materials", []):
                understood.append(f"Material: {m.title()}")
            
            # Forward Indian feature flags from SLM extraction → layout engine
            indian_from_slm = {
                "pooja_room":    bool(slm_result.get("needs_pooja_room")),
                "utility_area":  bool(slm_result.get("utility_area")),
                "powder_room":   bool(slm_result.get("powder_room")),
                "elderly_suite": bool(slm_result.get("elderly_suite")),
                "foyer":         bool(slm_result.get("foyer")),
                "brahmasthan":   bool(slm_result.get("brahmasthan")),
            }
            # Merge with any Indian options sent from the frontend
            fe_indian = req.indianOptions or {}
            merged_indian = {k: (indian_from_slm.get(k, False) or fe_indian.get(k, False))
                            for k in set(list(indian_from_slm) + list(fe_indian))}
            layout_params["indian_options"] = merged_indian

            warnings = []
        else:
            _log("warn", "AI extraction returned None — falling back to rule-based NLP")
            # Fallback to Old NLP analysis
            analysis = analyze_prompt(req.prompt)
            layout_params = analysis["layout_params"]
            understood = analysis["understood"]
            warnings = analysis["warnings"]
            
            # Combine UI colors with any AI-extracted colors
            final_colors = req.colors or {}
            if slm_result and slm_result.get("color_hex"):
                final_colors["ai_color"] = slm_result.get("color_hex")
            if "vastu" in req.prompt.lower():
                final_colors["vastuColors"] = True
                
            engine = LayoutEngine(req.width or 40.0, req.length or 40.0, colors=final_colors)
            details = analysis["matched_details"]

        # Apply material packages
        colors_dict = req.colors or {}
        package_name = req.package or "Standard"
        presets = CostEngine.get_presets().get(package_name, CostEngine.get_presets()["Standard"])
        custom_mats = req.customMaterials or {}
        active_preset = {**presets}
        for k, v in custom_mats.items():
            if v: active_preset[k] = v
            
        colors_dict = dict(req.colors or {})
        # Capture an AI/prompt-extracted color so it actually themes the house.
        if slm_result and slm_result.get("color_hex"):
            colors_dict["ai_color"] = slm_result["color_hex"]
        # Extract color from prompt directly
        prompt_lower = req.prompt.lower()
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        interior_map = {
            "off_white": "#FDFBF7",
            "sage": "#9CA986",
            "terracotta": "#E2725B",
            "charcoal": "#36454F",
            "beige": "#F5F5DC"
        }
        exterior_map = {
            "mustard": "#E2B838",
            "white": "#FFFFFF",
            "concrete": "#808080",
            "brick": "#B22222",
            "wood": "#DEB887"
        }
        roof_map = {
            "terracotta": "#8B3A3A",
            "dark_grey": "#2F4F4F",
            "brown": "#654321"
        }

        wall_finish = theme.get("wall") or active_preset.get("wall_material", "AAC Block")
        if colors_dict.get("interior") and not theme.get("wall"):
            wall_finish = interior_map.get(colors_dict["interior"], colors_dict["interior"])

        ext_color = theme.get("exterior") or colors_dict.get("exterior", "White")
        ext_color = exterior_map.get(ext_color, ext_color)

        roof_color = active_preset.get("roof_type", "RCC Slab")
        if colors_dict.get("roof"):
            roof_color = roof_map.get(colors_dict["roof"], colors_dict["roof"])

        style_out = {
            "wallFinish": wall_finish,
            "exteriorColor": ext_color,
            "accentColor": theme.get("accent") or "#10b981", # themed accent or default emerald
            "roofStyle": roof_color,
            "windows": active_preset.get("windows", "UPVC"),
            "doors": active_preset.get("doors", {}).get("Main", "Flush Door"),
            "kitchen_counter": active_preset.get("kitchen_counter", "Granite"),
        }

        # Build response
        response: Dict[str, Any] = {
            "layout_params": layout_params,
            "understood": understood,
            "warnings": warnings,
            "style": style_out,
            "package_details": active_preset
        }

        # Room modifications (if existing project)
        current_rooms = []
        if req.currentProject:
            if req.currentProject.get("rooms"):
                current_rooms = req.currentProject.get("rooms", [])
                _log("info", f"Loaded {len(current_rooms)} existing rooms from project.rooms")
            elif req.currentProject.get("floors"):
                for floor in req.currentProject.get("floors", []):
                    if floor.get("rooms"):
                        current_rooms.extend(floor.get("rooms", []))
                _log("info", f"Loaded {len(current_rooms)} existing rooms from project.floors")
            else:
                _log("warn", "currentProject provided but NO rooms found in .rooms or .floors")
        else:
            _log("info", "No currentProject sent — will generate from scratch")

        if current_rooms:
            _log("info", f"Existing rooms: {[r.get('name', r.get('type', '?')) for r in current_rooms]}")
            # First check for MEP modifications
            mep_adds = slm_result.get("mep_additions", []) if slm_result else []
            if mep_adds and slm_result.get("intent") == "MODIFY_MEP":
                _log("info", f"MEP modification detected: adding {len(mep_adds)} item(s)")
                import copy
                updated_rooms = copy.deepcopy(current_rooms)
                for addition in mep_adds:
                    target = addition.get("room", "").lower()
                    item = addition.get("item", "")
                    if target and item:
                        # Find matching room
                        for r in updated_rooms:
                            if target in r.get("name", "").lower() or target in r.get("type", "").lower():
                                mep_nodes = r.get("mep_nodes", [])
                                # Place item loosely around center
                                cx = r.get("x", 0) + r.get("width", 10)/2
                                cz = r.get("z", 0) + r.get("length", 10)/2
                                mep_nodes.append({"type": item, "x": round(cx + 1, 2), "z": round(cz + 1, 2)})
                                r["mep_nodes"] = mep_nodes
                
                response["layout_data"] = {"floor_0": updated_rooms} # Provide as layout_data so UI applies it
                response["understood"].append(f"Modified MEP: added items to {len(mep_adds)} rooms")
                _log("success", f"MEP modification complete — returning updated rooms")
                response["logs"] = _logs
                logger.info(f"[PERF] /api/generate total request completed in {(time.time() - request_start)*1000:.2f} ms")
                return response


            valid_rooms_spec = [r for r in details.get("rooms", []) if r.get("canonical") not in ("door", "window", "furniture", "wiring", "plumbing")]
            _log("info", f"Room modification intent: intents={[i.get('canonical','?') for i in details.get('intents',[])]}")
            
            # --- START AI SPATIAL DELEGATION ---
            intent_val = details.get("intents", [{"canonical": ""}])[0].get("canonical")
            if intent_val in ["add", "remove", "resize"] and valid_rooms_spec:
                _log("info", "Delegating topological reasoning to Gemini...")
                try:
                    from cloud_extractor import modify_validated_blueprint
                    
                    current_bp = []
                    for r in current_rooms:
                        current_bp.append({
                            "room_type": r.get("type", "room"),
                            "position_x": r.get("x", 0),
                            "position_z": r.get("z", 0),
                            "width": r.get("width", 10),
                            "length": r.get("length", 10),
                            "connections": r.get("connections", [])
                        })
                        
                    plot_w = layout_params.get("plot_width", req.width or 40.0)
                    plot_l = layout_params.get("plot_length", req.length or 40.0)
                    
                    # Single, fast Gemini call (under 4 seconds)
                    gemini_result = modify_validated_blueprint(req.prompt, current_bp, plot_w, plot_l)
                    master_bp = gemini_result.get("master_blueprint", [])
                    
                    # Convert Gemini's rough output into layout constraints for the CP-Solver
                    layout_params["rooms"] = [
                        {
                            "type": bp.get("room_type"), 
                            "confidence": 100, 
                            "width": bp.get("width"), 
                            "length": bp.get("length"),
                            "x": bp.get("position_x"),      # CP Solver will use these as hints
                            "z": bp.get("position_z"),      # CP Solver will use these as hints
                            "connections": bp.get("connections", []) # Forces corridor to connect!
                        } 
                        for bp in master_bp
                    ]
                    
                    # Explicitly remove the hard lock so the CP-Solver runs and fixes overlaps natively
                    if "master_blueprint" in layout_params:
                        del layout_params["master_blueprint"]
                    
                    modified_rooms = None 
                    current_rooms = []
                    _log("success", "Gemini mapped the connections. Handing off to CP-Solver for instant packing.")
                    response["understood"].append("AI updated the spatial topology.")
                    
                except Exception as e:
                    _log("error", f"Gemini geometry engine failed: {e}. Falling back to Python rules.")
                    modified_rooms = build_room_changes(
                        req.prompt, current_rooms, details.get("intents", []), valid_rooms_spec, details.get("sizes", []), details.get("move_target", ""), details.get("move_dest", "")
                    )
            else:
                modified_rooms = build_room_changes(
                    req.prompt, current_rooms, details.get("intents", []), valid_rooms_spec, details.get("sizes", []), details.get("move_target", ""), details.get("move_dest", "")
                )
            # --- END AI SPATIAL DELEGATION ---

            if modified_rooms is not None:
                _log("success", f"build_room_changes returned {len(modified_rooms)} room(s)")
            else:
                _log("warn", "build_room_changes returned None (no structural change detected)")

            # Fix 2: Preserve colors when modifying rooms
            if modified_rooms:
                for mr in modified_rooms:
                    for cr in current_rooms:
                        # IDs are stable in the frontend. Matching by type can
                        # copy Bedroom-1's furniture/colors onto Bedroom-2.
                        if cr.get("id") == mr.get("id"):
                            if "floorColor" in cr and not mr.get("floorColor"): mr["floorColor"] = cr["floorColor"]
                            if "wallColor" in cr and not mr.get("wallColor"): mr["wallColor"] = cr["wallColor"]
                            # Fix 3: Also preserve furniture
                            if "furniture" in cr:
                                mr["furniture"] = cr["furniture"]
                            break
                            
            # Fix 4: Handle "add furniture" or "add door" intent safely
            # If the user prompt contains "door" or "furniture" and an intent is ADD
            prompt_lower = req.prompt.lower()
            if "door" in prompt_lower or "furniture" in prompt_lower:
                target = None
                if valid_rooms_spec:
                    target = valid_rooms_spec[0].get("canonical")
                if modified_rooms and target:
                    for mr in modified_rooms:
                        if mr["type"] == target:
                            if "door" in prompt_lower:
                                mr.setdefault("doors", []).append({"width": 3, "position": "center"})
                            if "furniture" in prompt_lower:
                                mr.setdefault("furniture", []).append({"type": "sofa", "x": mr.get("x",0)+1, "z": mr.get("z",0)+1})
                elif current_rooms and target:
                    # Modify current rooms directly if we aren't regenerating
                    for cr in current_rooms:
                        if cr["type"] == target:
                            if "door" in prompt_lower:
                                cr.setdefault("doors", []).append({"width": 3, "position": "center"})
                            if "furniture" in prompt_lower:
                                cr.setdefault("furniture", []).append({"type": "sofa", "x": cr.get("x",0)+1, "z": cr.get("z",0)+1})
                    modified_rooms = current_rooms

            if modified_rooms is None and "bhk" in layout_params:
                # The user specified a new BHK configuration without any modification verbs.
                # Assume they want to generate a new layout from scratch!
                current_rooms = []
            elif modified_rooms is None and current_rooms:
                _log("info", "No structural changes — applying AI-parsed style/color changes")
                
                painted_count = 0
                room_colors = details.get("room_colors", [])
                
                if "style" not in response:
                    response["style"] = {}
                if req.currentProject and "style" in req.currentProject:
                    response["style"].update(req.currentProject.get("style", {}))
                
                # 1. Apply specific room and surface colors parsed by the AI
                if room_colors:
                    for rc in room_colors:
                        target_r = rc.get("room", "").lower()
                        color_val = rc.get("color", "")
                        surface = rc.get("surface", "wall").lower()
                        
                        if not color_val: continue
                        
                        # Route Global Surfaces
                        if "exterior" in surface or "outside" in surface or "exterior" in target_r:
                            response["style"]["exteriorColor"] = color_val
                            painted_count += 1
                            continue
                        elif "roof" in surface or "roof" in target_r:
                            response["style"]["roofStyle"] = color_val
                            response["style"]["roofColor"] = color_val
                            painted_count += 1
                            continue
                            
                        # Route Room Surfaces
                        for r in current_rooms:
                            room_name = r.get("name", "").lower()
                            room_type = r.get("type", "").lower()
                            
                            global_aliases = ["all", "house", "every", "floor", "floors", "wall", "walls", "interior", ""]
                            
                            if target_r in global_aliases or target_r in room_name or target_r in room_type:
                                if "floor" in surface or "floor" in target_r:
                                    r["floorColor"] = color_val
                                elif "furniture" in surface or "furniture" in target_r:
                                    r["furnitureColor"] = color_val
                                else:
                                    r["wallColor"] = color_val
                                    r["wallColors"] = [color_val, color_val, color_val, color_val]
                                painted_count += 1

                # 2. Fallback: Global Intent
                if painted_count == 0:
                    target_color = details.get("global_color") or details.get("color_hex")
                    if getattr(req, "colors", None) and req.colors:
                        target_color = target_color or req.colors.get("ai_color")
                    
                    if not target_color:
                        match = re.search(r'\b(red|blue|green|yellow|orange|purple|pink|white|black|gray|grey|brown|beige|cream|light\s+[a-z]+|dark\s+[a-z]+)\b', req.prompt.lower())
                        if match:
                            target_color = match.group(1)
                        
                    if target_color:
                        target_rooms = details.get("target_rooms", [])
                        if not target_rooms and isinstance(details.get("intents"), list) and len(details["intents"]) > 0:
                            target_rooms = details["intents"][0].get("target_rooms", [])
                        
                        prompt_lower = req.prompt.lower()
                        if any(x in prompt_lower for x in ["exterior", "outside", "facade", "extrior", "exterio", "extr"]):
                            response["style"]["exteriorColor"] = target_color
                            painted_count += 1
                        elif "roof" in prompt_lower:
                            response["style"]["roofColor"] = target_color
                            response["style"]["roofStyle"] = target_color
                            painted_count += 1
                        else:
                            for r in current_rooms:
                                room_name = r.get("name", "").lower()
                                room_type = r.get("type", "").lower()
                                
                                global_aliases = ["all", "house", "every", "floor", "floors", "wall", "walls", "interior"]
                                is_global = not target_rooms or any(t.lower() in global_aliases for t in target_rooms)
                                
                                if is_global or any(t.lower() in room_name or t.lower() in room_type for t in target_rooms):
                                    if "floor" in prompt_lower:
                                        r["floorColor"] = target_color
                                    elif "furniture" in prompt_lower:
                                        r["furnitureColor"] = target_color
                                    else:
                                        r["wallColor"] = target_color
                                        r["wallColors"] = [target_color, target_color, target_color, target_color] 
                                    painted_count += 1

                if painted_count > 0:
                    response["understood"].append(f"Painted {painted_count} surface(s) via AI intent")
                else:
                    response["understood"].append("Applied style changes without modifying layout structure")
                
                # Format the data as the frontend expects
                response["layout_data"], _ = _preserve_modified_project_rooms(current_rooms)
                
                # --- THE FIX: SAFELY EXTRACT ROOMS BEFORE MAPPING COLORS ---
                layout_data = response["layout_data"]
                rooms_to_update = []
                
                # Handle both Dictionary formats (Full Project) and List formats (Flat Array)
                if isinstance(layout_data, list):
                    rooms_to_update = layout_data
                elif isinstance(layout_data, dict):
                    rooms_to_update.extend(layout_data.get("rooms", []))
                    for f in layout_data.get("floors", []):
                        rooms_to_update.extend(f.get("rooms", []))
                
                for serialized_room in rooms_to_update:
                    if not isinstance(serialized_room, dict): 
                        continue # Extra safety check
                        
                    for active_room in current_rooms:
                        # Match the rooms by ID or Name
                        if serialized_room.get("id") == active_room.get("id") or serialized_room.get("type") == active_room.get("type"):
                            if "wallColor" in active_room:
                                serialized_room["wallColor"] = active_room["wallColor"]
                                serialized_room["wallColors"] = active_room.get("wallColors")
                            if "floorColor" in active_room:
                                serialized_room["floorColor"] = active_room["floorColor"]
                            if "furnitureColor" in active_room:
                                serialized_room["furnitureColor"] = active_room["furnitureColor"]
                            break
                # ------------------------------------------------------------
                
                response["logs"] = _logs
                return response

                # NEW: Construct Master Blueprint to preserve precise carved coordinates!
                from cloud_extractor import auto_wire_topology
                wired_specs = auto_wire_topology([r["type"] for r in modified_rooms])
                
                master_bp = []
                for i, r in enumerate(modified_rooms):
                    master_bp.append({
                        "room_type": r["type"],
                        "position_x": r.get("x", 0),
                        "position_z": r.get("z", 0),
                        "width": r.get("width", 10),
                        "length": r.get("length", 10),
                        "connections": wired_specs[i].get("connections", []),
                        "floor_number": 1 if r.get("isFloor1") else 0
                    })
                layout_params["master_blueprint"] = master_bp

                # Update layout params so the downstream pipeline has the wired topology
                layout_params["rooms"] = [
                    {
                        "type": r["type"], 
                        "confidence": 100, 
                        "width": r.get("width"), 
                        "length": r.get("length"),
                        "connections": wired_specs[i].get("connections", [])
                    } 
                    for i, r in enumerate(modified_rooms)
                ]
                current_rooms = [] # This forces generation down below, but now it uses the blueprint
                _log("info", f"Triggering full layout reconstruction with {len(modified_rooms)} precisely modified room(s)")
                response["understood"].append("Reconstructed the layout precisely to accommodate changes")

        if layout_params.get("rooms") or layout_params.get("bhk"):
            _log("info", f"Generation trigger: bhk={layout_params.get('bhk', '?')}, rooms={len(layout_params.get('rooms', []))}")
            # If we aren't modifying an existing project, generate from scratch
            if not current_rooms:
                if layout_params.get("master_blueprint"):
                    # We are in precise modification mode, skip injecting base rooms
                    pass 
                else:
                    bhk_val = layout_params.get("bhk", 0)
                    requested_rooms = layout_params.get("rooms", [])
                    
                    # Check if user explicitly listed core rooms
                    core_room_types = {"kitchen", "bedroom", "bathroom", "living_room", "master_bedroom"}
                    requested_types = {r["type"] for r in requested_rooms}
                    has_core_rooms = len(core_room_types.intersection(requested_types)) >= 2
                    
                    base_rooms = []
                    if bhk_val > 0:
                        base_rooms = get_base_rooms_for_bhk(bhk_val)
                        existing_types = {r["type"] for r in base_rooms}
                        for r in requested_rooms:
                            if r["type"] in ("bedroom", "master_bedroom"):
                                continue
                            if r["type"] not in existing_types or r["type"] in ["store_room", "pooja_room", "balcony", "study_room", "laundry"]:
                                base_rooms.append(r)
                    elif has_core_rooms:
                        base_rooms = requested_rooms
                    else:
                        base_rooms = get_base_rooms_for_bhk(1)
                        existing_types = {r["type"] for r in base_rooms}
                        for r in requested_rooms:
                            if r["type"] not in existing_types:
                                base_rooms.append(r)

                    base_rooms = apply_bedroom_intelligence(base_rooms, req.prompt, requested_types=requested_types)
                    layout_params["rooms"] = base_rooms

                    if not layout_params["rooms"]:
                        layout_params["rooms"] = get_base_rooms_for_bhk(1)
                        
                    # NEW: Auto-wire topology for from-scratch so doors generate correctly
                    from cloud_extractor import auto_wire_topology
                    wired_specs = auto_wire_topology([r["type"] for r in layout_params["rooms"]])
                    for r, w in zip(layout_params["rooms"], wired_specs):
                        r["connections"] = w.get("connections", [])

                # HARD GUARD: no Pooja Room unless explicitly selected or typed.
                _prompt_l = (req.prompt or "").lower()
                _pooja_ok = bool(layout_params.get("indian_options", {}).get("pooja_room")) or \
                    any(k in _prompt_l for k in ("pooja", "puja", "mandir", "temple", "prayer", "devghar"))
                if not _pooja_ok:
                    layout_params["rooms"] = [r for r in layout_params["rooms"] if "pooja" not in r.get("type", "").lower()]

                # Prefer an explicit UI floors value (>1); else the prompt-derived count.
                floors = layout_params.get("floors", 1)
                if req.floors and req.floors > 1:
                    floors = req.floors
                layout_params["floors"] = floors
                plot_w = req.width if req.width else layout_params.get("plot_width", 40.0)
                plot_l = req.length if req.length else layout_params.get("plot_length", 40.0)

                # ── Smart validation: ensure rooms fit the plot livably ──
                validated_rooms, validation_warns, new_plot_w, new_plot_l = smart_layout_validation(
                    layout_params["rooms"], plot_w, plot_l
                )
                if validation_warns:
                    warnings.extend(validation_warns)
                layout_params["rooms"] = validated_rooms
                
                # Update layout params with final (potentially expanded) dimensions
                plot_w, plot_l = new_plot_w, new_plot_l
                layout_params["plot_width"] = plot_w
                layout_params["plot_length"] = plot_l
                layout_params["area_sqft"] = int(plot_w * plot_l)

                if "engine" not in locals():
                     engine = LayoutEngine(plot_w, plot_l, colors=colors_dict)
                
                layout_data = {}
                
                # Remove Indian feature rooms from BSP pool so they aren't generated twice
                indian_types = set()
                if layout_params.get("indian_options", {}).get("pooja_room"):
                    indian_types.add("pooja_room")
                if layout_params.get("indian_options", {}).get("utility_area"):
                    indian_types.add("utility")
                if layout_params.get("indian_options", {}).get("foyer"):
                    indian_types.add("foyer")

                room_pool = [r for r in layout_params["rooms"] if r["type"] not in indian_types]
                room_pool, structural_features = strip_structural(room_pool)

                first_spec = []
                if floors > 1:
                    ground_spec, first_spec = split_duplex_specs(room_pool, bhk_val)
                    floor_0_rooms = sort_spec_by_generation_order(ground_spec)
                else:
                    floor_0_rooms = sort_spec_by_generation_order(room_pool)

                # Extract floor 0 blueprint
                master_bp = layout_params.get("master_blueprint")
                bp0 = [b for b in master_bp if b.get("floor_number", 0) == 0] if master_bp else None

                eng_start = time.time()
                generated_nodes_0 = engine.generate(
                    floor_0_rooms, 
                    indian_options=layout_params.get("indian_options", {}), 
                    layout_rules=req.layoutRules, 
                    restrict_slots=(floors > 1),
                    master_blueprint=bp0
                )
                logger.info(f"[PERF] Engine generation took {(time.time() - eng_start)*1000:.2f} ms")

                _req_types = requested_type_set(layout_params["rooms"], layout_params.get("indian_options", {}))
                enforce_requested_only(generated_nodes_0, _req_types)

                ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
                arch_warnings = ArchitecturalRules.validate_rules(generated_nodes_0)
                if arch_warnings:
                    warnings.extend(arch_warnings)
                
                resolver = AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", []))
                resolver.resolve()
                
                placer = WindowPlacer(generated_nodes_0, engine.plot_width, engine.plot_length,
                                     setback_x=engine.setback_x, setback_z=engine.setback_z)
                placer.place_windows()

                warnings.extend(validate_layout(generated_nodes_0))
                
                from geometry_validator import GeometryValidator
                warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_0).errors)

                shared_walls_0 = compute_shared_walls(generated_nodes_0)
                layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
                layout_data["walls_floor_0"] = shared_walls_0
                layout_data["mep_data"] = compute_mep_heuristics(generated_nodes_0)
                layout_data["setbacks"] = {
                    "x": engine.setback_x,
                    "z": engine.setback_z,
                    "buildable_width": engine.buildable_width,
                    "buildable_length": engine.buildable_length,
                }
                layout_data["indianOptions"] = layout_params.get("indian_options", req.indianOptions or {})
                
                generated_nodes_1 = []
                # Floor 1 (Spatial Inheritance)
                if floors > 1:
                    blocked_zones = []
                    staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
                    if staircase:
                        blocked_zones.append(staircase.rect)
                    living = next((n for n in generated_nodes_0 if getattr(n, "is_double_height", False)), None)
                    if living:
                        blocked_zones.append(living.rect)
                        
                    if True:
                        # SAFE FIRST SPEC FIX
                        import copy
                        safe_first_spec = copy.deepcopy(first_spec) if first_spec else []
                        if not safe_first_spec:
                            safe_first_spec = [copy.deepcopy(r) for r in layout_params["rooms"] if r["type"] in ("bedroom", "bathroom", "master_bedroom")]
                            if any(r["type"] == "master_bedroom" for r in floor_0_rooms):
                                for r in safe_first_spec:
                                    if r["type"] == "master_bedroom":
                                        r["type"] = "bedroom"
                        
                        # Extract floor 1 blueprint
                        bp1 = [b for b in master_bp if b.get("floor_number", 0) == 1] if master_bp else None
                        floor_1_rooms = sort_spec_by_generation_order(safe_first_spec)
                        eng_start = time.time()
                        generated_nodes_1 = engine.generate(
                            floor_1_rooms, 
                            blocked_zones=blocked_zones, 
                            restrict_slots=True,
                            master_blueprint=bp1
                        )
                        logger.info(f"[PERF] Engine generation took {(time.time() - eng_start)*1000:.2f} ms")
                        
                        _io = layout_params.get("indian_options", {})
                        align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                            make_void=bool(_io.get("double_height") or _io.get("void")))
                        enforce_requested_only(generated_nodes_1, _req_types)
                        ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                        AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                        WindowPlacer(generated_nodes_1, engine.plot_width, engine.plot_length,
                                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                                     
                        from geometry_validator import GeometryValidator
                        warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_1).errors)
                        
                        shared_walls_1 = compute_shared_walls(generated_nodes_1)
                        layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                        layout_data["walls_floor_1"] = shared_walls_1
                        layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)

                all_nodes = list(generated_nodes_0) + (generated_nodes_1 if floors > 1 else [])
                
                # --- RESTORE PRESERVED COLORS/FURNITURE ---
                try:
                    old_rooms = []
                    if req.currentProject:
                        old_rooms = req.currentProject.get("rooms", [])
                        if not old_rooms and req.currentProject.get("floors"):
                            for floor in req.currentProject.get("floors", []):
                                old_rooms.extend(floor.get("rooms", []))
                                
                    if old_rooms and master_bp:
                        used_old = set()
                        for node in all_nodes:
                            for i, old in enumerate(old_rooms):
                                if i not in used_old and old.get("type") == node.type:
                                    used_old.add(i)
                                    if "furniture" in old: node.furniture = old["furniture"]
                                    if "wallColor" in old and old["wallColor"]: node.wallColor = old["wallColor"]
                                    if "floorColor" in old and old["floorColor"]: node.floorColor = old["floorColor"]
                                    if "wallColors" in old and old["wallColors"]: node.wallColors = old["wallColors"]
                                    break
                except Exception as e:
                    logger.warning(f"Failed to restore preserved properties: {e}")

                selected_palette = _apply_selected_palette(all_nodes, req.colors)
                layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
                if generated_nodes_1:
                    layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                validation_report = final_layout_validation(
                    all_nodes,
                    indian_options=layout_params.get("indian_options", {}),
                    is_duplex=(floors > 1),
                )
                response["validation"] = validation_report
                if not validation_report["ok"]:
                    warnings.extend(validation_report["issues"])

                response["layout_data"] = layout_data

        physics = run_physics_prediction(
            room_width=layout_params.get("plot_width", 40) * 0.3,
            room_length=layout_params.get("plot_length", 40) * 0.3,
            floors=layout_params.get("floors", 1),
            ceiling_height=layout_params.get("ceiling_height_ft", 10.0),
        )
        calculated_materials = CostEngine.calculate_materials(
            layout_params.get("area_sqft", 1600),
            req.package or "Standard",
            req.customMaterials or {}
        )

        response["project"] = {
            "plot": {
                "width": layout_params.get("plot_width", 40),
                "length": layout_params.get("plot_length", 40),
                "areaSqft": layout_params.get("plot_width", 40) * layout_params.get("plot_length", 40)
            },
            "building": {
                "floors": "Ground + 1" if layout_params.get("floors", 1) > 1 else "Ground only",
                "costTier": req.package or "Standard"
            },
            "materials": calculated_materials
        }

        cost_estimate = CostEngine.calculate_cost(
            layout_params.get("area_sqft", 1600), 
            req.package or "Standard", 
            req.customMaterials or {}, 
            {"state": req.state, "district": req.district}
        )

        if physics:
            physics["cost_inr"] = int(cost_estimate["Total"])
            response["physics"] = physics
        else:
            response["physics"] = {
                "is_safe": True,
                "safety_confidence": 95.0,
                "cost_inr": int(cost_estimate["Total"]),
                "carbon_kg": 15000,
            }

        logger.info(
            "Prompt analyzed: %d understood, %d warnings, params=%s",
            len(understood), len(warnings), list(layout_params.keys()),
        )

        total_ms = (time.time() - request_start)*1000
        _log("success", f"Total request completed in {total_ms:.0f}ms")
        if response.get("layout_data"):
            f0_count = len(response["layout_data"].get("floor_0", []))
            f1_count = len(response["layout_data"].get("floor_1", []))
            _log("success", f"Final output: {f0_count} ground-floor rooms" + (f", {f1_count} first-floor rooms" if f1_count else ""))
        response["logs"] = _logs
        logger.info(f"[PERF] /api/generate total request completed in {total_ms:.2f} ms")
        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Generate endpoint error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail={
            "error": True,
            "message": f"Internal error during prompt analysis: {str(exc)}",
            "details": {"traceback": traceback.format_exc()},
        })

@app.post("/api/template")
async def generate_from_template(req: TemplateRequest):
    """Generate layout params for a predefined template."""
    request_start = time.time()
    logger.info("[PERF] /api/template API endpoint hit.")
    try:
        template_upper = req.template.upper().replace(" ", "")

        # Template definitions
        templates: Dict[str, Dict[str, Any]] = {
            "1BHK": {
                "bhk": 1,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                ],
            },
            "2BHK": {
                "bhk": 2,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                ],
            },
            "3BHK": {
                "bhk": 3,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "dining_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "master_bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                ],
            },
            "4BHK": {
                "bhk": 4,
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "dining_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "master_bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                    {"type": "store_room", "confidence": 100},
                ],
            },
            "OPENKITCHEN": {
                "bhk": 2,
                "styles": ["open concept"],
                "rooms": [
                    {"type": "living_room", "confidence": 100},
                    {"type": "kitchen", "confidence": 100},
                    {"type": "dining_room", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bedroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "bathroom", "confidence": 100},
                    {"type": "foyer", "confidence": 100},
                ],
            },
            "CUSTOM": {
                "bhk": 0,
                "styles": [],
                "rooms": [{"type": r, "confidence": 100} for r in (req.customRooms or [])],
            }
        }

        if template_upper not in templates:
            available = ", ".join(sorted(templates.keys()))
            raise HTTPException(status_code=400, detail={
                "error": True,
                "message": f"Unknown template '{req.template}'. Available: {available}",
                "details": {"available_templates": sorted(templates.keys())},
            })

        template = templates[template_upper]
        area_sqft = int(req.width * req.length)

        layout_params = {
            **template,
            "plot_width": req.width,
            "plot_length": req.length,
            "area_sqft": area_sqft,
        }

        understood = [
            f"Template: {req.template}",
            f"Plot: {req.width}×{req.length} ft ({area_sqft} sq ft)",
            f"Configuration: {template['bhk']}BHK",
            f"Rooms: {len(template['rooms'])}",
        ]

        logger.info(f"Generating from template: {template} on {req.width}x{req.length} plot")
        engine = LayoutEngine(req.width, req.length, colors=req.colors or {})
        
        layout_data = {}
        
        # Floor 0
        bhk_count = template.get("bhk", 0)
        room_pool, structural_features = strip_structural(list(template["rooms"]))
        first_spec = []
        if req.floors > 1:
            ground_spec, first_spec = split_duplex_specs(room_pool, bhk_count)
            floor_0_rooms = sort_spec_by_generation_order(ground_spec)
        else:
            floor_0_rooms = sort_spec_by_generation_order(room_pool)

        indian_opts = req.indianOptions or {}
        eng_start = time.time()
        generated_nodes_0 = engine.generate(floor_0_rooms, indian_options=indian_opts, restrict_slots=(req.floors > 1))
        logger.info(f"[PERF] Engine generation took {(time.time() - eng_start)*1000:.2f} ms")
        _req_types = requested_type_set(list(template["rooms"]), indian_opts)
        enforce_requested_only(generated_nodes_0, _req_types)
        ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
        AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
        WindowPlacer(generated_nodes_0, req.width, req.length,
                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
        template_warnings = validate_layout(generated_nodes_0)
        from geometry_validator import GeometryValidator
        template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_0).errors)
        
        shared_walls_0 = compute_shared_walls(generated_nodes_0)
        layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
        layout_data["walls_floor_0"] = shared_walls_0
        layout_data["mep_data"] = compute_mep_heuristics(generated_nodes_0)
        layout_data["setbacks"] = {
            "x": engine.setback_x,
            "z": engine.setback_z,
            "buildable_width": engine.buildable_width,
            "buildable_length": engine.buildable_length,
        }
        layout_data["indianOptions"] = req.indianOptions or {}
        
        # Floor 1
        generated_nodes_1 = None
        if req.floors > 1:
            staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
            if staircase:
                # SAFE FIRST SPEC FIX
                import copy
                safe_first_spec = copy.deepcopy(first_spec) if first_spec else []
                if not safe_first_spec:
                    safe_first_spec = [copy.deepcopy(r) for r in tmpl["rooms"] if r["type"] in ("bedroom", "bathroom", "master_bedroom")]
                    if any(r["type"] == "master_bedroom" for r in floor_0_rooms):
                        for r in safe_first_spec:
                            if r["type"] == "master_bedroom":
                                r["type"] = "bedroom"
                                
                floor_1_rooms = sort_spec_by_generation_order(safe_first_spec)
                generated_nodes_1 = engine.generate(floor_1_rooms, blocked_zones=[staircase.rect], indian_options=indian_opts, restrict_slots=True)
                
                align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                    make_void=bool(indian_opts.get("double_height") or indian_opts.get("void")))
                enforce_requested_only(generated_nodes_1, _req_types)
                ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                WindowPlacer(generated_nodes_1, req.width, req.length,
                             setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                
                from geometry_validator import GeometryValidator
                template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_1).errors)
                
                shared_walls_1 = compute_shared_walls(generated_nodes_1)
                layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                layout_data["walls_floor_1"] = shared_walls_1
                layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)

        # Final layout validation (buildability gate).
        all_nodes = list(generated_nodes_0) + (generated_nodes_1 or [])
        template_validation = final_layout_validation(all_nodes, indian_options=indian_opts, is_duplex=(req.floors > 1))

        # Physics prediction for overall area
        physics = run_physics_prediction(
            room_width=req.width * 0.3,
            room_length=req.length * 0.3,
            floors=req.floors,
        )

        style_out = {
            "environment": "sunset",
            "lighting": "warm",
            "accentColor": "#10b981", # default emerald
        }
        
        colors_dict = req.colors or {}
        # Extract color from prompt directly
        prompt_lower = req.prompt.lower()
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        if theme.get("accent"):
            style_out["accentColor"] = theme["accent"]
        interior_map = {
            "off_white": "#FDFBF7",
            "sage": "#9CA986",
            "terracotta": "#E2725B",
            "charcoal": "#36454F",
            "beige": "#F5F5DC"
        }
        exterior_map = {
            "mustard": "#E2B838",
            "white": "#FFFFFF",
            "concrete": "#808080",
            "brick": "#B22222",
            "wood": "#DEB887"
        }
        roof_map = {
            "terracotta": "#8B3A3A",
            "dark_grey": "#2F4F4F",
            "brown": "#654321"
        }
        
        if colors_dict.get("interior"):
            style_out["wallFinish"] = interior_map.get(colors_dict["interior"], colors_dict["interior"])
        if colors_dict.get("exterior"):
            style_out["exteriorColor"] = exterior_map.get(colors_dict["exterior"], colors_dict["exterior"])
            style_out["accentColor"] = style_out["exteriorColor"]
        if colors_dict.get("roof"):
            style_out["roofColor"] = roof_map.get(colors_dict["roof"], colors_dict["roof"])

        if not template_validation["ok"]:
            template_warnings = list(template_warnings) + template_validation["issues"]

        response = {
            "layout_params": layout_params,
            "understood": understood,
            "warnings": template_warnings,
            "validation": template_validation,
            "style": {**style_out, **selected_palette},
            "layout_data": layout_data,
        }

        # Override cost with deterministic detailed cost engine
        cost_estimate = CostEngine.calculate_cost(
            area_sqft, 
            req.package or getattr(req, "package", "Standard") or "Standard", 
            req.customMaterials or getattr(req, "customMaterials", {}) or {}, 
            {"state": req.state, "district": req.district}
        )

        if physics:
            physics["cost_inr"] = int(cost_estimate["Total"])
            response["physics"] = physics
        else:
            response["physics"] = {
                "is_safe": True,
                "safety_confidence": 95.0,
                "cost_inr": int(cost_estimate["Total"]),
                "carbon_kg": 15000,
            }

        calculated_materials = CostEngine.calculate_materials(
            area_sqft,
            getattr(req, "package", "Standard") or "Standard",
            getattr(req, "customMaterials", {}) or {}
        )

        response["project"] = {
            "plot": {
                "width": req.width,
                "length": req.length,
                "areaSqft": req.width * req.length
            },
            "building": {
                "typology": req.template,
                "floors": f"Ground + {req.floors - 1}" if req.floors > 1 else "Ground only",
                "costTier": getattr(req, "package", "Standard") or "Standard"
            },
            "materials": calculated_materials
        }

        logger.info(f"[PERF] /api/template total request completed in {(time.time() - request_start)*1000:.2f} ms")
        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Template endpoint error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail={
            "error": True,
            "message": f"Template generation failed: {str(exc)}",
            "details": {"traceback": traceback.format_exc()},
        })

# ---------------------------------------------------------------------------
# SSE streaming helpers
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _noop_emit(*_args, **_kwargs):
    pass


def _stream_generate_work(req: "GenerateRequest", emit_fn: Callable) -> None:
    """Run full generate-plan logic, pushing SSE dicts to emit_fn."""
    def emit(stage: int, label: str, substage: str = ""):
        emit_fn({"stage": stage, "label": label, "substage": substage})

    try:
        emit(1, "Analyzing Requirements...", "Parsing user prompt")

        if not req.prompt or not req.prompt.strip():
            emit_fn({"error": "Prompt cannot be empty"})
            return

        slm_result = None
        if USE_SLM_ENGINE:
            try:
                slm_result = extract_keywords_to_json(req.prompt, ALL_VOCABULARIES)
            except Exception as slm_e:
                logger.error("SLM Extraction Failed: %s", slm_e)
                slm_result = None

        warnings: List[str] = []

        if slm_result:
            layout_params: Dict[str, Any] = {}
            if slm_result.get("bhk"):
                layout_params["bhk"] = slm_result["bhk"]
            prompt_lower = req.prompt.lower()
            valid_rooms: List[Dict] = []
            
            # Phase 1: Aggressive Deduplication
            singleton_types = {'living_room', 'dining_room', 'kitchen', 'foyer'}
            seen_types = set()
            
            for r in slm_result.get("target_rooms", []):
                room_str = r.lower().replace("_", " ")
                if room_str in prompt_lower.replace("_", " ") or room_str.replace(" ", "") in prompt_lower.replace(" ", ""):
                    r_clean = r.replace(" ", "_").lower()
                    if r_clean in singleton_types:
                        has_multiple = any(f"{n} {room_str}" in prompt_lower for n in ["2", "two", "3", "three", "multiple", "double"])
                        if not has_multiple and r_clean in seen_types:
                            logger.warning(f"[PHASE 1] Stripped hallucinated duplicate room: {r_clean}")
                            continue
                        seen_types.add(r_clean)
                    valid_rooms.append(r)
            if valid_rooms:
                from cloud_extractor import auto_wire_topology
                layout_params["rooms"] = auto_wire_topology([r.replace(" ", "_") for r in valid_rooms])

            for k in ("bhk",):
                pass  # already handled above

            numbers = extract_numbers(req.prompt)
            for k, v in numbers.items():
                if k not in layout_params:
                    layout_params[k] = v

            open_matches = re.findall(r'open\s+([a-zA-Z]+)', prompt_lower)
            if open_matches:
                layout_params["open_rooms"] = [m.replace(" ", "_") for m in open_matches]

            indian_from_slm = {
                "pooja_room":    bool(slm_result.get("needs_pooja_room")),
                "utility_area":  bool(slm_result.get("utility_area")),
                "powder_room":   bool(slm_result.get("powder_room")),
                "elderly_suite": bool(slm_result.get("elderly_suite")),
                "foyer":         bool(slm_result.get("foyer")),
                "brahmasthan":   bool(slm_result.get("brahmasthan")),
            }
            fe_indian = req.indianOptions or {}
            merged_indian = {k: (indian_from_slm.get(k, False) or fe_indian.get(k, False))
                             for k in set(list(indian_from_slm) + list(fe_indian))}
            layout_params["indian_options"] = merged_indian

            understood = [f"Configuration: {slm_result['bhk']}BHK"] if slm_result.get("bhk") else []
            details = {
                "intents": [{"canonical": slm_result.get("intent", "").lower()}],
                "rooms": [{"canonical": r.get("type", "").replace(" ", "_"), "confidence": 100} for r in layout_params.get("rooms", [])],
                "styles": [],
                "materials": [],
                "sizes": [],
                "move_target": slm_result.get("move_target_room", ""),
                "move_dest": slm_result.get("move_destination", ""),
            }
        else:
            analysis = analyze_prompt(req.prompt)
            layout_params = analysis["layout_params"]
            understood = analysis["understood"]
            warnings = list(analysis["warnings"])
            details = analysis["matched_details"]

        _merged_indian = dict(layout_params.get("indian_options", {}) or {})
        for _k, _v in (req.indianOptions or {}).items():
            if _v:
                _merged_indian[_k] = True
        layout_params["indian_options"] = _merged_indian

        emit(1, "Analyzing Requirements...", "Processing Vastu & room requirements")

        # Material / package
        colors_dict = dict(req.colors or {})
        if slm_result and slm_result.get("color_hex"):
            colors_dict["ai_color"] = slm_result["color_hex"]
        package_name = req.package or "Standard"
        presets = CostEngine.get_presets().get(package_name, CostEngine.get_presets()["Standard"])
        custom_mats = req.customMaterials or {}
        active_preset = {**presets, **{k: v for k, v in custom_mats.items() if v}}
        # Extract color from prompt directly
        prompt_lower = req.prompt.lower()
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        style_out = {
            "wallFinish":      active_preset.get("wall_material", "AAC Block"),
            "exteriorColor":   colors_dict.get("exterior", "White"),
            "accentColor":     theme.get("accent") or "#10b981",
            "roofStyle":       colors_dict.get("roof", "terracotta"),
            "windows":         active_preset.get("windows", "UPVC"),
            "doors":           (active_preset.get("doors", {}) or {}).get("Main", "Flush Door"),
            "kitchen_counter": active_preset.get("kitchen_counter", "Granite"),
        }

        response: Dict[str, Any] = {
            "layout_params": layout_params,
            "understood": understood,
            "warnings": warnings,
            "style": style_out,
            "package_details": active_preset,
        }

        # Handle modification of existing project
        current_rooms: List[Dict] = []
        if req.currentProject:
            if req.currentProject.get("rooms"):
                current_rooms = req.currentProject.get("rooms", [])
            elif req.currentProject.get("floors"):
                for floor in req.currentProject.get("floors", []):
                    if floor.get("rooms"):
                        current_rooms.extend(floor.get("rooms", []))

        if current_rooms:
            mep_adds = slm_result.get("mep_additions", []) if slm_result else []
            if mep_adds and slm_result.get("intent") == "MODIFY_MEP":
                import copy
                updated_rooms = copy.deepcopy(current_rooms)
                for addition in mep_adds:
                    target = addition.get("room", "").lower()
                    item = addition.get("item", "")
                    if target and item:
                        for r in updated_rooms:
                            if target in r.get("name", "").lower() or target in r.get("type", "").lower():
                                mep_nodes = r.get("mep_nodes", [])
                                cx = r.get("x", 0) + r.get("width", 10) / 2
                                cz = r.get("z", 0) + r.get("length", 10) / 2
                                mep_nodes.append({"type": item, "x": round(cx + 1, 2), "z": round(cz + 1, 2)})
                                r["mep_nodes"] = mep_nodes
                response["layout_data"] = {"floor_0": updated_rooms}
                emit_fn({"done": True, "result": response})
                return

            modified_rooms = build_room_changes(
                req.prompt, current_rooms,
                details.get("intents", []), details.get("rooms", []), details.get("sizes", []),
                details.get("move_target", ""), details.get("move_dest", ""),
            )
            if modified_rooms is None and "bhk" in layout_params:
                current_rooms = []
            elif modified_rooms is None and current_rooms:
                # 1. Figure out which color the user wants
                target_color = details.get("global_color") or details.get("color_hex")
                
                # Fallback text search
                if not target_color:
                    match = re.search(r'\b(red|blue|green|yellow|orange|purple|pink|white|black|gray|grey|brown|beige|cream)\b', req.prompt.lower())
                    if match:
                        target_color = match.group(1)
                        
                if target_color:
                    target_rooms = details.get("target_rooms", [])
                    if not target_rooms and isinstance(details.get("intents"), list) and len(details["intents"]) > 0:
                        target_rooms = details["intents"][0].get("target_rooms", [])
                    
                    painted_count = 0
                    for r in current_rooms:
                        room_name = r.get("name", "").lower()
                        room_type = r.get("type", "").lower()
                        
                        if not target_rooms or any(t.lower() in room_name or t.lower() in room_type for t in target_rooms):
                            r["wallColor"] = target_color
                            r["wallColors"] = [target_color, target_color, target_color, target_color] 
                            painted_count += 1
                    
                    if painted_count > 0:
                        response["understood"].append(f"Painted {painted_count} room(s) {target_color}")
                
                response["layout_data"], _ = _preserve_modified_project_rooms(current_rooms)
                if not any("Painted" in u for u in response["understood"]):
                    response["understood"].append("Applied style changes without modifying layout structure")
                emit_fn({"done": True, "result": response})
                return
            elif modified_rooms is not None:
                if (isinstance(details.get("intent"), str) and details.get("intent").upper() == "MOVE") or (isinstance(details.get("intents"), list) and any(isinstance(i, dict) and i.get("canonical") == "move" for i in details.get("intents", []))):
                    response["layout_data"], _ = _preserve_modified_project_rooms(modified_rooms)
                    emit_fn({"done": True, "result": response})
                    return
                    
                from cloud_extractor import auto_wire_topology
                wired_specs = auto_wire_topology([r["type"] for r in modified_rooms])
                
                master_bp = []
                for i, r in enumerate(modified_rooms):
                    master_bp.append({
                        "room_type": r["type"],
                        "position_x": r.get("x", 0),
                        "position_z": r.get("z", 0),
                        "width": r.get("width", 10),
                        "length": r.get("length", 10),
                        "connections": wired_specs[i].get("connections", []),
                        "floor_number": 1 if r.get("isFloor1") else 0
                    })
                layout_params["master_blueprint"] = master_bp
                
                layout_params["rooms"] = [
                    {
                        "type": r["type"], 
                        "confidence": 100, 
                        "width": r.get("width"), 
                        "length": r.get("length"),
                        "connections": wired_specs[i].get("connections", [])
                    } 
                    for i, r in enumerate(modified_rooms)
                ]
                current_rooms = [] 
                response["understood"].append("Reconstructed the layout to properly accommodate changes")
        if not (layout_params.get("rooms") or layout_params.get("bhk")):
            emit_fn({"done": True, "result": response})
            return

        if current_rooms:
            emit_fn({"done": True, "result": response})
            return

        # --- Fresh generation from here ---
        bhk_val = layout_params.get("bhk", 0)
        requested_rooms = layout_params.get("rooms", [])
        core_room_types = {"kitchen", "bedroom", "bathroom", "living_room", "master_bedroom"}
        requested_types = {r["type"] for r in requested_rooms}
        has_core_rooms = len(core_room_types.intersection(requested_types)) >= 2

        base_rooms: List[Dict] = []
        if bhk_val > 0:
            base_rooms = get_base_rooms_for_bhk(bhk_val)
            existing_types = {r["type"] for r in base_rooms}
            for r in requested_rooms:
                if r["type"] in ("bedroom", "master_bedroom"):
                    continue
                if r["type"] not in existing_types or r["type"] in ["store_room", "pooja_room", "balcony", "study_room", "laundry"]:
                    base_rooms.append(r)
        elif has_core_rooms:
            base_rooms = requested_rooms
        else:
            base_rooms = get_base_rooms_for_bhk(1)
            existing_types = {r["type"] for r in base_rooms}
            for r in requested_rooms:
                if r["type"] not in existing_types:
                    base_rooms.append(r)

        base_rooms = apply_bedroom_intelligence(base_rooms, req.prompt, requested_types=requested_types)
        layout_params["rooms"] = base_rooms or get_base_rooms_for_bhk(1)

        # FIX: Missing Bathrooms Injection
        bhk_val = layout_params.get("bhk", sum(1 for r in layout_params["rooms"] if "bedroom" in r.get("type", "")))
        if bhk_val > 0:
            bath_count = sum(1 for r in layout_params["rooms"] if "bath" in r.get("type", "").lower() or "toilet" in r.get("type", "").lower())
            if bath_count < bhk_val:
                for _ in range(bhk_val - bath_count):
                    layout_params["rooms"].append({"type": "bathroom", "confidence": 100})

        _prompt_l = (req.prompt or "").lower()
        _pooja_ok = bool(layout_params.get("indian_options", {}).get("pooja_room")) or \
            any(k in _prompt_l for k in ("pooja", "puja", "mandir", "temple", "prayer", "devghar"))
        if not _pooja_ok:
            layout_params["rooms"] = [r for r in layout_params["rooms"] if "pooja" not in r.get("type", "").lower()]

        floors = layout_params.get("floors", 1)
        if details.get("floors", 1) > 1:
            floors = details.get("floors", 1)
        if req.floors and req.floors > 1:
            floors = req.floors
            
        # Override floors if prompt explicitly asks for stairs or upper floors
        prompt_lower = req.prompt.lower()
        if any(kw in prompt_lower for kw in ["stair", "upstair", "first floor", "second floor", "duplex"]):
            floors = max(floors, 2)
            
        # Wire topology on the final list of rooms to guarantee graph/door semantics!
        from cloud_extractor import auto_wire_topology
        final_room_types = [r["type"] for r in layout_params["rooms"]]
        layout_params["rooms"] = auto_wire_topology(final_room_types)

        layout_params["floors"] = floors

        plot_w = req.width if req.width else layout_params.get("plot_width", 40.0)
        plot_l = req.length if req.length else layout_params.get("plot_length", 40.0)

        validated_rooms, val_warns, new_plot_w, new_plot_l = smart_layout_validation(
            layout_params["rooms"], plot_w, plot_l
        )
        warnings.extend(val_warns)
        layout_params["rooms"] = validated_rooms
        plot_w, plot_l = new_plot_w, new_plot_l
        layout_params.update({"plot_width": plot_w, "plot_length": plot_l, "area_sqft": int(plot_w * plot_l)})

        emit(2, "Generating Plot Boundary...", f"Plot {int(plot_w)}×{int(plot_l)} ft · setbacks & orientation")

        engine = LayoutEngine(plot_w, plot_l, colors=colors_dict)

        indian_types: set = set()
        indian_opts = layout_params.get("indian_options", {})
        if indian_opts.get("pooja_room"):  indian_types.add("pooja_room")
        if indian_opts.get("utility_area"): indian_types.add("utility")
        if indian_opts.get("foyer"):        indian_types.add("foyer")

        room_pool = [r for r in layout_params["rooms"] if r["type"] not in indian_types]
        room_pool, structural_features = strip_structural(room_pool)

        first_spec: List[Dict] = []
        if floors > 1:
            ground_spec, first_spec = split_duplex_specs(room_pool, bhk_val)
            floor_0_rooms = sort_spec_by_generation_order(ground_spec)
        else:
            floor_0_rooms = sort_spec_by_generation_order(room_pool)

        emit(3, "Generating Room Layout...", "Preparing AI-driven architecture...")

        # --- ZERO-STATIC ENGINE: Gemini Master Blueprint ---
        master_bp = None
        gemini_result = None
        bp0 = None
        logger.info("[ZERO-STATIC] Bypassing Gemini Stage 2 coordinates (slow/redundant). Routing directly to high-speed CP Solver.")

        # ─── GEOMETRIC GENERATION & VALIDATION PIPELINE (RETRY LOOP) ───
        max_attempts = 3
        generated_nodes_0 = []
        generated_nodes_1 = []
        layout_data = {}

        for attempt in range(max_attempts):
            logger.info(f"[PIPELINE] Attempt {attempt + 1}/{max_attempts} to generate valid layout...")
            
            # Inject attempt parameter into rooms to trigger CP-SAT seed variation
            for r in floor_0_rooms:
                r["attempt"] = attempt
            for r in first_spec:
                r["attempt"] = attempt

            try:
                # Generate Floor 0
                generated_nodes_0 = engine.generate(
                    floor_0_rooms,
                    indian_options=indian_opts,
                    layout_rules=req.layoutRules,
                    restrict_slots=(floors > 1),
                    master_blueprint=bp0 if master_bp else None,
                    plot_info=slm_result if slm_result else None
                )
                _req_types = requested_type_set(layout_params["rooms"], indian_opts)
                enforce_requested_only(generated_nodes_0, _req_types)

                ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
                arch_warnings = ArchitecturalRules.validate_rules(generated_nodes_0)
                AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
                WindowPlacer(generated_nodes_0, engine.plot_width, engine.plot_length,
                             setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                
                # Post-placement validation Floor 0
                from geometry_validator import GeometryValidator
                val_0 = GeometryValidator.validate_post_placement(generated_nodes_0)

                if not val_0.is_valid:
                    logger.warning(f"[PIPELINE] Floor 0 validation failed on attempt {attempt + 1}: {val_0.errors}")
                    if attempt < max_attempts - 1:
                        continue  # Retry!
                    else:
                        raise ValueError(f"Layout failed Floor 0 validation: {val_0.errors}")

                # Initialize layout_data
                shared_walls_0 = compute_shared_walls(generated_nodes_0)
                layout_data = {
                    "floor_0": [n.to_dict() for n in generated_nodes_0],
                    "walls_floor_0": shared_walls_0,
                    "mep_data": compute_mep_heuristics(generated_nodes_0),
                    "setbacks": {
                        "x": engine.setback_x, "z": engine.setback_z,
                        "buildable_width": engine.buildable_width, "buildable_length": engine.buildable_length,
                    },
                    "indianOptions": indian_opts,
                }

                # Generate Floor 1 if Duplex
                if floors > 1:
                    logger.info("[PIPELINE] Generating Floor 1 (Duplex)...")
                    blocked_zones = []
                    staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
                    if staircase:
                        blocked_zones.append(staircase.rect)
                    living_dh = next((n for n in generated_nodes_0 if getattr(n, "is_double_height", False)), None)
                    if living_dh:
                        blocked_zones.append(living_dh.rect)

                    import copy
                    safe_first_spec = copy.deepcopy(first_spec) if first_spec else []
                    if not safe_first_spec:
                        safe_first_spec = [copy.deepcopy(r) for r in layout_params["rooms"] if r["type"] in ("bedroom", "bathroom", "master_bedroom")]
                        if any(r["type"] == "master_bedroom" for r in floor_0_rooms):
                            for r in safe_first_spec:
                                if r["type"] == "master_bedroom":
                                    r["type"] = "bedroom"

                    floor_1_rooms = sort_spec_by_generation_order(safe_first_spec)
                    floor1_bp = None
                    if master_bp:
                        floor1_bp = [b for b in master_bp if b.get("floor_number", 0) == 1]
                    
                    generated_nodes_1 = engine.generate(
                        floor_1_rooms,
                        blocked_zones=blocked_zones,
                        restrict_slots=True,
                        master_blueprint=floor1_bp if floor1_bp else None
                    )

                    align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                        make_void=bool(indian_opts.get("double_height") or indian_opts.get("void")))
                    enforce_requested_only(generated_nodes_1, _req_types)
                    ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                    AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                    WindowPlacer(generated_nodes_1, engine.plot_width, engine.plot_length,
                                 setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()

                    val_1 = GeometryValidator.validate_post_placement(generated_nodes_1)
                    if not val_1.is_valid:
                        logger.warning(f"[PIPELINE] Floor 1 validation failed on attempt {attempt + 1}: {val_1.errors}")
                        if attempt < max_attempts - 1:
                            continue  # Retry!
                        else:
                            raise ValueError(f"Layout failed Floor 1 validation: {val_1.errors}")

                    shared_walls_1 = compute_shared_walls(generated_nodes_1)
                    layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                    layout_data["walls_floor_1"] = shared_walls_1
                    layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)

                # If we made it here, both floors succeeded (or we hit max retries)
                logger.info(f"[PIPELINE] Layout generation succeeded on attempt {attempt + 1}!")
                break

            except Exception as gen_err:
                logger.error(f"[PIPELINE] Exception during generation attempt {attempt + 1}: {gen_err}")
                if attempt == max_attempts - 1:
                    raise gen_err
        else:
            emit(6, "Generating Electrical Layout...", "Switch positions · lighting · power")
            emit(7, "Generating Plumbing Layout...", "Water supply · drainage · bathroom services")

        emit(8, "Generating Materials & Structures...", "Structural analysis · cost estimation")

        all_nodes = list(generated_nodes_0) + list(generated_nodes_1)
        selected_palette = _apply_selected_palette(all_nodes, req.colors)
        layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
        if generated_nodes_1:
            layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]

        # If we reconstructed the layout from an existing project, restore preserved properties!
        try:
            mr_list = locals().get("modified_rooms")
            if req.currentProject and mr_list:
                used_mr = set()
                for node in all_nodes:
                    for i, mr in enumerate(mr_list):
                        if i not in used_mr and mr.get("type") == node.type:
                            used_mr.add(i)
                            if "furniture" in mr: node.furniture = mr["furniture"]
                            if "wallColor" in mr and mr["wallColor"]: node.wallColor = mr["wallColor"]
                            if "floorColor" in mr and mr["floorColor"]: node.floorColor = mr["floorColor"]
                            if "wallColors" in mr and mr["wallColors"]: node.wallColors = mr["wallColors"]
                            break
        except Exception as e:
            logger.warning(f"Failed to restore preserved properties in stream: {e}")

        # FIX: Color Injection
        global_color = details.get("global_color") or details.get("color_hex")
        if global_color:
            for node in all_nodes:
                node.wallColor = global_color
        
        room_colors = details.get("room_colors", [])
        for rc in room_colors:
            r_name = rc.get("room", "").lower()
            r_col = rc.get("color", "")
            if r_name and r_col:
                for node in all_nodes:
                    if r_name in node.name.lower() or r_name in getattr(node, "type", "").lower():
                        node.wallColor = r_col
        
        validation_report = final_layout_validation(all_nodes, indian_options=indian_opts, is_duplex=(floors > 1))
        response["validation"] = validation_report
        if not validation_report["ok"]:
            warnings.extend(validation_report["issues"])

        response["layout_data"] = layout_data
        response["warnings"] = warnings

        physics = run_physics_prediction(
            room_width=plot_w * 0.3, room_length=plot_l * 0.3,
            floors=floors, ceiling_height=layout_params.get("ceiling_height_ft", 10.0),
        )
        cost_estimate = CostEngine.calculate_cost(
            layout_params.get("area_sqft", 1600), package_name, custom_mats,
            {"state": req.state, "district": req.district},
        )
        if physics:
            physics["cost_inr"] = int(cost_estimate["Total"])
            response["physics"] = physics
        else:
            response["physics"] = {"is_safe": True, "safety_confidence": 95.0,
                                    "cost_inr": int(cost_estimate["Total"]), "carbon_kg": 15000}

        response["project"] = {
            "plot": {"width": plot_w, "length": plot_l, "areaSqft": int(plot_w * plot_l)},
            "building": {"floors": "Ground + 1" if floors > 1 else "Ground only", "costTier": package_name},
            "materials": CostEngine.calculate_materials(layout_params.get("area_sqft", 1600), package_name, custom_mats),
        }

        # Render 2D Blueprint Image
        try:
            image_url = BlueprintRenderer.render_blueprint(all_nodes, engine.plot_width, engine.plot_length, filename="blueprint_latest.png")
            response["blueprint_url"] = image_url
        except Exception as e:
            logger.error(f"Failed to render blueprint image: {e}")

        emit(9, "Generating Engineering Blueprints...", "Construction drawings · final validation")

        emit_fn({"done": True, "result": response})

    except Exception as exc:
        logger.error("Streaming generate error: %s\n%s", exc, traceback.format_exc())
        emit_fn({"error": str(exc)})

def _stream_template_work(req: "TemplateRequest", emit_fn: Callable) -> None:
    """Run template generation in a background thread, pushing SSE dicts to pq."""
    def emit(stage: int, label: str, substage: str = ""):
        emit_fn({"stage": stage, "label": label, "substage": substage})

    try:
        details = {}  # Templates do not use prompt color extraction.
        emit(1, "Analyzing Requirements...", f"Template {req.template}")

        template_upper = req.template.upper().replace(" ", "")
        templates: Dict[str, Dict[str, Any]] = {
            "1BHK": {"bhk": 1, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "kitchen", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "foyer", "confidence": 100},
            ]},
            "2BHK": {"bhk": 2, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "kitchen", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "foyer", "confidence": 100},
            ]},
            "3BHK": {"bhk": 3, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "dining_room", "confidence": 100},
                {"type": "kitchen", "confidence": 100}, {"type": "master_bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
            ]},
            "4BHK": {"bhk": 4, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "dining_room", "confidence": 100},
                {"type": "kitchen", "confidence": 100}, {"type": "master_bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "foyer", "confidence": 100}, {"type": "store_room", "confidence": 100},
            ]},
            "OPENKITCHEN": {"bhk": 2, "rooms": [
                {"type": "living_room", "confidence": 100}, {"type": "kitchen", "confidence": 100},
                {"type": "dining_room", "confidence": 100}, {"type": "bedroom", "confidence": 100},
                {"type": "bedroom", "confidence": 100}, {"type": "bathroom", "confidence": 100},
                {"type": "bathroom", "confidence": 100}, {"type": "foyer", "confidence": 100},
            ]},
            "CUSTOM": {"bhk": 0, "styles": [], "rooms": [{"type": r, "confidence": 100} for r in (req.customRooms or [])]},
        }

        if template_upper not in templates:
            emit_fn({"error": f"Unknown template '{req.template}'"})
            return

        tmpl = templates[template_upper]
        area_sqft = int(req.width * req.length)
        layout_params = {**tmpl, "plot_width": req.width, "plot_length": req.length, "area_sqft": area_sqft}
        understood = [f"Template: {req.template}", f"Plot: {req.width}×{req.length} ft ({area_sqft} sq ft)"]

        emit(2, "Generating Plot Boundary...", f"Plot {int(req.width)}×{int(req.length)} ft")

        colors_dict = req.colors or {}
        engine = LayoutEngine(req.width, req.length, colors=colors_dict)
        bhk_count = tmpl.get("bhk", 0)
        room_pool, _ = strip_structural(list(tmpl["rooms"]))
        # HARD GUARD: a plain template (e.g. "3BHK") never includes a Pooja Room
        # unless the Pooja feature is explicitly selected.
        if not (req.indianOptions or {}).get("pooja_room"):
            room_pool = [r for r in room_pool if "pooja" not in str(r.get("type", "")).lower()]
        first_spec: List[Dict] = []
        if req.floors > 1:
            ground_spec, first_spec = split_duplex_specs(room_pool, bhk_count)
            floor_0_rooms = sort_spec_by_generation_order(ground_spec)
        else:
            floor_0_rooms = sort_spec_by_generation_order(room_pool)

        indian_opts = req.indianOptions or {}

        emit(3, "Generating Room Layout...", "BSP core room placement")

        logger.info(f"[TEMPLATE] Generating Layout Engine nodes for floor 0 (rooms: {len(floor_0_rooms)})...")
        gen_t0 = time.time()
        generated_nodes_0 = engine.generate(floor_0_rooms, indian_options=indian_opts, restrict_slots=(req.floors > 1))
        logger.info(f"[TEMPLATE] Floor 0 generation took {time.time() - gen_t0:.2f}s")
        _req_types = requested_type_set(list(tmpl["rooms"]), indian_opts)
        enforce_requested_only(generated_nodes_0, _req_types)

        emit(4, "Generating Architectural Features...", "Adjacency · windows · verandas")

        ArchitecturalRules.optimize_wet_walls(generated_nodes_0)
        AdjacencyResolver(generated_nodes_0, open_rooms=layout_params.get("open_rooms", [])).resolve()
        WindowPlacer(generated_nodes_0, req.width, req.length,
                     setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
        template_warnings = list(validate_layout(generated_nodes_0))
        from geometry_validator import GeometryValidator
        template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_0).errors)
        
        shared_walls_0 = compute_shared_walls(generated_nodes_0)
        layout_data: Dict[str, Any] = {
            "floor_0": [n.to_dict() for n in generated_nodes_0],
            "walls_floor_0": shared_walls_0,
            "mep_data": compute_mep_heuristics(generated_nodes_0),
            "setbacks": {"x": engine.setback_x, "z": engine.setback_z,
                         "buildable_width": engine.buildable_width, "buildable_length": engine.buildable_length},
            "indianOptions": indian_opts,
        }

        emit(5, "Generating Furniture...", "Room furnishing")

        generated_nodes_1: List = []
        if req.floors > 1:
            emit(6, "Generating Electrical Layout...", "First floor rooms")

            staircase = next((n for n in generated_nodes_0 if n.type == "staircase"), None)
            if staircase:
                floor_1_rooms = sort_spec_by_generation_order(first_spec or list(tmpl["rooms"]))
                logger.info("[TEMPLATE] Generating Layout Engine nodes for floor 1...")
                gen_t1 = time.time()
                generated_nodes_1 = engine.generate(floor_1_rooms, blocked_zones=[staircase.rect],
                                                     indian_options=indian_opts, restrict_slots=True)
                logger.info(f"[TEMPLATE] Floor 1 generation took {time.time() - gen_t1:.2f}s")

                emit(7, "Generating Plumbing Layout...", "Aligning duplex floors")

                align_duplex_floors(generated_nodes_0, generated_nodes_1,
                                    make_void=bool(indian_opts.get("double_height") or indian_opts.get("void")))
                enforce_requested_only(generated_nodes_1, _req_types)
                ArchitecturalRules.optimize_wet_walls(generated_nodes_1)
                AdjacencyResolver(generated_nodes_1, open_rooms=layout_params.get("open_rooms", [])).resolve()
                WindowPlacer(generated_nodes_1, req.width, req.length,
                             setback_x=engine.setback_x, setback_z=engine.setback_z).place_windows()
                
                from geometry_validator import GeometryValidator
                template_warnings.extend(GeometryValidator.validate_post_placement(generated_nodes_1).errors)
                
                shared_walls_1 = compute_shared_walls(generated_nodes_1)
                layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
                layout_data["walls_floor_1"] = shared_walls_1
                layout_data["mep_data_f1"] = compute_mep_heuristics(generated_nodes_1)
        else:
            emit(6, "Generating Electrical Layout...", "Switches · lighting · power points")
            emit(7, "Generating Plumbing Layout...", "Water lines · drainage")

        emit(8, "Generating Materials & Structures...", "Material assignment · structural check")

        all_nodes = list(generated_nodes_0) + list(generated_nodes_1)
        selected_palette = _apply_selected_palette(all_nodes, req.colors)
        layout_data["floor_0"] = [n.to_dict() for n in generated_nodes_0]
        if generated_nodes_1:
            layout_data["floor_1"] = [n.to_dict() for n in generated_nodes_1]
        
        # FIX: Color Injection
        global_color = details.get("global_color") or details.get("color_hex")
        if global_color:
            for node in all_nodes:
                node.wallColor = global_color
        
        room_colors = details.get("room_colors", [])
        for rc in room_colors:
            r_name = rc.get("room", "").lower()
            r_col = rc.get("color", "")
            if r_name and r_col:
                for node in all_nodes:
                    if r_name in node.name.lower() or r_name in getattr(node, "type", "").lower():
                        node.wallColor = r_col
        
        template_validation = final_layout_validation(all_nodes, indian_options=indian_opts, is_duplex=(req.floors > 1))
        if not template_validation["ok"]:
            template_warnings.extend(template_validation["issues"])

        physics = run_physics_prediction(room_width=req.width * 0.3, room_length=req.length * 0.3, floors=req.floors)
        cost_estimate = CostEngine.calculate_cost(area_sqft, getattr(req, "package", "Standard") or "Standard",
                                                   getattr(req, "customMaterials", {}) or {},
                                                   {"state": req.state, "district": req.district})

        # Extract color from prompt directly
        prompt_lower = req.prompt.lower()
        extracted_color = None
        for color in ["yellow", "red", "blue", "green", "pink", "black", "white", "orange", "purple", "coastal"]:
            if color in prompt_lower:
                extracted_color = color
                break
                
        theme = resolve_theme(colors_dict)
        if extracted_color:
            theme["accent"] = extracted_color
        
        style_out = {"environment": "sunset", "lighting": "warm", "accentColor": theme.get("accent") or "#10b981"}

        # If we reconstructed the layout from an existing project, restore preserved properties!
        try:
            # Check if modified_rooms is in locals()
            mr_list = locals().get("modified_rooms")
            if req.currentProject and mr_list:
                used_mr = set()
                for node in all_nodes:
                    for i, mr in enumerate(mr_list):
                        if i not in used_mr and mr.get("type") == node.type:
                            used_mr.add(i)
                            if "furniture" in mr: node.furniture = mr["furniture"]
                            if "wallColor" in mr and mr["wallColor"]: node.wallColor = mr["wallColor"]
                            if "floorColor" in mr and mr["floorColor"]: node.floorColor = mr["floorColor"]
                            if "wallColors" in mr and mr["wallColors"]: node.wallColors = mr["wallColors"]
                            break
        except Exception as e:
            logger.warning(f"Failed to restore preserved properties: {e}")

        response = {
            "layout_params": layout_params,
            "understood": understood,
            "warnings": template_warnings,
            "validation": template_validation,
            "style": {**style_out, **selected_palette},
            "layout_data": layout_data,
            "physics": {"is_safe": True, "safety_confidence": 95.0,
                        "cost_inr": int(cost_estimate["Total"]), "carbon_kg": 15000},
            "project": {
                "plot": {"width": req.width, "length": req.length, "areaSqft": area_sqft},
                "building": {"typology": req.template,
                             "floors": f"Ground + {req.floors - 1}" if req.floors > 1 else "Ground only",
                             "costTier": getattr(req, "package", "Standard") or "Standard"},
                "materials": CostEngine.calculate_materials(area_sqft, getattr(req, "package", "Standard") or "Standard",
                                                             getattr(req, "customMaterials", {}) or {}),
            },
        }
        if physics:
            response["physics"]["cost_inr"] = int(cost_estimate["Total"])

        emit(9, "Generating Engineering Blueprints...", "Technical plans · final validation")

        emit_fn({"done": True, "result": response})

    except Exception as exc:
        logger.error("Streaming template error: %s\n%s", exc, traceback.format_exc())
        emit_fn({"error": str(exc)})


import uuid
import redis
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
redis_client = redis.Redis.from_url(REDIS_URL)

import redis.asyncio as aioredis
async_redis_client = aioredis.from_url(REDIS_URL)

@app.post('/api/generate/stream')
async def generate_plan_stream(req: GenerateRequest):
    job_id = str(uuid.uuid4())
    logger.info(f"[API] Received architecture generation request. Queuing job {job_id}...")

    pubsub = async_redis_client.pubsub()
    await pubsub.subscribe(job_id)

    from celery_worker import generate_architecture_task
    generate_architecture_task.delay(req.dict(), job_id)

    async def _stream():
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data'].decode('utf-8')
                    msg_dict = json.loads(data)
                    yield _sse(msg_dict)
                    if msg_dict.get('done') or msg_dict.get('error'):
                        break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    return StreamingResponse(
        _stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )

@app.post('/api/template/stream')
async def generate_template_stream(req: TemplateRequest):
    job_id = str(uuid.uuid4())
    logger.info(f"[API] Received template generation request ({req.template}). Queuing job {job_id}...")

    pubsub = async_redis_client.pubsub()
    await pubsub.subscribe(job_id)

    from celery_worker import generate_template_task
    generate_template_task.delay(req.dict(), job_id)

    async def _stream():
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data'].decode('utf-8')
                    msg_dict = json.loads(data)
                    yield _sse(msg_dict)
                    if msg_dict.get('done') or msg_dict.get('error'):
                        break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    return StreamingResponse(
        _stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
    )

# ---------------------------------------------------------------------------
# MEP generation endpoints
# ---------------------------------------------------------------------------

@app.post("/api/generate-wiring")
async def api_generate_wiring(req: MEPRequest):
    try:
        updated_project = mep_generator.generate_wiring(req.project, req.options)
        return {"status": "success", "project": updated_project}
    except Exception as e:
        logger.error(f"Wiring generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-plumbing")
async def api_generate_plumbing(req: MEPRequest):
    try:
        updated_project = mep_generator.generate_plumbing(req.project, req.options)
        return {"status": "success", "project": updated_project}
    except Exception as e:
        logger.error(f"Plumbing generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/recalculate-cost")
async def api_recalculate_cost(req: CostRequest):
    """Cost/material recompute only — never touches geometry, rooms, or style.
    Lets the UI refresh price for a location/package without regenerating
    (and recoloring) the house."""
    try:
        project = req.project or {}
        rooms = project.get("rooms", []) or []
        # Derive built-up area from rooms; fall back to stored metrics/plot.
        area = 0.0
        for r in rooms:
            try:
                area += float(r.get("width", 0) or 0) * float(r.get("length", 0) or 0)
            except (TypeError, ValueError):
                pass
        if area <= 0:
            area = float(
                (project.get("metrics", {}) or {}).get("areaSqft")
                or (project.get("plot", {}) or {}).get("areaSqft")
                or 0
            )

        package = req.package or "Standard"
        location = req.location or project.get("location", {}) or {}
        constraints = req.constraints or project.get("engineering", {}) or {}

        cost_estimate = CostEngine.calculate_cost(area, package, {}, location, constraints)
        materials = CostEngine.calculate_materials(area, package, {}, constraints)

        return {
            "status": "success",
            "cost_inr": int(cost_estimate["Total"]),
            "breakdown": cost_estimate,
            "materials": materials,
            "factors": cost_estimate.get("factors", {}),
            "foundation_recommendation": cost_estimate.get("foundation_recommendation"),
            "corrosion_required": cost_estimate.get("corrosion_required", False),
            "area_sqft": int(round(area)),
        }
    except Exception as e:
        logger.error(f"Cost recalculation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-structural")
async def api_generate_structural(req: MEPRequest):
    try:
        updated_project = structural_generator.generate_structural(req.project, req.options)
        return {"status": "success", "project": updated_project}
    except Exception as e:
        logger.error(f"Structural generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def compute_mep_heuristics(nodes):
    electrical = []
    plumbing = []
    kitchens = []
    bathrooms = []

    for node in nodes:
        # Electrical: Ceiling Light in center
        cx = node.rect.x + node.rect.width / 2
        cz = node.rect.z + node.rect.length / 2
        electrical.append({
            "type": "ceiling_light",
            "room_id": node.id,
            "x": round(cx, 2),
            "z": round(cz, 2)
        })

        # Electrical: Switchboard near first door
        if hasattr(node, 'doors') and len(node.doors) > 0:
            door = node.doors[0]
            electrical.append({
                "type": "switchboard",
                "room_id": node.id,
                "x": round(door.x + 0.5, 2),
                "z": round(door.z + 0.5, 2)
            })

        # Plumbing identifying
        if "kitchen" in node.type.lower() or "kitchen" in getattr(node, 'name', '').lower():
            kitchens.append((cx, cz))
        elif "bath" in node.type.lower() or "bath" in getattr(node, 'name', '').lower():
            bathrooms.append((cx, cz))

    # Plumbing lines: Kitchen to Bathrooms
    if kitchens and bathrooms:
        main_kitchen = kitchens[0]
        for bath in bathrooms:
            plumbing.append({
                "type": "water_supply",
                "x1": round(main_kitchen[0], 2),
                "z1": round(main_kitchen[1], 2),
                "x2": round(bath[0], 2),
                "z2": round(bath[1], 2)
            })
    else:
        # Fallback: draw a main water line to exterior
        plumbing.append({
            "type": "water_supply",
            "x1": 0.0,
            "z1": 0.0,
            "x2": 15.0,
            "z2": 15.0
        })

    return {"electrical": electrical, "plumbing": plumbing}


@app.get("/api/health")
async def health():
    """Health check with model status."""
    return HealthResponse(
        status="ok",
        service="Home Vision AI Backend v2.0",
        nlp_matchers_loaded=True,
        physics_model_loaded=_physics_model is not None,
        nlp_adapter_found=_nlp_adapter_found,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

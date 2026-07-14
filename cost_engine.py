# cost_engine.py
from typing import Dict, Any, List, Optional

MATERIAL_MAPPINGS = {
    "wall_material": {
        "Clay Brick": {"color": "#b25132", "roughness": 0.9, "cost_sqft": 45},
        "Fly Ash Brick": {"color": "#a8a29e", "roughness": 0.8, "cost_sqft": 35},
        "AAC Block": {"color": "#e5e5e5", "roughness": 0.7, "cost_sqft": 60},
        "Concrete Block": {"color": "#d4d4d8", "roughness": 0.85, "cost_sqft": 40},
        "Stone Masonry": {"color": "#78716c", "roughness": 1.0, "cost_sqft": 150}
    },
    "roof_type": {
        "RCC Slab": {"color": "#e2e8f0", "type": "flat", "cost_sqft": 180},
        "Sloped RCC": {"color": "#e2e8f0", "type": "hipped", "cost_sqft": 220},
        "Metal Roofing": {"color": "#94a3b8", "type": "gabled", "metalness": 0.7, "cost_sqft": 120},
        "Clay Tile Roof": {"color": "#9a3412", "type": "gabled", "cost_sqft": 160}
    },
    "flooring": {
        "Ceramic Tile": {"color": "#f8fafc", "roughness": 0.4, "metalness": 0.1, "cost_sqft": 45},
        "Vitrified Tile": {"color": "#f1f5f9", "roughness": 0.2, "metalness": 0.2, "cost_sqft": 80},
        "Marble": {"color": "#ffffff", "roughness": 0.1, "metalness": 0.3, "cost_sqft": 150},
        "Granite": {"color": "#1c1917", "roughness": 0.1, "metalness": 0.4, "cost_sqft": 200},
        "Wooden Flooring": {"color": "#78350f", "roughness": 0.6, "metalness": 0.0, "cost_sqft": 120},
        "Italian Marble": {"color": "#fafaf9", "roughness": 0.05, "metalness": 0.5, "cost_sqft": 450},
        "Anti-Skid Tile": {"color": "#e2e8f0", "roughness": 0.8, "metalness": 0.0, "cost_sqft": 50},
        "Exterior Tile": {"color": "#a8a29e", "roughness": 0.9, "metalness": 0.0, "cost_sqft": 60},
        "Stone Pavers": {"color": "#57534e", "roughness": 1.0, "metalness": 0.0, "cost_sqft": 90}
    },
    "paint": {
        "Distemper": {"color": "#fdf8f6", "cost_sqft": 15},
        "Emulsion": {"color": "#f8fafc", "cost_sqft": 25},
        "Premium Emulsion": {"color": "#ffffff", "cost_sqft": 40},
        "Luxury Paint": {"color": "#fdfbf7", "cost_sqft": 65},
        "Acrylic Paint": {"color": "#f1f5f9", "cost_sqft": 30},
        "Weatherproof Paint": {"color": "#f8fafc", "cost_sqft": 45},
        "Texture Finish": {"color": "#e2e8f0", "cost_sqft": 75},
        "Stone Cladding": {"color": "#a8a29e", "cost_sqft": 150}
    },
    "kitchen_counter": {
        "Granite": {"color": "#1c1917", "cost_sqft": 200},
        "Quartz": {"color": "#f8fafc", "cost_sqft": 400},
        "Marble": {"color": "#ffffff", "cost_sqft": 300},
        "Solid Surface": {"color": "#f1f5f9", "cost_sqft": 600}
    },
    "windows": {
        "Wooden": {"color": "#78350f", "cost_sqft": 400},
        "Steel": {"color": "#64748b", "cost_sqft": 250},
        "Aluminum": {"color": "#94a3b8", "cost_sqft": 300},
        "UPVC": {"color": "#ffffff", "cost_sqft": 450},
        "Premium Aluminum": {"color": "#475569", "cost_sqft": 600}
    },
    "doors": {
        "Flush Door": {"color": "#d6d3d1", "cost_sqft": 150},
        "Teak Door": {"color": "#451a03", "cost_sqft": 800},
        "Engineered Wood": {"color": "#78350f", "cost_sqft": 350},
        "Designer Door": {"color": "#292524", "cost_sqft": 600},
        "Laminate Door": {"color": "#a8a29e", "cost_sqft": 200},
        "Veneer Door": {"color": "#57534e", "cost_sqft": 400}
    },
    "boundary_wall": {
        "None": {"cost_rm": 0},
        "Brick Wall": {"cost_rm": 1500},
        "RCC Wall": {"cost_rm": 3000},
        "Decorative Wall": {"cost_rm": 4500}
    },
    "parking": {
        "None": {"cost_sqft": 0},
        "Concrete": {"cost_sqft": 80},
        "Paver Blocks": {"cost_sqft": 120},
        "Stone": {"cost_sqft": 200}
    }
}

PRESETS = {
    "Economy": {
        "wall_material": "Fly Ash Brick",
        "roof_type": "RCC Slab",
        "flooring": {
            "Living Room": "Ceramic Tile",
            "Bedrooms": "Ceramic Tile",
            "Kitchen": "Anti-Skid Tile",
            "Bathrooms": "Anti-Skid Tile",
            "Balcony": "Exterior Tile",
            "Other": "Ceramic Tile"
        },
        "paint": {
            "Interior": "Distemper",
            "Exterior": "Acrylic Paint"
        },
        "kitchen_counter": "Granite",
        "windows": "Aluminum",
        "doors": {
            "Main": "Flush Door",
            "Internal": "Flush Door"
        },
        "bathroom_quality": "Economy",
        "electrical_quality": "Economy",
        "plumbing_quality": "Economy",
        "boundary_wall": "None",
        "parking": "None"
    },
    "Standard": {
        "wall_material": "AAC Block",
        "roof_type": "RCC Slab",
        "flooring": {
            "Living Room": "Vitrified Tile",
            "Bedrooms": "Vitrified Tile",
            "Kitchen": "Vitrified Tile",
            "Bathrooms": "Anti-Skid Tile",
            "Balcony": "Anti-Skid Tile",
            "Other": "Vitrified Tile"
        },
        "paint": {
            "Interior": "Emulsion",
            "Exterior": "Weatherproof Paint"
        },
        "kitchen_counter": "Granite",
        "windows": "UPVC",
        "doors": {
            "Main": "Engineered Wood",
            "Internal": "Laminate Door"
        },
        "bathroom_quality": "Standard",
        "electrical_quality": "Standard",
        "plumbing_quality": "Standard",
        "boundary_wall": "Brick Wall",
        "parking": "Concrete"
    },
    "Premium": {
        "wall_material": "AAC Block",
        "roof_type": "RCC Slab",
        "flooring": {
            "Living Room": "Marble",
            "Bedrooms": "Wooden Flooring",
            "Kitchen": "Granite",
            "Bathrooms": "Premium Anti-Skid Tile",
            "Balcony": "Stone Pavers",
            "Other": "Vitrified Tile"
        },
        "paint": {
            "Interior": "Premium Emulsion",
            "Exterior": "Texture Finish"
        },
        "kitchen_counter": "Quartz",
        "windows": "UPVC",
        "doors": {
            "Main": "Teak Door",
            "Internal": "Veneer Door"
        },
        "bathroom_quality": "Premium",
        "electrical_quality": "Premium",
        "plumbing_quality": "Premium",
        "boundary_wall": "Decorative Wall",
        "parking": "Paver Blocks"
    },
    "Luxury": {
        "wall_material": "Clay Brick",
        "roof_type": "Sloped RCC",
        "flooring": {
            "Living Room": "Italian Marble",
            "Bedrooms": "Wooden Flooring",
            "Kitchen": "Quartz",
            "Bathrooms": "Stone Finish",
            "Balcony": "Stone Pavers",
            "Other": "Marble"
        },
        "paint": {
            "Interior": "Luxury Paint",
            "Exterior": "Stone Cladding"
        },
        "kitchen_counter": "Solid Surface",
        "windows": "Premium Aluminum",
        "doors": {
            "Main": "Designer Door",
            "Internal": "Teak Door"
        },
        "bathroom_quality": "Luxury",
        "electrical_quality": "Premium",
        "plumbing_quality": "Premium",
        "boundary_wall": "Decorative Wall",
        "parking": "Stone"
    }
}

class CostEngine:
    @staticmethod
    def get_presets():
        return PRESETS
        
    @staticmethod
    def get_materials():
        return MATERIAL_MAPPINGS
        
    # ── Cost factor tables (single source of truth) ──────────────────────
    BASE_RATE = 1800  # INR per sq ft, baseline standard house

    STATE_INDEX = {
        "Karnataka": 1.0,        # baseline
        "Maharashtra": 1.25,     # Mumbai / metro premium
        "Delhi": 1.20,
        "Kerala": 1.10,
        "Tamil Nadu": 1.08,
        "Telangana": 1.08,
        "Gujarat": 1.05,
        "Haryana": 1.05,
        "West Bengal": 1.0,
        "Rajasthan": 0.95,
        "Madhya Pradesh": 0.92,
        "Uttar Pradesh": 0.85,   # cheaper
        "Bihar": 0.85,
        "Odisha": 0.90,
        "Assam": 0.95,
    }

    # Strength / quality multiplier (the "package")
    QUALITY_MULTIPLIER = {
        "Standard": 1.0,    # code-compliant: Fe500, fly ash bricks
        "Premium": 1.15,    # Fe550D, richer mix, AAC blocks
        "Luxury": 1.4,      # high-end finishes + max structural factor
        # legacy aliases
        "Economy": 0.9,
        "Tier 1": 1.0,
    }

    # Seismic zone bumps steel quantity (~+10-20% in higher zones) → small
    # bump to total cost (steel is ~15% of build cost).
    SEISMIC_FACTOR = {"Zone II": 1.0, "Zone III": 1.02, "Zone IV": 1.05, "Zone V": 1.08}
    # Low SBC forces Isolated → Raft foundation: extra concrete.
    SBC_FACTOR = {"High": 1.0, "Medium": 1.0, "Low": 1.05}
    # Coastal / open terrain → corrosion protection + wind bracing.
    WIND_FACTOR = {"Urban Sheltered": 1.0, "Coastal": 1.04, "Open Terrain": 1.03}

    @staticmethod
    def calculate_cost(area_sqft: float, selected_package: str, overrides: Dict[str, Any],
                       location: Dict[str, str], constraints: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        constraints = constraints or {}

        state = (location or {}).get("state", "Karnataka")
        state_index = CostEngine.STATE_INDEX.get(state, 1.0)
        quality_mult = CostEngine.QUALITY_MULTIPLIER.get(selected_package, 1.0)

        seismic = constraints.get("seismicZone", "Zone III")
        sbc = constraints.get("sbc", "Medium")
        wind = constraints.get("windExposure", "Urban Sheltered")
        seismic_factor = CostEngine.SEISMIC_FACTOR.get(seismic, 1.0)
        sbc_factor = CostEngine.SBC_FACTOR.get(sbc, 1.0)
        wind_factor = CostEngine.WIND_FACTOR.get(wind, 1.0)

        base_rate = CostEngine.BASE_RATE

        # Guard against missing/NaN area so cost never resolves to NaN.
        try:
            area_val = float(area_sqft)
            if area_val != area_val or area_val < 0:  # NaN or negative
                area_val = 0.0
        except (TypeError, ValueError):
            area_val = 0.0

        # Core two-factor formula: Area × Base × State × Quality.
        total = area_val * base_rate * state_index * quality_mult
        # Engineering constraint surcharges.
        total *= seismic_factor * sbc_factor * wind_factor

        foundation = "Raft Foundation" if sbc == "Low" else (
            "Raft Foundation" if selected_package == "Premium" and sbc == "Medium" else "Isolated Footing")

        return {
            "Material Cost": total * 0.60,
            "Labor Cost": total * 0.25,
            "Equipment Cost": total * 0.05,
            "Professional Fees": total * 0.05,
            "Total": total,
            # Factor transparency for the UI.
            "factors": {
                "base_rate": base_rate,
                "state_index": state_index,
                "quality_multiplier": quality_mult,
                "seismic_factor": seismic_factor,
                "sbc_factor": sbc_factor,
                "wind_factor": wind_factor,
            },
            "foundation_recommendation": foundation,
            "corrosion_required": wind in ("Coastal",),
        }

    @staticmethod
    def calculate_materials(area_sqft: float, selected_package: str, overrides: Dict[str, Any],
                            constraints: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        preset = PRESETS.get(selected_package, PRESETS["Standard"])
        constraints = constraints or {}
        # Higher seismic zones require more reinforcement steel (IS 1893).
        seismic_steel = {"Zone II": 1.0, "Zone III": 1.0, "Zone IV": 1.12, "Zone V": 1.2}.get(
            constraints.get("seismicZone", "Zone III"), 1.0)

        def get_mat(key, default):
            return overrides.get(key, preset.get(key, default))

        # Guard area so quantities/costs never resolve to NaN.
        try:
            a = float(area_sqft)
            if a != a or a < 0:
                a = 0.0
        except (TypeError, ValueError):
            a = 0.0

        # Rough quantities
        rcc_vol = round(a * 0.015, 1)  # m3
        steel_tons = round(a * 0.002 * seismic_steel, 2)
        bricks = round(a * 7)
        paint_area = round(a * 3.5)
        tiles_area = round(a * 1.1)
        window_sets = max(4, int(a / 150))
        door_sets = max(3, int(a / 200))

        # Unit rates (INR) — package premium scales finish/opening rates.
        pkg_factor = {"Economy": 0.85, "Standard": 1.0, "Premium": 1.35, "Luxury": 1.9}.get(selected_package, 1.0)
        rate_rcc, rate_tmt, rate_brick = 7200, 68500, 8.5
        rate_roof = 180 * pkg_factor
        rate_floor = 135 * pkg_factor
        rate_paint = 32 * pkg_factor
        rate_window = 18500 * pkg_factor
        rate_door = 12500 * pkg_factor

        def line(_id, name, category, quantity, unit_cost, qty_num):
            return {
                "id": _id, "name": name, "category": category,
                "quantity": quantity, "status": "Available",
                "unitCost": int(round(unit_cost)),
                "total": int(round(unit_cost * qty_num)),
            }

        return [
            line("rcc", "RCC Concrete M25", "Structure", f"{rcc_vol} m3", rate_rcc, rcc_vol),
            line("tmt", "Fe550D TMT Steel", "Reinforcement", f"{steel_tons} tons", rate_tmt, steel_tons),
            line("wall", get_mat("wall_material", "AAC Block"), "Walling", f"{bricks} nos", rate_brick, bricks),
            line("roof", get_mat("roof_type", "RCC Slab"), "Roofing", f"{round(a)} sqft", rate_roof, a),
            line("floor", get_mat("flooring", {}).get("Living Room", "Vitrified Tile") if isinstance(get_mat("flooring", {}), dict) else "Vitrified Tile", "Finish", f"{tiles_area} sqft", rate_floor, tiles_area),
            line("paint", get_mat("paint", {}).get("Exterior", "Weatherproof Paint") if isinstance(get_mat("paint", {}), dict) else "Paint", "Finish", f"{paint_area} sqft", rate_paint, paint_area),
            line("windows", get_mat("windows", "UPVC Windows"), "Openings", f"{window_sets} sets", rate_window, window_sets),
            line("doors", get_mat("doors", {}).get("Main", "Flush Door") if isinstance(get_mat("doors", {}), dict) else "Flush Door", "Openings", f"{door_sets} sets", rate_door, door_sets),
        ]

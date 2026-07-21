import React, { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Layout, Sparkles, Box, X, ArrowRight, Loader2, Star,
  Home, Flame, Droplets, Users, DoorOpen, Compass, ShieldCheck, Bath,
  Sun, Wind, PackageOpen, Layers, Car, Mountain, ChevronDown, CheckCircle, Palette
} from 'lucide-react';
import { useProjectStore, LAND_UNITS } from '../store/useProjectStore';

// ─── Indian Feature Definitions ──────────────────────────────────────────────
const INDIAN_FEATURES = [
  // Vastu Core & Spiritual
  { id: 'pooja_room', label: 'Pooja Room', category: 'Vastu Core', description: 'Vastu prayer room (NE corner).', icon: Flame, recommended: true, color: 'orange' },
  { id: 'brahmasthan', label: 'Brahmasthan', category: 'Vastu Core', description: 'Sacred center kept free of kitchens/baths.', icon: Compass, recommended: false, color: 'rose' },
  { id: 'angan', label: 'Angan (Courtyard)', category: 'Vastu Core', description: 'Central open-to-sky courtyard.', icon: Sun, recommended: false, color: 'amber' },
  
  // Utility & Storage
  { id: 'utility_area', label: 'Utility / Wash', category: 'Utility & Storage', description: 'Wet kitchen attached to main kitchen.', icon: Droplets, recommended: true, color: 'blue' },
  { id: 'bhandar_ghar', label: 'Bhandar Ghar', category: 'Utility & Storage', description: 'Small store room attached to kitchen.', icon: PackageOpen, recommended: false, color: 'orange' },
  { id: 'maliya', label: 'Maliya (Lofts)', category: 'Utility & Storage', description: 'Overhead storage lofts in bedrooms.', icon: Layers, recommended: true, color: 'purple' },

  // Living & Comfort
  { id: 'elderly_suite', label: 'Elderly Suite', category: 'Living & Comfort', description: 'Master bedroom forced to ground floor.', icon: Users, recommended: false, color: 'green' },
  { id: 'powder_room', label: 'Powder Room', category: 'Living & Comfort', description: 'Half-bath near Living Room for guests.', icon: Bath, recommended: false, color: 'purple' },
  { id: 'diwan', label: 'Built-in Diwan', category: 'Living & Comfort', description: 'Built-in wooden seating in living area.', icon: Home, recommended: false, color: 'amber' },
  
  // Transitional & Outdoor
  { id: 'foyer', label: 'Entry Foyer', category: 'Transitional & Outdoor', description: 'Shoe drop zone inside main door.', icon: DoorOpen, recommended: true, color: 'amber' },
  { id: 'otta', label: 'Otta / Thinnai', category: 'Transitional & Outdoor', description: 'Front raised veranda for seating.', icon: Home, recommended: false, color: 'green' },
  { id: 'portico', label: 'Car Portico', category: 'Transitional & Outdoor', description: 'Covered car porch at the front.', icon: Car, recommended: true, color: 'blue' },
  { id: 'parapet', label: 'Parapet Wall', category: 'Transitional & Outdoor', description: 'Safety wall around the terrace.', icon: ShieldCheck, recommended: true, color: 'rose' },
  { id: 'mumty', label: 'Mumty', category: 'Transitional & Outdoor', description: 'Staircase headroom box on terrace.', icon: Layout, recommended: true, color: 'orange' },
];

const COLOR_MAP = {
  orange: { bg: 'bg-orange-500/10', border: 'border-orange-500/40', text: 'text-orange-400', ring: 'ring-orange-500' },
  blue:   { bg: 'bg-blue-500/10',   border: 'border-blue-500/40',   text: 'text-blue-400',   ring: 'ring-blue-500'   },
  purple: { bg: 'bg-purple-500/10', border: 'border-purple-500/40', text: 'text-purple-400', ring: 'ring-purple-500' },
  green:  { bg: 'bg-emerald-500/10',border: 'border-emerald-500/40',text: 'text-emerald-400',ring: 'ring-emerald-500'},
  amber:  { bg: 'bg-amber-500/10',  border: 'border-amber-500/40',  text: 'text-amber-400',  ring: 'ring-amber-500'  },
  rose:   { bg: 'bg-rose-500/10',   border: 'border-rose-500/40',   text: 'text-rose-400',   ring: 'ring-rose-500'   },
};

const EXTERIOR_COLORS = [
  { id: 'ivory', name: 'Ivory Cream', hex: '#FDF5E6', desc: 'Soft neutral exterior' },
  { id: 'terracotta', name: 'Terracotta Red', hex: '#E2725B', desc: 'Coastal/Southern earthy look' },
  { id: 'cream', name: 'Cream Ivory', hex: '#FDF5E6', desc: 'Heat-reflective, soft' },
  { id: 'beige', name: 'Earthy Beige', hex: '#F5F5DC', desc: 'Modern luxury, low maintenance' },
  { id: 'peach', name: 'Coral Peach', hex: '#FFDAB9', desc: 'Welcoming soft glow' },
  { id: 'sea_green', name: 'Sea Green', hex: '#2E8B57', desc: 'Visually cooling' },
  { id: 'indigo', name: 'Indigo White', hex: '#4B0082', desc: 'Traditional Rajasthani contrast' }
];

const INTERIOR_COLORS = [
  { id: 'off_white', name: 'Pearl White', hex: '#F8F8FF' },
  { id: 'warm_beige', name: 'Warm Beige', hex: '#F5F5DC' },
  { id: 'light_grey', name: 'Light Grey', hex: '#D3D3D3' }
];

const ROOF_COLORS = [
  { id: 'terracotta', name: 'Terracotta Red', hex: '#8B3A3A' },
  { id: 'dark_grey', name: 'Charcoal Grey', hex: '#2F4F4F' },
  { id: 'brown', name: 'Earthy Brown', hex: '#654321' }
];

const FURNITURE_COLORS = [
  { id: 'light_wood', name: 'Light Wood', hex: '#C8A878' },
  { id: 'dark_wood', name: 'Dark Wood', hex: '#5A3A22' },
  { id: 'walnut', name: 'Walnut', hex: '#4B3621' },
  { id: 'modern_gray', name: 'Modern Gray', hex: '#6B7280' },
  { id: 'white_oak', name: 'White Oak', hex: '#D8C2A0' },
  { id: 'teak', name: 'Teak', hex: '#9C6B3F' },
];

const FLOOR_COLORS = [
  { id: 'marble_white', name: 'Marble White', hex: '#F1F0EC' },
  { id: 'beige_marble', name: 'Beige Marble', hex: '#E6DCC8' },
  { id: 'granite', name: 'Granite', hex: '#4A4A52' },
  { id: 'wooden_flooring', name: 'Wooden Flooring', hex: '#8B5A2B' },
  { id: 'ceramic_tile', name: 'Ceramic Tile', hex: '#D7DDE5' },
  { id: 'concrete_finish', name: 'Concrete Finish', hex: '#8B929D' },
];

// ─── Options Panels ────────────────────────────────────────────────────────
function ColorSelectionPanel({ colors, setColors }) {
  const [vastuEnabled, setVastuEnabled] = useState(colors.vastuColors || false);

  const toggleVastu = () => {
    setVastuEnabled(!vastuEnabled);
    setColors(p => ({ ...p, vastuColors: !vastuEnabled }));
  };

  return (
    <div className="mt-5 p-5 rounded-2xl bg-black/40 border border-white/10 space-y-5">
      <div className="flex items-center gap-2 mb-2">
        <Palette size={18} className="text-blue-400" />
        <h3 className="text-sm font-bold tracking-wide text-white">Color Engine & Vastu Shastra</h3>
      </div>

      <div className="flex items-center justify-between p-3 rounded-xl bg-gradient-to-r from-emerald-900/40 to-transparent border border-emerald-500/30 cursor-pointer" onClick={toggleVastu}>
        <div>
          <h4 className="text-sm font-bold text-emerald-400 flex items-center gap-2">
            <Compass size={16} /> Apply Vastu Directional Colors
          </h4>
          <p className="text-xs text-neutral-400 mt-1">Automatically assigns specific colors to rooms based on their cardinal direction (e.g. North=Green, East=White, SW=Brown).</p>
        </div>
        <div className={`w-10 h-6 rounded-full p-1 transition-colors ${vastuEnabled ? 'bg-emerald-500' : 'bg-white/10'}`}>
          <div className={`w-4 h-4 bg-white rounded-full transition-transform ${vastuEnabled ? 'translate-x-4' : 'translate-x-0'}`} />
        </div>
      </div>

      {vastuEnabled && (
        <div className="flex items-center gap-2 p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs font-semibold">
          <Compass size={14} /> Colors Controlled By Vastu Mode — manual palettes disabled
        </div>
      )}

      <div className={`grid grid-cols-1 md:grid-cols-2 gap-5 transition-opacity ${vastuEnabled ? 'opacity-40 pointer-events-none select-none' : ''}`} aria-disabled={vastuEnabled}>
        <div>
          <label className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2 block">Exterior Facade Palette</label>
          <div className="grid grid-cols-5 gap-2">
            {EXTERIOR_COLORS.map(c => (
              <button
                key={c.id}
                type="button"
                onClick={() => setColors(p => ({ ...p, exterior: c.id }))}
                className={`group relative h-10 rounded-lg border transition-all ${colors.exterior === c.id ? 'border-white ring-2 ring-white/20' : 'border-white/10 hover:border-white/40'}`}
                style={{ backgroundColor: c.hex }}
                title={`${c.name} - ${c.desc}`}
              >
                {colors.exterior === c.id && <CheckCircle size={14} className="absolute -top-1.5 -right-1.5 text-white bg-black rounded-full" />}
              </button>
            ))}
            <label className={`relative h-10 rounded-lg border transition-all cursor-pointer flex items-center justify-center ${!EXTERIOR_COLORS.find(c => c.id === colors.exterior) ? 'border-white ring-2 ring-white/20' : 'border-white/10 hover:border-white/40'}`} style={!EXTERIOR_COLORS.find(c => c.id === colors.exterior) ? { backgroundColor: colors.exterior } : { backgroundColor: '#333' }} title="Custom Color">
               <input type="color" className="absolute opacity-0 w-full h-full cursor-pointer" onChange={(e) => setColors(p => ({ ...p, exterior: e.target.value }))} />
               <span className="text-[10px] font-bold text-neutral-300 pointer-events-none drop-shadow-md">RGBA</span>
               {!EXTERIOR_COLORS.find(c => c.id === colors.exterior) && <CheckCircle size={14} className="absolute -top-1.5 -right-1.5 text-white bg-black rounded-full" />}
            </label>
          </div>
        </div>

        <div>
          <label className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2 block">Interior Base Palette</label>
          <div className="grid grid-cols-4 gap-2">
            {INTERIOR_COLORS.map(c => (
              <button
                key={c.id}
                type="button"
                onClick={() => setColors(p => ({ ...p, interior: c.id }))}
                className={`group relative h-10 rounded-lg border transition-all ${colors.interior === c.id ? 'border-blue-500 ring-2 ring-blue-500/20' : 'border-white/10 hover:border-white/40'}`}
                style={{ backgroundColor: c.hex }}
                title={c.name}
              >
                {colors.interior === c.id && <CheckCircle size={14} className="absolute -top-1.5 -right-1.5 text-blue-500 bg-black rounded-full" />}
              </button>
            ))}
            <label className={`relative h-10 rounded-lg border transition-all cursor-pointer flex items-center justify-center ${!INTERIOR_COLORS.find(c => c.id === colors.interior) ? 'border-blue-500 ring-2 ring-blue-500/20' : 'border-white/10 hover:border-white/40'}`} style={!INTERIOR_COLORS.find(c => c.id === colors.interior) ? { backgroundColor: colors.interior } : { backgroundColor: '#333' }} title="Custom Color">
               <input type="color" className="absolute opacity-0 w-full h-full cursor-pointer" onChange={(e) => setColors(p => ({ ...p, interior: e.target.value }))} />
               <span className="text-[10px] font-bold text-neutral-300 pointer-events-none drop-shadow-md">RGBA</span>
               {!INTERIOR_COLORS.find(c => c.id === colors.interior) && <CheckCircle size={14} className="absolute -top-1.5 -right-1.5 text-blue-500 bg-black rounded-full" />}
            </label>
          </div>
        </div>

        <div>
          <label className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2 block">Roof Palette</label>
          <div className="grid grid-cols-4 gap-2">
            {ROOF_COLORS.map(c => (
              <button
                key={c.id}
                type="button"
                onClick={() => setColors(p => ({ ...p, roof: c.id }))}
                className={`group relative h-10 rounded-lg border transition-all ${colors.roof === c.id ? 'border-amber-500 ring-2 ring-amber-500/20' : 'border-white/10 hover:border-white/40'}`}
                style={{ backgroundColor: c.hex }}
                title={c.name}
              >
                {colors.roof === c.id && <CheckCircle size={14} className="absolute -top-1.5 -right-1.5 text-amber-500 bg-black rounded-full" />}
              </button>
            ))}
            <label className={`relative h-10 rounded-lg border transition-all cursor-pointer flex items-center justify-center ${!ROOF_COLORS.find(c => c.id === colors.roof) ? 'border-amber-500 ring-2 ring-amber-500/20' : 'border-white/10 hover:border-white/40'}`} style={!ROOF_COLORS.find(c => c.id === colors.roof) ? { backgroundColor: colors.roof } : { backgroundColor: '#333' }} title="Custom Color">
               <input type="color" className="absolute opacity-0 w-full h-full cursor-pointer" onChange={(e) => setColors(p => ({ ...p, roof: e.target.value }))} />
               <span className="text-[10px] font-bold text-neutral-300 pointer-events-none drop-shadow-md">RGBA</span>
               {!ROOF_COLORS.find(c => c.id === colors.roof) && <CheckCircle size={14} className="absolute -top-1.5 -right-1.5 text-amber-500 bg-black rounded-full" />}
            </label>
          </div>
        </div>


      </div>
    </div>
  );
}

function IndianOptionsPanel({ options, setOptions }) {
  const [expanded, setExpanded] = useState(false);

  const categories = useMemo(() => {
    const cats = {};
    INDIAN_FEATURES.forEach(f => {
      if (!cats[f.category]) cats[f.category] = [];
      cats[f.category].push(f);
    });
    return cats;
  }, []);

  // Initialize options to empty so no features are automatically selected.
  React.useEffect(() => {
    if (Object.keys(options).length === 0) {
      setOptions({});
    }
  }, []);

  const activeCount = Object.values(options).filter(Boolean).length;

  return (
    <div className="mt-5">
      <button 
        type="button" 
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 hover:bg-amber-500/15 transition-colors"
      >
        <div className="flex items-center gap-3">
          <ShieldCheck size={20} className="text-amber-400" />
          <div className="text-left">
            <h3 className="text-sm font-bold text-amber-300">Indian Architectural Features</h3>
            <p className="text-xs text-amber-500/70">{activeCount} features enabled</p>
          </div>
        </div>
        <ChevronDown size={20} className={`text-amber-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="p-4 border border-t-0 border-amber-500/20 rounded-b-xl bg-black/20 space-y-6 max-h-[40vh] overflow-y-auto custom-scrollbar">
              {Object.entries(categories).map(([cat, features]) => (
                <div key={cat}>
                  <h4 className="text-xs font-bold text-neutral-500 uppercase tracking-wider mb-3">{cat}</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                    {features.map((feat) => {
                      const active = !!options[feat.id];
                      const c = COLOR_MAP[feat.color];
                      const Icon = feat.icon;
                      return (
                        <button
                          key={feat.id}
                          type="button"
                          onClick={() => setOptions(p => ({ ...p, [feat.id]: !p[feat.id] }))}
                          className={`relative flex items-start gap-3 p-3 rounded-xl border text-left transition-all duration-200
                            ${active ? `${c.bg} ${c.border} ring-1 ${c.ring}` : 'bg-white/[0.03] border-white/[0.08] hover:border-white/20 hover:bg-white/[0.06]'}`}
                        >
                          {feat.recommended && (
                            <span className="absolute top-2 right-2 flex items-center gap-1 text-[9px] font-bold text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded-sm">
                              <Star size={8} fill="currentColor" /> REC
                            </span>
                          )}
                          <div className={`mt-0.5 shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${active ? c.bg : 'bg-white/5'}`}>
                            <Icon size={16} className={active ? c.text : 'text-neutral-500'} />
                          </div>
                          <div className="min-w-0 pr-6">
                            <div className={`text-sm font-semibold leading-tight ${active ? 'text-white' : 'text-neutral-300'}`}>
                              {feat.label}
                            </div>
                            <div className="text-[10px] text-neutral-500 mt-1 leading-snug">{feat.description}</div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Plot Size Selector ───────────────────────────────────────────────────────
function PlotSizeSelector({ inputUnit, setInputUnit, width, setWidth, length, setLength, inputArea, setInputArea, color }) {
  const isAreaUnit = inputUnit !== 'ft' && inputUnit !== 'm';
  const focusColor = color === 'blue' ? 'focus:border-blue-500' : color === 'emerald' ? 'focus:border-emerald-500' : 'focus:border-amber-500';
  return (
    <div className="flex flex-col gap-3 w-full">
      <div className="flex items-center gap-3">
        <label className="text-sm text-neutral-400 whitespace-nowrap">Plot Size</label>
        <select
          value={inputUnit}
          onChange={e => setInputUnit(e.target.value)}
          className="bg-black/50 border border-white/10 rounded-lg px-2 py-1 text-sm text-white focus:outline-none focus:border-white/20"
        >
          <option value="ft">Feet (W × L)</option>
          <option value="m">Meters (W × L)</option>
          <optgroup label="Total Area">
            {Object.values(LAND_UNITS).map(u => (
              <option key={u.id} value={u.id}>{u.label}</option>
            ))}
          </optgroup>
        </select>
      </div>
      {isAreaUnit ? (
        <input
          type="number" required
          placeholder={`Total Area in ${Object.values(LAND_UNITS).find(u => u.id === inputUnit)?.label || 'Sq Ft'}`}
          value={inputArea === '' ? '' : inputArea}
          onChange={e => setInputArea(e.target.value === '' ? '' : Number(e.target.value))}
          className={`w-full bg-black/50 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none ${focusColor}`}
        />
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <input type="number" required placeholder={`Width (${inputUnit})`}
            value={width === '' ? '' : width}
            onChange={e => setWidth(e.target.value === '' ? '' : Number(e.target.value))}
            className={`w-full bg-black/50 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none ${focusColor}`}
          />
          <input type="number" required placeholder={`Length (${inputUnit})`}
            value={length === '' ? '' : length}
            onChange={e => setLength(e.target.value === '' ? '' : Number(e.target.value))}
            className={`w-full bg-black/50 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none ${focusColor}`}
          />
        </div>
      )}
      
    </div>
  );
}

// ─── Main Modal ───────────────────────────────────────────────────────────────
export default function ProjectSetupModal() {
  const { setOnboardingDone, generateFromTemplate, generateWithAI, apiError, clearApiError, closeSetupModal, project } = useProjectStore();
  const areaBudget = useProjectStore(state => state.lastAreaBudget);

  const [mode, setMode] = useState(null);
  const [loading, setLoading] = useState(false);

  // Dimensions
  const [inputUnit, setInputUnit] = useState('ft');
  const [width, setWidth] = useState(40);
  const [length, setLength] = useState(40);
  const floors = 1;
  const [inputArea, setInputArea] = useState(1600);

  // Colors
  const [colorPrefs, setColorPrefs] = useState({
    exterior: 'ivory',
    interior: 'off_white',
    roof: 'terracotta',
    vastuColors: false
  });

  const [template, setTemplate] = useState('3BHK');
  const [customCounts, setCustomCounts] = useState({
    living_room: 1, bedroom: 2, kitchen: 1, bathroom: 1, balcony: 0, store_room: 0, pooja_room: 0,
  });
  const [prompt, setPrompt] = useState('');
  const [indianOptions, setIndianOptions] = useState({});

  const getFinalDimensions = () => {
    if (inputUnit === 'ft') return { w: width, l: length };
    if (inputUnit === 'm')  return { w: width * 3.28084, l: length * 3.28084 };
    const unitDef = Object.values(LAND_UNITS).find(u => u.id === inputUnit);
    if (unitDef) {
      const areaSqft = inputArea * unitDef.sqftRatio;
      const w = Math.sqrt(areaSqft / 1.5);
      return { w: Math.round(w), l: Math.round(w * 1.5) };
    }
    return { w: width, l: length };
  };

  const handleGenerateTemplate = async () => {
    setLoading(true);
    const { w, l } = getFinalDimensions();
    if (!w || !l || w <= 0 || l <= 0) { setLoading(false); return; }
    // Ensure colors and specific rules are passed
    const customOptions = { ...indianOptions, promptInjection: "CRITICAL REQUIREMENT: Bathrooms MUST be attached to bedrooms. Do not place a bathroom separately from a bedroom. A bathroom must share a wall and an intersection with a bedroom." };
    await generateFromTemplate(template, w, l, floors, [], customOptions, colorPrefs, "Standard", "India");
    useProjectStore.getState().setCameraView('top');
    setLoading(false);
  };

  const handleGenerateCustom = async () => {
    setLoading(true);
    const rooms = [];
    Object.entries(customCounts).forEach(([type, count]) => {
      for (let i = 0; i < count; i++) rooms.push(type);
    });
    if (!rooms.includes('foyer')) rooms.push('foyer');
    const { w, l } = getFinalDimensions();
    if (!w || !l || w <= 0 || l <= 0) { setLoading(false); return; }
    const customOptions = { ...indianOptions, promptInjection: "CRITICAL REQUIREMENT: Bathrooms MUST be attached to bedrooms. Do not place a bathroom separately from a bedroom. A bathroom must share a wall and an intersection with a bedroom." };
    await generateFromTemplate('CUSTOM', w, l, floors, rooms, customOptions, colorPrefs, "Standard", "India");
    useProjectStore.getState().setCameraView('top');
    setLoading(false);
  };

  const handleGenerateAI = async (e) => {
    e?.preventDefault();
    const { w, l } = getFinalDimensions();
    if (!prompt.trim() || !w || !l || w <= 0 || l <= 0) return;
    setLoading(true);
    const finalPrompt = `${prompt}\n\nCRITICAL REQUIREMENT: Bathrooms MUST be attached to bedrooms. Do not place a bathroom separately from a bedroom. A bathroom must share a wall and an intersection with a bedroom.`;
    await generateWithAI(finalPrompt, w, l, indianOptions, colorPrefs, "Standard", "India", {}, floors);
    useProjectStore.getState().setCameraView('top');
    setLoading(false);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 backdrop-blur-xl p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="relative w-full max-w-4xl rounded-3xl bg-neutral-900/95 border border-white/10 shadow-2xl flex flex-col max-h-[92vh]"
      >
        {/* Header */}
        <div className="px-8 py-6 border-b border-white/[0.07] flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-2xl font-bold text-white tracking-tight">Welcome to Home Vision AI</h2>
            <p className="text-neutral-400 text-sm mt-0.5">Design your perfect Indian home — powered by local AI.</p>
          </div>
          {(mode || (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).length > 0) && (
            <button
              onClick={() => { setMode(null); clearApiError(); closeSetupModal(); }}
              className="p-2 rounded-full hover:bg-white/10 text-neutral-400 hover:text-white transition-colors"
            >
              <X size={22} />
            </button>
          )}
        </div>

        {/* Content */}
        <div className="p-8 overflow-y-auto flex-1 custom-scrollbar">
          {apiError && (
            <div className="mb-5 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {apiError}
            </div>
          )}

          {apiError && areaBudget && !areaBudget.fits && mode === 'ai' && (
            <div className="mb-5 rounded-xl border border-amber-500/25 bg-amber-500/10 p-4">
              <p className="text-sm font-semibold text-amber-200">
                This layout needs at least {areaBudget.required_sqft?.toLocaleString()} sq ft; approximately {areaBudget.available_sqft?.toLocaleString()} sq ft is buildable.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  onClick={async () => {
                    clearApiError(); setLoading(true);
                    const { w, l } = getFinalDimensions();
                    await generateWithAI(`${prompt}\nUse two floors and move suitable private rooms upstairs.`, w, l, indianOptions, colorPrefs, "Standard", "India", {}, 2);
                    setLoading(false);
                  }}
                  className="rounded-lg bg-amber-400 px-3 py-2 text-xs font-bold text-black"
                >Add a Second Floor</button>
                <button
                  onClick={() => {
                    setInputUnit('ft');
                    setWidth(areaBudget.recommended_plot?.width || width);
                    setLength(areaBudget.recommended_plot?.length || length);
                    clearApiError();
                  }}
                  className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-white"
                >Increase Plot Size</button>
                <button
                  onClick={async () => {
                    clearApiError(); setLoading(true);
                    const { w, l } = getFinalDimensions();
                    await generateWithAI(`${prompt}\nOptimize for this exact plot. Preserve essential rooms and remove the lowest-priority optional spaces first.`, w, l, indianOptions, colorPrefs, "Standard", "India", {}, floors);
                    setLoading(false);
                  }}
                  className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-white"
                >Optimize Layout</button>
              </div>
            </div>
          )}

          <AnimatePresence mode="wait">

            {/* ── Home Screen ── */}
            {!mode && (
              <motion.div key="menu" initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }}>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <button
                    onClick={() => setMode('ai')}
                    className="relative flex flex-col items-center text-center p-8 rounded-2xl bg-gradient-to-br from-amber-500/10 to-purple-600/10 border border-amber-500/30 hover:border-amber-400/60 hover:from-amber-500/15 hover:to-purple-600/15 transition-all group overflow-hidden"
                  >
                    <div className="absolute top-3 right-3 flex items-center gap-1 text-[11px] font-bold text-amber-300 bg-amber-500/15 px-2 py-1 rounded-full border border-amber-500/30">
                      <Star size={10} fill="currentColor" /> Recommended
                    </div>
                    <div className="w-16 h-16 rounded-full bg-amber-500/15 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                      <Sparkles size={30} className="text-amber-400" />
                    </div>
                    <h3 className="text-xl font-bold mb-2 text-amber-100">Generate with AI</h3>
                    <p className="text-sm text-neutral-400">Describe your dream home in plain English or Hindi.</p>
                  </button>

                  <button
                    onClick={() => setMode('custom')}
                    className="flex flex-col items-center text-center p-8 rounded-2xl bg-white/[0.04] border border-white/[0.08] hover:border-emerald-500/40 hover:bg-emerald-500/[0.07] transition-all group"
                  >
                    <div className="w-16 h-16 rounded-full bg-emerald-500/10 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                      <Box size={30} className="text-emerald-400" />
                    </div>
                    <h3 className="text-xl font-bold mb-2 text-emerald-100">Custom Builder</h3>
                    <p className="text-sm text-neutral-400">Specify exact room counts and land size for full control.</p>
                  </button>
                </div>

                <div className="mt-6 p-4 rounded-2xl bg-amber-500/5 border border-amber-500/15 flex items-start gap-3">
                  <ShieldCheck size={18} className="text-amber-400 shrink-0 mt-0.5" />
                  <p className="text-sm text-neutral-400">
                    <span className="text-amber-300 font-semibold">22 Indian Architectural features</span> — Pooja Room, Utility Wash, Foyer, Jali Screens, Sump Tanks, and Vastu Colors — are available inside each mode.
                  </p>
                </div>
              </motion.div>
            )}

            {/* ── Generate with AI ── */}
            {mode === 'ai' && (
              <motion.div key="ai" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="space-y-5">
                <form onSubmit={handleGenerateAI} className="space-y-5">
                  <div className="flex flex-wrap gap-2 mb-2">
                    {[
                      'Generate a 2BHK house',
                      'Generate a 3BHK house',
                      'Generate a 4BHK house',
                      'Generate a duplex 3BHK house',
                      'Generate a duplex 4BHK house',
                      '1BHK with pooja room and courtyard'
                    ].map(preset => (
                      <button
                        key={preset}
                        type="button"
                        onClick={() => setPrompt(preset)}
                        className="bg-white/5 hover:bg-white/10 text-xs text-neutral-300 border border-white/10 rounded-full px-3 py-1.5 transition"
                      >
                        {preset}
                      </button>
                    ))}
                  </div>

                  <div className="grid grid-cols-1 gap-5">
                    <PlotSizeSelector inputUnit={inputUnit} setInputUnit={setInputUnit} width={width} setWidth={setWidth}
                      length={length} setLength={setLength} inputArea={inputArea} setInputArea={setInputArea} color="amber" />
                  </div>

                  <div className="relative group">
                    <div className="absolute -inset-0.5 bg-gradient-to-r from-amber-500 to-purple-600 rounded-2xl blur opacity-25 group-focus-within:opacity-80 transition duration-700" />
                    <textarea
                      value={prompt} onChange={e => setPrompt(e.target.value)}
                      placeholder="Describe your layout (e.g. 3BHK with pooja room, angan, and jali screens)..."
                      rows={3}
                      className="relative w-full bg-neutral-900 border border-white/10 rounded-2xl p-5 text-base text-white placeholder-neutral-500 focus:outline-none resize-none"
                    />
                  </div>

                  <ColorSelectionPanel colors={colorPrefs} setColors={setColorPrefs} />
                  <IndianOptionsPanel options={indianOptions} setOptions={setIndianOptions} />

                  <div className="flex justify-end pt-4">
                    <button type="submit" disabled={loading || !prompt.trim()}
                      className="flex items-center gap-2 bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-neutral-950 px-7 py-3.5 rounded-xl font-bold transition-all shadow-lg shadow-amber-500/20 disabled:opacity-50 disabled:cursor-not-allowed">
                      {loading ? <Loader2 className="animate-spin" size={18} /> : 'Generate with AI'}
                      {!loading && <Sparkles size={18} />}
                    </button>
                  </div>
                </form>
              </motion.div>
            )}


            {/* ── Custom Builder ── */}
            {mode === 'custom' && (
              <motion.div key="custom" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="space-y-6">
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  {Object.entries(customCounts).map(([type, count]) => (
                    <div key={type} className="flex items-center justify-between p-4 rounded-xl bg-white/[0.04] border border-white/10">
                      <span className="text-sm font-semibold capitalize text-neutral-300">
                        {type.replace(/_/g, ' ')}
                      </span>
                      <div className="flex items-center gap-3">
                        <button onClick={() => setCustomCounts(p => ({ ...p, [type]: Math.max(0, p[type] - 1) }))}
                          className="w-7 h-7 rounded-full bg-black/50 hover:bg-white/10 text-white flex items-center justify-center transition-colors text-sm">−</button>
                        <span className="w-4 text-center font-bold text-white">{count}</span>
                        <button onClick={() => setCustomCounts(p => ({ ...p, [type]: p[type] + 1 }))}
                          className="w-7 h-7 rounded-full bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 flex items-center justify-center transition-colors text-sm">+</button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-1 gap-5">
                  <PlotSizeSelector inputUnit={inputUnit} setInputUnit={setInputUnit} width={width} setWidth={setWidth}
                    length={length} setLength={setLength} inputArea={inputArea} setInputArea={setInputArea} color="emerald" />
                </div>

                <ColorSelectionPanel colors={colorPrefs} setColors={setColorPrefs} />
                <IndianOptionsPanel options={indianOptions} setOptions={setIndianOptions} />

                <div className="flex justify-end pt-4">
                  <button onClick={handleGenerateCustom} disabled={loading}
                    className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-600 text-slate-950 px-7 py-3.5 rounded-xl font-bold transition-colors disabled:opacity-50">
                    {loading ? <Loader2 className="animate-spin" size={18} /> : 'Generate Custom Layout'}
                    {!loading && <ArrowRight size={18} />}
                  </button>
                </div>
              </motion.div>
            )}

          </AnimatePresence>
        </div>

        {/* Back button */}
        {mode && (
          <div className="px-8 py-4 border-t border-white/[0.05] shrink-0 flex items-center">
            <button onClick={() => { setMode(null); clearApiError(); }}
              className="text-sm text-neutral-500 hover:text-white transition-colors flex items-center gap-1">
              ← Back to home
            </button>
          </div>
        )}
      </motion.div>
    </div>
  );
}

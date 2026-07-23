import React, { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Layout, Sparkles, Box, X, ArrowRight, Loader2, Star,
  Home, Flame, Droplets, Users, DoorOpen, Compass, ShieldCheck, Bath,
  Sun, Wind, PackageOpen, Layers, Car, Mountain, ChevronDown, CheckCircle, Palette
} from 'lucide-react';
import { useProjectStore, LAND_UNITS } from '../store/useProjectStore';


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

function ColorSelectionPanel({ colors, setColors }) {
  return (
    <div className="mt-5 p-5 rounded-2xl bg-black/40 border border-white/10 space-y-5">
      <div className="flex items-center gap-2 mb-2">
        <Palette size={18} className="text-blue-400" />
        <h3 className="text-sm font-bold tracking-wide text-white">Color Engine</h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
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
  const { setOnboardingDone, generateFromTemplate, generateWithAI, analyzePrompt, apiError, clearApiError, closeSetupModal, project } = useProjectStore();
  const areaBudget = useProjectStore(state => state.lastAreaBudget);

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
    roof: 'terracotta'
  });

  const [prompt, setPrompt] = useState('');

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

  const handleGenerateAI = async (e) => {
    e?.preventDefault();
    const { w, l } = getFinalDimensions();
    if (!prompt.trim() || !w || !l || w <= 0 || l <= 0) return;
    setLoading(true);
    await generateWithAI(
      prompt,
      w,
      l,
      {},
      colorPrefs,
      "Standard",
      "Global",
      {},
      floors
    );
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
            <p className="text-neutral-400 text-sm mt-0.5">Design your perfect home.</p>
          </div>
          {(project.floors ? project.floors[project.current_floor_index || 0].rooms : []).length > 0 && (
            <button
              onClick={() => { clearApiError(); closeSetupModal(); }}
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

          {apiError && areaBudget && !areaBudget.fits && (
            <div className="mb-5 rounded-xl border border-amber-500/25 bg-amber-500/10 p-4">
              <p className="text-sm font-semibold text-amber-200">
                This layout needs at least {areaBudget.required_sqft?.toLocaleString()} sq ft; approximately {areaBudget.available_sqft?.toLocaleString()} sq ft is buildable.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  onClick={async () => {
                    clearApiError(); setLoading(true);
                    const { w, l } = getFinalDimensions();
                    await generateWithAI(`${prompt}\nUse two floors and move suitable private rooms upstairs.`, w, l, {}, colorPrefs, "Standard", "Global", {}, 2);
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
                    await generateWithAI(`${prompt}\nOptimize for this exact plot. Preserve essential rooms and remove the lowest-priority optional spaces first.`, w, l, {}, colorPrefs, "Standard", "Global", {}, floors);
                    setLoading(false);
                  }}
                  className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-white"
                >Optimize Layout</button>
              </div>
            </div>
          )}

          <AnimatePresence mode="wait">
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

                  <div className="flex justify-end pt-4">
                    <button type="submit" disabled={loading || !prompt.trim()}
                      className="flex items-center gap-2 bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-neutral-950 px-7 py-3.5 rounded-xl font-bold transition-all shadow-lg shadow-amber-500/20 disabled:opacity-50 disabled:cursor-not-allowed">
                      {loading ? <Loader2 className="animate-spin" size={18} /> : 'Generate with AI'}
                      {!loading && <Sparkles size={18} />}
                    </button>
                  </div>
                </form>
              </motion.div>
          </AnimatePresence>
        </div>
      </motion.div>
    </div>
  );
}

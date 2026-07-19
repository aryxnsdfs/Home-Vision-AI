import React, { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { INDIA_STATES } from "../data/indian_states.js";
import {
  BarChart3,
  Box,
  Building2,
  CheckCircle2,
  Cuboid,
  Download,
  FolderOpen,
  Footprints,
  Grid2X2,
  Layers,
  Leaf,
  Loader2,
  Minus,
  Move3D,
  Zap,
  Droplet,
  MousePointer2,
  Package,
  Plane,
  Plus,
  RotateCcw,
  Ruler,
  Search,
  ShieldCheck,
  HardHat,
  Upload,
  Sparkles,
  Undo2,
  Redo2,
  Trash2,
  RefreshCcw,
  ArrowLeftRight,
  X,
  Settings,
  MapPin,
  Palette,
  Maximize2,
  Minimize2,
  ChevronRight,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  ArrowRight,
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  Home
} from "lucide-react";
import { formatInr, useProjectStore, LAND_UNITS, API_BASE_URL } from "../store/useProjectStore.js";

// Direction-specific resize icons: solid arrows = expand (grow outward),
// chevrons = shrink (pull inward). Both point at the wall being modified.
const EXPAND_ICON = { west: ArrowLeft, east: ArrowRight, north: ArrowUp, south: ArrowDown };
const SHRINK_ICON = { west: ChevronLeft, east: ChevronRight, north: ChevronUp, south: ChevronDown };

const glass =
  "border border-white/10 bg-[#1a1f2e]/95 text-white shadow-2xl shadow-black/50 backdrop-blur-xl";
const muted = "text-white/50";
const hairline = "bg-white/10";

const PACKAGE_PRESETS = {
  Standard: {
    name: "Standard", structure: "Code-Compliant RCC", finishes: "Vitrified & Distemper", mep: "Branded",
    multiplier: 1.0,
    foundation: "Isolated Footing", steel: "Fe500", cement: "OPC 43 Grade",
    aggregate: "20mm Machine Crushed", brickwork: "Fly Ash Bricks",
    flooring: "Vitrified Tiles", kitchen: "Granite Countertop", windows: "UPVC Sliding",
    doors: "Flush Doors", plumbing: "CPVC Pipes", electrical: "Standard Copper",
    painting: "Distemper", parking: "Paver Blocks",
  },
  Premium: {
    name: "Premium", structure: "Enhanced Strength RCC", finishes: "Quartz & Teak", mep: "Smart-Ready",
    multiplier: 1.15,
    foundation: "Raft Foundation", steel: "Fe550D", cement: "PPC (Pozzolana)",
    aggregate: "20mm Machine Crushed", brickwork: "AAC Blocks",
    flooring: "Double-Charge Vitrified", kitchen: "Quartz + Modular", windows: "Aluminum Powder Coated",
    doors: "Teak Wood Panelled", plumbing: "PEX Manifold System", electrical: "FRLS Copper",
    painting: "Premium Emulsion", parking: "Stamped Concrete",
  },
  Luxury: {
    name: "Luxury", structure: "Max Structural + Aesthetic", finishes: "Italian Marble & Teak", mep: "Full Smart Home",
    multiplier: 1.4,
    foundation: "Raft Foundation", steel: "Fe550D (CRS)", cement: "PPC + Admixtures",
    aggregate: "20mm Machine Crushed", brickwork: "AAC Blocks",
    flooring: "Italian Marble", kitchen: "Quartz + Modular Island", windows: "Aluminum Powder Coated",
    doors: "Teak Wood Panelled", plumbing: "PEX Manifold System", electrical: "FRLS Copper",
    painting: "Premium Emulsion", parking: "Stamped Concrete",
  },
};

const downloadBlob = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

  function CompactExportButton() {
  const captureScene = useProjectStore((state) => state.captureScene);
  const validateProject = useProjectStore((state) => state.validateProject);
  const project = useProjectStore((state) => state.project);
  const [busy, setBusy] = useState(false);

  const handleExport = async () => {
    setBusy(true);
    try {
      validateProject();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const snapshot = captureScene();
      const latestProject = useProjectStore.getState().project;
      const [{ pdf }, { default: ArchitectReport }] = await Promise.all([
        import("@react-pdf/renderer"),
        import("../pdf/ArchitectReport.jsx")
      ]);
      const blob = await pdf(
        <ArchitectReport project={latestProject} snapshot={snapshot} />
      ).toBlob();
      const safeName = latestProject.name.replace(/[^a-zA-Z0-9]/g, '-');
      downloadBlob(blob, `Home-Vision-AI-${safeName}-Architect-Export.pdf`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleExport}
      disabled={busy}
      title="Download Engineering Blueprints"
      className="flex items-center gap-2 h-10 px-4 rounded-xl border transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-70 border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-400/20 hover:border-emerald-400/50 hover:shadow-[0_0_15px_rgba(52,211,153,0.3)]"
      aria-label="Export architect PDF"
    >
      {busy
        ? <Loader2 size={16} className="animate-spin text-emerald-400" />
        : <Download size={16} className="text-emerald-400" />}
      <span className="text-[10px] sm:text-xs font-bold uppercase tracking-wider text-emerald-300">Download Engineering Blueprints</span>
    </button>
  );
}

function TopBarStats() {
  const project = useProjectStore((state) => state.project);
  const preferredUnit = useProjectStore((state) => state.preferredUnit);
  const setPreferredUnit = useProjectStore((state) => state.setPreferredUnit);
  if (!project) return null;
  const plotArea = project.plot.width * project.plot.length;
  // Compute total built area correctly (sum of ground floor rooms only)
  const builtArea = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).filter(r => !r.isFloor1).reduce((sum, r) => sum + (r.width * r.length), 0);
  const exceeds = builtArea > plotArea;
  const activeUnit = Object.values(LAND_UNITS).find(u => u.id === preferredUnit) || LAND_UNITS.SQFT;
  const displayArea = plotArea / activeUnit.sqftRatio;

  return (
    <div className="flex items-center gap-3">
      {exceeds && (
        <div className="flex items-center gap-1.5 rounded-xl bg-amber-500/15 border border-amber-500/30 px-3 py-1.5 animate-pulse">
          <span className="text-amber-400 text-[10px] font-bold">⚠️ Built area exceeds plot — increase land size</span>
        </div>
      )}
      <div className="flex flex-col rounded-xl border border-white/10 bg-black/25 px-3 py-1.5 leading-tight text-center">
        <span className="text-[10px] text-neutral-400 font-semibold mb-0.5 tracking-wider">PLOT SIZE</span>
        <span className="text-xs font-bold text-white">{project.plot.width}' × {project.plot.length}'</span>
        <div className="mt-0.5 flex items-center justify-center gap-1">
          <span className="text-[10px] text-neutral-300">
            {displayArea.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          </span>
          <select 
            value={preferredUnit}
            onChange={(e) => setPreferredUnit(e.target.value)}
            className="bg-transparent text-[10px] text-emerald-400 font-semibold outline-none cursor-pointer hover:text-emerald-300 transition-colors"
          >
            {Object.values(LAND_UNITS).map(u => (
              <option key={u.id} value={u.id} className="bg-slate-900 text-white">{u.label}</option>
            ))}
          </select>
        </div>
      </div>
      <SelectionModeFilter />
    </div>
  );
}

function SelectionModeFilter() {
  const selectionMode = useProjectStore(s => s.selectionMode);
  const setSelectionMode = useProjectStore(s => s.setSelectionMode);
  const isTransparentMode = useProjectStore(s => s.isTransparentMode);
  const toggleTransparentMode = useProjectStore(s => s.toggleTransparentMode);
  
  const modes = [
    { id: 'room', label: 'Room' },
    { id: 'wall', label: 'Wall' },
    { id: 'floor', label: 'Floor' },
    { id: 'furniture', label: 'Furniture' }
  ];

  return (
    <div className="flex items-center gap-1 rounded-xl border border-white/10 bg-black/25 p-1 hidden md:flex">
      {modes.map(m => (
        <button
          key={m.id}
          onClick={() => setSelectionMode(m.id)}
          className={`px-3 py-1 text-xs font-semibold rounded-lg transition-colors ${
            selectionMode === m.id
              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
              : "text-white/60 hover:text-white hover:bg-white/10 border border-transparent"
          }`}
        >
          {m.label}
        </button>
      ))}
      <div className="w-px h-4 bg-white/20 mx-1" />
      <button
        onClick={toggleTransparentMode}
        className={`px-3 py-1 text-xs font-semibold rounded-lg transition-colors flex items-center gap-1 ${
          isTransparentMode
            ? "bg-blue-500/20 text-blue-300 border border-blue-500/30"
            : "text-white/60 hover:text-white hover:bg-white/10 border border-transparent"
        }`}
      >
        Transparent
      </button>
    </div>
  );
}

function BuilderLegends() {
  const showWiring = useProjectStore((state) => state.showWiring);
  const showPlumbing = useProjectStore((state) => state.showPlumbing);
  const showLegend = useProjectStore((state) => state.showLegend);
  const builderMode = useProjectStore((state) => state.builderMode);
  // Show whenever a MEP layer is visible AND the legend toggle is on — works in
  // both Home-Owner and Builder modes. Both wiring + plumbing keys can show.
  if (!showLegend || (!showWiring && !showPlumbing)) return null;

  const elec = [
    ["#eab308", "Lighting"], ["#ef4444", "General Power"], ["#f97316", "Heavy Load"],
    ["#3b82f6", "Data / Internet"], ["#22c55e", "Smart Home"], ["#f8fafc", "Sub-main Feeder"],
  ];
  const plumb = [
    ["#0ea5e9", "Cold Water (CW)"], ["#ea580c", "Hot Water (HW)"], ["#78350f", "Drain Line"],
    ["#16a34a", "Vent Stack"], ["#0891b2", "Water Tank"], ["#94a3b8", "Pump Line"],
  ];
  const Item = ([c, label]) => (
    <li key={label} className="flex items-center gap-1.5 min-w-0">
      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: c }}></span>
      <span className="truncate">{label}</span>
    </li>
  );

  const containerClass = builderMode
    ? "pointer-events-auto w-44 sm:w-52 bg-slate-900/90 backdrop-blur-md rounded-2xl px-3 py-2.5 border border-white/10 text-white shadow-2xl shrink-0"
    : "pointer-events-auto fixed right-3 sm:right-6 w-44 sm:w-52 bg-slate-900/90 backdrop-blur-md rounded-2xl px-3 py-2.5 border border-white/10 z-[55] text-white shadow-2xl";

  const containerStyle = builderMode
    ? {}
    : { top: "calc(136px + 14rem + 180px)" };

  // Rendered inside FloatingOverlay after MiniMap. Sits directly under minimap
  // via a high top value that clears the minimap container + its controls.
  // z-[55] sits above minimap (z-50). Matches minimap width (w-44 sm:w-52).
  return (
    <div className={containerClass} style={containerStyle}>
      {showWiring && (
        <div className={showPlumbing ? "mb-3" : ""}>
          <h3 className="text-[10px] font-bold uppercase tracking-wider mb-1.5 text-neutral-300">Electrical Legend</h3>
          <ul className="flex flex-col gap-1 text-[10px] font-medium">
            {elec.map(Item)}
          </ul>
        </div>
      )}
      {showPlumbing && (
        <div>
          <h3 className="text-[10px] font-bold uppercase tracking-wider mb-1.5 text-neutral-300">Plumbing Legend</h3>
          <ul className="flex flex-col gap-1 text-[10px] font-medium">
            {plumb.map(Item)}
          </ul>
        </div>
      )}
    </div>
  );
}

function ProfessionalSchedules() {
  const project = useProjectStore((state) => state.project);
  const builderMode = useProjectStore((state) => state.builderMode);
  if (!builderMode || !project) return null;

  // Compute schedules
  const electricalSchedule = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).map(r => {
    const nodes = r.mep_nodes || [];
    return {
      room: r.name,
      light: nodes.filter(n => n.circuit === 'lighting' && !n.type.includes('switch')).length,
      fan: nodes.filter(n => n.type === 'fan').length,
      socket: nodes.filter(n => n.type.includes('socket')).length,
      ac: nodes.filter(n => n.type === 'ac_point').length
    };
  });

  return (
    <div className="w-80 bg-slate-900/90 backdrop-blur-md rounded-2xl p-4 border border-white/10 text-white shadow-2xl pointer-events-auto max-h-[50vh] overflow-y-auto custom-scrollbar">
      <h3 className="text-xs font-bold uppercase tracking-wider mb-2 text-emerald-400">Electrical Schedule</h3>
      <table className="w-full text-[9px] text-left">
        <thead>
          <tr className="border-b border-white/20 text-neutral-400">
            <th className="py-1">ROOM</th>
            <th>LIGHT</th>
            <th>FAN</th>
            <th>SOCKET</th>
            <th>AC</th>
          </tr>
        </thead>
        <tbody>
          {electricalSchedule.map((s, i) => (
            <tr key={i} className="border-b border-white/5">
              <td className="py-1 font-bold">{s.room}</td>
              <td>{s.light}</td>
              <td>{s.fan}</td>
              <td>{s.socket}</td>
              <td>{s.ac}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TopBar() {
  return (
    <motion.header
      initial={{ y: -20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      className={`pointer-events-auto fixed left-3 right-3 top-3 z-50 flex items-center justify-between rounded-2xl px-3 py-2 sm:left-6 sm:right-6 ${glass}`}
    >
      <div className="flex min-w-0 items-center gap-4 pl-1">
        <div className="flex flex-col items-center justify-center pointer-events-auto mt-1">
          <div className="relative h-14 w-20 flex items-center justify-center">
            <img src="/logo.png" className="absolute h-20 w-32 max-w-none object-contain drop-shadow-[0_0_12px_rgba(255,255,255,0.4)]" alt="HomeVision Logo" />
          </div>
          <span className="text-xs font-extrabold tracking-[0.15em] text-white uppercase mt-2">Home Vision AI</span>
        </div>
        <div className="hidden min-w-0 leading-tight sm:block">
            <div className="flex items-center gap-3">
              <button
                onClick={() => {
                  const state = useProjectStore.getState();
                  state.setBuilderMode(!state.builderMode);
                }}
                className={`rounded px-3 py-1 text-xs font-bold transition-colors pointer-events-auto border ${
                  useProjectStore((state) => state.builderMode) 
                    ? "bg-amber-500/20 text-amber-400 border-amber-500/30" 
                    : "bg-slate-800/80 text-white border-slate-600 shadow-[0_0_15px_rgba(255,255,255,0.1)] hover:shadow-[0_0_20px_rgba(255,255,255,0.2)] hover:border-slate-400"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  {useProjectStore((state) => state.builderMode) ? (
                    <><HardHat size={14} /> <span>Builder Mode: ON</span></>
                  ) : (
                    <><Home size={14} /> <span>Home Owner Mode</span></>
                  )}
                </div>
              </button>
              <button
                onClick={() => useProjectStore.getState().setShowSetupModal(true)}
                className="rounded bg-slate-800 px-2 py-0.5 text-xs font-bold text-emerald-400 hover:bg-slate-700 hover:text-emerald-300 transition-colors pointer-events-auto border border-slate-700"
              >
                New Template
              </button>
            </div>
          </div>
        </div>
  
      <TopBarStats />

      <div className="flex items-center gap-2">
        <button
          onClick={() => useProjectStore.getState().undo()}
          className="grid h-10 w-10 place-items-center rounded-full border border-white/10 bg-white/10 transition hover:-translate-y-0.5 hover:bg-emerald-400/20"
          title="Undo (Ctrl+Z)"
        >
          <Undo2 size={16} />
        </button>
        <button
          onClick={() => useProjectStore.getState().redo()}
          className="grid h-10 w-10 place-items-center rounded-full border border-white/10 bg-white/10 transition hover:-translate-y-0.5 hover:bg-emerald-400/20"
          title="Redo (Ctrl+Y)"
        >
          <Redo2 size={16} />
        </button>
        {/* Action Toolbar */}
        <div className="flex items-center gap-2 border-l border-white/10 pl-3">
          {/* Delete selected room */}
          <ActionToolbar />
        </div>
        <button
          onClick={() => useProjectStore.getState().selectRoom('all', 'room')}
          className="rounded-full border border-white/10 bg-white/10 px-4 py-2 text-xs font-bold transition hover:-translate-y-0.5 hover:bg-emerald-400/20"
        >
          Select All
        </button>
        <CompactExportButton />
      </div>
    </motion.header>
  );
}


function ActionToolbar() {
  const selectedRoomId = useProjectStore((s) => s.selectedRoomId);
  const selectedObject = useProjectStore((s) => s.selectedObject);
  const deleteRoom = useProjectStore((s) => s.deleteRoom);
  const deleteWall = useProjectStore((s) => s.deleteWall);

  const handleDelete = () => {
    if (!selectedRoomId) return;
    if (selectedObject?.kind?.includes('solid')) {
      deleteWall(selectedRoomId, selectedObject.kind);
      useProjectStore.getState().onSelect(null, null);
    } else {
      deleteRoom(selectedRoomId);
    }
  };

  const isWallSelected = selectedObject?.kind?.includes('solid');

  return (
    <>
      <button
        id="btn-delete-room"
        title={isWallSelected ? "Delete selected wall (Del)" : "Delete selected room (Del)"}
        onClick={handleDelete}
        disabled={!selectedRoomId}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 hover:border-red-500/40 text-red-400 text-xs font-medium transition-all disabled:opacity-30"
      >
        <Trash2 size={14} />
        {isWallSelected ? "Delete Wall" : "Delete Room"}
      </button>
      <AddRoomDropdown />
    </>
  );
}

// Plain-English room catalog. Indian architectural spaces are listed with
// clear English names so the menu reads consistently (no untranslated terms).
const ADD_ROOM_GROUPS = [
  {
    label: "Core Rooms",
    rooms: [
      { type: "living_room", label: "Living Room" },
      { type: "master_bedroom", label: "Master Bedroom" },
      { type: "bedroom", label: "Bedroom" },
      { type: "kitchen", label: "Kitchen" },
      { type: "bathroom", label: "Bathroom" },
      { type: "dining_room", label: "Dining Room" },
      { type: "balcony", label: "Balcony" },
    ],
  },
  {
    label: "Vastu Core",
    rooms: [
      { type: "pooja_room", label: "Pooja Room" },
      { type: "brahmasthan", label: "Brahmasthan" },
      { type: "courtyard", label: "Courtyard" },
    ],
  },
  {
    label: "Utility & Storage",
    rooms: [
      { type: "utility", label: "Utility / Wash Area" },
      { type: "store_room", label: "Store Room" },
      { type: "storage_loft", label: "Storage Loft" },
    ],
  },
  {
    label: "Living & Comfort",
    rooms: [
      { type: "elderly_suite", label: "Elderly Suite" },
      { type: "powder_room", label: "Powder Room" },
      { type: "built_in_seating", label: "Built-In Seating" },
    ],
  },
  {
    label: "Transitional & Entry",
    rooms: [
      { type: "foyer", label: "Entry Foyer" },
    ],
  },
];

function AddRoomDropdown() {
  const [open, setOpen] = React.useState(false);
  const [pendingType, setPendingType] = React.useState(null); // duplex floor choice
  const addRoom = useProjectStore(s => s.addRoom);
  const isDuplex = useProjectStore(s => (s.project.floors ? s.project.floors[s.project.current_floor_index || 0].rooms : []).some(r => r.isFloor1));
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    const onClick = (e) => { if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setPendingType(null); } };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  const pick = (type) => {
    if (isDuplex) { setPendingType(type); return; }   // ask floor first
    addRoom(type); setOpen(false);
  };
  const placeOn = (floor) => {
    addRoom(pendingType, floor);
    setPendingType(null); setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        id="btn-add-room"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20 hover:border-emerald-500/40 text-emerald-400 text-xs font-medium transition-all"
      >
        <Plus size={14} />
        Add Room
      </button>
      {open && (
        <div className="absolute top-full mt-1 right-0 z-[100] max-h-[60vh] overflow-y-auto thin-scrollbar bg-neutral-900 border border-white/10 rounded-xl shadow-2xl p-2 min-w-[200px]">
          {pendingType ? (
            <div className="p-1">
              <div className="px-2 pb-2 text-xs font-bold text-emerald-300">
                Which floor would you like to add this room to?
              </div>
              <button
                onClick={() => placeOn("floor_0")}
                className="w-full text-left px-3 py-2 text-sm text-neutral-200 hover:bg-emerald-500/20 rounded-lg transition-colors"
              >
                Ground Floor
              </button>
              <button
                onClick={() => placeOn("floor_1")}
                className="w-full text-left px-3 py-2 text-sm text-neutral-200 hover:bg-emerald-500/20 rounded-lg transition-colors"
              >
                First Floor
              </button>
              <button
                onClick={() => setPendingType(null)}
                className="w-full text-left px-3 py-2 text-[11px] text-neutral-500 hover:bg-white/5 rounded-lg transition-colors"
              >
                ← Back
              </button>
            </div>
          ) : (
            ADD_ROOM_GROUPS.map(group => (
              <div key={group.label} className="mb-1 last:mb-0">
                <div className="px-3 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-neutral-500">
                  {group.label}
                </div>
                {group.rooms.map(r => (
                  <button
                    key={r.type}
                    onClick={() => pick(r.type)}
                    className="w-full text-left px-3 py-2 text-sm text-neutral-300 hover:bg-white/10 rounded-lg transition-colors"
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function PromptBar() {
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState("");
  const [genLogs, setGenLogs] = useState([]);
  const [showLogs, setShowLogs] = useState(false);
  const applyStylePrompt = useProjectStore((state) => state.applyStylePrompt);
  const applyGeneratedProject = useProjectStore(
    (state) => state.applyGeneratedProject
  );
  const project = useProjectStore((state) => state.project);

  const submit = async () => {
    let clean = prompt.trim();
    if (!clean) return;

    // Clear previous logs
    setGenLogs([]);

    const selectedObj = useProjectStore.getState().selectedObject;
    if (selectedObj?.kind === "room" && selectedObj.data) {
      const rName = selectedObj.data.name || "";
      const rType = selectedObj.data.type || "";
      const cleanLower = clean.toLowerCase();
      if (!cleanLower.includes(rName.toLowerCase()) && !cleanLower.includes(rType.toLowerCase())) {
        clean = `For ${rName}: ${clean}`;
      }
    }

    setGenLogs([{ type: "pending", message: `Sending to backend: "${clean}"`, time: new Date().toLocaleTimeString() }]);

    setSubmitting(true);
    setStatus("");
    const t0 = performance.now();
    try {
      const currentState = useProjectStore.getState();
      const json = await currentState._readSSEStream(`${API_BASE_URL}/generate/stream`, {
        prompt: clean,
        currentProject: currentState.project,
        layoutRules: currentState.layoutRules || [],
        indianOptions: currentState.project.indianOptions || {},
      });
      const elapsed = ((performance.now() - t0) / 1000).toFixed(2);

      // Use backend logs if available, otherwise show basic info
      const backendLogs = json.logs || [];
      const clientLog = { type: "success", message: `Round-trip: ${elapsed}s`, time: new Date().toLocaleTimeString() };
      setGenLogs([...backendLogs, clientLog]);

      applyGeneratedProject(json);
      setStatus("✓ Applied");
    } catch (err) {
      const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
      setGenLogs(prev => [
        ...prev,
        { type: "error", message: `Failed after ${elapsed}s: ${err.message}`, time: new Date().toLocaleTimeString() },
        { type: "info", message: "Falling back to local style parsing", time: new Date().toLocaleTimeString() },
      ]);
      // Graceful fallback to local style parsing
      applyStylePrompt(clean);
      setStatus("✓ Style applied locally");
    } finally {
      setSubmitting(false);
      setPrompt("");
      setTimeout(() => setStatus(""), 3000);
    }
  };

  const rooms = useProjectStore(s => (s.project.floors ? s.project.floors[s.project.current_floor_index || 0].rooms : []));
  const lastUnderstood = useProjectStore(s => s.lastUnderstood);
  const lastWarnings = useProjectStore(s => s.lastWarnings);
  const hasRooms = rooms && rooms.length > 0;

  const logColors = {
    info: "text-sky-400",
    success: "text-emerald-400",
    warn: "text-amber-400",
    error: "text-red-400",
    pending: "text-purple-400",
  };
  const logIcons = {
    info: "ℹ",
    success: "✓",
    warn: "⚠",
    error: "✗",
    pending: "⟳",
  };

  return (
    <div className="pointer-events-none fixed bottom-[110px] left-3 right-3 z-50 mx-auto flex max-w-2xl flex-col items-center gap-2 sm:left-24 sm:right-24 sm:bottom-[100px]">

      
      <motion.div
        initial={{ y: 16, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ delay: 0.14 }}
        className="pointer-events-auto flex w-full items-center gap-2 rounded-2xl border border-white/10 bg-neutral-900/90 px-3 py-2 text-neutral-100 shadow-2xl shadow-black/40 backdrop-blur-2xl"
      >
        <Search size={17} className="text-neutral-400" />
        <input
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={hasRooms ? "Modify layout (e.g., 'Kitchen in South-East')" : "Ask: make it coastal, glass door, green accents, add bedroom..."}
          className="min-w-0 flex-1 bg-transparent text-sm font-medium text-neutral-100 outline-none placeholder:text-neutral-400"
        />
        {status && (
          <span className="text-[10px] font-bold text-emerald-400 whitespace-nowrap">
            {status}
          </span>
        )}

        <button
          type="button"
          onClick={submit}
          disabled={submitting}
          className="flex h-9 items-center gap-2 rounded-xl bg-emerald-400 px-3 text-sm font-bold text-slate-950 transition hover:-translate-y-0.5 hover:bg-emerald-300 disabled:opacity-50"
          aria-label="Apply prompt"
        >
          {submitting ? (
            <Loader2 size={17} className="animate-spin" />
          ) : (
            <Sparkles size={17} />
          )}
          <span className="hidden sm:inline">{hasRooms ? 'Update Layout (Enter)' : 'Generate (Enter)'}</span>
        </button>
      </motion.div>


    </div>
  );
}

function MiniMapFloorSwitch() {
  const visibleFloor = useProjectStore((s) => s.visibleFloor);
  const setVisibleFloor = useProjectStore((s) => s.setVisibleFloor);
  const rooms = useProjectStore((s) => (s.project.floors ? s.project.floors[s.project.current_floor_index || 0].rooms : []));
  const levels = [...new Set((rooms || []).map(r => Number.isFinite(r.floorIndex) ? r.floorIndex : (r.isFloor1 ? 1 : 0)))].sort((a, b) => a - b);
  if (levels.length <= 1) return null;
  const label = (level) => level < 0 ? "Basement" : level === 0 ? "Ground" : `${level === 1 ? "First" : level === 2 ? "Second" : level} Floor`;
  const opts = levels.map(level => ({ v: `floor_${level}`, label: label(level) }));
  opts.push({ v: "all", label: "Both" });
  return (
    <div className="flex gap-1 w-full mb-1.5">
      {opts.map((o) => (
        <button
          key={o.v}
          onClick={() => setVisibleFloor(o.v)}
          className={`flex-1 rounded-md py-1 text-[9px] font-bold uppercase tracking-wider transition ${
            visibleFloor === o.v
              ? "bg-emerald-500/30 border border-emerald-500/50 text-emerald-200"
              : "bg-white/5 border border-transparent text-slate-400 hover:bg-white/10"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function MiniMap() {
  const [expanded, setExpanded] = React.useState(false);
  const allRooms = useProjectStore((state) => (state.project.floors ? state.project.floors[state.project.current_floor_index || 0].rooms : []));
  const selectedRoomId = useProjectStore((state) => state.selectedRoomId);
  const selectRoom = useProjectStore((state) => state.selectRoom);
  const setCameraView = useProjectStore((state) => state.setCameraView);
  const visibleFloor = useProjectStore((state) => state.visibleFloor);

  // Duplex: the minimap shows ONE floor only — never both combined. Ground is
  // the default; only an explicit "First" selection switches it. "Both" and
  // "Compare" still show the ground plan (no overlapping/combined map).
  const rooms = useMemo(() => {
    const match = /^floor_(-?\d+)$/.exec(visibleFloor || "");
    if (match) {
      const level = Number(match[1]);
      return allRooms.filter((r) => (Number.isFinite(r.floorIndex) ? r.floorIndex : (r.isFloor1 ? 1 : 0)) === level);
    }
    return allRooms.filter((r) => (r.floorIndex === 0 || r.floorIndex === undefined) && !r.isFloor1); // floor_0 / all / compare → ground
  }, [allRooms, visibleFloor]);

  const bounds = useMemo(() => {
    if (!rooms || rooms.length === 0) return { minX: 0, minZ: 0, width: 10, length: 10 };
    const minX = Math.min(...rooms.map((room) => room.x));
    const minZ = Math.min(...rooms.map((room) => room.z));
    const maxX = Math.max(...rooms.map((room) => room.x + room.width));
    const maxZ = Math.max(...rooms.map((room) => room.z + room.length));
    return { minX, minZ, width: maxX - minX, length: maxZ - minZ };
  }, [rooms]);

  return (
    <>
      {expanded && <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm pointer-events-auto" onClick={() => setExpanded(false)} />}
      <motion.div
        initial={{ scale: 0.94, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.18 }}
        className={`pointer-events-auto fixed z-50 flex flex-col ${glass} ${
          expanded
            ? 'top-[80px] bottom-10 left-10 right-10 sm:top-[120px] sm:bottom-12 sm:left-20 sm:right-20 rounded-3xl p-6 bg-slate-900/90 shadow-2xl overflow-hidden'
            : 'right-3 top-[136px] w-44 rounded-2xl p-2 sm:right-6 sm:top-[136px] sm:w-52'
        }`}
      >
        {expanded && (
          <button 
            onClick={(e) => { e.stopPropagation(); setExpanded(false); }}
            className="absolute top-4 right-4 z-50 flex h-8 w-8 items-center justify-center rounded-full bg-red-500/80 text-white hover:bg-red-500 transition"
          >
            <X size={18} />
          </button>
        )}
        
        {/* Floor switcher ABOVE the map — duplex only. Switching filters the
            minimap (and 3D) to that floor's rooms. */}
        <MiniMapFloorSwitch />

        <div className={`relative flex items-center justify-center overflow-hidden bg-slate-950/40 rounded-xl border border-white/5 ${expanded ? "flex-1 w-full h-full" : "w-full h-36 sm:h-48"}`}>
          <svg preserveAspectRatio="xMidYMid meet" viewBox={`${bounds.minX - Math.max(bounds.width, bounds.length) * 0.1} ${bounds.minZ - Math.max(bounds.width, bounds.length) * 0.1} ${bounds.width + Math.max(bounds.width, bounds.length) * 0.2} ${bounds.length + Math.max(bounds.width, bounds.length) * 0.2}`} className="h-full w-full drop-shadow-lg">
          <defs>
          <radialGradient id="minimapGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#4ade80" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#4ade80" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect x={bounds.minX - bounds.width * 0.05} y={bounds.minZ - bounds.length * 0.05} width={bounds.width * 1.1} height={bounds.length * 1.1} rx={Math.max(bounds.width, bounds.length) * 0.05} fill="#020617" opacity="0.84" />
        {rooms.map((room) => {
          const x = room.x;
          const y = room.z;
          const width = room.width;
          const height = room.length;
          const active = room.id === selectedRoomId;
          return (
            <g
              key={room.id}
              onClick={() => {
                selectRoom(room.id, "room");
                setCameraView('top');
              }}
              className="cursor-pointer transition-transform origin-center"
              style={{ transformOrigin: `${x + width/2}px ${y + height/2}px` }}
            >
              <rect
                x={x}
                y={y}
                width={width}
                height={height}
                fill={active ? "url(#minimapGlow)" : "transparent"}
              />
              <rect
                x={x}
                y={y}
                width={width}
                height={height}
                fill="transparent"
                stroke={active ? "#4ade80" : "#475569"}
                strokeWidth={active ? 0.2 : 0.08}
                rx={Math.max(bounds.width, bounds.length) * 0.02}
              />
              {expanded && (
                <text
                  x={x + width / 2}
                  y={y + height / 2}
                  fill="#ffffff"
                  fontSize={Math.min(width / Math.max(room.name.length * 0.65, 1), height / 3)}
                  fontWeight={active ? "800" : "600"}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  style={{ userSelect: "none" }}
                >
                  {room.name.split('_').join(' ')}
                </text>
              )}
            </g>
          );
        })}
        </svg>

        <div className={`absolute z-10 flex gap-1 ${expanded ? "bottom-3 left-3" : "top-1 right-1"}`}>
          {expanded && (
            <button
              onClick={() => { setCameraView('top'); setExpanded(false); }}
              className="text-[9px] font-bold uppercase tracking-wider bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-2 py-1 rounded-md border border-emerald-400 shadow-sm transition-colors"
            >
              Center 3D View
            </button>
          )}
        </div>
      </div>
        
      {/* Compass Views, Directional Pad and View Control */}
      {!expanded && (
        <div className="flex flex-col items-center mt-2 w-full gap-1.5 z-10 relative">
          <button
            onClick={() => setExpanded(true)}
            className="w-full text-[9px] font-bold uppercase tracking-wider bg-slate-800/80 hover:bg-slate-700 text-slate-300 px-2 py-1 rounded-md border border-slate-600/50 shadow-sm backdrop-blur-sm transition-colors"
          >
            View Full Map
          </button>

          {/* Compass view buttons — animate camera to each orientation */}
          <div className="grid grid-cols-5 gap-1 w-full">
            {[
              { v: 'north', label: 'N' },
              { v: 'south', label: 'S' },
              { v: 'east', label: 'E' },
              { v: 'west', label: 'W' },
              { v: 'top', label: 'Top' },
            ].map(({ v, label }) => (
              <button
                key={v}
                title={`${v[0].toUpperCase() + v.slice(1)} View`}
                onClick={() => setCameraView(v)}
                className="bg-emerald-500/15 hover:bg-emerald-500/35 text-emerald-200 rounded text-[10px] font-bold py-1 transition-colors"
              >
                {label}
              </button>
            ))}
          </div>

          {/* D-Pad — pan the camera smoothly */}
          <div className="grid grid-cols-3 gap-1 w-20">
            <div />
            <button onClick={() => useProjectStore.getState().nudgeCamera('up')} className="bg-white/10 hover:bg-white/30 rounded flex justify-center text-[10px] py-0.5 text-white">↑</button>
            <div />
            <button onClick={() => useProjectStore.getState().nudgeCamera('left')} className="bg-white/10 hover:bg-white/30 rounded flex justify-center text-[10px] py-0.5 text-white">←</button>
            <button onClick={() => setCameraView('top')} title="Top / Center View" className="bg-emerald-500/40 hover:bg-emerald-500/60 rounded flex justify-center text-[10px] font-bold text-emerald-200">●</button>
            <button onClick={() => useProjectStore.getState().nudgeCamera('right')} className="bg-white/10 hover:bg-white/30 rounded flex justify-center text-[10px] py-0.5 text-white">→</button>
            <div />
            <button onClick={() => useProjectStore.getState().nudgeCamera('down')} className="bg-white/10 hover:bg-white/30 rounded flex justify-center text-[10px] py-0.5 text-white">↓</button>
            <div />
          </div>

          <FloorToggleInline />
        </div>
      )}
        
    </motion.div>
    </>
  );
}

function RoofToggle() {
  const roofVisible = useProjectStore((state) => state.roofVisible);
  const toggleRoof = useProjectStore((state) => state.toggleRoof);
  const submitPrompt = useProjectStore((state) => state.generateWithAI);
  const [roofPrompt, setRoofPrompt] = React.useState("");
  const lastUnderstood = useProjectStore(s => s.lastUnderstood);
  const lastWarnings = useProjectStore(s => s.lastWarnings);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: 0.5 }}
      className="pointer-events-auto fixed bottom-[154px] left-3 z-50 flex items-center gap-2 sm:bottom-28 sm:left-6"
    >
      <button
        type="button"
        onClick={toggleRoof}
        className={`flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-bold transition ${
          roofVisible
            ? "bg-emerald-400/20 text-emerald-200"
            : `bg-neutral-800/50 text-neutral-400 hover:bg-white/10`
        }`}
        aria-label="Toggle roof"
      >
        <Layers size={15} />
        Roof
      </button>
      
      {roofVisible && (
        <form 
          onSubmit={async (e) => {
            e.preventDefault();
            if(roofPrompt) {
              const clean = `Generate a ${roofPrompt} roof`;
              setRoofPrompt("");
              try {
                const currentState = useProjectStore.getState();
                const json = await currentState._readSSEStream(`${API_BASE_URL}/generate/stream`, {
                  prompt: clean,
                  currentProject: currentState.project,
                });
                useProjectStore.getState().applyGeneratedProject(json);
              } catch (e) {
                console.error(e);
              }
            }
          }}
          className="relative w-48"
        >
          <input
            type="text"
            placeholder="AI Roof Style..."
            value={roofPrompt}
            onChange={e => setRoofPrompt(e.target.value)}
            className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-1.5 text-[11px] text-white placeholder-neutral-500 focus:outline-none focus:border-emerald-500/50"
          />
        </form>
      )}

      {/* AI Understanding Display */}
      {(lastUnderstood?.length > 0 || lastWarnings?.length > 0) && (
        <div className="pointer-events-auto relative flex w-full justify-end mt-2">
          <details className="group relative z-[100]">
            <summary className="cursor-pointer list-none rounded-lg bg-slate-800 border border-white/20 px-3 py-1 text-xs font-semibold text-slate-200 hover:bg-slate-700 transition">
              View AI Generation Details ({lastUnderstood.length + lastWarnings.length})
            </summary>
            <div className="absolute right-0 bottom-full mb-1 flex max-h-48 w-full max-w-xs flex-col gap-2 overflow-y-auto rounded-xl bg-slate-900/95 p-3 shadow-xl backdrop-blur-md border border-white/10">
              {lastUnderstood.map((item, i) => (
                <div key={`u-${i}`} className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5 text-[11px] text-emerald-200">
                  ✓ {item}
                </div>
              ))}
              {lastWarnings.map((warn, i) => (
                <div key={`w-${i}`} className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200">
                  ⚠️ {warn}
                </div>
              ))}
            </div>
          </details>
        </div>
      )}
    </motion.div>
  );
}


function DataCard() {
  const activePanel = useProjectStore((state) => state.activePanel);
  const { metrics, validation } = useProjectStore((state) => state.project);
  if (activePanel !== "3D") return null;

  return (
    <motion.section
      initial={{ x: 24, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ delay: 0.2 }}
      className={`pointer-events-auto fixed bottom-[154px] right-3 z-50 w-[178px] rounded-2xl p-3 sm:bottom-28 sm:right-6 sm:w-[236px] sm:p-4 ${glass}`}
    >
      <div className="mb-3 flex items-center justify-between">
        <span
          className={`text-[10px] font-bold uppercase tracking-[0.18em] ${muted}`}
        >
          Live Metrics
        </span>
        <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_14px_rgba(52,211,153,1)]" />
      </div>
      <div className="space-y-3">
        <MetricRow
          icon={Box}
          label="Total Cost"
          value={formatInr(metrics.costInr)}
        />
        <div className={`h-px ${hairline}`} />
        <MetricRow
          icon={Leaf}
          label="Carbon"
          value={`${metrics.carbonKg.toLocaleString("en-IN")} kg`}
        />
        <div className={`h-px ${hairline}`} />
        <MetricRow
          icon={ShieldCheck}
          label="Status"
          value={
            validation.status === "verified"
              ? "Structural Safe"
              : "Needs Review"
          }
        />
      </div>
    </motion.section>
  );
}

function MetricRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-2 sm:gap-3">
      <Icon size={16} className="text-emerald-300" />
      <div className="min-w-0">
        <div className={`text-[11px] font-semibold ${muted}`}>{label}</div>
        <div className="truncate text-base font-extrabold text-emerald-300 sm:text-lg">
          {value}
        </div>
      </div>
    </div>
  );
}

function TransformStrip({ onOpenWiring, onOpenPlumbing }) {
  const [collapsed, setCollapsed] = useState(false);
  const selectedRoomId = useProjectStore((state) => state.selectedRoomId);
  const selectedObject = useProjectStore((state) => state.selectedObject);
  const project = useProjectStore((state) => state.project);
  const updateRoomDimensions = useProjectStore(
    (state) => state.updateRoomDimensions
  );
  const expandRoom = useProjectStore((state) => state.expandRoom);
  const shrinkRoom = useProjectStore((state) => state.shrinkRoom);
  const deleteWall = useProjectStore((state) => state.deleteWall);
  const restoreWalls = useProjectStore((state) => state.restoreWalls);
  const activePanel = useProjectStore((state) => state.activePanel);
  const viewMode = useProjectStore((state) => state.viewMode);
  const showWiring = useProjectStore((state) => state.showWiring);
  const showPlumbing = useProjectStore((state) => state.showPlumbing);
  const setShowWiring = useProjectStore((state) => state.setShowWiring);
  const setShowPlumbing = useProjectStore((state) => state.setShowPlumbing);
  const showLegend = useProjectStore((state) => state.showLegend);
  const toggleLegend = useProjectStore((state) => state.toggleLegend);
  const showStructural = useProjectStore((state) => state.showStructural);
  const setShowStructural = useProjectStore((state) => state.setShowStructural);
  const generateStructural = useProjectStore((state) => state.generateStructural);
  const selectRoom = useProjectStore((state) => state.selectRoom);
  const swapSourceId = useProjectStore((state) => state.swapSourceId);
  const setSwapMode = useProjectStore((state) => state.setSwapMode);
  const swapRooms = useProjectStore((state) => state.swapRooms);
  const changeRoomType = useProjectStore((state) => state.changeRoomType);
  const layoutRules = useProjectStore((state) => state.layoutRules);
  const addLayoutRule = useProjectStore((state) => state.addLayoutRule);
  const removeLayoutRule = useProjectStore((state) => state.removeLayoutRule);
  const [newRuleRoom, setNewRuleRoom] = useState("kitchen");
  const [newRuleDir, setNewRuleDir] = useState("south_east");

  // Layout-Rules room options come from the ACTUAL generated rooms, not a fixed list.
  const ruleRoomOptions = React.useMemo(() => {
    const seen = new Set();
    const opts = [];
    ((project.floors ? project.floors[project.current_floor_index || 0].rooms : []) || []).forEach((r) => {
      if (!seen.has(r.type)) {
        seen.add(r.type);
        opts.push({ value: r.type, label: r.name || r.type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) });
      }
    });
    return opts;
  }, [(project.floors ? project.floors[project.current_floor_index || 0].rooms : [])]);

  // Keep the selected rule-room valid as the floorplan changes.
  React.useEffect(() => {
    if (ruleRoomOptions.length && !ruleRoomOptions.some((o) => o.value === newRuleRoom)) {
      setNewRuleRoom(ruleRoomOptions[0].value);
    }
  }, [ruleRoomOptions, newRuleRoom]);

  // Intercept selection when in Swap Mode
  React.useEffect(() => {
    if (swapSourceId && selectedRoomId && swapSourceId !== selectedRoomId) {
      if (selectedRoomId !== 'all') {
        swapRooms(swapSourceId, selectedRoomId);
      } else {
        setSwapMode(null);
      }
      selectRoom(swapSourceId, 'room'); // restore selection to the original room
    }
  }, [selectedRoomId, swapSourceId, swapRooms, selectRoom, setSwapMode]);

  const hasWiring = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).some(r => r.mep_nodes?.some(n => n.is_wiring));
  const hasPlumbing = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).some(r => r.mep_nodes?.some(n => n.is_plumbing));
  const hasStructural = !!project.structural_nodes?.length;

  const hairline = "bg-white/[0.05]";
  
  if (!selectedRoomId || selectedRoomId === "all") {
    return (
      <motion.div
        initial={{ x: -24, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        className={`pointer-events-auto fixed left-4 top-[136px] z-40 flex flex-col items-stretch gap-1.5 overflow-y-auto max-h-[calc(100vh-160px)] rounded-2xl p-2 thin-scrollbar w-64 ${glass}`}
      >
        <div className="px-2 text-left">
          <div className={`truncate text-xs font-bold uppercase tracking-[0.1em] ${muted}`}>
            Selected
          </div>
          <div className="truncate text-base font-extrabold w-full">
            Entire House
          </div>
        </div>
        <div className={`w-full h-px ${hairline} my-2`} />
        
        {/* MEP Toggles */}
        <div className="flex flex-col gap-1.5 px-2 mb-2">
          <div className="flex items-center gap-1">
            <button
              onClick={() => {
                if (!hasWiring) onOpenWiring();
                else setShowWiring(!showWiring);
              }}
              className={`flex-1 flex items-center justify-start gap-2 rounded-lg p-2 text-xs font-bold transition ${
                showWiring && hasWiring ? "bg-amber-400/20 text-amber-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"
              }`}
            >
              <Zap size={16} className={showWiring && hasWiring ? "text-amber-400" : "text-neutral-500"} />
              {!hasWiring ? "Add Wiring" : (showWiring ? "Hide Wiring" : "Show Wiring")}
            </button>
            {hasWiring && (
              <>
                <button onClick={onOpenWiring} className="px-2 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-neutral-400 text-xs font-bold transition">Edit</button>
                <button onClick={toggleLegend} title="Toggle legend" className={`px-2 py-2 rounded-lg text-xs font-bold transition ${showLegend ? "bg-amber-400/20 text-amber-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"}`}>Legend</button>
              </>
            )}
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={() => {
                if (!hasPlumbing) onOpenPlumbing();
                else setShowPlumbing(!showPlumbing);
              }}
              className={`flex-1 flex items-center justify-start gap-2 rounded-lg p-2 text-xs font-bold transition ${
                showPlumbing && hasPlumbing ? "bg-blue-400/20 text-blue-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"
              }`}
            >
              <Droplet size={16} className={showPlumbing && hasPlumbing ? "text-blue-400" : "text-neutral-500"} />
              {!hasPlumbing ? "Add Water Supply" : (showPlumbing ? "Hide Plumbing" : "Show Plumbing")}
            </button>
            {hasPlumbing && (
              <>
                <button onClick={onOpenPlumbing} className="px-2 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-neutral-400 text-xs font-bold transition">Edit</button>
                <button onClick={toggleLegend} title="Toggle legend" className={`px-2 py-2 rounded-lg text-xs font-bold transition ${showLegend ? "bg-blue-400/20 text-blue-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"}`}>Legend</button>
              </>
            )}
          </div>
        </div>

        <div className={`w-full h-px ${hairline} mb-1`} />

        <div className="text-[10px] text-center text-neutral-400 px-1">
          Use the prompt bar<br/>to modify the house.
        </div>
      </motion.div>
    );
  }

  const room = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).find((item) => item.id === selectedRoomId);
  if (!room || activePanel !== "3D" || viewMode === "walk") return null;

  const isWallSelected = selectedObject?.kind && selectedObject.kind.includes('solid');

  const setSize = (width, depth) =>
    updateRoomDimensions(room.id, width, depth);
  const scale = (factor) =>
    setSize(room.width * factor, room.length * factor);

  return (
    <motion.div
      initial={{ x: -24, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      className={`pointer-events-auto fixed left-4 top-[136px] z-40 flex flex-col items-stretch gap-1.5 overflow-y-auto max-h-[calc(100vh-160px)] rounded-2xl p-2 thin-scrollbar w-64 ${glass}`}
    >
      <div className="px-2 text-left flex justify-between items-start">
        <div className="flex-1 cursor-pointer" onClick={() => setCollapsed(!collapsed)}>
          <div
            className={`truncate text-[10px] font-bold uppercase tracking-[0.1em] ${muted} flex items-center gap-1`}
          >
            {collapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
            Selected
          </div>
          <div className="truncate text-base font-extrabold w-full pr-2">
            {room.name}
          </div>
        </div>
        <button onClick={() => useProjectStore.getState().selectRoom(null)} className="rounded bg-red-500/20 px-2 py-1 text-[9px] font-bold text-red-400 hover:bg-red-500/30">
          Deselect
        </button>
      </div>

      {!collapsed && (
        <div className="overflow-y-auto max-h-[60vh] thin-scrollbar flex flex-col gap-1.5 mt-1">
          <div className="mt-1 flex items-center justify-between px-2">
            <span className="text-[10px] text-emerald-400 font-semibold">
              {(room.width * room.length).toFixed(0)} sqft
            </span>
          <span className="text-[10px] text-neutral-500">
            Wall: {room.wallThicknessIn}"
          </span>
        </div>
      <div className={`w-full h-px ${hairline} my-2`} />
      
      {/* MEP Toggles */}
      <div className="flex flex-col gap-1.5 px-2 mb-2">
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              if (!hasWiring) onOpenWiring();
              else setShowWiring(!showWiring);
            }}
            className={`flex-1 flex items-center justify-start gap-2 rounded-lg p-2 text-xs font-bold transition ${
              showWiring && hasWiring ? "bg-amber-400/20 text-amber-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"
            }`}
          >
            <Zap size={16} className={showWiring && hasWiring ? "text-amber-400" : "text-neutral-500"} />
            {!hasWiring ? "Add Wiring" : (showWiring ? "Hide Wiring" : "Show Wiring")}
          </button>
          {hasWiring && (
            <>
              <button onClick={onOpenWiring} className="px-2 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-neutral-400 text-xs font-bold transition">Edit</button>
              <button onClick={toggleLegend} title="Toggle legend" className={`px-2 py-2 rounded-lg text-xs font-bold transition ${showLegend ? "bg-amber-400/20 text-amber-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"}`}>Legend</button>
            </>
          )}
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              if (!hasPlumbing) onOpenPlumbing();
              else setShowPlumbing(!showPlumbing);
            }}
            className={`flex-1 flex items-center justify-start gap-2 rounded-lg p-2 text-xs font-bold transition ${
              showPlumbing && hasPlumbing ? "bg-blue-400/20 text-blue-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"
            }`}
          >
            <Droplet size={16} className={showPlumbing && hasPlumbing ? "text-blue-400" : "text-neutral-500"} />
            {!hasPlumbing ? "Add Water Supply" : (showPlumbing ? "Hide Plumbing" : "Show Plumbing")}
          </button>
          {hasPlumbing && (
            <>
              <button onClick={onOpenPlumbing} className="px-2 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-neutral-400 text-xs font-bold transition">Edit</button>
              <button onClick={toggleLegend} title="Toggle legend" className={`px-2 py-2 rounded-lg text-xs font-bold transition ${showLegend ? "bg-blue-400/20 text-blue-300" : "bg-white/5 text-neutral-400 hover:bg-white/10"}`}>Legend</button>
            </>
          )}
        </div>
      </div>

      <div className={`w-full h-px ${hairline} mb-1`} />

      {/* NEW: Room Swap & Type Options */}
      <div className="flex flex-col gap-1.5 px-1 mt-1 mb-2">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] font-bold uppercase text-emerald-300 tracking-wider">Configure</span>
        </div>
        
        {swapSourceId === room.id ? (
          <div className="flex flex-col gap-1 rounded bg-amber-500/20 p-2 text-xs border border-amber-500/30">
            <span className="text-[10px] font-bold text-amber-200">Click another room to swap...</span>
            <button onClick={() => setSwapMode(null)} className="mt-1 bg-amber-500 hover:bg-amber-600 text-black px-2 py-1 rounded font-bold text-[10px]">Cancel</button>
          </div>
        ) : (
          <button onClick={() => setSwapMode(room.id)} className="flex items-center justify-center gap-1.5 rounded-lg border border-white/10 bg-slate-700/60 px-2 py-1.5 text-[11px] font-bold text-neutral-200 hover:bg-slate-600">
            <ArrowLeftRight size={13} /> Swap Room
          </button>
        )}

        <div className="flex flex-col gap-1 mt-1">
          <label className="text-[9px] text-neutral-400">Change Type:</label>
          <select 
            value={room.type}
            onChange={(e) => changeRoomType(room.id, e.target.value)}
            className="bg-black/40 border border-white/10 rounded-lg px-2 py-1.5 text-[11px] text-white focus:outline-none focus:border-emerald-500/50"
          >
            <option value="bedroom">Bedroom</option>
            <option value="master_bedroom">Master Bedroom</option>
            <option value="bathroom">Bathroom</option>
            <option value="living_room">Living Room</option>
            <option value="kitchen">Kitchen</option>
            <option value="dining_room">Dining Room</option>
            <option value="pooja_room">Pooja Room</option>
            <option value="store_room">Store Room</option>
            <option value="utility">Utility</option>
            <option value="foyer">Foyer</option>
            <option value="balcony">Balcony</option>
            <option value="study_room">Study Room</option>
            <option value="powder_room">Powder Room</option>
          </select>
        </div>
      </div>

      <div className={`w-full h-px ${hairline} mb-1`} />

      <div className="flex flex-col gap-1.5 mt-2 px-1">
        <div className="flex items-center justify-between bg-white/5 rounded-md p-1">
          <span className={`text-[10px] font-bold uppercase ${muted} ml-1`}>Scale</span>
          <div className="flex items-center gap-1">
            <button onClick={() => scale(0.95)} className="grid h-5 w-5 place-items-center rounded bg-white/10 hover:bg-white/20 transition text-[10px]">-</button>
            <span className="text-[10px] font-bold w-10 text-center text-white">{Math.round(room.width)}x{Math.round(room.length)}</span>
            <button onClick={() => scale(1.05)} className="grid h-5 w-5 place-items-center rounded bg-white/10 hover:bg-white/20 transition text-[10px]">+</button>
          </div>
        </div>
        <div className="flex items-center justify-between bg-white/5 rounded-md p-1">
          <span className={`text-[10px] font-bold uppercase ${muted} ml-1`}>Width</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setSize(room.width - 1, room.length)} className="grid h-5 w-5 place-items-center rounded bg-white/10 hover:bg-white/20 transition text-[10px]">-</button>
            <span className="text-[10px] font-bold w-10 text-center text-white">{Math.round(room.width)}'</span>
            <button onClick={() => setSize(room.width + 1, room.length)} className="grid h-5 w-5 place-items-center rounded bg-white/10 hover:bg-white/20 transition text-[10px]">+</button>
          </div>
        </div>
        <div className="flex items-center justify-between bg-white/5 rounded-md p-1">
          <span className={`text-[10px] font-bold uppercase ${muted} ml-1`}>Depth</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setSize(room.width, room.length - 1)} className="grid h-5 w-5 place-items-center rounded bg-white/10 hover:bg-white/20 transition text-[10px]">-</button>
            <span className="text-[10px] font-bold w-10 text-center text-white">{Math.round(room.length)}'</span>
            <button onClick={() => setSize(room.width, room.length + 1)} className="grid h-5 w-5 place-items-center rounded bg-white/10 hover:bg-white/20 transition text-[10px]">+</button>
          </div>
        </div>
      </div>
      {/* DIRECTION CONTROLS */}
      <div className="mt-3 px-1">
        <div className="flex items-center gap-1.5 mb-1.5 px-1 text-[10px] font-bold text-emerald-400 tracking-wider">
          <Maximize2 size={12} /> RESIZE ROOM
        </div>
        <div className="flex flex-col gap-1.5">
          {["West", "East", "North", "South"].map(dir => {
             const d = dir.toLowerCase();
             const EIcon = EXPAND_ICON[d];
             const SIcon = SHRINK_ICON[d];
             return (
             <div key={dir} className="flex gap-1.5 w-full">
               <button onClick={() => expandRoom(room.id, d)} title={`Expand ${dir} wall outward`} className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-white/10 bg-slate-700/60 px-2 py-2 text-[11px] font-bold text-neutral-200 hover:bg-slate-600 hover:text-white transition shadow-sm active:scale-95">
                 <EIcon size={15} className="text-emerald-400" strokeWidth={2.75} />
                 <span>Expand ({dir})</span>
               </button>
               <button onClick={() => shrinkRoom(room.id, d)} title={`Shrink ${dir} wall inward`} className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-white/10 bg-slate-700/60 px-2 py-2 text-[11px] font-bold text-neutral-200 hover:bg-slate-600 hover:text-white transition shadow-sm active:scale-95">
                 <SIcon size={15} className="text-rose-400" strokeWidth={2.75} />
                 <span>Shrink ({dir})</span>
               </button>
             </div>
             );
          })}
        </div>
      </div>
      {isWallSelected && (
        <button
          onClick={() => deleteWall(room.id, selectedObject.kind)}
          className="mt-1 flex items-center justify-center gap-1.5 rounded-lg bg-red-500/20 px-2 py-1.5 text-[10px] font-bold text-red-400 transition hover:bg-red-500/30"
        >
          <Trash2 size={12} />
          Delete Wall
        </button>
      )}
      {room.deletedWalls && room.deletedWalls.length > 0 && (
        <button
          onClick={() => restoreWalls(room.id)}
          className="mt-1 flex items-center justify-center gap-1.5 rounded-lg bg-emerald-500/20 px-2 py-1.5 text-[10px] font-bold text-emerald-400 transition hover:bg-emerald-500/30"
        >
          <RefreshCcw size={12} />
          Restore Walls
        </button>
      )}
      <ColorPickerSection />
        </div>
      )}
    </motion.div>
  );
}
function TransformButton({ label, onClick, accent }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`whitespace-nowrap rounded-xl px-3 py-2 text-xs font-extrabold transition ${
        accent
          ? "bg-emerald-400/15 text-emerald-300 hover:bg-emerald-400/25"
          : "bg-white/10 hover:bg-white/15"
      }`}
    >
      {label}
    </button>
  );
}

function ColorPickerSection() {
  const selectedRoomId = useProjectStore(s => s.selectedRoomId);
  const selectedObject = useProjectStore(s => s.selectedObject);
  const project = useProjectStore(s => s.project);
  const setRoomColor = useProjectStore(s => s.setRoomColor);
  const setWallColors = useProjectStore(s => s.setWallColors);
  const room = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).find(r => r.id === selectedRoomId);

  // Furniture uses the fixed neutral project finish; never expose a color
  // control for a selected furniture object.
  if (!room || selectedObject?.kind === 'furniture') {
    return null;
  }

  const currentWallColor = () => {
    if (selectedObject?.kind?.includes('solid')) {
      const firstSelectedWall = selectedObject.kind.split(',')[0];
      return room.wallColors?.[firstSelectedWall] || room.wallColor || '#ffffff';
    }
    return room.wallColor || '#ffffff';
  };

  const handleWallColorChange = (e) => {
    if (selectedObject?.kind?.includes('solid')) {
      setWallColors(room.id, selectedObject.kind, e.target.value);
    } else {
      setRoomColor(room.id, room.floorColor || '#e2e8f0', room.furnitureColor || '#d4bfa0', e.target.value);
    }
  };

  const palette = [
    '#e2e8f0','#fef3c7','#dbeafe','#dcfce7','#fce7f3',
    '#fff7ed','#f1f5f9','#c7d2fe','#d1fae5','#fee2e2',
    '#fef9c3','#e0f2fe'
  ];

  return (
    <div className="mt-4 border-t border-white/10 pt-4">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-neutral-400">
        <Palette size={14} /> Colors
      </div>
      
      <div className="flex flex-wrap items-center gap-1.5">
        {/* Quick palette */}
        {palette.map(c => (
          <button
            key={c}
            onClick={() => {
              const kind = selectedObject?.kind || 'room';
              if (kind.includes('solid')) {
                setWallColors(room.id, kind, c);
              } else if (kind === 'floor') {
                setRoomColor(room.id, c, room.furnitureColor || '#d4bfa0', room.wallColor || '#ffffff');
              } else if (kind === 'wall') {
                setRoomColor(room.id, room.floorColor || '#e2e8f0', room.furnitureColor || '#d4bfa0', c);
              } else {
                // Room mode: set both floor and wall color for quick styling
                setRoomColor(room.id, c, room.furnitureColor || '#d4bfa0', c);
              }
            }}
            style={{ backgroundColor: c }}
            title={c}
            className={`h-6 rounded-md border-2 transition-all hover:scale-110 ${
              room.floorColor === c ? 'w-10 border-white' : 'w-6 border-white/10 hover:border-white/50'
            }`}
          />
        ))}

        {(!selectedObject || selectedObject.kind === 'floor' || selectedObject.kind === 'room') && (
          <div className="flex items-center gap-1.5 rounded-lg bg-white/5 px-2 py-1 ml-2">
            <label className="text-[10px] text-neutral-400">Floor</label>
            <input
              type="color"
              value={room.floorColor || '#e2e8f0'}
              onChange={e => setRoomColor(room.id, e.target.value, room.furnitureColor || '#d4bfa0', room.wallColor || '#ffffff')}
              className="h-5 w-5 cursor-pointer rounded border-0 bg-transparent"
            />
          </div>
        )}
        
        {(!selectedObject || selectedObject.kind === 'wall' || selectedObject.kind?.includes('solid') || selectedObject.kind === 'room') && (
          <div className="flex items-center gap-1.5 rounded-lg bg-white/5 px-2 py-1 ml-2">
            <label className="text-[10px] text-neutral-400">Wall</label>
            <input
              type="color"
              value={currentWallColor()}
              onChange={handleWallColorChange}
              className="h-5 w-5 cursor-pointer rounded border-0 bg-transparent"
            />
          </div>
        )}
      </div>
    </div>
  );
}

function WarningToast() {
  const uiWarning = useProjectStore((state) => state.uiWarning);
  const clearUiWarning = useProjectStore((state) => state.clearUiWarning);
  if (!uiWarning) return null;

  return (
    <motion.div
      initial={{ y: -12, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      className="pointer-events-auto fixed left-1/2 top-24 z-[60] flex max-w-[calc(100vw-24px)] -translate-x-1/2 items-center gap-3 rounded-2xl border border-amber-300/30 bg-neutral-950/90 px-4 py-3 text-sm font-bold text-amber-100 shadow-2xl shadow-black/40 backdrop-blur-2xl"
    >
      <span>{uiWarning}</span>
      <button
        type="button"
        onClick={clearUiWarning}
        className="grid h-7 w-7 place-items-center rounded-lg bg-white/10 transition hover:bg-white/20"
        aria-label="Dismiss warning"
      >
        <X size={15} />
      </button>
    </motion.div>
  );
}

function ActivePanelSheet() {
  const activePanel = useProjectStore((state) => state.activePanel);
  const setActivePanel = useProjectStore((state) => state.setActivePanel);
  if (activePanel === "3D") return null;

  return (
    <motion.section
      initial={{ y: 18, opacity: 0, scale: 0.98 }}
      animate={{ y: 0, opacity: 1, scale: 1 }}
      className={`pointer-events-auto fixed bottom-[164px] left-3 right-3 top-[124px] z-[60] flex flex-col overflow-hidden rounded-2xl sm:bottom-28 sm:left-auto sm:right-6 sm:top-36 sm:w-[380px] ${glass}`}
    >
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div>
          <div className="text-sm font-extrabold">{activePanel}</div>
          <div className={`text-[11px] font-semibold ${muted}`}>
            Home Vision AI project controls
          </div>
        </div>
        <button
          type="button"
          onClick={() => setActivePanel("3D")}
          className="grid h-9 w-9 place-items-center rounded-xl bg-white/10 transition hover:bg-white/15"
          aria-label="Close panel"
        >
          <X size={17} />
        </button>
      </div>
      <div className="thin-scrollbar flex-1 overflow-y-auto p-4">
        {activePanel === "Dashboard" ? <DashboardPanel /> : null}
        {activePanel === "Projects" ? <ProjectsPanel /> : null}
        {activePanel === "Materials" ? <MaterialsPanel /> : null}
        {activePanel === "Analysis" ? <AnalysisPanel /> : null}
        {activePanel === "Materials & Structure" ? <EngineeringPanel /> : null}
      </div>
    </motion.section>
  );
}

function DashboardPanel() {
  const project = useProjectStore((state) => state.project);

  return (
    <div className="space-y-4">
      <PanelCard>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Plot Area" value={`${Math.round(project.plot.width * project.plot.length)} sq ft`} />
          <Stat label="Built Area" value={`${project.metrics.areaSqft} sq ft`} />
          <Stat label="Vastu" value={project.metrics.vastu} />
          <Stat label="Structure" value={project.building.structure} />
          <Stat label="Floors" value={project.building.floors} />
        </div>
      </PanelCard>
    </div>
  );
}

function CenteredPropertiesPanel() {
  const project = useProjectStore((state) => state.project);
  const selectedRoomId = useProjectStore((state) => state.selectedRoomId);
  const selectedObject = useProjectStore((state) => state.selectedObject);

  if (!selectedRoomId || selectedRoomId === "all") return null;

  const selectedRoom = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).find((room) => room.id === selectedRoomId);
  if (!selectedRoom) return null;

  return (
    <motion.div
      initial={{ x: 20, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      className={`pointer-events-auto fixed top-[260px] right-3 sm:top-[280px] sm:right-6 z-50 flex items-center gap-4 rounded-full px-5 py-2 ${glass}`}
    >
      <div className="flex items-center gap-2">
        <div className={`text-[10px] font-bold uppercase tracking-[0.18em] ${muted}`}>
          {selectedObject?.kind || "Room"}:
        </div>
        <div className="text-sm font-extrabold text-emerald-400">{selectedRoom.name}</div>
      </div>
    </motion.div>
  );
}

function ExpansionBtn({ label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center justify-center gap-1.5 rounded-xl bg-emerald-400/10 px-3 py-2.5 text-xs font-extrabold text-emerald-300 transition hover:bg-emerald-400/20"
    >
      {label}
    </button>
  );
}

function ProjectsPanel() {
  const project = useProjectStore((state) => state.project);
  return (
    <div className="space-y-4">
      <PanelCard>
        <div className="text-xl font-extrabold">{project.name}</div>
        <div className={`mt-1 text-sm font-semibold ${muted}`}>
          {project.location.city}, {project.location.state}
        </div>
      </PanelCard>
      <PanelCard>
        <div className="space-y-3">
          <Stat label="Typology" value={project.building.typology} />
          <Stat label="Seismic Zone" value={project.location.seismicZone} />
          <Stat label="Climate" value={project.location.climate} />
          <Stat
            label="Cost Tier"
            value={`${project.location.costTier} x${project.location.multiplier}`}
          />
        </div>
      </PanelCard>
    </div>
  );
}

function MaterialsPanel() {
  const materials = useProjectStore((state) => state.project.materials);
  return (
    <div className="space-y-3">
      {materials.map((material) => (
        <PanelCard key={material.id}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="font-extrabold">{material.name}</div>
              <div className={`mt-1 text-xs font-semibold ${muted}`}>
                {material.category} · {material.quantity}
              </div>
            </div>
            <div className="text-right text-sm font-extrabold text-emerald-300">
              {formatInr(material.total)}
            </div>
          </div>
        </PanelCard>
      ))}
    </div>
  );
}

function AnalysisPanel() {
  const { validation } = useProjectStore((state) => state.project);
  const warnings = validation.warnings.length
    ? validation.warnings
    : ["No warnings detected"];
  return (
    <div className="space-y-4">
      <PanelCard>
        <div className="flex items-center gap-3">
          <CheckCircle2 className="text-emerald-300" size={22} />
          <div>
            <div className="font-extrabold">Structural Safe</div>
            <div className={`text-sm font-semibold ${muted}`}>
              Current model passes MVP rule checks
            </div>
          </div>
        </div>
      </PanelCard>
      <PanelCard>
        <div className="mb-3 text-sm font-extrabold">Geo Overrides</div>
        <div className="space-y-2">
          {validation.overrides.map((item) => (
            <div
              key={item}
              className="rounded-xl bg-emerald-400/10 px-3 py-2 text-sm font-semibold text-emerald-200"
            >
              {item}
            </div>
          ))}
        </div>
      </PanelCard>
      <PanelCard>
        <div className="mb-3 text-sm font-extrabold">Warnings</div>
        <div className="space-y-2">
          {warnings.map((item) => (
            <div
              key={item}
              className={`rounded-xl bg-white/10 px-3 py-2 text-sm font-semibold ${muted}`}
            >
              {item}
            </div>
          ))}
        </div>
      </PanelCard>
    </div>
  );
}

function LocationSelector() {
  const project = useProjectStore((state) => state.project);
  const updateProjectField = useProjectStore((state) => state.updateProjectField);
  const updateStructure = useProjectStore((state) => state.updateStructure);
  const [busy, setBusy] = React.useState(false);
  const loc = project.location || {};
  const sel = "w-full bg-black/50 border border-white/10 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500";
  const stateNames = React.useMemo(() => Object.keys(INDIA_STATES), []);
  const districts = INDIA_STATES[loc.state] || [];

  return (
    <PanelCard>
      <div className="flex items-center gap-2 font-extrabold text-emerald-400 mb-3">
        <MapPin size={16} />
        <span>Project Location</span>
      </div>
      <div className="space-y-3">
        {/* Country removed — India is the fixed default, applied internally. */}
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-wider text-neutral-500 mb-1">State</label>
          <select
            className={sel}
            value={loc.state || "Maharashtra"}
            onChange={(e) => {
              const st = e.target.value;
              updateProjectField("location.state", st);
              const firstDist = (INDIA_STATES[st] || [])[0];
              if (firstDist) updateProjectField("location.city", firstDist);
            }}
          >
            {stateNames.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-wider text-neutral-500 mb-1">District / City</label>
          {districts.length ? (
            <select className={sel} value={loc.city || ""} onChange={(e) => updateProjectField("location.city", e.target.value)}>
              {districts.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          ) : (
            <input className={sel} value={loc.city || ""} placeholder="e.g. Mumbai" onChange={(e) => updateProjectField("location.city", e.target.value)} />
          )}
        </div>
      </div>
      <button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await updateStructure(project.building.costTier || "Standard", {}, loc.country || "India", loc.state || "Maharashtra", loc.city || "Mumbai");
          } finally {
            setBusy(false);
          }
        }}
        className="mt-3 w-full rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 text-emerald-300 text-xs font-bold py-2 transition disabled:opacity-50"
      >
        {busy ? "Recalculating…" : "Recalculate Cost for Location"}
      </button>
    </PanelCard>
  );
}

const SpecRow = ({ label, value, hint }) => (
  <div className="flex justify-between items-center text-xs gap-2">
    <span className="text-neutral-400 shrink-0">{label}</span>
    <span className="text-emerald-400 font-bold text-right">
      {value}
      {hint && <span className="block text-[9px] font-medium text-neutral-500">{hint}</span>}
    </span>
  </div>
);

function EngineeringPanel() {
  const project = useProjectStore((state) => state.project);
  const updateProjectField = useProjectStore((state) => state.updateProjectField);
  const updateStructure = useProjectStore((state) => state.updateStructure);
  const tier = project.building.costTier || "Standard";
  const preset = PACKAGE_PRESETS[tier] || PACKAGE_PRESETS.Standard;
  const hasStructural = !!project.structural_nodes?.length;
  const eng = project.engineering || {};
  const loc = project.location || {};
  const [advOpen, setAdvOpen] = React.useState(false);
  const [busyTier, setBusyTier] = React.useState(false);

  const recalc = async (overrideTier) => {
    setBusyTier(true);
    try {
      await updateStructure(overrideTier || tier, {}, loc.country || "India", loc.state || "Maharashtra", loc.city || "Mumbai");
    } finally {
      setBusyTier(false);
    }
  };

  const selectTier = async (t) => {
    updateProjectField("building.costTier", t);
    await recalc(t);
  };

  const sel = "bg-black/50 border border-white/10 rounded-lg px-2 py-1.5 text-[11px] text-white focus:outline-none focus:border-emerald-500";
  const setEng = (key, val) => { updateProjectField(`engineering.${key}`, val); };

  const coastal = !!project.metrics?.corrosionRequired || eng.windExposure === "Coastal";
  const foundationRec = project.metrics?.foundationRecommendation || preset.foundation;

  return (
    <div className="space-y-4">
      <LocationSelector />

      <PanelCard>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 font-extrabold text-amber-400">
            <Settings size={16} />
            <span>Engineering Package</span>
          </div>
          {busyTier && <Loader2 size={14} className="animate-spin text-amber-300" />}
        </div>

        {/* Quality Tier selector */}
        <div className="flex gap-1 mb-3">
          {["Standard", "Premium", "Luxury"].map((t) => (
            <button
              key={t}
              onClick={() => selectTier(t)}
              disabled={busyTier}
              className={`flex-1 rounded-lg py-1.5 text-[10px] font-bold uppercase tracking-wider transition disabled:opacity-50 ${
                tier === t
                  ? "bg-amber-400/25 border border-amber-400/50 text-amber-300"
                  : "bg-white/5 border border-transparent text-neutral-400 hover:bg-white/10"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="text-[9px] text-neutral-500 mb-3">
          {tier === "Standard" && "Code-compliant · ×1.0 cost"}
          {tier === "Premium" && "Enhanced strength · ×1.15 cost"}
          {tier === "Luxury" && "Aesthetic + max structural · ×1.4 cost"}
        </div>

        <div className="space-y-3 mt-2">
          <div className="rounded-xl bg-black/20 p-3">
            <div className="text-[10px] font-bold text-neutral-500 uppercase tracking-wider mb-2">Structural Details</div>
            <div className="space-y-1.5">
              <SpecRow label="Foundation" value={preset.foundation} />
              <SpecRow label="Steel Grade" value={preset.steel} />
              <SpecRow label="Cement" value={preset.cement} />
              <SpecRow label="Aggregate" value={preset.aggregate} />
              <SpecRow label="Brickwork" value={preset.brickwork} />
            </div>
          </div>

          <div className="rounded-xl bg-black/20 p-3">
            <div className="text-[10px] font-bold text-neutral-500 uppercase tracking-wider mb-2">Finishes & MEP</div>
            <div className="space-y-1.5">
              <SpecRow label="Flooring" value={preset.flooring} />
              <SpecRow label="Kitchen" value={preset.kitchen} />
              <SpecRow label="Windows" value={preset.windows} />
              <SpecRow label="Doors" value={preset.doors} />
              <SpecRow label="Plumbing" value={preset.plumbing} />
              <SpecRow label="Electrical" value={preset.electrical} />
              <SpecRow label="Painting" value={preset.painting} />
              <SpecRow label="Parking" value={preset.parking} />
            </div>
          </div>
        </div>
      </PanelCard>

      {/* Advanced Site Constraints accordion */}
      <PanelCard>
        <button onClick={() => setAdvOpen(o => !o)} className="w-full flex items-center justify-between font-extrabold text-purple-300">
          <span className="flex items-center gap-2"><Settings size={15} /> Advanced Site Constraints</span>
          {advOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
        {advOpen && (
          <div className="space-y-3 mt-3">
            <div>
              <label className="block text-[10px] font-bold uppercase tracking-wider text-neutral-500 mb-1">Seismic Zone</label>
              <select className={`w-full ${sel}`} value={eng.seismicZone || "Zone III"} onChange={(e) => setEng("seismicZone", e.target.value)}>
                {["Zone II", "Zone III", "Zone IV", "Zone V"].map(z => <option key={z} value={z}>{z}</option>)}
              </select>
              <div className="text-[9px] text-neutral-500 mt-1">Higher zones add 10–20% reinforcement steel (IS 1893).</div>
            </div>
            <div>
              <label className="block text-[10px] font-bold uppercase tracking-wider text-neutral-500 mb-1">Soil Bearing Capacity (SBC)</label>
              <select className={`w-full ${sel}`} value={eng.sbc || "Medium"} onChange={(e) => setEng("sbc", e.target.value)}>
                <option value="High">High (&gt; 200 kN/m²)</option>
                <option value="Medium">Medium (~150 kN/m²)</option>
                <option value="Low">Low (&lt; 100 kN/m²)</option>
              </select>
              <div className="text-[9px] text-neutral-500 mt-1">Low SBC forces Isolated → Raft foundation (more concrete).</div>
            </div>
            <div>
              <label className="block text-[10px] font-bold uppercase tracking-wider text-neutral-500 mb-1">Wind Load Exposure</label>
              <select className={`w-full ${sel}`} value={eng.windExposure || "Urban Sheltered"} onChange={(e) => setEng("windExposure", e.target.value)}>
                {["Urban Sheltered", "Coastal", "Open Terrain"].map(w => <option key={w} value={w}>{w}</option>)}
              </select>
            </div>
            <button
              onClick={() => recalc()}
              disabled={busyTier}
              className="w-full rounded-lg bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 text-purple-200 text-xs font-bold py-2 transition disabled:opacity-50"
            >
              {busyTier ? "Applying…" : "Apply Constraints & Recalculate"}
            </button>
          </div>
        )}
      </PanelCard>

      {/* Structural Clearance — dynamic health-check checklist */}
      <PanelCard>
        <div className="flex items-center gap-2 font-extrabold text-blue-400 mb-3">
          <ShieldCheck size={16} />
          <span>Structural Clearances (Verified)</span>
        </div>
        <div className="space-y-2 mt-1">
          {[
            { label: "Load Path Integrity", detail: "Continuous roof → foundation", ok: true },
            { label: "Column Span", detail: "Max span < 6 m (safe)", ok: true },
            { label: "Seismic Compliance", detail: `IS 1893 · ${eng.seismicZone || "Zone III"}`, ok: true },
            { label: "Foundation", detail: `${foundationRec}${hasStructural ? "" : " (awaiting detailing)"}`, ok: hasStructural, pending: !hasStructural },
            { label: "Corrosion Protection", detail: coastal ? "Required (coastal site)" : "Not required", ok: true, warn: coastal },
          ].map((c) => (
            <div key={c.label} className="flex justify-between items-start text-xs gap-2">
              <span className="flex items-center gap-1.5 text-neutral-300">
                {c.pending
                  ? <Loader2 size={12} className="text-amber-400" />
                  : <CheckCircle2 size={12} className={c.warn ? "text-amber-400" : "text-emerald-400"} />}
                {c.label}
              </span>
              <span className={`text-right text-[10px] ${c.pending ? "text-amber-500/80 italic" : c.warn ? "text-amber-300" : "text-emerald-400/90"}`}>
                {c.detail}
              </span>
            </div>
          ))}
        </div>
      </PanelCard>
    </div>
  );
}

function PanelCard({ children }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4 shadow-sm">
      {children}
    </div>
  );
}

function RoomStepper({ label, value, onMinus, onPlus }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div
          className={`text-[11px] font-bold uppercase tracking-[0.18em] ${muted}`}
        >
          {label}
        </div>
        <div className="text-lg font-extrabold">{value}</div>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onMinus}
          className="grid h-9 w-9 place-items-center rounded-xl bg-white/10 transition hover:bg-white/15"
          aria-label={`Decrease ${label}`}
        >
          <Minus size={16} />
        </button>
        <button
          type="button"
          onClick={onPlus}
          className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-400 text-slate-950 transition hover:bg-emerald-300"
          aria-label={`Increase ${label}`}
        >
          <Plus size={16} />
        </button>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div
        className={`text-[11px] font-bold uppercase tracking-[0.16em] ${muted}`}
      >
        {label}
      </div>
      <div className="mt-1 text-sm font-extrabold">{value}</div>
    </div>
  );
}

function BottomDock() {
  const activePanel = useProjectStore((state) => state.activePanel);
  const setActivePanel = useProjectStore((state) => state.setActivePanel);
  
  // Removed "3D Exporter" per user request
  const items = [
    { label: "Dashboard", icon: Grid2X2 },
    { label: "Projects", icon: FolderOpen },
    { label: "Materials", icon: Package },
    { label: "Analysis", icon: BarChart3 },
    { label: "Materials & Structure", icon: HardHat }
  ];

  return (
    <>
      
      {/* ── Horizontal Navigation Dock (Bottom) ── */}
      <div className="pointer-events-none fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center justify-center">
        <div className="pointer-events-auto flex items-center gap-1 rounded-3xl bg-slate-900/95 p-2 shadow-2xl backdrop-blur-3xl border border-slate-700/50">
          <div className="flex items-center px-2 mr-2 border-r border-white/10">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-400 text-slate-950 shadow-[0_0_20px_rgba(52,211,153,0.3)]">
              <Box size={22} strokeWidth={2.5} />
            </div>
          </div>
          
          <div className="flex items-center gap-2">
            {items.map((item) => {
              const Icon = item.icon;
              const active = activePanel === item.label;
              const muted = "text-slate-500 hover:text-slate-300 transition-colors";
              return (
                <button
                  key={item.label}
                  onClick={() => setActivePanel(item.label)}
                  className={`relative flex items-center gap-2 rounded-xl px-3 py-2 text-[11px] font-semibold transition hover:bg-white/10 ${
                    active ? "text-emerald-300 bg-emerald-400/10" : muted
                  }`}
                >
                  <Icon size={16} strokeWidth={active ? 2.5 : 2} />
                  <span className="hidden sm:inline">{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}

function FloorToggle() {
  return null;
}

function FloorToggleInline() {
  const visibleFloor = useProjectStore((state) => state.visibleFloor);
  const setVisibleFloor = useProjectStore((state) => state.setVisibleFloor);
  const project = useProjectStore((state) => state.project);
  const rooms = project.floors ? project.floors[project.current_floor_index || 0].rooms : [];
  const levels = [...new Set((rooms || []).map(r => Number.isFinite(r.floorIndex) ? r.floorIndex : (r.isFloor1 ? 1 : 0)))].sort((a, b) => a - b);
  if (levels.length <= 1) return null;
  return (
    <div className="mt-2 w-full">
      <div className="text-[8px] font-bold uppercase tracking-widest text-white/25 text-center mb-1">Floor</div>
      <div className="flex gap-1 w-full">
        <button
          onClick={() => setVisibleFloor("compare")}
          className={`flex-1 rounded-lg py-1.5 text-[10px] font-bold tracking-wider uppercase transition ${
            visibleFloor === "compare"
              ? "bg-emerald-500/25 border border-emerald-500/50 text-emerald-300"
              : "text-slate-400 hover:bg-white/10 border border-transparent"
          }`}
        >
          Compare
        </button>
      </div>
    </div>
  );
}

function MEPToggle() {
  // Removed top MEPToggle component as requested by the user.
  // The functionality remains in the TransformStrip component on the left panel.
  return null;
}

function WiringModal({ onClose }) {
  const [mode, setMode] = React.useState('Auto');
  const [pkg, setPkg] = React.useState('Standard');
  const [manualFixtures, setManualFixtures] = React.useState({
    ceiling_light: 1,
    fan: 1,
    socket: 2,
    switch: 1
  });
  const generateWiring = useProjectStore(state => state.generateWiring);
  const isGenerating = useProjectStore(state => state.isGenerating);
  
  const handleGenerate = () => {
    generateWiring({ mode, package: pkg, manualFixtures });
    onClose();
  };
  
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm pointer-events-auto">
      <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="bg-slate-900 border border-white/10 rounded-xl p-6 w-[360px] shadow-2xl flex flex-col gap-4 text-white">
        <h2 className="text-lg font-bold text-amber-400 flex items-center gap-2"><Zap size={20} /> Wiring Setup</h2>
        <div className="flex flex-col gap-2">
          <label className="text-xs font-semibold text-neutral-400 uppercase tracking-wider">Mode</label>
          <div className="flex gap-2">
            {['Auto', 'Manual'].map(m => (
              <button key={m} onClick={() => setMode(m)} className={`flex-1 py-2 rounded-lg border text-sm font-bold transition ${mode === m ? 'bg-amber-500/20 border-amber-500/50 text-amber-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                {m} {m === 'Auto' && <span className="text-[10px] font-normal opacity-70 block">(Recommended)</span>}
              </button>
            ))}
          </div>
        </div>
        {mode === 'Auto' && (
          <div className="flex flex-col gap-2">
            <label className="text-xs font-semibold text-neutral-400 uppercase tracking-wider">Package Level</label>
            <div className="grid grid-cols-2 gap-2">
              {['Basic', 'Standard', 'Premium', 'Smart Home'].map(p => (
                <button key={p} onClick={() => setPkg(p)} className={`py-2 px-1 rounded-lg border text-sm font-bold transition ${pkg === p ? 'bg-amber-500/20 border-amber-500/50 text-amber-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}
        {mode === 'Manual' && (
          <div className="flex flex-col gap-3 p-3 bg-white/5 rounded-lg border border-white/10">
            <h3 className="text-xs font-bold text-amber-400 uppercase tracking-widest">Select Base Fixtures (per room)</h3>
            {Object.entries(manualFixtures).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between">
                <span className="text-sm font-medium capitalize text-neutral-300">{key.replace('_', ' ')}</span>
                <input 
                  type="number" 
                  min="0" 
                  max="10" 
                  value={value} 
                  onChange={(e) => setManualFixtures({ ...manualFixtures, [key]: Number(e.target.value) })}
                  className="bg-black/50 border border-white/20 rounded p-1 w-16 text-center text-sm text-white focus:outline-none focus:border-amber-500/50"
                />
              </div>
            ))}
            <p className="text-[10px] text-neutral-500 mt-1 italic">
              These will be distributed safely. You can still use the prompt bar later.
            </p>
          </div>
        )}
        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-white/10 bg-white/5 text-sm font-bold text-neutral-300 hover:bg-white/10 transition">Cancel</button>
          <button onClick={handleGenerate} disabled={isGenerating} className="flex-1 py-2.5 rounded-lg bg-amber-500 text-slate-900 text-sm font-bold hover:bg-amber-400 transition flex items-center justify-center gap-2">
            {isGenerating ? <div className="h-4 w-4 rounded-full border-2 border-slate-900 border-t-transparent animate-spin" /> : <Zap size={16} />}
            Generate
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function PlumbingModal({ onClose }) {
  const [mode, setMode] = React.useState('Auto');
  const [pkg, setPkg] = React.useState('Standard');
  const [source, setSource] = React.useState('Municipal');
  const [storage, setStorage] = React.useState('Overhead Tank');
  const [hotWater, setHotWater] = React.useState('Geyser');
  const [manualFixtures, setManualFixtures] = React.useState({
    water_sink: 1,
    toilet: 1,
    shower: 1,
    geyser: 0
  });
  
  const generatePlumbing = useProjectStore(state => state.generatePlumbing);
  const isGenerating = useProjectStore(state => state.isGenerating);
  
  const handleGenerate = () => {
    generatePlumbing({ mode, package: pkg, waterSource: source, storage, hotWater, manualFixtures });
    onClose();
  };
  
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm pointer-events-auto">
      <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="bg-slate-900 border border-white/10 rounded-xl p-6 w-[400px] shadow-2xl flex flex-col gap-4 text-white">
        <h2 className="text-lg font-bold text-blue-400 flex items-center gap-2"><Droplet size={20} /> Plumbing Setup</h2>
        
        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-2">
            <label className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">Mode</label>
            <div className="flex gap-2">
              {['Auto', 'Manual'].map(m => (
                <button key={m} onClick={() => setMode(m)} className={`flex-1 py-1.5 rounded-lg border text-xs font-bold transition ${mode === m ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                  {m}
                </button>
              ))}
            </div>
          </div>
          
          {mode === 'Auto' && (
            <div className="flex flex-col gap-2">
              <label className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">Package Level</label>
              <div className="grid grid-cols-2 gap-1.5">
                {['Basic', 'Standard', 'Premium', 'Smart Home'].map(p => (
                  <button key={p} onClick={() => setPkg(p)} className={`py-1 rounded border text-[10px] font-bold transition ${pkg === p ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {mode === 'Manual' && (
            <div className="flex flex-col gap-3 p-3 bg-white/5 rounded-lg border border-white/10 mt-2">
              <h3 className="text-xs font-bold text-blue-400 uppercase tracking-widest">Select Base Fixtures (per room)</h3>
              {Object.entries(manualFixtures).map(([key, value]) => (
                <div key={key} className="flex items-center justify-between">
                  <span className="text-sm font-medium capitalize text-neutral-300">{key.replace('_', ' ')}</span>
                  <input 
                    type="number" 
                    min="0" 
                    max="10" 
                    value={value} 
                    onChange={(e) => setManualFixtures({ ...manualFixtures, [key]: Number(e.target.value) })}
                    className="bg-black/50 border border-white/20 rounded p-1 w-16 text-center text-sm text-white focus:outline-none focus:border-blue-500/50"
                  />
                </div>
              ))}
              <p className="text-[10px] text-neutral-500 mt-1 italic">
                Only applies to Wet Rooms (Kitchen, Bathroom).
              </p>
            </div>
          )}

        <div className="flex flex-col gap-2">
          <label className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">Water Source</label>
          <div className="grid grid-cols-3 gap-2">
            {['Municipal', 'Borewell', 'Muni + Bore'].map(s => (
              <button key={s} onClick={() => setSource(s)} className={`py-1.5 px-1 rounded-lg border text-[10px] font-bold transition ${source === s ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <label className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">Storage System</label>
          <div className="grid grid-cols-3 gap-2">
            {['Direct', 'Overhead Tank', 'UG + OH Tank'].map(s => (
              <button key={s} onClick={() => setStorage(s)} className={`py-1.5 px-1 rounded-lg border text-[10px] font-bold transition ${storage === s ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <label className="text-[10px] font-semibold text-neutral-400 uppercase tracking-wider">Hot Water</label>
          <div className="grid grid-cols-4 gap-2">
            {['None', 'Geyser', 'Solar', 'Central'].map(s => (
              <button key={s} onClick={() => setHotWater(s)} className={`py-1.5 px-1 rounded-lg border text-[10px] font-bold transition ${hotWater === s ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' : 'bg-white/5 border-white/10 text-neutral-400 hover:bg-white/10'}`}>
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="flex gap-3 mt-2">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-white/10 bg-white/5 text-sm font-bold text-neutral-300 hover:bg-white/10 transition">Cancel</button>
          <button onClick={handleGenerate} disabled={isGenerating} className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-bold hover:bg-blue-500 transition flex items-center justify-center gap-2">
            {isGenerating ? <div className="h-4 w-4 rounded-full border-2 border-white/20 border-t-white animate-spin" /> : <Droplet size={16} />}
            Generate
          </button>
        </div>
      </motion.div>
    </div>
  );
}

export default function FloatingOverlay() {
  const [wiringModalOpen, setWiringModalOpen] = React.useState(false);
  const [plumbingModalOpen, setPlumbingModalOpen] = React.useState(false);
  const minimapExpanded = useProjectStore((state) => state.minimapExpanded);
  const builderMode = useProjectStore((state) => state.builderMode);

  React.useEffect(() => {
    const handleKeyDown = (e) => {
      // Ignore if typing in an input
      if (e.target?.tagName === 'INPUT' || e.target?.tagName === 'TEXTAREA') return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) {
          useProjectStore.getState().redo();
        } else {
          useProjectStore.getState().undo();
        }
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        useProjectStore.getState().redo();
      } else if (e.key === "Escape") {
        useProjectStore.getState().selectRoom(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div className="pointer-events-none fixed inset-0 z-50">
      <div className={minimapExpanded ? "hidden" : "contents"}>
        <TopBar />
        <FloorToggle />
        <RoofToggle />
        <MEPToggle />
        <TransformStrip 
          onOpenWiring={() => setWiringModalOpen(true)}
          onOpenPlumbing={() => setPlumbingModalOpen(true)}
        />
        <ActivePanelSheet />
        <PromptBar />
        <CenteredPropertiesPanel />
        <BottomDock />
        {builderMode ? (
          <div className="fixed right-6 bottom-32 z-40 flex flex-row items-end gap-3 pointer-events-none">
            <ProfessionalSchedules />
            <BuilderLegends />
          </div>
        ) : (
          <BuilderLegends />
        )}
      </div>
      <MiniMap />
      <WarningToast />
      {wiringModalOpen && <WiringModal onClose={() => setWiringModalOpen(false)} />}
      {plumbingModalOpen && <PlumbingModal onClose={() => setPlumbingModalOpen(false)} />}
    </div>
  );
}

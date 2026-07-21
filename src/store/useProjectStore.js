import { create } from "zustand";
// API base resolution order:
//   1. VITE_API_URL build-time env (best for production — point at the backend).
//   2. localhost dev → the local FastAPI on :8000.
//   3. Fallback → same-origin "/api" (reverse-proxy deployments).
export const API_BASE_URL = (import.meta.env && import.meta.env.VITE_API_URL)
  ? String(import.meta.env.VITE_API_URL).replace(/\/$/, "")
  : ((window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
      ? "http://127.0.0.1:8000/api"
      : "/api");
  
const inr = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "Price unavailable";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0
  }).format(n);
};

const roundToGrid = (value, grid = 0.5) => Math.max(grid, Math.round(Number(value) / grid) * grid);
// Generated coordinates are solver boundaries. Rounding each edge separately
// to a 0.5 ft grid can turn two touching rooms into a visible slit, so preserve
// backend geometry to two decimals when materializing a project.
const preserveGeometry = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) / 100 : fallback;
};
const roomArea = (rooms) => rooms.reduce((sum, room) => sum + room.width * room.length, 0);
const bounds2d = (room) => ({
  minX: room.x,
  maxX: room.x + room.width,
  minZ: room.z,
  maxZ: room.z + room.length
});

export const checkCollisions = (newRoomState, allRooms) => {
  const next = bounds2d(newRoomState);
  return allRooms.some((room) => {
    if (room.id === newRoomState.id) return false;
    const current = bounds2d(room);
    return (
      next.minX < current.maxX &&
      next.maxX > current.minX &&
      next.minZ < current.maxZ &&
      next.maxZ > current.minZ
    );
  });
};

const collisionWarning = "Cannot expand: Room collision detected.";

// Exact overflow message required by the room-overflow rules.
export const INSUFFICIENT_SPACE_MSG =
  "Insufficient space available for this room. Try resizing nearby rooms or generating a larger layout.";

// Minimum buildable area (ft²) per room type — mirrors backend ROOM_MINIMUMS.
// Used to reject additions that would create a tiny/unusable room.
const ROOM_MIN_AREA = {
  living_room: 150, dining_room: 80, kitchen: 60, master_bedroom: 160,
  bedroom: 140, bathroom: 40, foyer: 30, corridor: 40, balcony: 40,
  store_room: 25, pooja_room: 20, utility: 30, garage: 150, study_room: 60,
  staircase: 30, laundry: 25, veranda: 40, parking: 100, powder_room: 20,
};
const OVERFLOW_BUFFER = 1.15;
const minAreaFor = (type) => (ROOM_MIN_AREA[type] || 40) * OVERFLOW_BUFFER;

// Palette id → hex maps (mirror the backend) so the exterior facade and roof
// colors the user picked are reflected in the 3D scene and persist across edits.
const EXTERIOR_HEX = {
  mustard: "#E4A010", gold: "#E4A010", cream: "#FDF5E6", ivory: "#FDF5E6", peach: "#FFDAB9",
  sea_green: "#2E8B57", indigo: "#4B0082", white: "#FFFFFF", concrete: "#808080",
  brick: "#B22222", wood: "#DEB887",
};
const ROOF_HEX = {
  terracotta: "#9C3B27", dark_grey: "#2F4F4F", dark_gray: "#2F4F4F",
  brown: "#654321", slate: "#0F172A", metal: "#475569",
};
const _hexOrMap = (val, map) => {
  if (typeof val !== "string" || !val) return null;
  const v = val.trim().toLowerCase();
  if (v.startsWith("#")) return val;
  return map[v.replace(/ /g, "_")] || null;
};
export const exteriorHexFor = (c) => _hexOrMap(c, EXTERIOR_HEX);
export const roofHexFor = (c) => _hexOrMap(c, ROOF_HEX);

const withAreaMetrics = (project, rooms) => ({
  ...project,
  floors: project.floors.map((f, i) => i === project.current_floor_index ? { ...f, rooms } : f),
  metrics: {
    ...project.metrics,
    areaSqft: Math.round(roomArea(rooms))
  }
});
/* original withAreaMetrics replaced */
const withAreaMetrics_old = (project, rooms) => ({
  ...project,
  rooms,
  metrics: {
    ...project.metrics,
    areaSqft: Math.round(roomArea(rooms))
  }
});

const initialRooms = [];

const materialCatalog = [
  { id: "rcc", name: "RCC Concrete", category: "Structure", quantity: "18.6 m3", unitCost: 7200, total: 133920 },
  { id: "tmt", name: "Fe550D TMT Steel", category: "Reinforcement", quantity: "2.4 tons", unitCost: 68500, total: 164400 },
  { id: "flyash", name: "Fly Ash Bricks", category: "Walling", quantity: "9500 nos", unitCost: 8.5, total: 80750 },
  { id: "aac", name: "AAC Blocks", category: "Partition", quantity: "1120 blocks", unitCost: 78, total: 87360 },
  { id: "waterproof", name: "Advanced Damp-Proofing", category: "Climate", quantity: "2150 sq ft", unitCost: 42, total: 90300 },
  { id: "flooring", name: "Vitrified Tiles", category: "Finish", quantity: "1715 sq ft", unitCost: 135, total: 231525 },
  { id: "paint", name: "Premium Acrylic Paint", category: "Finish", quantity: "4860 sq ft", unitCost: 32, total: 155520 },
  { id: "windows", name: "UPVC Windows", category: "Openings", quantity: "14 sets", unitCost: 18500, total: 259000 },
  { id: "doors", name: "Flush Doors", category: "Openings", quantity: "9 sets", unitCost: 12500, total: 112500 }
];

const defaultProject = {
  id: "HVAI-IND-2026-001",
  name: "Beautiful 2BHK Starter Home",
  client: "Home Vision AI Concept",
  location: {
    city: "Mumbai",
    state: "Maharashtra",
    seismicZone: "III",
    climate: "Coastal/Humid",
    costTier: "Tier 1",
    multiplier: 1.3
  },
  plot_id: "HVAI-IND-2026-001",
  plot_dimensions: { width: 42, length: 40, areaSqft: 1680, unit: "feet" },
  plot: { width: 42, length: 40, areaSqft: 1680 },
  building_type: "duplex",
  building: {
    typology: "2BHK",
    floors: "Ground only",
    structure: "RCC Frame",
    wallMaterial: "Fly Ash Bricks + AAC Partitions",
    roofing: "Flat RCC Slab with runoff slope",
    foundation: "Pile Foundation",
    ceilingHeightFt: 10.5,
    costTier: "Standard"
  },
  current_floor_index: 0,
  floors: [
    {
      floor_id: "uuid-floor-0",
      level: 0,
      height: 10.5,
      rooms: initialRooms,
      structural_elements: [],
      walls: [],
      doors: [],
      windows: [],
      stairs: []
    }
  ],
  style: {
    floorMaterial: "vitrified_tiles",
    wallFinish: "warm_white",
    doorMaterial: "teak_wood",
    lighting: "golden_hour",
    accentColor: "#2563eb",
    environment: "city",
    site: "urban_luxury",
    furnitureStyle: "modern_minimal",
    lastPrompt: ""
  },
  metrics: {
    costInr: 5860000,
    carbonKg: 48200,
    structuralSafety: "Verified",
    vastu: "Compliant",
    areaSqft: 708
  },
  validation: {
    status: "verified",
    errors: [],
    warnings: ["Coastal corrosion package required", "Damp-proofing included for monsoon exposure"],
    overrides: ["Epoxy-coated TMT bars", "Advanced damp-proofing", "Salt-mist resistant exterior paint"]
  },
  materials: materialCatalog
};

// A template is a new design session, not an edit of the currently displayed
// house. Always clone the defaults so no room/layout arrays are shared with the
// previous project or with another generation.
const createFreshProject = () => {
  const project = structuredClone(defaultProject);
  project.id = `HVAI-${Date.now()}-${Math.random().toString(36).slice(2, 8).toUpperCase()}`;
  project.plot_id = project.id;
  project.name = "New Home Vision Project";
  project.floors = project.floors.map((floor, index) => ({
    ...floor,
    floor_id: `floor-${index}`,
    rooms: [],
    walls: [],
    doors: [],
    windows: [],
    structural_elements: [],
    stairs: [],
  }));
  project.rooms = [];
  project.walls = [];
  project.layout_data = {};
  project.outdoor_areas = [];
  project.style = { ...project.style, lastPrompt: "" };
  project.metrics = { ...project.metrics, areaSqft: 0 };
  return project;
};

const extractStyle = (prompt) => {
  const text = prompt.toLowerCase();
  const style = {};

  if (text.includes("marble")) style.floorMaterial = text.includes("dark") ? "dark_marble" : "italian_marble";
  if (text.includes("vitrified")) style.floorMaterial = "vitrified_tiles";
  if (text.includes("kota")) style.floorMaterial = "kota_stone";
  if (text.includes("wood") || text.includes("oak")) style.floorMaterial = "wood_laminate";

  if (text.includes("concrete")) style.wallFinish = "matte_concrete";
  if (text.includes("brick")) style.wallFinish = "exposed_brick";
  if (text.includes("white")) style.wallFinish = "warm_white";
  if (text.includes("texture")) style.wallFinish = "texture_paint";

  if (text.includes("sunset") || text.includes("warm") || text.includes("golden")) style.lighting = "golden_hour";
  if (text.includes("neon") || text.includes("cyber")) style.lighting = "neon_cyberpunk";
  if (text.includes("bright") || text.includes("daylight")) style.lighting = "crisp_noon";
  if (text.includes("moody") || text.includes("dark")) style.lighting = "moody_overcast";

  if (text.includes("beach") || text.includes("coastal") || text.includes("sea")) {
    style.environment = "sunset";
    style.site = "coastal_villa";
  }
  if (text.includes("mountain") || text.includes("snow") || text.includes("hill")) {
    style.environment = "forest";
    style.site = "mountain_retreat";
  }
  if (text.includes("city") || text.includes("urban") || text.includes("penthouse")) {
    style.environment = "city";
    style.site = "urban_luxury";
  }
  if (text.includes("garden") || text.includes("courtyard") || text.includes("aangan") || text.includes("farm")) {
    style.environment = "park";
    style.site = "garden_courtyard";
  }

  if (text.includes("green")) style.accentColor = "#22c55e";
  if (text.includes("blue")) style.accentColor = "#2563eb";
  if (text.includes("amber") || text.includes("gold")) style.accentColor = "#f59e0b";
  if (text.includes("pink")) style.accentColor = "#ec4899";
  if (text.includes("premium") || text.includes("luxury")) style.furnitureStyle = "premium_luxury";
  if (text.includes("minimal") || text.includes("modern")) style.furnitureStyle = "modern_minimal";
  if (text.includes("main door") || text.includes("entrance") || text.includes("front door")) {
    if (text.includes("teak") || text.includes("saagwan")) style.doorMaterial = "teak_wood";
    else if (text.includes("glass")) style.doorMaterial = "glass_door";
    else if (text.includes("steel")) style.doorMaterial = "steel_door";
    else if (text.includes("wood") || text.includes("oak")) style.doorMaterial = "dark_wood";
  }
  style.lastPrompt = prompt;

  return style;
};

export const formatInr = inr;

const MAX_HISTORY = 30;

export const LAND_UNITS = {
  SQFT: { id: "sqft", label: "Sq Ft", sqftRatio: 1 },
  SQYD: { id: "sqyd", label: "Sq Yd (Gaj)", sqftRatio: 9 },
  SQM: { id: "sqm", label: "Sq Meters", sqftRatio: 10.7639 },
  ACRE: { id: "acre", label: "Acres", sqftRatio: 43560 },
  CENT: { id: "cent", label: "Cent", sqftRatio: 435.6 },
  BIGHA: { id: "bigha", label: "Bigha", sqftRatio: 27225 }
};

export const useProjectStore = create((set, get) => ({
  project: defaultProject,
  preferredUnit: "sqft",
  setPreferredUnit: (unit) => set({ preferredUnit: unit }),
  selectedRoomId: "living",
  selectedObject: { roomId: "living", kind: "room" },
  activePanel: "3D",
  selectionMode: "room", // 'room', 'wall', 'floor', 'furniture'
  viewMode: "fly",
  roofVisible: false,
  minimapExpanded: false,
  exportSnapshot: null,
  snapshotHandler: null,
  isExporting: false,
  uiWarning: null,
  apiError: null,
  onboardingDone: false,
  // Real-time generation progress (null = not generating)
  generationProgress: null,
  // Incremented for every new design session. Responses from an older session
  // are ignored, even if their network request finishes later.
  generationEpoch: 0,
  costPresets: null,
  costMaterials: null,
  
  fetchCostPresets: async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/cost-presets`);
      const data = await res.json();
      set({ costPresets: data.presets, costMaterials: data.materials });
    } catch (err) {
      console.error("Failed to fetch cost presets", err);
    }
  },
  lastUnderstood: [],
  lastWarnings: [],
  visibleFloor: "floor_0", // 'floor_-1', 'floor_0', 'floor_1', 'floor_2', or 'all'
  showWiring: false,
  showPlumbing: false,
  showStructural: false,
  builderMode: false,

  // Undo / Redo state
  history: [],
  historyIndex: -1,
  
  isTransparentMode: false,
  toggleTransparentMode: () => set((state) => ({ isTransparentMode: !state.isTransparentMode })),

  mepNodes: [],
  showLegend: true, // MEP legend visibility (wiring/plumbing key)
  toggleLegend: () => set((s) => ({ showLegend: !s.showLegend })),
  setMenu: (menu) => set({ activeMenu: menu }),
  setShowWiring: (val) => set({ showWiring: val }),
  setShowPlumbing: (val) => set({ showPlumbing: val }),
  setShowStructural: (val) => set({ showStructural: val }),
  setBuilderMode: (val) => set({ builderMode: val }),

  pushHistory: () => {
    set((state) => {
      // Deep copy the project to avoid reference mutations
      const snapshot = JSON.parse(JSON.stringify(state.project));
      const newHistory = state.history.slice(0, state.historyIndex + 1);
      newHistory.push(snapshot);
      if (newHistory.length > MAX_HISTORY) newHistory.shift();
      return { history: newHistory, historyIndex: newHistory.length - 1 };
    });
  },

  undo: () => {
    set((state) => {
      if (state.historyIndex <= 0) return state; // Can't undo further
      const newIndex = state.historyIndex - 1;
      const previousState = JSON.parse(JSON.stringify(state.history[newIndex]));
      return { project: previousState, historyIndex: newIndex, uiWarning: null, selectedRoomId: null, selectedObject: null };
    });
  },

  redo: () => {
    set((state) => {
      if (state.historyIndex >= state.history.length - 1) return state; // Can't redo further
      const newIndex = state.historyIndex + 1;
      const nextState = JSON.parse(JSON.stringify(state.history[newIndex]));
      return { project: nextState, historyIndex: newIndex, uiWarning: null, selectedRoomId: null, selectedObject: null };
    });
  },

  setOnboardingDone: (done) => set({ onboardingDone: done }),
  setShowSetupModal: (show) => set({ showSetupModal: show }),
  startNewTemplate: () => set((state) => ({
    project: createFreshProject(),
    onboardingDone: false,
    showSetupModal: true,
    selectedRoomId: null,
    selectedObject: null,
    visibleFloor: "floor_0",
    history: [],
    historyIndex: -1,
    apiError: null,
    lastUnderstood: [],
    lastWarnings: [],
    generationProgress: null,
    generationEpoch: state.generationEpoch + 1,
    uiWarning: null,
  })),
  setMinimapExpanded: (expanded) => set({ minimapExpanded: expanded }),
  closeSetupModal: () => set({ showSetupModal: false }),
  clearApiError: () => set({ apiError: null }),
  selectRoom: (roomId, kind = "room", append = false) => set((state) => {
    if (!roomId) return { selectedRoomId: null, selectedObject: null };
    if (append && state.selectedRoomId === roomId && state.selectedObject?.kind?.includes('solid') && kind.includes('solid')) {
      let currentKinds = state.selectedObject.kind.split(',');
      if (currentKinds.includes(kind)) {
         currentKinds = currentKinds.filter(k => k !== kind);
      } else {
         currentKinds.push(kind);
      }
      if (currentKinds.length === 0) return { selectedRoomId: roomId, selectedObject: { roomId, kind: "room" } };
      return { selectedRoomId: roomId, selectedObject: { roomId, kind: currentKinds.join(',') } };
    }
    return { selectedRoomId: roomId, selectedObject: { roomId, kind } };
  }),
  setActivePanel: (activePanel) => set({ activePanel }),
  setSelectionMode: (selectionMode) => set({ selectionMode }),
  setViewMode: (viewMode) => set({ viewMode }),
  cameraView: null,
  setCameraView: (view) => set({ cameraView: view }),
  toggleRoof: () => set((state) => ({ roofVisible: !state.roofVisible })),
  setMobileMove: (mobileMove) => set({ mobileMove }),
  setSnapshotHandler: (handler) => set({ snapshotHandler: handler }),
  setExportSnapshot: (exportSnapshot) => set({ exportSnapshot }),
  setIsExporting: (isExporting) => set({ isExporting }),
  setVisibleFloor: (visibleFloor) => set({ visibleFloor }),
  resetProject: () => set({ onboardingDone: false, project: { ...get().project, floors: get().project.floors.map((f, i) => i === get().project.current_floor_index ? { ...f, rooms: [] } : f) } }),

  swapSourceId: null,
  setSwapMode: (roomId) => set({ swapSourceId: roomId }),

  swapRooms: (roomIdA, roomIdB) => {
    get().pushHistory();
    set((state) => {
      const rooms = [...state.project.floors[state.project.current_floor_index].rooms];
      const idxA = rooms.findIndex(r => r.id === roomIdA);
      const idxB = rooms.findIndex(r => r.id === roomIdB);
      if (idxA === -1 || idxB === -1) return state;

      const roomA = rooms[idxA];
      const roomB = rooms[idxB];

      // SIZE CHECK: Only swap if area difference is within 40%
      const areaA = roomA.width * roomA.length;
      const areaB = roomB.width * roomB.length;
      
      const maxArea = Math.max(areaA, areaB);
      const minArea = Math.min(areaA, areaB);
      
      if (minArea < maxArea * 0.6) {
        return { uiWarning: `Cannot swap: Size mismatch! ${roomA.name} is ${areaA} sqft, ${roomB.name} is ${areaB} sqft. They must be roughly the same size.` };
      }

      const metaA = {
        name: roomA.name, type: roomA.type, wallThicknessIn: roomA.wallThicknessIn,
        floorColor: roomA.floorColor, furnitureColor: roomA.furnitureColor,
        wallColor: roomA.wallColor, furniture: roomA.furniture, mep_nodes: roomA.mep_nodes, materials: roomA.materials
      };
      const metaB = {
        name: roomB.name, type: roomB.type, wallThicknessIn: roomB.wallThicknessIn,
        floorColor: roomB.floorColor, furnitureColor: roomB.furnitureColor,
        wallColor: roomB.wallColor, furniture: roomB.furniture, mep_nodes: roomB.mep_nodes, materials: roomB.materials
      };

      rooms[idxA] = { ...roomA, ...metaB };
      rooms[idxB] = { ...roomB, ...metaA };

      return { project: { ...state.project, rooms }, swapSourceId: null, uiWarning: null };
    });
  },

  changeRoomType: (roomId, newType) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map(r => {
        if (r.id !== roomId) return r;
        
        // FLOATING WALL FIX: When room type changes, update thickness and clear any deleted wall segments 
        // to force the 3D engine to redraw the actual physical lines tightly without holes.
        const newThickness = (newType === 'bathroom' || newType === 'kitchen') ? 8 : 6;
        
        return { 
          ...r, 
          type: newType, 
          name: newType.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
          wallThicknessIn: newThickness,
          deletedWalls: [] // Reset walls to snap together tightly
        };
      });
      return { project: { ...state.project, rooms } };
    });
  },

  cameraOffset: { x: 0, z: 0 },
  // panNudge is a screen-relative pan request resolved by the camera rig against
  // the CURRENT view orientation (Up = forward, Right = right-on-screen, etc.).
  panNudge: null,
  nudgeCamera: (direction) => {
    set((s) => {
      const seq = (s.panNudge?.seq || 0) + 1;
      if (direction === 'reset') {
        return { cameraOffset: { x: 0, z: 0 }, panNudge: { dir: 'reset', seq } };
      }
      return { panNudge: { dir: direction, seq } };
    });
  },

  layoutRules: [],
  addLayoutRule: (rule) => set((state) => ({ layoutRules: [...state.layoutRules, rule] })),
  removeLayoutRule: (index) => set((state) => {
    const newRules = [...state.layoutRules];
    newRules.splice(index, 1);
    return { layoutRules: newRules };
  }),
  clearLayoutRules: () => set({ layoutRules: [] }),

  captureScene: () => {
    const handler = get().snapshotHandler;
    if (!handler) return null;
    const snapshot = handler();
    set({ exportSnapshot: snapshot });
    return snapshot;
  },

  clearUiWarning: () => set({ uiWarning: null }),

  updateRoom: (roomId, patch) => {
    get().pushHistory();
    set((state) => {
      const current = state.project.floors[state.project.current_floor_index].rooms.find((room) => room.id === roomId);
      if (!current) return state;
      const nextRoom = {
        ...current,
        ...patch,
        x: patch.x !== undefined ? roundToGrid(patch.x) : current.x,
        z: patch.z !== undefined ? roundToGrid(patch.z) : current.z,
        width: patch.width !== undefined ? roundToGrid(patch.width) : current.width,
        length: patch.length !== undefined ? roundToGrid(patch.length) : current.length,
        wallThicknessIn:
          current.type === "kitchen" || current.type === "bathroom"
            ? Math.max(8, patch.wallThicknessIn ?? current.wallThicknessIn)
            : Math.max(4, patch.wallThicknessIn ?? current.wallThicknessIn)
      };
      if (checkCollisions(nextRoom, state.project.floors[state.project.current_floor_index].rooms)) return { uiWarning: collisionWarning };
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map((room) => (room.id === roomId ? nextRoom : room));
      return { project: withAreaMetrics(state.project, rooms), uiWarning: null };
    });
  },

  updateRoomDimensions: (roomId, width, depth) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map((room) => ({ ...room }));
      const target = rooms.find((room) => room.id === roomId);
      if (!target) return state;

      const newWidth = roundToGrid(width);
      const newDepth = roundToGrid(depth);

      const deltaW = newWidth - target.width;
      const deltaL = newDepth - target.length;

      if (deltaW < 0) { // Shrinking width
        const oldRight = target.x + target.width;
        target.width = newWidth;
        rooms.forEach(r => {
          if (Math.abs(r.x - oldRight) < 1.5) {
            r.x = roundToGrid(r.x + deltaW);
            r.width = roundToGrid(r.width - deltaW);
          }
        });
      } else if (deltaW > 0) { // Expanding width
        target.width = newWidth;
      }

      if (deltaL < 0) { // Shrinking depth
        const oldBottom = target.z + target.length;
        target.length = newDepth;
        rooms.forEach(r => {
          if (Math.abs(r.z - oldBottom) < 1.5) {
            r.z = roundToGrid(r.z + deltaL);
            r.length = roundToGrid(r.length - deltaL);
          }
        });
      } else if (deltaL > 0) { // Expanding depth
        target.length = newDepth;
      }

      if (target.x < 0 || target.z < 0 || target.x + target.width > state.project.plot.width || target.z + target.length > state.project.plot.length) {
        return { uiWarning: "Cannot resize: Reached plot boundary." };
      }

      if (deltaW > 0 || deltaL > 0) {
        // Run collision resolver for expanding rooms
        for (let iter = 0; iter < 40; iter++) {
          let stable = true;
          for (let i = 0; i < rooms.length; i++) {
            for (let j = i + 1; j < rooms.length; j++) {
              const a = bounds2d(rooms[i]);
              const b = bounds2d(rooms[j]);
              const ox = Math.min(a.maxX, b.maxX) - Math.max(a.minX, b.minX);
              const oz = Math.min(a.maxZ, b.maxZ) - Math.max(a.minZ, b.minZ);
              
              if (ox > 0 && oz > 0) {
                stable = false;
                let mover;
                if (rooms[i].id === roomId) mover = rooms[j];
                else if (rooms[j].id === roomId) mover = rooms[i];
                else mover = rooms[j];

                if (ox < oz) {
                  if (mover.x > Math.min(a.minX, b.minX)) { mover.x += ox; mover.width -= ox; }
                  else mover.width -= ox;
                } else {
                  if (mover.z > Math.min(a.minZ, b.minZ)) { mover.z += oz; mover.length -= oz; }
                  else mover.length -= oz;
                }
                mover.width = Math.max(4, roundToGrid(mover.width));
                mover.length = Math.max(4, roundToGrid(mover.length));
              }
            }
          }
          if (stable) break;
        }
      }

      return { project: withAreaMetrics(state.project, rooms), uiWarning: null };
    });
  },

  /* ── NEW: Smart room expansion with auto-relayout ─── */
  expandRoom: (roomId, direction) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map((r) => ({ ...r }));
      const target = rooms.find((r) => r.id === roomId);
      if (!target) return state;

      const step = 1;

      // Expand the target room in the given direction
      switch (direction) {
        case "east":
          target.width = roundToGrid(target.width + step);
          break;
        case "west":
          target.x = roundToGrid(target.x - step);
          target.width = roundToGrid(target.width + step);
          break;
        case "north":
          target.length = roundToGrid(target.length + step);
          break;
        case "south":
          target.z = roundToGrid(target.z - step);
          target.length = roundToGrid(target.length + step);
          break;
      }

      const isHorizontal = direction === "east" || direction === "west";
      const sign = direction === "east" || direction === "north" ? 1 : -1;

      // Check plot boundary FIRST
      if (target.x < 0 || target.z < 0 || target.x + target.width > state.project.plot.width || target.z + target.length > state.project.plot.length) {
        return { uiWarning: "Cannot expand: Reached plot boundary." };
      }

      // Iteratively resolve collisions by shrinking (collapsing) adjacent rooms
      let success = true;
      for (let iter = 0; iter < 40; iter++) {
        let stable = true;
        for (let i = 0; i < rooms.length; i++) {
          for (let j = i + 1; j < rooms.length; j++) {
            const a = bounds2d(rooms[i]);
            const b = bounds2d(rooms[j]);
            const ox = Math.min(a.maxX, b.maxX) - Math.max(a.minX, b.minX);
            const oz = Math.min(a.maxZ, b.maxZ) - Math.max(a.minZ, b.minZ);
            
            if (ox > 0 && oz > 0) {
              stable = false;
              let mover;
              if (rooms[i].id === roomId) mover = rooms[j];
              else if (rooms[j].id === roomId) mover = rooms[i];
              else {
                if (isHorizontal) {
                  mover = sign > 0 ? (rooms[i].x >= rooms[j].x ? rooms[i] : rooms[j]) : (rooms[i].x <= rooms[j].x ? rooms[i] : rooms[j]);
                } else {
                  mover = sign > 0 ? (rooms[i].z >= rooms[j].z ? rooms[i] : rooms[j]) : (rooms[i].z <= rooms[j].z ? rooms[i] : rooms[j]);
                }
              }
              
              if (isHorizontal) {
                if (mover.width - ox < 4) {
                  success = false;
                  break;
                }
                const isEast = mover.x >= target.x;
                if (isEast) {
                  mover.x = roundToGrid(mover.x + ox);
                  mover.width = roundToGrid(mover.width - ox);
                } else {
                  mover.width = roundToGrid(mover.width - ox);
                }
              } else {
                if (mover.length - oz < 4) {
                  success = false;
                  break;
                }
                const isSouth = mover.z >= target.z;
                if (isSouth) {
                  mover.z = roundToGrid(mover.z + oz);
                  mover.length = roundToGrid(mover.length - oz);
                } else {
                  mover.length = roundToGrid(mover.length - oz);
                }
              }
            }
          }
          if (!success) break;
        }
        if (stable || !success) break;
      }

      if (!success) {
        return { uiWarning: "Cannot expand: Adjacent room would be too small." };
      }

      return { project: withAreaMetrics(state.project, rooms), uiWarning: null };
    });
  },

  shrinkRoom: (roomId, direction) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map((r) => ({ ...r }));
      const target = rooms.find((r) => r.id === roomId);
      if (!target) return state;

      const step = 1;
      const minSize = 4;

      switch (direction) {
        case "east":
          if (target.width - step >= minSize) {
            const oldRight = target.x + target.width;
            target.width = roundToGrid(target.width - step);
            // Pull adjacent rooms
            rooms.forEach(r => {
              const overlap = Math.max(0, Math.min(r.z + r.length, target.z + target.length) - Math.max(r.z, target.z));
              if (r.id !== target.id && Math.abs(r.x - oldRight) < 1.5 && overlap > 0.5) {
                r.x = roundToGrid(r.x - step);
                r.width = roundToGrid(r.width + step);
              }
            });
          }
          break;
        case "west":
          if (target.width - step >= minSize) {
            const oldLeft = target.x;
            target.x = roundToGrid(target.x + step);
            target.width = roundToGrid(target.width - step);
            // Pull adjacent rooms
            rooms.forEach(r => {
              const overlap = Math.max(0, Math.min(r.z + r.length, target.z + target.length) - Math.max(r.z, target.z));
              if (r.id !== target.id && Math.abs((r.x + r.width) - oldLeft) < 1.5 && overlap > 0.5) {
                r.width = roundToGrid(r.width + step);
              }
            });
          }
          break;
        case "north":
          if (target.length - step >= minSize) {
            const oldBottom = target.z + target.length;
            target.length = roundToGrid(target.length - step);
            // Pull adjacent rooms
            rooms.forEach(r => {
              const overlap = Math.max(0, Math.min(r.x + r.width, target.x + target.width) - Math.max(r.x, target.x));
              if (r.id !== target.id && Math.abs(r.z - oldBottom) < 1.5 && overlap > 0.5) {
                r.z = roundToGrid(r.z - step);
                r.length = roundToGrid(r.length + step);
              }
            });
          }
          break;
        case "south":
          if (target.length - step >= minSize) {
            const oldTop = target.z;
            target.z = roundToGrid(target.z + step);
            target.length = roundToGrid(target.length - step);
            // Pull adjacent rooms
            rooms.forEach(r => {
              const overlap = Math.max(0, Math.min(r.x + r.width, target.x + target.width) - Math.max(r.x, target.x));
              if (r.id !== target.id && Math.abs((r.z + r.length) - oldTop) < 1.5 && overlap > 0.5) {
                r.length = roundToGrid(r.length + step);
              }
            });
          }
          break;
      }

      return { project: withAreaMetrics(state.project, rooms), uiWarning: null };
    });
  },

  applyStylePrompt: (prompt) => {
    get().pushHistory();
    set((state) => ({
      project: {
        ...state.project,
        style: {
          ...state.project.style,
          ...extractStyle(prompt)
        }
      }
    }));
  },

  deleteWall: (roomId, wallId) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map((room) => {
        if (room.id !== roomId) return room;
        return {
          ...room,
          deletedWalls: [...(room.deletedWalls || []), wallId]
        };
      });
      return { project: { ...state.project, rooms } };
    });
  },

  restoreWalls: (roomId) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.map((room) => {
        if (room.id !== roomId) return room;
        return { ...room, deletedWalls: [] };
      });
      return { project: { ...state.project, rooms } };
    });
  },

  // ── Generation progress helpers ────────────────────────────────────────
  // `meta` carries form-data for personalised mock animation:
  //   { title, prompt, floors, colors, features[] }
  _startProgress: (meta = {}) => set({ generationProgress: { finalizing: false, meta } }),
  _finishProgress: () => set(s => ({
    generationProgress: s.generationProgress ? { ...s.generationProgress, finalizing: true } : null
  })),
  clearGenerationProgress: () => set({ generationProgress: null }),

  // ── Shared SSE reader ──────────────────────────────────────────────────
  _readSSEStream: async (url, body) => {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) throw new Error(`Backend returned ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop(); // keep incomplete line
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const text = line.slice(6).trim();
        if (!text || text === "") continue;
        let evt;
        try { evt = JSON.parse(text); } catch { continue; }

        if (evt.capacity) {
          set(s => ({
            generationProgress: s.generationProgress
              ? { ...s.generationProgress, capacity: evt.capacity }
              : s.generationProgress,
            lastAreaBudget: evt.capacity,
          }));
          continue;
        }
        if (evt.error) throw new Error(evt.error);
        if (evt.done) return evt.result;
      }
    }
    throw new Error("Stream ended without a result");
  },

  generateFromTemplate: async (template, width, length, floors = 1, customRooms = null, indianOptions = {}, colors = null, packageLevel = "Standard", country = "India") => {
    const requestEpoch = get().generationEpoch + 1;
    set({ apiError: null, generationEpoch: requestEpoch });
    const features = Object.entries(indianOptions || {}).filter(([, v]) => v).map(([k]) => k.replace(/_/g, ' '));
    get()._startProgress({
      title: template,
      prompt: `${template} home — ${Math.round(width)}×${Math.round(length)} ft plot`,
      floors,
      colors: colors || {},
      features,
    });
    try {
      const data = await get()._readSSEStream(`${API_BASE_URL}/template/stream`, {
        template, width, length, floors, customRooms, indianOptions, colors,
        package: packageLevel, country,
      });
      if (get().generationEpoch !== requestEpoch) return;
      get()._finishProgress();
      await new Promise(r => setTimeout(r, 600));
      set({ lastUnderstood: data.understood || [], lastWarnings: data.warnings || [], onboardingDone: true, showSetupModal: false });
      if (data.area_budget) set({ lastAreaBudget: data.area_budget });
      get().applyGeneratedProject(data);
      get()._applyPaletteColors(colors);
    } catch (err) {
      if (get().generationEpoch !== requestEpoch) return;
      set({ apiError: err.message, generationProgress: null });
    }
  },

  analysisResult: null,
  isAnalyzing: false,

  analyzePrompt: async (prompt, width, length, floors = 1) => {
    set({ 
      apiError: null, 
      isAnalyzing: true, 
      analysisResult: null,
      lastPrompt: prompt,
      lastWidth: width,
      lastLength: length,
      lastFloors: floors
    });
    try {
      const res = await fetch(`${API_BASE_URL}/analyze-prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, width, length, floors })
      });
      if (!res.ok) throw new Error("Failed to analyze prompt");
      const data = await res.json();
      set({ analysisResult: data, isAnalyzing: false });
    } catch (err) {
      set({ apiError: err.message, isAnalyzing: false });
    }
  },

  generateWithAI: async (prompt, width, length, indianOptions = {}, colors = null, packageLevel = "Standard", country = "India", customMaterials = {}, floors = 1, analysisId = null, clarifications = null) => {
    const requestEpoch = get().generationEpoch + 1;
    set({ apiError: null, generationEpoch: requestEpoch, analysisResult: null });
    const features = Object.entries(indianOptions || {}).filter(([, v]) => v).map(([k]) => k.replace(/_/g, ' '));
    get()._startProgress({
      title: null,
      prompt: prompt || "",
      floors,
      colors: colors || {},
      features,
    });
    try {
      const data = await get()._readSSEStream(`${API_BASE_URL}/generate/stream`, {
        prompt, width, length, floors, use_ai: true,
        currentProject: get().project, requestMode: "create", indianOptions, colors,
        package: packageLevel, country, customMaterials,
        layoutRules: get().layoutRules || [],
        analysis_id: analysisId,
        clarifications: clarifications
      });
      if (get().generationEpoch !== requestEpoch) return;
      get()._finishProgress();
      await new Promise(r => setTimeout(r, 600));
      set({ lastUnderstood: data.understood || [], lastWarnings: data.warnings || [], onboardingDone: true, showSetupModal: false });
      if (data.area_budget) set({ lastAreaBudget: data.area_budget });
      get().applyGeneratedProject(data);
      get()._applyPaletteColors(colors);
    } catch (err) {
      if (get().generationEpoch !== requestEpoch) return;
      set({ apiError: err.message, generationProgress: null });
    }
  },

  applyGeneratedProject: (payload) =>
    set((state) => {
      const isReplacement = Boolean(payload?.replace_project);
      const baseProject = isReplacement ? createFreshProject() : state.project;
      // Handle both new layout_data format and fallback to legacy flat rooms array
      let rawRooms = [];
      const layoutData = payload?.layout_data || {};
      const floorKeys = Object.keys(layoutData)
        .filter(key => /^floor_-?\d+$/.test(key))
        .sort((a, b) => Number(a.slice(6)) - Number(b.slice(6)));
      if (floorKeys.length > 0) {
        floorKeys.forEach((floorKey) => {
          const index = Number(floorKey.slice(6));
          rawRooms.push(...(layoutData[floorKey] || []).map(r => ({
            ...r,
            floorIndex: index,
            isFloor1: index === 1,
          })));
        });
      } else if (Array.isArray(payload?.rooms)) {
        rawRooms = payload.rooms;
      }

      const candidateWalls = Object.keys(layoutData)
        .filter(key => /^walls_floor_-?\d+$/.test(key))
        .flatMap(key => {
          const index = Number(key.replace("walls_floor_", ""));
          return (layoutData[key] || []).map(w => ({ ...w, floorIndex: index, isFloor1: index === 1 }));
        });

      const candidateRooms = rawRooms.length > 0
        ? rawRooms.map((room, index) => ({
            // Backend room IDs are stable identities used by targeted edits.
            // Appending the array index on every response changed the ID after
            // every modification and made the next command target stale data.
            id: room.id ? String(room.id) : `room-${index}`,
            name: room.name || room.type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()) || `Room ${index + 1}`,
            type: room.type || "living_room",
            width: preserveGeometry(room.width ?? room.widthFt ?? 10, 10),
            length: preserveGeometry(room.length ?? room.depth ?? room.depthFt ?? 10, 10),
            x: preserveGeometry(room.x ?? 0),
            z: preserveGeometry(room.z ?? 0),
            wallThicknessIn: Math.max(4, room.wallThicknessIn ?? 6),
            doors: room.doors || [],
            windows: room.windows || [],
            floorColor: room.floorColor || '',
            furnitureColor: room.furnitureColor || '',
            wallColor: room.wallColor || '',
            wallColors: room.wallColors || {},
            mep_nodes: (room.mep_nodes && room.mep_nodes.length > 0) ? room.mep_nodes : [
              { type: 'ceiling_light', x: roundToGrid(room.x ?? 0) + (room.width ?? 10)/2, z: roundToGrid(room.z ?? 0) + (room.length ?? 10)/2 },
              { type: 'switch', x: roundToGrid(room.x ?? 0) + 1, z: roundToGrid(room.z ?? 0) + 1 },
              ...(room.type === 'kitchen' ? [{ type: 'water_sink', x: roundToGrid(room.x ?? 0) + (room.width ?? 10)/2, z: roundToGrid(room.z ?? 0) + 0.5 }] : []),
              ...(room.type === 'bathroom' ? [{ type: 'water_sink', x: roundToGrid(room.x ?? 0) + 1, z: roundToGrid(room.z ?? 0) + 0.5 }, { type: 'geyser', x: roundToGrid(room.x ?? 0) + (room.width ?? 10) - 1, z: roundToGrid(room.z ?? 0) + 0.5 }] : [])
            ],
            furniture: room.furniture || [],
            roof_type: room.roof_type || 'flat',
            is_outdoor: Boolean(room.is_outdoor),
            floorIndex: Number.isFinite(room.floorIndex) ? room.floorIndex : (room.isFloor1 ? 1 : 0),
            isFloor1: room.isFloor1 || room.floorIndex === 1 || false
          }))
        : null;

      // Filter out any rooms with invalid dimensions (NaN, zero, negative)
      const finalRooms = candidateRooms
        ? candidateRooms.filter(r =>
            Number.isFinite(r.width) && r.width > 1 &&
            Number.isFinite(r.length) && r.length > 1 &&
            Number.isFinite(r.x) && Number.isFinite(r.z)
          )
        : null;

      // Extract plot dimensions from layout_params if available
      const layoutParams = payload?.layout_params || {};
      const plotWidth = layoutParams.plot_width || state.project.plot.width;
      const plotLength = layoutParams.plot_length || state.project.plot.length;
      const areaSqft = layoutParams.area_sqft || state.project.plot.areaSqft;
      const floorLevels = floorKeys.map(key => {
        const level = Number(key.slice(6));
        return {
          floor_id: `floor-${level}`,
          level,
          rooms: (finalRooms || []).filter(room => room.floorIndex === level),
          walls: candidateWalls.filter(wall => wall.floorIndex === level),
        };
      });
      if (import.meta.env.DEV) {
        console.info("[LAYOUT APPLY AUDIT]", {
          replaceProject: isReplacement,
          returnedFloorKeys: floorKeys,
          roomCounts: Object.fromEntries(floorLevels.map(floor => [
            `floor_${floor.level}`,
            floor.rooms.reduce((counts, room) => {
              counts[room.type] = (counts[room.type] || 0) + 1;
              return counts;
            }, {}),
          ])),
          rejectedInvalidRooms: candidateRooms ? candidateRooms.length - finalRooms.length : 0,
        });
      }
      const activeLevel = state.project.floors?.[state.project.current_floor_index]?.level
        ?? state.project.current_floor_index
        ?? 0;
      const nextFloors = floorLevels.length > 0
        ? floorLevels.map((returnedFloor) => {
            const existingFloor = isReplacement ? null : state.project.floors?.find((floor, index) =>
              (floor.level ?? index) === returnedFloor.level
            );
            return {
              ...(existingFloor || {}),
              ...returnedFloor,
              height: existingFloor?.height ?? state.project.building?.ceilingHeightFt ?? 10.5,
            };
          })
        : (isReplacement ? [] : state.project.floors);
      const nextCurrentFloorIndex = Math.max(
        0,
        nextFloors.findIndex((floor, index) => (floor.level ?? index) === activeLevel)
      );

      const project = {
        ...baseProject,
        ...(payload?.project || {}),
        plot: {
          width: plotWidth,
          length: plotLength,
          areaSqft: areaSqft
        },
        layout_data: payload?.layout_data
          ? (isReplacement
              ? { ...payload.layout_data }
              : { ...(state.project.layout_data || {}), ...payload.layout_data })
          : (isReplacement ? {} : state.project.layout_data),
        floor_levels: floorLevels.length ? floorLevels : (isReplacement ? [] : state.project.floor_levels || []),
        outdoor_areas: layoutData.outdoor_areas ?? (isReplacement ? [] : state.project.outdoor_areas ?? []),
        indianOptions: payload?.layout_data?.indianOptions || (isReplacement ? {} : state.project.indianOptions || {}),
        current_floor_index: nextCurrentFloorIndex,
        floors: nextFloors,
        rooms: nextFloors[nextCurrentFloorIndex]?.rooms || state.project.rooms || [],
        walls: candidateWalls.length > 0 || isReplacement ? candidateWalls : state.project.walls,
        building: {
          ...baseProject.building,
          ...(payload?.building || {})
        },
        metrics: {
          ...baseProject.metrics,
          ...(payload?.physics ? {
            costInr: payload.physics.cost_inr,
            carbonKg: payload.physics.carbon_kg,
            structuralSafety: payload.physics.is_safe ? "Verified" : "Check Needed",
          } : {})
        },
        style: {
          ...baseProject.style,
          ...(payload?.style || {})
        }
      };

      return {
        project: withAreaMetrics(project, project.floors[project.current_floor_index]?.rooms || []),
        visibleFloor: floorLevels.length > 1 ? "all" : state.visibleFloor,
        selectedRoomId: null,
        selectedObject: null,
        uiWarning: null,
        ...(payload?.understood ? { lastUnderstood: payload.understood } : {}),
        ...(payload?.warnings ? { lastWarnings: payload.warnings } : {}),
        ...(payload?.unplaced_rooms ? { lastUnplacedRooms: payload.unplaced_rooms } : { lastUnplacedRooms: [] }),
      };
    }),

  // Merge the user-selected exterior/roof palette (as hex) and the Vastu flag
  // into project.style, and persist the raw colors on the project so they
  // survive regeneration and editing. Interior/floor/furniture/Vastu wall
  // colors are baked per-room by the backend; this handles the building shell.
  _applyPaletteColors: (colors) =>
    set((state) => {
      if (!colors) return {};
      const style = { ...state.project.style };
      style.vastuColors = !!colors.vastuColors;
      // Manual facade/roof palettes are disabled while Vastu mode is on.
      if (!colors.vastuColors) {
        const ext = exteriorHexFor(colors.exterior);
        const rf = roofHexFor(colors.roof);
        if (ext) style.exteriorColor = ext;
        if (rf) style.roofColor = rf;
      }
      return { project: { ...state.project, style, colors } };
    }),

  updateProjectField: (path, value) =>
    set((state) => {
      const project = structuredClone(state.project);
      const keys = path.split(".");
      let node = project;
      keys.slice(0, -1).forEach((key) => {
        node = node[key];
      });
      node[keys.at(-1)] = value;
      return { project };
    }),

  validateProject: () =>
    set((state) => {
      const errors = [];
      const warnings = [];
      const overrides = [...state.project.validation.overrides];

      state.project.floors[state.project.current_floor_index].rooms.forEach((room) => {
        const span = Math.max(room.width, room.length);
        if (state.project.building.structure.includes("RCC") && span > 20) {
          warnings.push(`${room.name}: RCC beam/column check required for ${span} ft span`);
        }
        if ((room.type === "kitchen" || room.type === "bathroom") && room.wallThicknessIn < 8) {
          errors.push(`${room.name}: plumbing wall must be at least 8 in`);
        }
      });

      if (state.project.location.climate.includes("Coastal") && !overrides.includes("Epoxy-coated TMT bars")) {
        overrides.push("Epoxy-coated TMT bars");
      }

      return {
        project: {
          ...state.project,
          validation: {
            status: errors.length ? "blocked" : "verified",
            errors,
            warnings,
            overrides
          }
        }
      };
    }),

  setRoomColor: (roomId, floorColor, furnitureColor, wallColor) => {
    get().pushHistory();
    set((state) => {
      const { project } = state;
      const fIdx = project.current_floor_index || 0;
      if (!project.floors) return state;

      const newRooms = project.floors[fIdx].rooms.map((r) =>
        r.id === roomId ? { ...r, floorColor, furnitureColor, wallColor } : r
      );
      const newFloors = [...project.floors];
      newFloors[fIdx] = { ...newFloors[fIdx], rooms: newRooms };

      return {
        project: {
          ...project,
          rooms: newRooms,
          floors: newFloors
        }
      };
    });
  },

  updateMaterial: (roomId, category, material) => {
    get().pushHistory();
    set((state) => {
      const { project } = state;
      if (!project || !project.floors) return state;

      const fIdx = project.current_floor_index || 0;
      const newRooms = project.floors[fIdx].rooms.map((r) => {
        if (r.id !== roomId) return r;
        const newMaterials = { ...(r.materials || {}) };
        newMaterials[category] = material;
        return { ...r, materials: newMaterials };
      });

      const newFloors = [...project.floors];
      newFloors[fIdx] = { ...newFloors[fIdx], rooms: newRooms };

      return {
        ...state,
        project: {
          ...project,
          rooms: newRooms,
          floors: newFloors
        }
      };
    });
  },

  updateStructure: async (packageData, customMaterials = {}, country = "India", state = "Maharashtra", district = "Mumbai") => {
    // Cost/material recompute ONLY. Never regenerate geometry or restyle the
    // house — recalculating the price must not move rooms or change colors.
    const { project } = get();
    if (!project) return;

    set({ isGenerating: true });
    try {
      const location = { ...(project.location || {}), country, state, city: district };
      const constraints = project.engineering || {};
      const res = await fetch(`${API_BASE_URL}/recalculate-cost`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          package: packageData,
          location,
          constraints,
        }),
      });
      const data = await res.json();
      if (data && data.status === "success") {
        set((s) => ({
          project: {
            ...s.project,
            location,
            building: { ...(s.project.building || {}), costTier: packageData },
            materials: Array.isArray(data.materials) && data.materials.length
              ? data.materials
              : s.project.materials,
            metrics: {
              ...s.project.metrics,
              costInr: Number.isFinite(data.cost_inr) ? data.cost_inr : s.project.metrics.costInr,
              costFactors: data.factors || s.project.metrics.costFactors || null,
              foundationRecommendation: data.foundation_recommendation || null,
              corrosionRequired: !!data.corrosion_required,
            },
          },
        }));
      }
    } catch (err) {
      console.error("Cost recalculation failed", err);
    } finally {
      set({ isGenerating: false });
    }
  },

  setWallColors: (roomId, wallIdsString, color) => {
    get().pushHistory();
    set((state) => {
      const wallIds = wallIdsString ? wallIdsString.split(',') : [];
      if (wallIds.length === 0) return state;

      const { project } = state;
      if (!project || !project.floors) return state;

      const fIdx = project.current_floor_index || 0;
      const newRooms = project.floors[fIdx].rooms.map((r) => {
        if (r.id !== roomId) return r;
        const newWallColors = { ...(r.wallColors || {}) };
        wallIds.forEach(wid => {
          newWallColors[wid] = color;
        });
        return { ...r, wallColors: newWallColors };
      });

      const newFloors = [...project.floors];
      newFloors[fIdx] = { ...newFloors[fIdx], rooms: newRooms };

      return {
        project: {
          ...project,
          rooms: newRooms,
          floors: newFloors
        }
      };
    });
  },

  deleteRoom: (roomId) => {
    get().pushHistory();
    set((state) => {
      const rooms = state.project.floors[state.project.current_floor_index].rooms.filter((r) => r.id !== roomId);
      const newSelectedId = rooms[0]?.id || null;
      return {
        project: withAreaMetrics(state.project, rooms),
        selectedRoomId: newSelectedId,
        selectedObject: newSelectedId ? { roomId: newSelectedId, kind: 'room' } : null,
        uiWarning: null
      };
    });
  },

  // Dynamic room addition — architect-style host split.
  // A new room is NEVER appended outside the building. Instead it is carved
  // from a "host" room on the target floor: the host shrinks by exactly the
  // donated area, the external footprint never moves, and the result is
  // vetoed if either the host or the new room would fall below its minimum.
  addRoom: (roomType, forceFloor = null) => {
    const state = get();
    const rooms = state.project.floors[state.project.current_floor_index].rooms;

    // Target floor: explicit choice (duplex prompt) wins; else follow the
    // currently-viewed floor.
    const targetFloor1 = forceFloor
      ? forceFloor === "floor_1"
      : state.visibleFloor === "floor_1";
    const floorRooms = rooms.filter((r) => !!r.isFloor1 === targetFloor1);

    if (floorRooms.length === 0) {
      set({ uiWarning: INSUFFICIENT_SPACE_MSG });
      return;
    }

    // Host mapping: which existing room donates space to the new one.
    const HOST_MAP = {
      utility: ["kitchen"],
      utility_area: ["kitchen"],
      store_room: ["kitchen", "utility", "utility_area"],
      storage_loft: ["kitchen", "utility", "bedroom"],
      bathroom: ["master_bedroom", "bedroom"],
      powder_room: ["living_room", "foyer"],
      pooja_room: ["living_room", "dining_room"],
      balcony: ["bedroom", "master_bedroom", "living_room"],
      courtyard: ["living_room", "dining_room"],
      laundry: ["kitchen", "utility", "utility_area"],
      study_room: ["bedroom", "living_room"],
      built_in_seating: ["living_room", "bedroom"],
      elderly_suite: ["living_room", "bedroom"],
    };
    const ROOM_MIN_DIM = {
      living_room: 10, dining_room: 8, kitchen: 7, master_bedroom: 10,
      bedroom: 9, bathroom: 5, foyer: 4, powder_room: 4, pooja_room: 4,
      utility: 4, utility_area: 4, store_room: 4, balcony: 4, courtyard: 6,
      study_room: 7, laundry: 4, elderly_suite: 9, built_in_seating: 3,
      storage_loft: 4,
    };
    const minDimFor = (t) => ROOM_MIN_DIM[t] || 4;
    const minAreaRaw = (t) => ROOM_MIN_AREA[t] || 40;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

    const newMinDim = minDimFor(roomType);
    const newMinArea = minAreaRaw(roomType);
    const targetArea = newMinArea * 1.1; // a touch above minimum for usability

    // Try a carve on one candidate host; return {newRect,newHost} or null.
    const tryCarve = (host) => {
      const hostMinDim = minDimFor(host.type);
      const hostMinArea = minAreaRaw(host.type);
      if (host.width >= host.length) {
        const sliceW = clamp(targetArea / host.length, newMinDim, host.width * 0.45);
        const remW = host.width - sliceW;
        const newArea = sliceW * host.length;
        if (remW < hostMinDim || remW * host.length < hostMinArea || newArea < newMinArea * 0.9 || sliceW < newMinDim) return null;
        // New room sits on host's east edge → it shares its WEST wall with the
        // host. Put a door there so the room is enterable.
        const dw = Math.min(3, sliceW * 0.6, host.length * 0.6);
        return {
          newRect: { x: host.x + remW, z: host.z, width: sliceW, length: host.length },
          newHost: { ...host, width: remW },
          door: { wall_orientation: "west", x: 0, z: host.length / 2, width: dw, height: 7 },
        };
      } else {
        const sliceL = clamp(targetArea / host.width, newMinDim, host.length * 0.45);
        const remL = host.length - sliceL;
        const newArea = host.width * sliceL;
        if (remL < hostMinDim || host.width * remL < hostMinArea || newArea < newMinArea * 0.9 || sliceL < newMinDim) return null;
        // New room sits on host's south edge → shares its NORTH wall with host.
        const dw = Math.min(3, host.width * 0.6, sliceL * 0.6);
        return {
          newRect: { x: host.x, z: host.z + remL, width: host.width, length: sliceL },
          newHost: { ...host, length: remL },
          door: { wall_orientation: "north", x: host.width / 2, z: 0, width: dw, height: 7 },
        };
      }
    };

    // Candidate hosts: preferred types first, then every other room largest-first.
    // Trying all of them means a button is never "static" — if ANY room can
    // donate the space, the new room is added.
    const prefer = HOST_MAP[roomType] || [];
    const preferredHosts = prefer
      .flatMap((t) => floorRooms.filter((r) => r.type === t))
      .sort((a, b) => b.width * b.length - a.width * a.length);
    const otherHosts = floorRooms
      .filter((r) => !preferredHosts.includes(r))
      .sort((a, b) => b.width * b.length - a.width * a.length);
    const candidates = [...preferredHosts, ...otherHosts];

    let host = null, newRect = null, newHost = null, newDoor = null;
    for (const c of candidates) {
      const res = tryCarve(c);
      if (res) { host = c; newRect = res.newRect; newHost = res.newHost; newDoor = res.door; break; }
    }
    if (!host) {
      set({ uiWarning: INSUFFICIENT_SPACE_MSG });
      return;
    }

    get().pushHistory();
    const newId = `${roomType}-${Date.now()}`;
    const newRoom = {
      id: newId,
      name: roomType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      type: roomType,
      width: newRect.width,
      length: newRect.length,
      x: newRect.x,
      z: newRect.z,
      wallThicknessIn: roomType === "bathroom" || roomType === "kitchen" ? 8 : 6,
      doors: newDoor ? [newDoor] : [],
      windows: [],
      floorColor: "",
      furnitureColor: "",
      isFloor1: targetFloor1,
    };
    const updatedRooms = rooms.map((r) => (r.id === host.id ? newHost : r)).concat(newRoom);
    set((s) => ({
      project: withAreaMetrics(s.project, updatedRooms),
      selectedRoomId: newId,
      selectedObject: { roomId: newId, kind: "room" },
      uiWarning: null,
    }));
  },
  
  generateWiring: async (options) => {
    set({ isGenerating: true, uiWarning: null });
    try {
      const state = get();
      const res = await fetch(`${API_BASE_URL}/generate-wiring`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: state.project, options: options }),
      });
      if (!res.ok) throw new Error("Failed to generate wiring");
      const data = await res.json();
      set({ project: data.project, isGenerating: false, showWiring: true });
    } catch (err) {
      console.error(err);
      set({ uiWarning: err.message, isGenerating: false });
    }
  },

  generatePlumbing: async (options) => {
    set({ isGenerating: true, uiWarning: null });
    try {
      const state = get();
      const res = await fetch(`${API_BASE_URL}/generate-plumbing`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: state.project, options: options }),
      });
      if (!res.ok) throw new Error("Failed to generate plumbing");
      const data = await res.json();
      set({ project: data.project, isGenerating: false, showPlumbing: true });
    } catch (err) {
      console.error(err);
      set({ uiWarning: err.message, isGenerating: false });
    }
  },

  generateStructural: async () => {
    set({ isGenerating: true, uiWarning: null });
    try {
      const state = get();
      const res = await fetch(`${API_BASE_URL}/generate-structural`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: state.project, options: {} }),
      });
      if (!res.ok) throw new Error("Failed to generate structural plan");
      const data = await res.json();
      set({ project: data.project, isGenerating: false, showStructural: true });
    } catch (err) {
      console.error(err);
      set({ uiWarning: err.message, isGenerating: false });
    }
  },
}));

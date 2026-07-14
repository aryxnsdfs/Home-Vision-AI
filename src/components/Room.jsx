import React, { useMemo, useState, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { a, useSpring } from "@react-spring/three";
import { Line, Billboard, Text } from "@react-three/drei";
import { useProjectStore } from "../store/useProjectStore.js";
import { ArrowLeft, ArrowRight, ArrowUp, ArrowDown } from "lucide-react";

const SCALE = 0.18;
export const WALL_HEIGHT = 2.6;

// Compact engineering label that holds a near-constant on-screen size (gently
// larger up close, smaller far away) so MEP labels stay readable without
// overlapping walls and each other at any zoom level.
function MepLabel({ text, color = "#ffffff", base = 0.085, y = 0.34 }) {
  const ref = useRef();
  useFrame(({ camera }) => {
    if (!ref.current) return;
    const wp = ref.current.getWorldPosition(new THREE.Vector3());
    const d = camera.position.distanceTo(wp);
    ref.current.scale.setScalar(THREE.MathUtils.clamp(d * 0.05, 0.5, 2.6));
  });
  return (
    <Billboard ref={ref} follow position={[0, y, 0]}>
      <Text
        fontSize={base}
        color={color}
        anchorX="center"
        anchorY="middle"
        outlineWidth={0.012}
        outlineColor="#000000"
        fontWeight="bold"
        renderOrder={999}
        material-depthTest={false}
      >
        {text}
      </Text>
    </Billboard>
  );
}

const floorPalette = {
  vitrified_tiles: { color: "#d7dde5", roughness: 0.38, metalness: 0.02 },
  italian_marble: { color: "#f3f4f6", roughness: 0.16, metalness: 0.04 },
  dark_marble: { color: "#101827", roughness: 0.14, metalness: 0.08 },
  kota_stone: { color: "#8a927a", roughness: 0.72, metalness: 0.01 },
  wood_laminate: { color: "#8b5a2b", roughness: 0.5, metalness: 0.01 },
  oak_bedroom: { color: "#b9854f", roughness: 0.58, metalness: 0.01 },
  kitchen_stone: { color: "#d8d3c6", roughness: 0.42, metalness: 0.02 },
  bathroom_ceramic: { color: "#dbeafe", roughness: 0.32, metalness: 0.01 },
  parking_concrete: { color: "#8b929d", roughness: 0.74, metalness: 0.01 },
  living_marble: { color: "#ece7dc", roughness: 0.22, metalness: 0.03 }
};

const wallPalette = {
  warm_white: "#f0ece4",
  matte_concrete: "#8a9099",
  exposed_brick: "#a24632",
  texture_paint: "#d8dde4",
  // Exterior and Interior Palette mappings
  off_white: "#F8F8FF",
  warm_beige: "#F5F5DC",
  light_grey: "#D3D3D3",
  mustard: "#E4A010",
  terracotta: "#E2725B",
  cream: "#FDF5E6",
  beige: "#F5F5DC",
  peach: "#FFDAB9",
  sea_green: "#2E8B57",
  indigo: "#4B0082"
};

export const roomBounds = (room) => ({
  x: room.x * SCALE,
  z: room.z * SCALE,
  width: room.width * SCALE,
  length: room.length * SCALE
});

const roomFloor = (room, style) => {
  if (room.floorColor) return { color: room.floorColor, roughness: 0.5, metalness: 0.05 };
  if (room.type === "bedroom" || room.type === "master_bedroom") return floorPalette.oak_bedroom;
  if (room.type === "kitchen") return floorPalette.kitchen_stone;
  if (room.type === "bathroom") return floorPalette.bathroom_ceramic;
  if (room.type === "garage" || room.type === "parking") return floorPalette.parking_concrete;
  if (room.type === "living_room" && style?.floorMaterial === "vitrified_tiles")
    return floorPalette.living_marble;
  if (style?.floorMaterial?.startsWith('#')) return { color: style.floorMaterial, roughness: 0.6, metalness: 0 };
  return floorPalette[style?.floorMaterial] || floorPalette.vitrified_tiles;
};

const roomWallColor = (room, style) => {
  if (room.wallColor) return room.wallColor;
  if (room.type === "kitchen") return "#e8ecf0";
  if (room.type === "bathroom") return "#dce8f0";
  if (room.type === "garage" || room.type === "parking") return "#b8bec6";
  if (style?.wallFinish?.startsWith('#')) return style.wallFinish;
  return wallPalette[style?.wallFinish] || wallPalette.warm_white;
};

/* ── Sprite-based label (pure Three.js – no drei Text) ── */
function RoomLabel({ text, position, onClick, isXRay, bounds, compact = false }) {
  const texture = useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 112;
    const ctx = canvas.getContext("2d");

    // Pill background
    const r = 24;
    ctx.fillStyle = "rgba(5, 7, 12, 0.82)";
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.lineTo(canvas.width - r, 0);
    ctx.quadraticCurveTo(canvas.width, 0, canvas.width, r);
    ctx.lineTo(canvas.width, canvas.height - r);
    ctx.quadraticCurveTo(canvas.width, canvas.height, canvas.width - r, canvas.height);
    ctx.lineTo(r, canvas.height);
    ctx.quadraticCurveTo(0, canvas.height, 0, canvas.height - r);
    ctx.lineTo(0, r);
    ctx.quadraticCurveTo(0, 0, r, 0);
    ctx.closePath();
    ctx.fill();

    // Accent border
    ctx.strokeStyle = "rgba(74, 222, 128, 0.55)";
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Text
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 36px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, canvas.width / 2, canvas.height / 2);

    const tex = new THREE.CanvasTexture(canvas);
    tex.needsUpdate = true;
    return tex;
  }, [text]);

  // Compact rooms (bath/utility/pooja/store) get a smaller label.
  const defaultScaleX = (isXRay ? 0.65 : 1.3) * (compact ? 0.7 : 1);
  let scaleX = defaultScaleX;
  if (bounds && bounds.width && bounds.length) {
    // Keep the label inside the room footprint on BOTH axes so it never
    // spills onto walls or a neighbouring room.
    const maxSpan = Math.min(bounds.width, bounds.length) * 0.9;
    if (scaleX > maxSpan) scaleX = maxSpan;
  }
  const scale = [scaleX, scaleX * (0.285 / 1.3), 1];

  return (
    <sprite position={position} scale={scale} onClick={onClick}>
      <spriteMaterial
        map={texture}
        transparent
        depthTest={false}
        sizeAttenuation
        opacity={isXRay ? 0.3 : 1}
      />
    </sprite>
  );
}

/* ── Interactive Expansion Arrow (Replaced Cones) ── */
function ExpansionArrow({ position, rotation, direction, roomId, accent }) {
  const [hovered, setHovered] = useState(false);
  const expandRoom = useProjectStore((state) => state.expandRoom);

  // We'll use a simple group with an invisible hit box and a visual arrow using thin boxes
  return (
    <group position={position} rotation={rotation}>
      <mesh
        onClick={(e) => {
          e.stopPropagation();
          expandRoom(roomId, direction);
        }}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHovered(true);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          setHovered(false);
          document.body.style.cursor = "auto";
        }}
        visible={false}
      >
        <boxGeometry args={[0.5, 0.5, 0.5]} />
        <meshBasicMaterial transparent opacity={0} />
      </mesh>
      
      {/* Visual Arrow pointing +Z (which gets rotated) */}
      <group position={[0, 0, 0]}>
        {/* Shaft */}
        <mesh position={[0, 0, 0.1]}>
          <boxGeometry args={[0.04, 0.04, 0.2]} />
          <meshStandardMaterial color={hovered ? "#39FF14" : accent} emissive={hovered ? "#39FF14" : accent} emissiveIntensity={hovered ? 1.5 : 0.8} />
        </mesh>
        {/* Head left */}
        <mesh position={[-0.05, 0, 0.15]} rotation={[0, -Math.PI / 4, 0]}>
          <boxGeometry args={[0.04, 0.04, 0.15]} />
          <meshStandardMaterial color={hovered ? "#39FF14" : accent} emissive={hovered ? "#39FF14" : accent} emissiveIntensity={hovered ? 1.5 : 0.8} />
        </mesh>
        {/* Head right */}
        <mesh position={[0.05, 0, 0.15]} rotation={[0, Math.PI / 4, 0]}>
          <boxGeometry args={[0.04, 0.04, 0.15]} />
          <meshStandardMaterial color={hovered ? "#39FF14" : accent} emissive={hovered ? "#39FF14" : accent} emissiveIntensity={hovered ? 1.5 : 0.8} />
        </mesh>
      </group>
    </group>
  );
}

  /* 🪟 Glass Window Pane 🪟 */
  function WindowPane({ position, size, rotation = [0, 0, 0], isJali = false, isChhajja = false }) {
    // Determine the direction the window faces based on size dimensions
    // size is [width, height, thickness] for Z-facing (North/South)
    // size is [thickness, height, width] for X-facing (East/West)
    const isZFacing = size[2] < size[0];
    const chhajjaDepth = 0.6;
    const chhajjaWidth = isZFacing ? size[0] + 0.4 : size[2] + 0.4;
    
    return (
      <group position={position} rotation={rotation}>
        {isJali ? (
          <mesh raycast={() => null}>
            <boxGeometry args={size} />
            <meshStandardMaterial
              color="#e5e7eb"
              roughness={0.9}
              transparent
              opacity={0.7}
              wireframe={true} // Simple representation of Jali (lattice)
            />
          </mesh>
        ) : (
          <mesh raycast={() => null}>
            <boxGeometry args={size} />
            <meshPhysicalMaterial
              color="#88ccff"
              transmission={0.92}
              transparent
              opacity={0.35}
              roughness={0.05}
              ior={1.5}
              thickness={0.3}
              clearcoat={1}
              depthWrite={false}
            />
          </mesh>
        )}
        
        {/* Chhajja overhang */}
        {isChhajja && (
          <mesh position={[0, size[1]/2 + 0.05, isZFacing ? (position[2] > 5 ? chhajjaDepth/2 : -chhajjaDepth/2) : 0]} 
                rotation={isZFacing ? [0, 0, 0] : [0, Math.PI/2, 0]} castShadow>
            <boxGeometry args={[chhajjaWidth, 0.05, chhajjaDepth]} />
            <meshStandardMaterial color="#cbd5e1" roughness={0.9} />
          </mesh>
        )}
      </group>
    );
  }
  
  function DoorModel({ position, size, rotation, transparent }) {
    return (
      <mesh position={position} rotation={rotation} raycast={() => null} castShadow={!transparent} receiveShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={transparent ? "#cbd5e1" : "#8b5a2b"} roughness={0.8} transparent={transparent} opacity={transparent ? 0.15 : 1} />
    </mesh>
  );
}

/* ── Wall Builder Helper ── */
// Generates wall segments with cutouts for doors and windows
function buildWallSegmentsWithOpenings(wallKind, length, thickness, openings, wallHeight) {
  // openings: { center: float, width: float, height: float, sill: float, isWindow: boolean }
  const segments = [];
  
  // Sort openings by position
  openings.sort((a, b) => a.center - b.center);
  
  let currentPos = 0;
  
  for (const op of openings) {
    const opStart = op.center - op.width / 2;
    const opEnd = op.center + op.width / 2;
    
    // Solid wall before the opening
    if (opStart > currentPos + 0.01) {
      const segLength = opStart - currentPos;
      const px = currentPos + segLength / 2;
      segments.push({
        id: `${wallKind}-solid-${Math.round(px*100)}`,
        kind: `${wallKind}-solid`,
        px,
        py: wallHeight / 2,
        sx: segLength,
        sy: wallHeight,
        sz: thickness
      });
    }
    
    // Above opening (Header)
    const headerHeight = wallHeight - (op.sill + op.height);
    if (headerHeight > 0.01) {
      segments.push({
        kind: `${wallKind}-header`,
        px: op.center,
        py: op.sill + op.height + headerHeight / 2,
        sx: op.width,
        sy: headerHeight,
        sz: thickness
      });
    }
    
    // Below opening (Sill) - for windows
    if (op.sill > 0.01) {
      segments.push({
        kind: `${wallKind}-sill`,
        px: op.center,
        py: op.sill / 2,
        sx: op.width,
        sy: op.sill,
        sz: thickness
      });
    }
    
    currentPos = Math.max(currentPos, opEnd);
  }
  
  // Solid wall after the last opening
  if (currentPos < length - 0.01) {
    const segLength = length - currentPos;
    const px = currentPos + segLength / 2;
    segments.push({
      id: `${wallKind}-solid-${Math.round(px*100)}`,
      kind: `${wallKind}-solid`,
      px,
      py: wallHeight / 2,
      sx: segLength,
      sy: wallHeight,
      sz: thickness
    });
  }
  
  return segments;
}

function wallSegmentsFor(room, bounds, wallThickness) {
  const segments = [];
  const windowPanes = [];
  const doorPanes = [];
  const roomHeight = room.is_double_height ? WALL_HEIGHT * 2 : WALL_HEIGHT;
  
  // Gather openings per wall orientation (north, south, east, west)
  // Coordinates are relative to room
  // north = -z face, south = +z face
  // west = -x face, east = +x face
  
  const walls = {
    north: { length: bounds.width, openings: [] }, // z = 0, x from 0 to width
    south: { length: bounds.width, openings: [] }, // z = length, x from 0 to width
    west:  { length: bounds.length, openings: [] }, // x = 0, z from 0 to length
    east:  { length: bounds.length, openings: [] }  // x = width, z from 0 to length
  };
  
  // Parse doors
  (room.doors || []).forEach(d => {
    const w = (d.width || 3.0) * SCALE;
    const h = Math.min((d.height || 7.0) * SCALE, roomHeight - 0.2);
    
    if (d.wall_orientation === 'north' || d.wall_orientation === 'south') {
      walls[d.wall_orientation].openings.push({
        center: d.x * SCALE, width: w, height: h, sill: 0, isWindow: false
      });
      // Only render main doors for exterior or explicitly marked
      if (d.is_main) {
        doorPanes.push({
          pos: [d.x * SCALE, h / 2, d.wall_orientation === 'north' ? 0 : bounds.length],
          size: [w, h, wallThickness + 0.02],
          rot: [0, 0, 0],
          is_main: d.is_main
        });
      }
    } else if (d.wall_orientation === 'east' || d.wall_orientation === 'west') {
      walls[d.wall_orientation].openings.push({
        center: d.z * SCALE, width: w, height: h, sill: 0, isWindow: false
      });
      if (d.is_main) {
        doorPanes.push({
          pos: [d.wall_orientation === 'west' ? 0 : bounds.width, h / 2, d.z * SCALE],
          size: [wallThickness + 0.02, h, w],
          rot: [0, 0, 0],
          is_main: d.is_main
        });
      }
    }
  });
  
  // Parse windows
  (room.windows || []).forEach(w => {
    const width = (w.width || 4.0) * SCALE;
    const height = (w.height || 4.0) * SCALE;
    const sill = (w.sill_height || 3.0) * SCALE;
    
    if (w.wall_orientation === 'north' || w.wall_orientation === 'south') {
      walls[w.wall_orientation].openings.push({
        center: w.x * SCALE, width, height, sill, isWindow: true
      });
      windowPanes.push({
        pos: [w.x * SCALE, sill + height/2, w.wall_orientation === 'north' ? 0 : bounds.length],
        size: [width, height, wallThickness + 0.01],
        rot: [0, 0, 0]
      });
    } else if (w.wall_orientation === 'east' || w.wall_orientation === 'west') {
      walls[w.wall_orientation].openings.push({
        center: w.z * SCALE, width, height, sill, isWindow: true
      });
      windowPanes.push({
        pos: [w.wall_orientation === 'west' ? 0 : bounds.width, sill + height/2, w.z * SCALE],
        size: [wallThickness + 0.01, height, width], // rotated
        rot: [0, 0, 0]
      });
    }
  });

  // Build segments for each wall.
  // The backend marks circulation rooms (corridor/hallway/staircase) with
  // suppress_wall_faces — faces shared with adjacent rooms. Skip those faces
  // so only the adjacent room renders the shared wall (prevents double-thick).
  const suppress = new Set(room.suppress_wall_faces || []);

  // North wall (z = 0, along x)
  if (!suppress.has('north')) {
    const northSegs = buildWallSegmentsWithOpenings('north', bounds.width, wallThickness, walls.north.openings, roomHeight);
    northSegs.forEach(s => segments.push({ ...s, pz: 0 }));
  }

  // South wall (z = bounds.length, along x)
  if (!suppress.has('south')) {
    const southSegs = buildWallSegmentsWithOpenings('south', bounds.width, wallThickness, walls.south.openings, roomHeight);
    southSegs.forEach(s => segments.push({ ...s, pz: bounds.length }));
  }

  // West wall (x = 0, along z) -> Need to rotate coordinates
  if (!suppress.has('west')) {
    const westSegs = buildWallSegmentsWithOpenings('west', bounds.length, wallThickness, walls.west.openings, roomHeight);
    westSegs.forEach(s => segments.push({ ...s, pz: s.px, px: 0, sx: wallThickness, sz: s.sx }));
  }

  // East wall (x = bounds.width, along z)
  if (!suppress.has('east')) {
    const eastSegs = buildWallSegmentsWithOpenings('east', bounds.length, wallThickness, walls.east.openings, roomHeight);
    eastSegs.forEach(s => segments.push({ ...s, pz: s.px, px: bounds.width, sx: wallThickness, sz: s.sx }));
  }

  // Filter out any walls that were deleted
  const filteredSegments = room.deletedWalls 
    ? segments.filter(s => !room.deletedWalls.includes(s.id))
    : segments;

  return { segments: filteredSegments, windowPanes, doorPanes };
}

/* ── Selection outline ──
   Native lineSegments + EdgesGeometry. Avoids drei <Edges>, whose derived
   material lacks addEventListener and crashed scene projection / PDF capture
   ("derivedMaterial.addEventListener is not a function"). */
function OutlineBox({ size, color, position = [0, 0, 0] }) {
  const geo = useMemo(
    () => new THREE.EdgesGeometry(new THREE.BoxGeometry(size[0], size[1], size[2])),
    [size[0], size[1], size[2]]
  );
  React.useEffect(() => () => geo.dispose(), [geo]);
  return (
    <lineSegments position={position} geometry={geo} renderOrder={999}>
      <lineBasicMaterial color={color} toneMapped={false} depthTest={false} transparent />
    </lineSegments>
  );
}

/* ── Main Room component ── */
export default function Room({
  room,
  selected,
  style,
  accent,
  showLabel,
  onSelect,
  transparent = false,
  buildingBounds = null,
  exteriorColor = null
}) {
  const [hovered, setHovered] = useState(false);
  const floor = roomFloor(room, style);
  const wallColor = roomWallColor(room, style);
  const bounds = roomBounds(room);
  const wallThickness = Math.max(0.06, (room.wallThicknessIn || 6) * 0.012);
  const showWiring = useProjectStore((state) => state.showWiring);
  const showPlumbing = useProjectStore((state) => state.showPlumbing);
  const builderMode = useProjectStore((state) => state.builderMode);
  const indianOptions = useProjectStore((state) => state.project.indianOptions || {});
  const isJali = indianOptions.jali;
  const isChhajja = indianOptions.chhajja;
  const isMaliya = indianOptions.maliya && (room.type.includes("bedroom") || room.type.includes("kitchen"));

  // Safety guard: skip rooms with invalid geometry to prevent Three.js NaN errors
  if (!Number.isFinite(bounds.width) || bounds.width <= 0.01 ||
      !Number.isFinite(bounds.length) || bounds.length <= 0.01) {
    return null;
  }

  
  const { segments: wallSegments, windowPanes, doorPanes } = useMemo(
    () => wallSegmentsFor(room, bounds, wallThickness),
    [bounds.length, bounds.width, room, wallThickness]
  );
  
  const actualWallColor = room.wallColor || wallColor || "#f8fafc";
  const actualFloorColor = room.floorColor || floor.color || "#e2e8f0";

  // Which of this room's walls face the building exterior? (raw room coords vs
  // building perimeter). Exterior walls get the exterior facade color.
  const EXT_TOL = 1.0;
  const exteriorSides = {
    north: buildingBounds && Math.abs(room.z - buildingBounds.minZ) < EXT_TOL,
    south: buildingBounds && Math.abs((room.z + room.length) - buildingBounds.maxZ) < EXT_TOL,
    west:  buildingBounds && Math.abs(room.x - buildingBounds.minX) < EXT_TOL,
    east:  buildingBounds && Math.abs((room.x + room.width) - buildingBounds.maxX) < EXT_TOL,
  };
  const extHex = exteriorColor
    ? (exteriorColor.startsWith("#") ? exteriorColor
        : (wallPalette[exteriorColor.toLowerCase().replace(/ /g, "_")] || null))
    : null;
  const floorRoughness = floor.roughness ?? 0.6;
  const floorMetalness = floor.metalness ?? 0.01;
  
  const glow = hovered ? "#38bdf8" : "#000000";
  const spring = useSpring({ scaleY: 1, from: { scaleY: 0.04 }, delay: 90 });

  const handleClick = (kind) => (event) => {
    event.stopPropagation();
    const mode = useProjectStore.getState().selectionMode;

    if (mode === "room") {
      onSelect(room.id, "room", event.shiftKey);
      return;
    }

    if (mode === "wall" && !kind.includes("solid")) return;
    if (mode === "floor" && kind !== "floor") return;
    if (mode === "furniture" && kind !== "furniture") return;
    
    onSelect(room.id, kind, event.shiftKey);
  };

  const selectedObject = useProjectStore((state) => state.selectedObject);
  const isSelectedRoom = selected && selectedObject?.kind === "room";
  const isSelectedFloor = selected && (selectedObject?.kind === "floor" || isSelectedRoom);

  const isTransparentMode = useProjectStore((state) => state.isTransparentMode);
  const isXRay = showWiring || showPlumbing || isTransparentMode;

  return (
    <group position={[bounds.x, 0, bounds.z]}>
      <group visible={!transparent}>
      {/* ── Floor ── */}
        <mesh
          receiveShadow={!isXRay}
          onPointerOver={() => setHovered(true)}
          onPointerOut={() => setHovered(false)}
          onClick={handleClick("floor")}
          position={[bounds.width / 2, 0.01, bounds.length / 2]}
          userData={{ hideInBlueprint: true }}
        >
          <boxGeometry args={[bounds.width, 0.04, bounds.length]} />
          <meshPhysicalMaterial
            color={actualFloorColor}
            roughness={floorRoughness}
            metalness={floorMetalness}
            clearcoat={0.25}
            reflectivity={0.3}
            transparent={isXRay}
            opacity={isXRay ? 0.3 : 1}
          />
        </mesh>
        
        {isSelectedFloor && (
          <OutlineBox size={[bounds.width, 0.02, bounds.length]} color={accent} position={[bounds.width / 2, 0.05, bounds.length / 2]} />
        )}

      {/* ── Baseboard strips ── */}
      {!isXRay && (
        <>
          <mesh position={[bounds.width / 2, 0.035, bounds.length - 0.01]} receiveShadow userData={{ hideInBlueprint: true }}>
            <boxGeometry args={[bounds.width, 0.06, 0.018]} />
            <meshStandardMaterial color="#1a1a1a" roughness={0.7} />
          </mesh>
          <mesh position={[bounds.width / 2, 0.035, 0.01]} receiveShadow userData={{ hideInBlueprint: true }}>
            <boxGeometry args={[bounds.width, 0.06, 0.018]} />
            <meshStandardMaterial color="#1a1a1a" roughness={0.7} />
          </mesh>
          <mesh position={[0.01, 0.035, bounds.length / 2]} receiveShadow userData={{ hideInBlueprint: true }}>
            <boxGeometry args={[0.018, 0.06, bounds.length]} />
            <meshStandardMaterial color="#1a1a1a" roughness={0.7} />
          </mesh>
          <mesh position={[bounds.width - 0.01, 0.035, bounds.length / 2]} receiveShadow userData={{ hideInBlueprint: true }}>
            <boxGeometry args={[0.018, 0.06, bounds.length]} />
            <meshStandardMaterial color="#1a1a1a" roughness={0.7} />
          </mesh>
        </>
      )}

      {/* ── Walls with spring anim ── */}
      <a.group scale-y={spring.scaleY}>
        {wallSegments.map(({ id, kind, px, py, pz, sx, sy, sz }, idx) => {
          const _selKind = selectedObject?.kind || "";
          const isSelectedWall = selected && !!id && _selKind.includes(id);
          let segWallColor = room.wallColors?.[id];
          if (segWallColor) {
            segWallColor = wallPalette[segWallColor] || (segWallColor.startsWith('#') ? segWallColor : wallPalette.warm_white);
          } else {
            // Exterior-facing walls take the facade color; interior walls take
            // the room's interior paint.
            const orientation = (kind || "").split("-")[0];
            if (extHex && exteriorSides[orientation]) {
              segWallColor = extHex;
            } else {
              segWallColor = actualWallColor;
            }
          }
          
          // Slightly overlap walls to prevent floating point gaps, but use very tiny overlap to prevent Z-fighting blurriness.
          // Exterior facade segments overlap by a full wall thickness so the
          // colored facade is continuous — no white strips where interior
          // partition walls or corners meet the outer face.
          // Overlap walls along their long axis so perpendicular segments meet
          // INSIDE the corner instead of leaving a square hole. Exterior facade
          // segments overlap more (continuous painted facade); interior walls
          // overlap a full thickness so every corner intersection is filled.
          const isExtSeg = !!extHex && segWallColor === extHex;
          const extBump = isExtSeg ? wallThickness * 2.2 : wallThickness * 1.1;
          const overSx = sx > wallThickness ? sx + extBump : sx;
          const overSz = sz > wallThickness ? sz + extBump : sz;

          return (
          <React.Fragment key={`${id || kind}-${idx}`}>
          <mesh
            castShadow={!isXRay}
            receiveShadow={!isXRay}
            position={[px, py, pz]}
            onClick={handleClick(id || kind)}
          >
            <boxGeometry args={[overSx, sy, overSz]} />
              <meshPhysicalMaterial
                color={isXRay ? "#ffffff" : segWallColor}
                roughness={isXRay ? 0.1 : 0.9}
                metalness={isXRay ? 0.1 : 0}
                transmission={isXRay ? 0.95 : 0}
                clearcoat={isXRay ? 1 : 0}
                emissive={glow}
                emissiveIntensity={hovered ? 0.04 : 0}
                transparent={isXRay}
                opacity={isXRay ? 0.15 : 1}
                depthWrite={!isXRay}
              />
          </mesh>
          {isSelectedWall && <OutlineBox size={[overSx, sy, overSz]} color="#34d399" position={[px, py, pz]} />}
          </React.Fragment>
        )})}

        {/* ── Corner posts ──
            Each wall is a box centred ON its edge line, so the OUTER corner
            square (beyond both wall faces) is covered by neither wall → a white
            notch at every building corner. A short post at each corner fills
            that notch and inherits the facade colour when the corner is on the
            building exterior, so corners are always solid and correctly painted. */}
        {!isXRay && (() => {
          const cornerH = room.is_double_height ? WALL_HEIGHT * 2 : WALL_HEIGHT;
          const t = wallThickness * 1.35; // overlap both meeting walls + the notch
          const postColor = (sa, sb) =>
            (extHex && (exteriorSides[sa] || exteriorSides[sb])) ? extHex : actualWallColor;
          const corners = [
            { x: 0, z: 0, c: postColor("north", "west") },
            { x: bounds.width, z: 0, c: postColor("north", "east") },
            { x: 0, z: bounds.length, c: postColor("south", "west") },
            { x: bounds.width, z: bounds.length, c: postColor("south", "east") },
          ];
          return corners.map((p, i) => (
            <mesh key={`post-${i}`} position={[p.x, cornerH / 2, p.z]} castShadow receiveShadow>
              <boxGeometry args={[t, cornerH, t]} />
              <meshPhysicalMaterial color={p.c} roughness={0.9} metalness={0} />
            </mesh>
          ));
        })()}
      </a.group>

      {/* ── Glass windows ── */}
      {windowPanes.map((pane, idx) => (
        <WindowPane
          key={`win-${idx}`}
          position={pane.pos}
          size={pane.size}
          rotation={pane.rot}
          isJali={isJali}
          isChhajja={isChhajja}
        />
      ))}
      
      {/* ── Maliya (Loft) ── */}
      {isMaliya && !isXRay && (
        <mesh position={[bounds.width / 2, (room.is_double_height ? WALL_HEIGHT * 2 : WALL_HEIGHT) - 0.5, 0.4]} castShadow receiveShadow>
          <boxGeometry args={[bounds.width - 0.1, 0.08, 0.8]} />
          <meshStandardMaterial color="#cbd5e1" roughness={0.8} />
        </mesh>
      )}

      {/* ── Main Doors ── */}
      {doorPanes.map((pane, idx) => (
        <DoorModel
          key={`door-${idx}`}
          position={pane.pos}
          size={pane.size}
          rotation={pane.rot}
        />
      ))}

      </group>
      {/* 3D MEP Traces & Fixtures */}
      {(showWiring || showPlumbing) && room.mep_nodes && room.mep_nodes.map((node, i) => {
        // Defensive: skip malformed nodes so toggling visibility never throws.
        if (!node || typeof node.x !== "number" || typeof node.z !== "number") return null;
        // node coords are absolute, convert to local bounds
        const nx = (node.x - room.x) * SCALE;
        const nz = (node.z - room.z) * SCALE;
        const ny = (node.y || 0.5) * SCALE; // fallback height if not provided

        const isWiring = showWiring && node.is_wiring;
        const isPlumbing = showPlumbing && node.is_plumbing;

        if (isWiring || isPlumbing) {
          let fixtureMesh = null;
          const nodeType = node.type || "fixture";
          let labelText = nodeType.toUpperCase().replace("_", " ");
          if (nodeType.includes("ceiling") || nodeType.includes("chandelier")) labelText = "LIGHT";
          else if (nodeType.includes("switchboard")) labelText = "SWITCHBOARD";
          else if (nodeType.includes("socket")) labelText = "SOCKET";
          else if (nodeType.includes("switch")) labelText = "SWITCH";
          else if (nodeType.includes("fan")) labelText = "FAN";

          // Labels always show while a MEP mode is active (consistent visibility),
          // and stay compact/readable via distance stabilization.
          const labelBlock = nodeType !== "main_db" ? (
            <MepLabel text={labelText} color={isPlumbing ? "#bae6fd" : "#fde68a"} />
          ) : null;

          if (nodeType === "main_db") {
            fixtureMesh = (
              <group>
                <mesh>
                  <boxGeometry args={[0.8, 1.0, 0.2]} />
                  <meshStandardMaterial color="#ef4444" roughness={0.4} metalness={0.6} />
                </mesh>
                <MepLabel text="MAIN DB" color="#fca5a5" y={0.7} base={0.11} />
              </group>
            );
          } else if (nodeType === "switchboard") {
            fixtureMesh = (
              <group>
                <mesh>
                  <boxGeometry args={[0.3, 0.4, 0.05]} />
                  <meshStandardMaterial color="#cbd5e1" />
                </mesh>
                {labelBlock}
              </group>
            );
          } else if (nodeType.includes("ceiling") || nodeType.includes("chandelier") || nodeType.includes("fan")) {
            fixtureMesh = (
              <group>
                <mesh>
                  <cylinderGeometry args={[0.2, 0.2, 0.1, 16]} />
                  <meshStandardMaterial color="#fff" emissive="#fef08a" emissiveIntensity={2} />
                </mesh>
                {labelBlock}
              </group>
            );
          } else if (nodeType.includes("switch")) {
            fixtureMesh = (
              <group>
                <mesh>
                  <boxGeometry args={[0.15, 0.2, 0.02]} />
                  <meshStandardMaterial color="#fff" roughness={0.2} />
                </mesh>
                {labelBlock}
              </group>
            );
          } else if (nodeType.includes("socket")) {
            fixtureMesh = (
              <group>
                <mesh>
                  <boxGeometry args={[0.15, 0.15, 0.02]} />
                  <meshStandardMaterial color="#ddd" roughness={0.5} />
                </mesh>
                {labelBlock}
              </group>
            );
          } else if (isPlumbing) {
            fixtureMesh = (
              <group>
                <mesh>
                  <boxGeometry args={[0.4, 0.2, 0.3]} />
                  <meshStandardMaterial color="#e2e8f0" roughness={0.1} />
                </mesh>
                <mesh position={[0, 0.15, 0]}>
                  <cylinderGeometry args={[0.02, 0.02, 0.1]} />
                  <meshStandardMaterial color="#94a3b8" metalness={0.8} />
                </mesh>
                {labelBlock}
              </group>
            );
          } else {
            // Generic fallback fixture
            fixtureMesh = (
              <group>
                <mesh>
                  <boxGeometry args={[0.2, 0.2, 0.2]} />
                  <meshStandardMaterial color={isWiring ? "#eab308" : "#3b82f6"} />
                </mesh>
                {labelBlock}
              </group>
            );
          }

          return <group key={`mep-node-${i}`} position={[nx, ny, nz]}>{fixtureMesh}</group>;
        }
        return null;
      })}

        {/* MEP Paths (Wiring & Plumbing) */}
        {[(showWiring ? room.wiring_paths || [] : []), (showPlumbing ? room.plumbing_paths || [] : [])].flat().map((path, i) => {
          if (!path || !path.from || !path.to) return null;
          // paths are absolute coords, convert to local bounds
          const fromX = (path.from.x - room.x) * SCALE;
          const fromY = path.from.y * SCALE;
          const fromZ = (path.from.z - room.z) * SCALE;
          const toX = (path.to.x - room.x) * SCALE;
          const toY = path.to.y * SCALE;
          const toZ = (path.to.z - room.z) * SCALE;
          
          let color = "#eab308"; // Default Yellow
          if (path.circuit_type === "lighting") color = "#eab308";
          else if (path.circuit_type === "general_power") color = "#ef4444";
          else if (path.circuit_type === "heavy_power") color = "#f97316"; // Orange
          else if (path.circuit_type === "data") color = "#3b82f6"; // Blue
          else if (path.circuit_type === "smart") color = "#22c55e"; // Green
          else if (path.circuit_type === "sub_main") color = "#f8fafc"; // White
          else if (path.circuit_type === "water_main") color = "#94a3b8"; // Slate
          else if (path.circuit_type === "cold_water") color = "#0ea5e9"; // Light blue
          else if (path.circuit_type === "hot_water") color = "#ea580c"; // Dark Orange
          else if (path.circuit_type === "drainage") color = "#78350f"; // Brown
          else if (path.circuit_type === "vent") color = "#16a34a"; // Green
        
        const rawPoints = [
          [fromX, fromY, fromZ],
          [fromX, toY, fromZ],
          [fromX, toY, toZ],
          [toX, toY, toZ]
        ];
        
        // Remove consecutive duplicate points to prevent LineGeometry NaN crash
        const points = rawPoints.filter((pt, index, arr) => {
          if (index === 0) return true;
          const prev = arr[index - 1];
          // Keep point if it differs from the previous point by more than epsilon
          return Math.abs(pt[0] - prev[0]) > 0.001 || 
                 Math.abs(pt[1] - prev[1]) > 0.001 || 
                 Math.abs(pt[2] - prev[2]) > 0.001;
        });
        
        if (points.length < 2) return null;

        return (
          <Line 
            key={`wire-${i}`} 
            points={points} 
            color={color} 
            lineWidth={path.is_home_run ? 3 : 2} 
            opacity={0.8}
            transparent
          />
        );
      })}

      {/* RMST Plumbing Paths */}
      {showPlumbing && room.plumbing_paths && room.plumbing_paths.map((path, i) => {
        if (!path || !path.from || !path.to) return null;
        // paths are absolute coords, convert to local bounds
        const fromX = (path.from.x - room.x) * SCALE;
        const fromY = path.from.y * SCALE;
        const fromZ = (path.from.z - room.z) * SCALE;
        const toX = (path.to.x - room.x) * SCALE;
        const toY = path.to.y * SCALE;
        const toZ = (path.to.z - room.z) * SCALE;

        const rawPoints = [
          [fromX, fromY, fromZ],
          [fromX, toY, fromZ],
          [fromX, toY, toZ],
          [toX, toY, toZ]
        ];
        
        // Remove consecutive duplicate points to prevent LineGeometry NaN crash
        const points = rawPoints.filter((pt, index, arr) => {
          if (index === 0) return true;
          const prev = arr[index - 1];
          // Keep point if it differs from the previous point by more than epsilon
          return Math.abs(pt[0] - prev[0]) > 0.001 || 
                 Math.abs(pt[1] - prev[1]) > 0.001 || 
                 Math.abs(pt[2] - prev[2]) > 0.001;
        });
        
        if (points.length < 2) return null;

        return (
          <Line 
            key={`pipe-${i}`} 
            points={points} 
            color="#3b82f6"
            lineWidth={3}
            opacity={0.7}
            transparent
          />
        );
      })}

      {/* 🔮 Interior point light 🔮 */}
      <pointLight
        position={[
          bounds.width / 2,
          WALL_HEIGHT - 0.12,
          bounds.length / 2
        ]}
        intensity={0.35}
        distance={4}
        color="#ffeedd"
        decay={2}
      />

      {/* ── Selection highlight glow on floor ── */}
      {selected && (
        <mesh position={[bounds.width / 2, 0.006, bounds.length / 2]}>
          <boxGeometry
            args={[bounds.width + 0.04, 0.005, bounds.length + 0.04]}
          />
          <meshStandardMaterial
          color={hovered ? accent : "#10b981"}
          emissive={hovered ? accent : "#10b981"}
          emissiveIntensity={hovered ? 0.8 : 0.4}
          transparent
          opacity={hovered ? 1 : 0.8}
        /></mesh>
      )}

      {/* ── Selection perimeter outline ── */}
      {selected && (
        <OutlineBox size={[bounds.width, WALL_HEIGHT, bounds.length]} color="#34d399" position={[bounds.width / 2, WALL_HEIGHT / 2, bounds.length / 2]} />
      )}

      {/* 🏷️ Label (sprite-based) 🏷️ */}
      {showLabel && !transparent && (
        <RoomLabel
          text={room.name}
          bounds={bounds}
          compact={/bath|toilet|powder|pooja|store|utility|wash/i.test(room.type || "")}
          position={[
            bounds.width / 2,
            WALL_HEIGHT + 0.45,
            bounds.length / 2
          ]}
          onClick={handleClick("room")}
          isXRay={isXRay}
        />
      )}

      {/* ── Expansion arrows (only visible when room is selected) ── */}
      {selected && (
        <>
          {/* East (+X) */}
          <ExpansionArrow
            position={[bounds.width, WALL_HEIGHT / 2, bounds.length / 2]}
            rotation={[0, Math.PI / 2, 0]}
            direction="east"
            roomId={room.id}
            accent={accent}
          />
          {/* West (-X) */}
          <ExpansionArrow
            position={[0, WALL_HEIGHT / 2, bounds.length / 2]}
            rotation={[0, -Math.PI / 2, 0]}
            direction="west"
            roomId={room.id}
            accent={accent}
          />
          {/* South (+Z) */}
          <ExpansionArrow
            position={[bounds.width / 2, WALL_HEIGHT / 2, bounds.length]}
            rotation={[0, 0, 0]}
            direction="south"
            roomId={room.id}
            accent={accent}
          />
          {/* North (-Z) */}
          <ExpansionArrow
            position={[bounds.width / 2, WALL_HEIGHT / 2, 0]}
            rotation={[0, Math.PI, 0]}
            direction="north"
            roomId={room.id}
            accent={accent}
          />
        </>
      )}
    </group>
  );
}

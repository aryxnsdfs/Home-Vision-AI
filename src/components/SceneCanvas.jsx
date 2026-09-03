import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Environment,
  Grid,
  OrbitControls,
  Line,
  Html,
  DragControls,
  Text
} from "@react-three/drei";
import * as THREE from "three";
import Plinth from "./Plinth.jsx";
import HouseRoom, { WALL_HEIGHT as ROOM_WALL_HEIGHT, roomBounds } from "./Room.jsx";
import StructuralLayer from "./StructuralLayer.jsx";
import { useProjectStore } from "../store/useProjectStore.js";

function ProceduralFurniture({ room, isSelected, accent, onClick, color }) {
  const seed = Array.from(room.type || "").reduce((s, c) => s + c.charCodeAt(0), 0);
  const random = (idx) => {
    const x = Math.sin(seed + idx) * 10000;
    return x - Math.floor(x);
  };
  const f = Math.min(1, (room.width * 0.18) / 2.0);
  const blocks = [];
  const numBlocks = Math.floor(random(1) * 4) + 2;
  for (let i = 0; i < numBlocks; i++) {
    const w = (random(i * 4 + 1) * 0.8 + 0.4) * f;
    const h = (random(i * 4 + 2) * 0.6 + 0.2) * f;
    const d = (random(i * 4 + 3) * 0.8 + 0.4) * f;
    const x = (random(i * 4 + 4) * 0.6 - 0.3) * (room.width * 0.18);
    const z = (random(i * 4 + 5) * 0.6 - 0.3) * (room.length * 0.18);
    blocks.push(
      <mesh key={i} castShadow receiveShadow position={[x, h / 2, z]}>
        <boxGeometry args={[w, h, d]} />
        <GlassPhysicalMaterial type="sofa" />
        {isSelected && <BoxEdges args={[w, h, d]} color={accent} scale={1.05} />}
      </mesh>
    );
  }
  const roomW = room.width * 0.18;
  const roomL = room.length * 0.18;
  return (
    <group position={[roomW * 0.5, 0, roomL * 0.5]} onClick={onClick}>
      {blocks}
    </group>
  );
}

const SCALE = 0.18;
const EYE_LEVEL = 1.6;
const WALL_HEIGHT = ROOM_WALL_HEIGHT;

/* ── Native edge outline ──
   Replaces drei <Edges>, whose derived material has no addEventListener and
   crashed scene projection / PDF capture ("derivedMaterial.addEventListener
   is not a function"). Mirrors parent box geometry via explicit EdgesGeometry. */
function BoxEdges({ args, color = "#ffffff", scale = 1 }) {
  const geo = useMemo(
    () => new THREE.EdgesGeometry(new THREE.BoxGeometry(args[0], args[1], args[2])),
    [args[0], args[1], args[2]]
  );
  useEffect(() => () => geo.dispose(), [geo]);
  return (
    <lineSegments geometry={geo} scale={scale}>
      <lineBasicMaterial color={color} toneMapped={false} />
    </lineSegments>
  );
}

/* ── Fixed, solid low-poly furniture finish ──
   Furniture must not inherit a room/style colour: that produced the pale,
   transparent glass look in older saved projects. */
const glassFurniture = {
  sofa: { color: "#9a7255" },
  table: { color: "#79533a" },
  chair: { color: "#ad8564" },
  bed: { color: "#b48b69" },
  wardrobe: { color: "#805a40" },
  kitchen: { color: "#987052" },
  car: { color: "#76513b" },
  bath: { color: "#aa8262" }
};

function GlassPhysicalMaterial({ type = "sofa" }) {
  const tint = glassFurniture[type] || glassFurniture.sofa;
  return (
    <meshStandardMaterial
      key={`furniture-${type}`}
      color={tint.color}
      roughness={0.72}
      metalness={0.03}
    />
  );
}
/* ── Screenshot helpers ── */
const sanitizeCanvasDataUrl = (value) => {
  if (typeof value !== "string") return null;
  const clean = value.trim();
  if (clean.startsWith("data:image/jpeg;base64,")) return clean;
  if (clean.startsWith("data:image/png;base64,")) {
    return clean.replace("data:image/png;base64,", "data:image/jpeg;base64,");
  }
  return null;
};

const SNAPSHOT_W = 1200;
const SNAPSHOT_H = 815;

function takeSnapshot(gl, scene, camera, options = {}) {
  const size = gl.getSize(new THREE.Vector2());
  const ratio = gl.getPixelRatio();
  const background = scene.background;
  const clearColor = gl.getClearColor(new THREE.Color()).clone();
  const clearAlpha = gl.getClearAlpha();
  
  const savedCamera = {
    position: camera.position.clone(),
    quaternion: camera.quaternion.clone(),
    zoom: camera.zoom
  };
  
  const originalMaterials = [];
  const originalVisibilities = [];
  
  if (options.blueprint) {
    const wireframeMat = new THREE.MeshBasicMaterial({ color: "#f8fbff", wireframe: true });
    scene.traverse(node => {
      if (node.userData && node.userData.hideInBlueprint) {
        originalVisibilities.push([node, node.visible]);
        node.visible = false;
      }
      if (node.isMesh) {
        // Skip troika-three-text Text meshes — their custom material getter
        // returns derived/array materials. Saving via getter then restoring via
        // setter corrupts _baseMaterial and crashes the render loop with
        // "baseMaterial.addEventListener is not a function". Hide them instead.
        if (typeof node.text === 'string' || node._derivedMaterial) {
          originalVisibilities.push([node, node.visible]);
          node.visible = false;
          return;
        }
        originalMaterials.push([node, node.material]);
        node.material = wireframeMat;
      }
    });
    scene.background = new THREE.Color("#0b3d91");
    gl.setClearColor("#0b3d91", 1);
  }
  
  const captureCamera = options.camera || new THREE.PerspectiveCamera(camera.fov || 42, 1800 / 1220, 0.1, 100);
  if (!options.camera) {
    captureCamera.position.copy(camera.position);
    captureCamera.quaternion.copy(camera.quaternion);
  }
  captureCamera.updateProjectionMatrix();
  
  // These four views are printed a few inches wide. Capturing 1800x1220 at up
  // to 2x device ratio built ~3600x2440 buffers, and four JPEGs that large are
  // megabytes of data URI for react-pdf to decode — enough to stall the export
  // outright on a busy scene. Capture at print resolution instead.
  gl.setPixelRatio(1);
  gl.setSize(SNAPSHOT_W, SNAPSHOT_H, false);
  gl.render(scene, captureCamera);

  let dataUrl = null;
  try {
    dataUrl = sanitizeCanvasDataUrl(gl.domElement.toDataURL("image/jpeg", 0.82));
  } catch (err) {
    // A lost WebGL context taints or empties the canvas; the drawings matter
    // more than the render, so carry on without this view.
    console.warn("Scene snapshot unavailable for this view.", err);
    dataUrl = null;
  }
  // A blank/lost context yields a token-sized image. Embedding those wastes
  // time and prints an empty box, so drop them.
  if (typeof dataUrl === "string" && dataUrl.length < 1024) dataUrl = null;
  
  originalMaterials.forEach(([node, mat]) => {
    node.material = mat;
  });
  originalVisibilities.forEach(([node, vis]) => {
    node.visible = vis;
  });
  
  scene.background = background;
  gl.setClearColor(clearColor, clearAlpha);
  
  camera.position.copy(savedCamera.position);
  camera.quaternion.copy(savedCamera.quaternion);
  camera.zoom = savedCamera.zoom;
  camera.updateProjectionMatrix();
  
  gl.setPixelRatio(ratio);
  gl.setSize(size.x, size.y, false);
  gl.render(scene, camera);
  
  return dataUrl;
}

function SnapshotBridge() {
  const { gl, scene, camera } = useThree();
  const setSnapshotHandler = useProjectStore(state => state.setSnapshotHandler);
  
  useEffect(() => {
    setSnapshotHandler(() => {
      const blueprintCam = new THREE.OrthographicCamera(-6.2, 6.2, 4.2, -4.2, 0.1, 100);
      blueprintCam.position.set(2.2, 16, 1.8);
      blueprintCam.lookAt(2.2, 0, 1.8);
      blueprintCam.updateProjectionMatrix();
      
      const frontCam = new THREE.PerspectiveCamera(38, 1800 / 1220, 0.1, 100);
      frontCam.position.set(2.6, 3.1, -7.8);
      frontCam.lookAt(2.2, 1, 1.5);
      
      const sideCam = new THREE.PerspectiveCamera(38, 1800 / 1220, 0.1, 100);
      sideCam.position.set(8.8, 3, 2);
      sideCam.lookAt(2.2, 1, 1.8);
      
      const perspectiveCam = new THREE.PerspectiveCamera(42, 1800 / 1220, 0.1, 100);
      perspectiveCam.position.set(5.8, 5.2, 7.4);
      perspectiveCam.lookAt(1.8, 0.7, 1.8);
      
      return {
        blueprint: takeSnapshot(gl, scene, camera, { blueprint: true, camera: blueprintCam }),
        perspective: takeSnapshot(gl, scene, camera, { camera: perspectiveCam }),
        front: takeSnapshot(gl, scene, camera, { camera: frontCam }),
        side: takeSnapshot(gl, scene, camera, { camera: sideCam })
      };
    });
  }, [camera, gl, scene, setSnapshotHandler]);
  
  return null;
}

/* ── Light set ── */
function Sunlight() {
  return (
    <>
      <ambientLight intensity={0.35} color="#b8c4d4" />
      <hemisphereLight skyColor="#1a2a4a" groundColor="#0a0c10" intensity={0.5} />
      <directionalLight
        castShadow
        position={[50, 50, 25]}
        intensity={2.3}
        color="#fff4df"
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-left={-30}
        shadow-camera-right={30}
        shadow-camera-top={30}
        shadow-camera-bottom={-30}
        shadow-camera-near={1}
        shadow-camera-far={100}
      />
    </>
  );
}

/* ── Environment and Site Context ── */
function SiteContext({ site, accent }) {
  const n = {
    coastal_villa: { road: "#4a5568", water: "#0ea5e9", garden: "#4a7c3f" },
    mountain_retreat: { road: "#3d4a5c", water: "#60a5fa", garden: "#2d5016" },
    garden_courtyard: { road: "#5c534a", water: "#38bdf8", garden: "#166534" },
    urban_luxury: { road: "#2d3748", water: "#60a5fa", garden: "#16a34a" }
  }[site] || { road: "#2d3748", water: "#60a5fa", garden: "#16a34a" };
  
  return (
    <group userData={{ hideInBlueprint: true }}>
      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[1.8, -0.075, 1.7]}>
        <planeGeometry args={[90, 90]} />
        <meshPhysicalMaterial color="#0a0c10" roughness={0.92} clearcoat={0.02} />
      </mesh>
      
      <Grid
        position={[1.8, -0.055, 1.7]}
        args={[50, 50]}
        infiniteGrid
        fadeDistance={50}
        fadeStrength={4}
        cellSize={0.5}
        sectionSize={2.5}
        cellColor="#1e293b"
        sectionColor="#475569"
        followCamera={false}
      />
      
      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[2.2, -0.045, -3.2]}>
        <planeGeometry args={[18, 1.3]} />
        <meshPhysicalMaterial color={n.road} roughness={0.72} clearcoat={0.04} />
      </mesh>
      
      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[3.8, -0.04, -1.7]}>
        <planeGeometry args={[1.4, 2.7]} />
        <meshPhysicalMaterial color="#6b7280" roughness={0.64} clearcoat={0.06} />
      </mesh>
      
      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[-3.2, -0.035, 4.2]}>
        <circleGeometry args={[1.4, 24]} />
        <meshPhysicalMaterial color={n.garden} roughness={0.88} clearcoat={0.02} />
      </mesh>
      
      {site === "coastal_villa" && (
        <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[-5.5, -0.03, 0.5]}>
          <planeGeometry args={[2.4, 12]} />
          <meshPhysicalMaterial color={n.water} roughness={0.18} metalness={0.08} clearcoat={0.6} />
        </mesh>
      )}
    </group>
  );
}

/* ── Plot boundary visualizer ──
   `offset` lets the boundary be rendered either at scene root (default house
   offset) or inside an already-offset floor group (pass [0,0,0]).
   `label` prints a floor caption (e.g. "DUPLEX" / "GROUND FLOOR") above the
   plot-size text — used in compare mode to tell the two plans apart. */
function PlotBoundary({ plot, accent, offset = [0,0,0], label = null }) {
  if (!plot) return null;
  const n = SCALE;
  const u = offset[0];
  const d = offset[2];
  const l = u;
  const c = d;
  const i = plot.width * n;
  const x = plot.length * n;
  return (
    <group position={[l + i / 2, 0.02, c + x / 2]} userData={{ hideInBlueprint: true }}>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[i, x]} />
        <meshBasicMaterial color={accent} opacity={0.1} transparent side={THREE.DoubleSide} />
      </mesh>
      <lineSegments>
        <edgesGeometry args={[new THREE.PlaneGeometry(i, x).rotateX(Math.PI / 2)]} />
        <lineDashedMaterial color={accent} dashSize={0.2} gapSize={0.1} scale={1} />
      </lineSegments>
      {[[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([y, p], b) => (
        <mesh key={b} position={[y * i / 2, 0.05, p * x / 2]}>
          <boxGeometry args={[0.06, 0.1, 0.06]} />
          <meshBasicMaterial color={accent} />
        </mesh>
      ))}
      <Text position={[0, 0.05, x / 2 + 0.8]} rotation={[-Math.PI/2, 0, 0]} fontSize={0.35} color={accent} anchorX="center" anchorY="middle" fontStyle="italic" fontWeight="bold">
        PLOT BOUNDARY: {plot.width}' x {plot.length}'
      </Text>
      {label && (
        <Text position={[0, 0.05, x / 2 + 1.4]} rotation={[-Math.PI/2, 0, 0]} fontSize={0.6} color={accent} anchorX="center" anchorY="middle" fontWeight="bold">
          {label}
        </Text>
      )}
      <Text position={[0, 0.05, -x / 2 - 1]} rotation={[-Math.PI/2, 0, Math.PI]} fontSize={1.0} color={accent} anchorX="center" anchorY="middle" fontWeight="bold">
        N
      </Text>
      {/* Push S further when label present (compare mode) so it doesn't overlap */}
      <Text position={[0, 0.05, x / 2 + (label ? 2.4 : 1.6)]} rotation={[-Math.PI/2, 0, 0]} fontSize={1.0} color={accent} anchorX="center" anchorY="middle" fontWeight="bold">
        S
      </Text>
      <Text position={[i / 2 + 1, 0.05, 0]} rotation={[-Math.PI/2, 0, -Math.PI/2]} fontSize={1.0} color={accent} anchorX="center" anchorY="middle" fontWeight="bold">
        E
      </Text>
      <Text position={[-i / 2 - 1, 0.05, 0]} rotation={[-Math.PI/2, 0, Math.PI/2]} fontSize={1.0} color={accent} anchorX="center" anchorY="middle" fontWeight="bold">
        W
      </Text>
    </group>
  );
}

/* ── First floor slab ── */
function InterflorSlab({ rooms }) {
  return (
    <group name="inter-floor-supported-slab" userData={{ hideInBlueprint: true }}>
      {rooms
        .filter(room => !room.is_outdoor && room.roof_type !== "open")
        .map(room => {
          const b = roomBounds(room);
          return (
            <mesh key={`slab-${room.id}`} receiveShadow position={[b.x + b.width / 2, -0.18, b.z + b.length / 2]}>
              <boxGeometry args={[b.width + 0.04, 0.2, b.length + 0.04]} />
              <meshPhysicalMaterial color="#e0e0e0" roughness={0.15} clearcoat={0.5} />
            </mesh>
          );
        })}
    </group>
  );
}

/* ── Furniture Models ── */
function BedModel({ x, z, roomWidth, roomLength, isSelected, accent, onClick, color }) {
  const i = Math.min(1, (roomWidth * SCALE) / 1.5, (roomLength * SCALE) / 1.5);
  const roomW = roomWidth * SCALE;
  const roomL = roomLength * SCALE;
  const bedW = 0.95 * i;
  const bedL = 1.16 * i;
  const clearance = 0.08;
  const zOffset = clamp(roomL - 0.8 * i, bedL / 2 + clearance, roomL - bedL / 2 - clearance);
  const xOffset = clamp(roomW * 0.45, bedW / 2 + clearance, roomW - bedW / 2 - clearance);
  return (
    <group position={[x + xOffset, 0.23 * i, z + zOffset]} scale={i} onClick={onClick}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[0.95, 0.28, 1.16]} />
        <GlassPhysicalMaterial type="bed" opacity={0.62} customColor={color} />
        <BoxEdges args={[0.95, 0.28, 1.16]} color={isSelected ? accent : "#e2e8f0"} scale={isSelected ? 1.05 : 1} />
      </mesh>
      <mesh castShadow receiveShadow position={[0, 0.2, -0.44]}>
        <boxGeometry args={[0.82, 0.12, 0.22]} />
        <GlassPhysicalMaterial type="chair" opacity={0.5} />
      </mesh>
      {[-0.68, 0.68].map(p => (
        <mesh key={p} castShadow receiveShadow position={[p, -0.04, -0.4]}>
          <boxGeometry args={[0.28, 0.22, 0.32]} />
          <GlassPhysicalMaterial type="table" opacity={0.56} />
        </mesh>
      ))}
    </group>
  );
}

function WardrobeModel({ x, z, roomWidth, roomLength, isSelected, accent, onClick, color }) {
  const f = Math.min(1, (roomWidth * SCALE) / 1.2, (roomLength * SCALE) / 1.2);
  const roomW = roomWidth * SCALE;
  const roomL = roomLength * SCALE;
  const wardrobeW = 0.28 * f;
  const wardrobeL = 0.82 * f;
  const clearance = 0.08;
  const xOffset = clamp(roomW * 0.8, wardrobeW / 2 + clearance, roomW - wardrobeW / 2 - clearance);
  const zOffset = clamp(roomL * 0.2, wardrobeL / 2 + clearance, roomL - wardrobeL / 2 - clearance);
  return (
    <group position={[x + xOffset, 0.7 * f, z + zOffset]} scale={f} onClick={onClick}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[0.28, 1.4, 0.82]} />
        <GlassPhysicalMaterial type="wardrobe" opacity={0.68} customColor={color} />
        <BoxEdges args={[0.28, 1.4, 0.82]} color={isSelected ? accent : "#cbd5e1"} scale={isSelected ? 1.05 : 1} />
      </mesh>
      <mesh position={[0.19, 0, 0]}>
        <boxGeometry args={[0.02, 1.12, 0.02]} />
        <meshStandardMaterial color="#fef3c7" />
      </mesh>
    </group>
  );
}

function BathroomModel({ x, z, scale = 1, isSelected, accent, onClick, color }) {
  return (
    <group position={[x, 0.15 * scale, z]} scale={scale} onClick={onClick}>
      <mesh castShadow receiveShadow position={[-0.2, 0, 0]}>
        <boxGeometry args={[0.3, 0.25, 0.45]} />
        <GlassPhysicalMaterial type="bath" opacity={0.7} customColor={color} />
        <BoxEdges args={[0.3, 0.25, 0.45]} color={isSelected ? accent : "#cbd5e1"} scale={isSelected ? 1.05 : 1} />
      </mesh>
      <mesh castShadow receiveShadow position={[0.48, 0.16, -0.22]}>
        <boxGeometry args={[0.36, 0.22, 0.28]} />
        <meshPhysicalMaterial color="#e0f2fe" roughness={0.18} clearcoat={0.28} />
      </mesh>
      <mesh castShadow receiveShadow position={[-0.48, 0.48, 0.25]}>
        <boxGeometry args={[0.08, 0.9, 0.08]} />
        <meshPhysicalMaterial color="#cbd5e1" roughness={0.24} metalness={0.4} />
      </mesh>
      <mesh castShadow receiveShadow position={[-0.48, 0.9, 0.25]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.16, 0.16, 0.04, 18]} />
        <meshPhysicalMaterial color="#94a3b8" roughness={0.2} metalness={0.45} />
      </mesh>
    </group>
  );
}

function CarModel({ x, z, accent, isSelected, onClick }) {
  return (
    <group position={[x, 0.18, z]} rotation={[0, Math.PI / 2, 0]} onClick={onClick}>
      <mesh castShadow receiveShadow position={[0, 0.18, 0]}>
        <boxGeometry args={[0.78, 0.34, 1.28]} />
        <GlassPhysicalMaterial type="car" opacity={0.58} />
        {isSelected && <BoxEdges args={[0.78, 0.34, 1.28]} color={accent} scale={1.05} />}
      </mesh>
      <mesh castShadow receiveShadow position={[0, 0.42, -0.08]}>
        <boxGeometry args={[0.56, 0.24, 0.62]} />
        <GlassPhysicalMaterial type="chair" opacity={0.48} />
      </mesh>
      {[-0.48, 0.48].flatMap(l =>
        [-0.34, 0.34].map(c => (
          <mesh
            key={`${c}-${l}`}
            castShadow
            receiveShadow
            position={[c, -0.02, l]}
            rotation={[Math.PI / 2, 0, 0]}
          >
            <cylinderGeometry args={[0.12, 0.12, 0.08, 14]} />
            <meshPhysicalMaterial color="#0f172a" roughness={0.55} />
          </mesh>
        ))
      )}
    </group>
  );
}
const clamp = (val, min, max) => Math.max(min, Math.min(max, val));

const FURNITURE_FINISHES = ["#9b7357", "#5f7181", "#738a78", "#a36f58", "#70677d"];

function assetFinish(type = "asset") {
  const semantic = String(type).toLowerCase();
  if (/(?:treadmill|bike|cycle|fitness|gym|weight)/.test(semantic)) return "#3f556b";
  if (/(?:sofa|seating|chair|bench)/.test(semantic)) return "#527a7b";
  if (/(?:bed|wardrobe|dresser)/.test(semantic)) return "#a77862";
  if (/(?:table|desk|counter|console|shelf|rack|unit)/.test(semantic)) return "#796553";
  if (/(?:bath|basin|toilet|shower|sink)/.test(semantic)) return "#8c9daa";
  const hash = Array.from(semantic).reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return FURNITURE_FINISHES[hash % FURNITURE_FINISHES.length];
}

function ManifestFurniture({ item, isSelected, accent, onClick }) {
  const type = String(item?.type || "furniture").toLowerCase();
  const width = Math.max(0.12, Number(item?.width || 1) * SCALE);
  const length = Math.max(0.12, Number(item?.length || 1) * SCALE);
  const height = Math.max(0.12, Math.min(0.75, Number(item?.height || 0.8) * SCALE));
  const color = assetFinish(type);
  const isTreadmill = /treadmill/.test(type);
  const isBike = /bike|cycle/.test(type);
  const isSofa = /sofa|seating|bench/.test(type);
  const isTable = /table|desk|counter|island|rack|console|unit/.test(type);

  return (
    <group position={[Number(item?.x || 0) * SCALE, height / 2, Number(item?.z || 0) * SCALE]} onClick={onClick}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[width, height, length]} />
        <meshStandardMaterial color={color} roughness={0.7} metalness={0.04} />
        {isSelected && <BoxEdges args={[width, height, length]} color={accent} scale={1.06} />}
      </mesh>
      {isSofa && (
        <mesh castShadow receiveShadow position={[0, height * 0.42, -length * 0.34]}>
          <boxGeometry args={[width, height * 0.65, Math.max(0.06, length * 0.22)]} />
          <meshStandardMaterial color="#604536" roughness={0.75} />
        </mesh>
      )}
      {isTable && (
        <mesh castShadow receiveShadow position={[0, height * 0.54, 0]}>
          <boxGeometry args={[width * 0.92, Math.max(0.04, height * 0.14), length * 0.92]} />
          <meshStandardMaterial color="#c6a27e" roughness={0.68} />
        </mesh>
      )}
      {isTreadmill && (
        <mesh castShadow receiveShadow position={[0, height * 0.72, -length * 0.27]} rotation={[-0.34, 0, 0]}>
          <boxGeometry args={[width * 0.68, Math.max(0.05, height * 0.22), Math.max(0.06, length * 0.18)]} />
          <meshStandardMaterial color="#334155" roughness={0.5} metalness={0.25} />
        </mesh>
      )}
      {isBike && (
        <mesh castShadow receiveShadow position={[0, height * 0.72, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[Math.min(width, length) * 0.28, 0.025, 8, 16]} />
          <meshStandardMaterial color="#334155" roughness={0.45} metalness={0.35} />
        </mesh>
      )}
    </group>
  );
}

function InteriorObjects({ rooms, accent }) {
  const selectedRoomId = useProjectStore(state => state.selectedRoomId);
  const selectedObject = useProjectStore(state => state.selectedObject);
  const selectRoom = useProjectStore(state => state.selectRoom);
  const selectionMode = useProjectStore(state => state.selectionMode);

  return (
    <>
      {rooms.map(room => {
        if (!room.width || !room.length || room.width <= 0 || room.length <= 0) return null;

        const xPos = room.x * SCALE;
        const zPos = room.z * SCALE;
        const roomW = room.width * SCALE;
        const roomL = room.length * SCALE;
        const clearance = 0.08;
        const isSelected = selectedRoomId === room.id && selectedObject?.kind === "furniture";
        // Always use the shared furniture finish.  Old room-level palette data
        // is intentionally ignored so saved layouts cannot turn translucent.
        const color = null;

        const handleFurnitureClick = e => {
          if (selectionMode === "furniture") {
            e.stopPropagation();
            selectRoom(room.id, "furniture");
          }
        };

        // The backend/Gemini manifest is already measured, collision-checked,
        // and capped at two primary objects.  Render it directly rather than
        // replacing it with the old procedural room blocks.
        const fallback = [{
          type: "furniture",
          width: Math.min(4, Math.max(1.5, room.width * 0.28)),
          length: Math.min(2, Math.max(1, room.length * 0.16)),
          height: 1.6,
          x: room.width / 2,
          z: room.length / 2,
        }];
        const manifest = Array.isArray(room.furniture) && room.furniture.length
          ? room.furniture.slice(0, 2)
          : fallback;
        const furnitureElement = manifest.map((item, index) => (
          <ManifestFurniture
            key={`${room.id}-${item.type || "asset"}-${index}`}
            item={item}
            isSelected={isSelected}
            accent={accent}
            onClick={handleFurnitureClick}
          />
        ));

        if (!furnitureElement) return null;

        if (isSelected && selectionMode === "furniture") {
          return (
            <DragControls
              key={room.id}
              axisLock="y"
            >
              <group position={[xPos, 0, zPos]}>
                {furnitureElement}
              </group>
            </DragControls>
          );
        }

        return (
          <group key={room.id} position={[xPos, 0, zPos]}>
            {furnitureElement}
          </group>
        );
      })}
    </>
  );
}

/* ── Roof slab rendering ── */
/* ── Roof slab rendering ── */
function RoofSlab({ rooms, visible, accent, baseY = 0, isTopFloor = false, indianOptions = {}, roofColor }) {
  const roofStyle = useProjectStore(state => state.project?.style?.roofStyle) || "terracotta";
  const roofColorHex = useProjectStore(state => state.project?.style?.roofColor);
  const vastuOn = useProjectStore(state => state.project?.style?.vastuColors);

  let fallbackColor = "#1e293b"; // Default slate
  if (roofStyle === "terracotta") fallbackColor = "#9c3b27";
  if (roofStyle === "concrete") fallbackColor = "#64748b";
  if (roofStyle === "slate") fallbackColor = "#0f172a";
  if (roofStyle === "metal") fallbackColor = "#475569";
  if (roofStyle === "shingle") fallbackColor = "#3f3f46";
  
  // Explicit roof palette color wins (unless Vastu mode controls colors).
  if (!vastuOn && typeof roofColorHex === "string" && roofColorHex.startsWith("#")) {
    fallbackColor = roofColorHex;
  }

  // --- THE FIX: Prioritize the injected roofColor, and STRIP SPACES! ---
  const rawColor = roofColor || fallbackColor;
  const finalRoofColor = typeof rawColor === "string" ? rawColor.replace(/\s+/g, "") : rawColor;
  // --------------------------------------------------------------------

  const bounds = useMemo(() => {
    if (!rooms || rooms.length === 0) return null;
    const points = rooms
      .filter(r => r.roof_type !== "open")
      .flatMap(r => {
        const b = roomBounds(r);
        return b ? [[b.x, b.z], [b.x + b.width, b.z + b.length]] : [];
      });
    if (points.length === 0) return null;
    const minX = Math.min(...points.map(p => p[0])) - 0.12;
    const maxX = Math.max(...points.map(p => p[0])) + 0.12;
    const minZ = Math.min(...points.map(p => p[1])) - 0.12;
    const maxZ = Math.max(...points.map(p => p[1])) + 0.12;
    return {
      x: (minX + maxX) / 2,
      z: (minZ + maxZ) / 2,
      width: maxX - minX,
      length: maxZ - minZ
    };
  }, [rooms]);

  if (!visible || !rooms) return null;

  return (
    <group name="roof-slab" userData={{ hideInBlueprint: true }} position={[0, baseY, 0]}>
      {rooms.map(room => {
        if (room.roof_type === "open" || (room.is_double_height && !isTopFloor)) return null;
        const b = roomBounds(room);
        if (!b) return null;
        const h = room.is_double_height ? WALL_HEIGHT * 2 : WALL_HEIGHT;
        return (
          <group key={`roof-${room.id}`} position={[b.x + b.width / 2, h + 0.08, b.z + b.length / 2]}>
            <mesh castShadow receiveShadow>
              <boxGeometry args={[b.width, 0.16, b.length]} />
              <meshPhysicalMaterial color={finalRoofColor} roughness={0.8} clearcoat={0.1} />
            </mesh>
            <mesh castShadow position={[0, 0.11, 0]}>
              <boxGeometry args={[b.width + 0.18, 0.06, b.length + 0.18]} />
              <meshStandardMaterial color={finalRoofColor} roughness={0.9} />
            </mesh>
          </group>
        );
      })}
      {isTopFloor && bounds && indianOptions?.flat_terrace && indianOptions?.parapet && (
        <group position={[bounds.x * SCALE, WALL_HEIGHT + 0.5, bounds.z * SCALE]}>
          <mesh castShadow position={[0, 0, (bounds.length * SCALE) / 2]}>
            <boxGeometry args={[bounds.width * SCALE, 1, 0.2]} />
            <meshStandardMaterial color="#cbd5e1" />
          </mesh>
          <mesh castShadow position={[0, 0, -(bounds.length * SCALE) / 2]}>
            <boxGeometry args={[bounds.width * SCALE, 1, 0.2]} />
            <meshStandardMaterial color="#cbd5e1" />
          </mesh>
          <mesh castShadow position={[(bounds.width * SCALE) / 2, 0, 0]}>
            <boxGeometry args={[0.2, 1, bounds.length * SCALE]} />
            <meshStandardMaterial color="#cbd5e1" />
          </mesh>
          <mesh castShadow position={[-(bounds.width * SCALE) / 2, 0, 0]}>
            <boxGeometry args={[0.2, 1, bounds.length * SCALE]} />
            <meshStandardMaterial color="#cbd5e1" />
          </mesh>
        </group>
      )}
      {isTopFloor && indianOptions?.mumty && rooms.find(r => r.type === "staircase") && (() => {
        const stair = rooms.find(r => r.type === "staircase");
        const b = roomBounds(stair);
        return (
          <group position={[b.x + b.width / 2, WALL_HEIGHT + 1.4, b.z + b.length / 2]}>
            <mesh castShadow>
              <boxGeometry args={[b.width, 2.6, b.length]} />
              <meshStandardMaterial color="#e2e8f0" />
            </mesh>
            <mesh castShadow position={[0, 1.35, 0]}>
              <boxGeometry args={[b.width + 0.2, 0.2, b.length + 0.2]} />
              <meshStandardMaterial color="#94a3b8" />
            </mesh>
          </group>
        );
      })()}
    </group>
  );
}
// Feet → scene-unit pan factor for the D-pad (store nudges in feet).
const PAN_UNIT = SCALE * 2;

function CameraController({ focusCenter, focusDist, controlsRef }) {
  const cameraView = useProjectStore(state => state.cameraView);
  const panNudge = useProjectStore(state => state.panNudge);
  const { camera, gl } = useThree();

  const desiredPos = useRef(null);
  const desiredTarget = useRef(null);
  const initialized = useRef(false);

  // Cancel any in-flight lerp the moment the user touches the canvas so
  // rotation / zoom is never blocked after a house generation.
  useEffect(() => {
    const cancel = () => {
      desiredPos.current = null;
      desiredTarget.current = null;
    };
    const canvas = gl.domElement;
    canvas.addEventListener('pointerdown', cancel);
    return () => canvas.removeEventListener('pointerdown', cancel);
  }, [gl.domElement]);

  // Floor focus: re-anchor the orbit target on the active floor center.
  // Preserve viewing angle + zoom by carrying the camera's current offset
  // from the old target over to the new one (offset = position - target).
  useEffect(() => {
    const ctrls = controlsRef?.current;
    if (!ctrls) return;
    const next = new THREE.Vector3(focusCenter[0], focusCenter[1], focusCenter[2]);
    if (!initialized.current) {
      ctrls.target.copy(next);
      ctrls.update();
      initialized.current = true;
      return;
    }
    const offset = camera.position.clone().sub(ctrls.target);
    desiredTarget.current = next;
    desiredPos.current = next.clone().add(offset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusCenter]);

  // D-pad panning:
  //   Up/Down = move forward/back along ground plane (not elevation).
  //   Left/Right = strafe left/right.
  useEffect(() => {
    if (!panNudge || panNudge.dir === 'reset') return;
    const ctrls = controlsRef?.current;
    if (!ctrls) return;
    const step = (focusDist || 18) * 0.12;

    const fwd = new THREE.Vector3();
    camera.getWorldDirection(fwd);
    fwd.y = 0;
    if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1);
    fwd.normalize();
    const right = new THREE.Vector3().crossVectors(fwd, camera.up).normalize();

    const delta = new THREE.Vector3();
    if (panNudge.dir === 'up') delta.copy(fwd).multiplyScalar(step);
    else if (panNudge.dir === 'down') delta.copy(fwd).multiplyScalar(-step);
    else if (panNudge.dir === 'right') delta.copy(right).multiplyScalar(step);
    else if (panNudge.dir === 'left') delta.copy(right).multiplyScalar(-step);

    desiredTarget.current = ctrls.target.clone().add(delta);
    desiredPos.current = camera.position.clone().add(delta);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panNudge]);

  // Compass / reset views: animate camera to a fixed orientation around the target.
  useEffect(() => {
    if (!cameraView) return;
    const tx = focusCenter[0];
    const ty = focusCenter[1];
    const tz = focusCenter[2];
    const dist = focusDist || 18;
    let pos = null;
    switch (cameraView) {
      case 'south': pos = [tx, dist / 2.5, tz + dist]; break;
      case 'north': pos = [tx, dist / 2.5, tz - dist]; break;
      case 'east':  pos = [tx + dist, dist / 2.5, tz]; break;
      case 'west':  pos = [tx - dist, dist / 2.5, tz]; break;
      case 'top':   pos = [tx + 0.01, dist * 1.6, tz + 0.02]; break;
      case 'reset': pos = [tx + dist * 0.6, dist * 0.7, tz + dist * 0.9]; break;
      default: pos = null;
    }
    if (pos) {
      desiredPos.current = new THREE.Vector3(pos[0], pos[1], pos[2]);
      desiredTarget.current = new THREE.Vector3(tx, ty, tz);
    }
    useProjectStore.getState().setCameraView(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraView]);

  useFrame(() => {
    const ctrls = controlsRef?.current;
    if (!ctrls) return;
    // Hard floor: never let the camera dip into/through the ground plane.
    if (camera.position.y < 0.08) camera.position.y = 0.08;
    if (!desiredPos.current) return;
    camera.position.lerp(desiredPos.current, 0.12);
    ctrls.target.lerp(desiredTarget.current, 0.12);
    ctrls.update();
    if (camera.position.distanceTo(desiredPos.current) < 0.05) {
      desiredPos.current = null;
      desiredTarget.current = null;
    }
  });

  return null;
}

/* ── Walkthrough controller ── */
function WalkthroughController({ sceneOffset = [0, 0, 0] }) {
  const viewMode = useProjectStore(state => state.viewMode);
  const mobileMove = useProjectStore(state => state.mobileMove);
  const { camera, gl } = useThree();
  const keys = useRef({});
  const mouseState = useRef({ yaw: Math.PI, pitch: -0.06, dragging: false, lastX: 0, lastY: 0 });

  useEffect(() => {
    const handleKeyDown = e => { keys.current[e.code] = true; };
    const handleKeyUp = e => { keys.current[e.code] = false; };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, []);

  useEffect(() => {
    if (viewMode === "walk") {
      const rooms = useProjectStore.getState().project.rooms || [];
      const offset = sceneOffset; // Offset spawn relative to centered geometry
      
      console.log("=== WALK MODE DEBUG ===");
      console.log("Total rooms:", rooms.length);
      console.log("All rooms:", rooms.map(r => ({
        id: r.id,
        type: r.type,
        name: r.name,
        x: r.x,
        z: r.z,
        width: r.width,
        length: r.length,
        isFloor1: r.isFloor1,
        doors: r.doors?.length || 0
      })));

      const entranceRoom = rooms.find(r => !r.isFloor1 && (r.type === "foyer" || r.type === "living_room")) ||
                            rooms.find(r => !r.isFloor1) ||
                            rooms[0];
      
      console.log("Entrance room picked:", entranceRoom ? {
        id: entranceRoom.id,
        type: entranceRoom.type,
        name: entranceRoom.name,
        x: entranceRoom.x,
        z: entranceRoom.z,
        width: entranceRoom.width,
        length: entranceRoom.length
      } : "NONE");

      let spawnX = 0;
      let spawnZ = 0;
      let yaw = Math.PI;

      if (entranceRoom) {
        const worldX = entranceRoom.x * SCALE + offset[0];
        const worldZ = entranceRoom.z * SCALE + offset[2];
        const worldW = entranceRoom.width * SCALE;
        const worldL = entranceRoom.length * SCALE;
        spawnX = worldX + worldW / 2;
        spawnZ = worldZ + worldL / 2;
        yaw = Math.PI;
      }

      camera.fov = 75;
      camera.near = 0.01;
      camera.far = 500;
      camera.position.set(spawnX, EYE_LEVEL, spawnZ);
      camera.updateProjectionMatrix();
      
      mouseState.current.yaw = yaw;
      mouseState.current.pitch = -0.06;
      camera.rotation.order = "YXZ";
      camera.rotation.y = mouseState.current.yaw;
      camera.rotation.x = mouseState.current.pitch;
      camera.rotation.z = 0;
    } else if (viewMode === "fly" || viewMode === "orbit") {
      camera.fov = 42;
      camera.near = 0.1;
      camera.far = 2000;
      camera.updateProjectionMatrix();
      camera.position.set(4.6, 4.9, 6.8);
      camera.lookAt(1.8, 0.7, 1.8);
    }
  }, [camera, viewMode]);

  useEffect(() => {
    const canvas = gl.domElement;
    
    const handlePointerDown = p => {
      if (useProjectStore.getState().viewMode === "walk") {
        mouseState.current.dragging = true;
        mouseState.current.lastX = p.clientX;
        mouseState.current.lastY = p.clientY;
      }
    };

    const handlePointerMove = p => {
      if (!mouseState.current.dragging || useProjectStore.getState().viewMode !== "walk") return;
      const dx = p.clientX - mouseState.current.lastX;
      const dy = p.clientY - mouseState.current.lastY;
      
      mouseState.current.lastX = p.clientX;
      mouseState.current.lastY = p.clientY;
      
      mouseState.current.yaw -= dx * 0.004;
      mouseState.current.pitch = THREE.MathUtils.clamp(mouseState.current.pitch - dy * 0.003, -0.9, 0.55);
      
      camera.rotation.order = "YXZ";
      camera.rotation.y = mouseState.current.yaw;
      camera.rotation.x = mouseState.current.pitch;
      camera.rotation.z = 0;
    };

    const handlePointerUp = () => {
      mouseState.current.dragging = false;
    };

    canvas.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);

    return () => {
      canvas.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, [camera, gl.domElement]);

  useFrame((state, delta) => {
    if (viewMode !== "walk") return;
    
    const speed = 4.5;
    const forwardKeys = Number(keys.current.KeyW || keys.current.ArrowUp) - Number(keys.current.KeyS || keys.current.ArrowDown) + (mobileMove?.forward || 0);
    const strafeKeys = Number(keys.current.KeyD || keys.current.ArrowRight) - Number(keys.current.KeyA || keys.current.ArrowLeft) + (mobileMove?.strafe || 0);

    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    dir.y = 0;
    dir.normalize();

    const right = new THREE.Vector3().crossVectors(dir, camera.up).normalize().multiplyScalar(-1);

    camera.position.addScaledVector(dir, forwardKeys * speed * delta);
    camera.position.addScaledVector(right, strafeKeys * speed * delta);

    camera.position.x = THREE.MathUtils.clamp(camera.position.x, -20, 20);
    camera.position.z = THREE.MathUtils.clamp(camera.position.z, -20, 20);
    camera.position.y = EYE_LEVEL;
  });

  return null;
}

/* ── Scene content ── */
function SceneContent() {
  const project = useProjectStore(state => state.project);
  const viewMode = useProjectStore((state) => state.viewMode);
  const selectedRoomId = useProjectStore((state) => state.selectedRoomId);
  const selectRoom = useProjectStore((state) => state.selectRoom);
  const setSnapshotHandler = useProjectStore((state) => state.setSnapshotHandler);
  const roofVisible = useProjectStore((state) => state.roofVisible);
  const controlsRef = useRef();

  const visibleFloor = useProjectStore(state => state.visibleFloor);

  const { houseCenter, maxDist } = useMemo(() => {
    let cx = ((project.plot?.width || 40) / 2) * SCALE;
    let cz = ((project.plot?.length || 40) / 2) * SCALE;
    let dist = 18;

    const floors = project?.floors || [];
    const currentFloor = floors[project?.current_floor_index || 0] || {};
    const floorRooms = currentFloor.rooms || project?.rooms || [];

    // Safe filtering to prevent reference errors & Infinity math crashes
    const validRooms = floorRooms.filter(r =>
      r && Number.isFinite(r.x) && Number.isFinite(r.z) &&
      Number.isFinite(r.width) && Number.isFinite(r.length)
    );

    if (validRooms.length > 0) {
      const minX = Math.min(...validRooms.map(r => r.x));
      const maxX = Math.max(...validRooms.map(r => r.x + r.width));
      const minZ = Math.min(...validRooms.map(r => r.z));
      const maxZ = Math.max(...validRooms.map(r => r.z + r.length));

      cx = (minX + (maxX - minX) / 2) * SCALE;
      cz = (minZ + (maxZ - minZ) / 2) * SCALE;
      dist = Math.max(maxX - minX, maxZ - minZ) * SCALE * 1.5;
      if (dist < 18) dist = 18;
    }

    return { houseCenter: [cx, 0, cz], maxDist: dist };
  }, [project]);

  // This offset places the house perfectly at [0,0,0]
  const sceneOffset = [-houseCenter[0], 0, -houseCenter[2]];

  // Per-floor orbit focus. Centers use the same -12/-10 + sceneOffset transform
  // as roomBounds so the target lands on the true floor center, and sit at
  // floor mid-height so vertical orbit reads as "around" the floor.
  const { focusCenter, focusDist, focusMinDist, focusMaxDist } = useMemo(() => {
    const FIRST_BASE_Y = WALL_HEIGHT + 0.2;          // first-floor slab top
    const TOP_Y = FIRST_BASE_Y + WALL_HEIGHT;        // building top when 2 floors

    const bbox = (rooms) => {
      if (!rooms.length) return null;
      return {
        minX: Math.min(...rooms.map(r => r.x)),
        maxX: Math.max(...rooms.map(r => r.x + r.width)),
        minZ: Math.min(...rooms.map(r => r.z)),
        maxZ: Math.max(...rooms.map(r => r.z + r.length))
      };
    };
    const toFocus = (b, yMid) => {
      const cx = ((b.minX + b.maxX) / 2) * SCALE + sceneOffset[0];
      const cz = ((b.minZ + b.maxZ) / 2) * SCALE + sceneOffset[2];
      const span = Math.max(b.maxX - b.minX, b.maxZ - b.minZ) * SCALE;
      return { center: [cx, yMid, cz], span };
    };

    const gRooms = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).filter(r => !r.isFloor1);
    const fRooms = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).filter(r => r.isFloor1);
    const gb = bbox(gRooms);
    const fb = bbox(fRooms);
    const allb = bbox((project.floors ? project.floors[project.current_floor_index || 0].rooms : []));

    let pick;
    if (visibleFloor === "floor_0" && gb) {
      pick = toFocus(gb, WALL_HEIGHT / 2);
    } else if (visibleFloor === "floor_1" && fb) {
      pick = toFocus(fb, FIRST_BASE_Y + WALL_HEIGHT / 2);
    } else if (visibleFloor === "compare" && gb) {
      // Both plans sit side-by-side at ground level; frame the midpoint and
      // widen the span so the whole comparison is visible and zoomable.
      const gw = (gb.maxX - gb.minX) * SCALE;
      const offset = gw + 4.5; // must match compareGap
      const gf = toFocus(gb, WALL_HEIGHT / 2);
      pick = { center: [gf.center[0] + offset / 2, gf.center[1], gf.center[2]], span: gf.span * 2.1 };
    } else if (allb) {
      pick = toFocus(allb, (fb ? TOP_Y : WALL_HEIGHT) / 2);
    } else {
      pick = { center: [0, WALL_HEIGHT / 2, 0], span: 12 };
    }

    const dist = Math.max(pick.span * 1.5, 8);
    return {
      focusCenter: pick.center,
      focusDist: dist,
      focusMinDist: 0.6,        // close enough to inspect furniture
      focusMaxDist: dist * 5.0  // generous outer bound so zoom never locks
    };
  }, [project, visibleFloor]);
  const showWiring = useProjectStore(state => state.showWiring);
  const showStructural = useProjectStore(state => state.showStructural);

  const accentColor = project.style.accentColor;
  // Drop any room with non-finite geometry BEFORE it reaches Three.js — a
  // single NaN x/z/width/length otherwise floods the console with
  // "computeBoundingSphere: radius is NaN" and can blank the scene. This can
  // happen with partial/failed backend data.
  const _finiteRoom = (r) =>
    Number.isFinite(r?.x) && Number.isFinite(r?.z) &&
    Number.isFinite(r?.width) && r.width > 0 &&
    Number.isFinite(r?.length) && r.length > 0;
  const _safeRooms = (project.floors?.length
    ? project.floors.flatMap(floor => floor?.rooms || [])
    : (project.rooms || [])
  ).filter(_finiteRoom);
  const groundFloorRooms = _safeRooms.filter(room => room.floorIndex === 0 || (room.floorIndex === undefined && !room.isFloor1));
  const firstFloorRooms = _safeRooms.filter(room => room.floorIndex === 1 || room.isFloor1);
  const floorWalls = project.walls?.length
    ? project.walls
    : (project.floors?.[project.current_floor_index || 0]?.walls || []);
  const groundFloorWalls = floorWalls.filter(wall => wall.floorIndex === 0 || (wall.floorIndex === undefined && !wall.isFloor1));
  const firstFloorWalls = floorWalls.filter(wall => wall.floorIndex === 1 || wall.isFloor1);

  // Building perimeter bounds (raw room coords) per floor — used to identify
  // exterior-facing walls so the exterior facade palette colors them.
  const boundsOf = (rs) => rs.length ? {
    minX: Math.min(...rs.map(r => r.x)), maxX: Math.max(...rs.map(r => r.x + r.width)),
    minZ: Math.min(...rs.map(r => r.z)), maxZ: Math.max(...rs.map(r => r.z + r.length)),
  } : null;
  const groundBounds = boundsOf(groundFloorRooms);
  const firstBounds = boundsOf(firstFloorRooms);
  // Exterior facade color is disabled while Vastu directional mode is active.
  // Otherwise fall back to a warm default facade so outer walls are NEVER left
  // white when the user hasn't explicitly picked an exterior colour.
  const DEFAULT_FACADE = "#FDF5E6"; // ivory cream default facade
  const exteriorColor = project.style?.vastuColors
    ? null
    : (project.style?.exteriorColor || DEFAULT_FACADE);

  useEffect(() => {
    if (import.meta.env.DEV) {
      console.info("[SCENE FLOOR AUDIT]", {
        plot: project.plot,
        visibleFloor,
        ground: { count: groundFloorRooms.length, bounds: groundBounds },
        first: { count: firstFloorRooms.length, bounds: firstBounds },
      });
    }
  }, [project.colors, project.style?.wallFinish, project.style?.exteriorColor, project.style?.floorMaterial, project.style?.furnitureColor, project.plot, visibleFloor, _safeRooms.length]);

  // Compare mode: lay both floors side-by-side at ground level (view-only,
  // no geometry regen). First floor is shifted +X beside the ground floor.
  const isCompare = visibleFloor === "compare";
  const compareGap = 4.5; // scene units between the two plans — enough for compass E/W labels
  const compareOffsetX = groundBounds
    ? (groundBounds.maxX - groundBounds.minX) * SCALE + compareGap
    : 8;
  const groundVisible = isCompare || visibleFloor === "all" || visibleFloor === "floor_0";
  const firstVisible = isCompare || visibleFloor === "all" || visibleFloor === "floor_1";

  return (
    <>
      <color attach="background" args={["#05070a"]} />
      <SnapshotBridge />
      <Sunlight />
      <Environment preset={project.style.environment} background={false} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[10, 15, -5]} intensity={1.2} castShadow />

      {/* Dark ground plate */}
      <mesh receiveShadow position={[0, 0.01, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[100, 100]} />
        <meshStandardMaterial color="#0f172a" roughness={0.9} />
      </mesh>

      <SiteContext site={project.style.site} accent={accentColor} />
      <PlotBoundary plot={project.plot} accent={accentColor} offset={sceneOffset} label={isCompare ? "GROUND FLOOR" : null} />



      {/* House Group */}
      <group position={sceneOffset}>
        <Plinth rooms={groundFloorRooms} />

        {[-1, 0, 1, 2].map(floor => {
           const getFloor = (r) => r.floorIndex !== undefined ? r.floorIndex : (r.isFloor1 ? 1 : 0);
           const floorRooms = _safeRooms.filter(r => getFloor(r) === floor);
           if (floorRooms.length === 0) return null;
           
           const isVisible = isCompare || visibleFloor === "all" || visibleFloor === `floor_${floor}`;
           const isTopFloor = Math.max(..._safeRooms.map(getFloor)) === floor;
           const pos = isCompare
              ? [compareOffsetX * Math.max(0, floor), 0, 0]
              : [0, floor * (WALL_HEIGHT + 0.2), 0];
              
           const fw = floorWalls.filter(w => getFloor(w) === floor);
           const bnd = boundsOf(floorRooms);
           const supportingRooms = _safeRooms.filter(room =>
             getFloor(room) === floor - 1 && !room.is_outdoor && room.roof_type !== "open"
           );
           
           return (
             <group key={`floor-${floor}`} position={pos} visible={isVisible}>
               {floor > 0 && <InterflorSlab rooms={supportingRooms.length ? supportingRooms : floorRooms} />}
               {floorRooms.map(room => (
                 <HouseRoom
                   key={room.id}
                   room={room}
                   selected={room.id === selectedRoomId}
                   style={project.style}
                   accent={accentColor}
                   showLabel={viewMode !== "walk"}
                   onSelect={selectRoom}
                   transparent={showStructural}
                   buildingBounds={bnd}
                   exteriorColor={exteriorColor}
                   globalProperties={project.globalProperties}
                   rooms={floorRooms}
                   exteriorWalls={fw}
                 />
               ))}
               <InteriorObjects rooms={floorRooms} accent={accentColor} />
               <RoofSlab
                 rooms={floorRooms}
                 visible={roofVisible && !isCompare}
                 accent={accentColor}
                 baseY={0}
                 isTopFloor={isTopFloor}
                 indianOptions={project.indianOptions}
                 roofColor={project?.style?.roofColor || project?.style?.roofStyle}
               />
               {floor === 0 && <StructuralLayer houseCenter={houseCenter} />}
             </group>
           );
        })}
      </group>

      <CameraController focusCenter={focusCenter} focusDist={focusDist} controlsRef={controlsRef} />
      <WalkthroughController sceneOffset={sceneOffset} />

      {viewMode !== "walk" ? (
        <OrbitControls
          ref={controlsRef}
          makeDefault
          enableDamping={false}
          autoRotate={false}
          enablePan={viewMode === "fly"}
          rotateSpeed={0.65}
          zoomSpeed={0.9}
          zoomToCursor
          minDistance={focusMinDist}
          maxDistance={focusMaxDist}
          minPolarAngle={0}
          maxPolarAngle={Math.PI / 2 - 0.05}
        />
      ) : null}
    </>
  );
}

/* ── Default Export Component ── */
export default function SceneCanvas() {
  return (
    <div id="walkthrough-lock-target" className="fixed inset-0 z-0 overflow-hidden bg-slate-950">
      <Canvas
        shadows
        dpr={[1, 2]}
        camera={{ position: [3, 20, 4], fov: 42, near: 0.01 }}
        gl={{ preserveDrawingBuffer: true, antialias: true, alpha: false }}
      >
        <Suspense fallback={null}>
          <SceneContent />
        </Suspense>
      </Canvas>
    </div>
  );
}

import React from 'react';
import { Box } from '@react-three/drei';
import { useProjectStore } from '../store/useProjectStore';

const SCALE = 0.18;

export default function StructuralLayer({ houseCenter }) {
  const project = useProjectStore((state) => state.project);
  const showStructural = useProjectStore((state) => state.showStructural);

  if (!showStructural || !project.structural_nodes) return null;

  return (
    <group>
      {/* Nodes: Columns and Footings */}
      {project.structural_nodes.map((node, i) => {
        const h = node.height || 3.0;
        const w = node.width || 0.4;
        const l = node.length || 0.4;
        
        // Node's position is its bottom-center natively from python generator
        // Box origin in Three.js is center, so we shift Y by h / 2.
        const py = (node.y || 0) + h / 2;

        const isFooting = node.type === "footing";
        const color = isFooting ? "#64748b" : "#94a3b8"; // Slate for concrete

        return (
          <Box
            key={`struct_node_${i}`}
            args={[w * SCALE, h * SCALE, l * SCALE]}
            position={[node.x * SCALE - (houseCenter ? houseCenter.x : 0), py * SCALE, node.z * SCALE - (houseCenter ? houseCenter.z : 0)]}
            castShadow={!isFooting}
            receiveShadow
          >
            <meshStandardMaterial color={color} roughness={0.9} metalness={0.1} />
          </Box>
        );
      })}

      {/* Paths: Beams */}
      {project.structural_paths && project.structural_paths.map((path, i) => {
        const { from, to, type } = path;
        if (!from || !to) return null;
        
        const dx = to.x - from.x;
        const dz = to.z - from.z;
        const len = Math.sqrt(dx * dx + dz * dz);
        if (len === 0) return null;

        const midX = from.x + dx / 2;
        const midZ = from.z + dz / 2;
        const angle = Math.atan2(dz, dx);
        
        const isPlinth = type === "plinth_beam";
        const bw = 0.3; // Beam width
        const bh = 0.4; // Beam height
        
        const py = isPlinth ? -0.2 : (from.y || 3.0) - bh / 2;

        return (
          <Box
            key={`struct_path_${i}`}
            args={[len * SCALE, bh * SCALE, bw * SCALE]}
            position={[midX * SCALE - (houseCenter ? houseCenter.x : 0), py * SCALE, midZ * SCALE - (houseCenter ? houseCenter.z : 0)]}
            rotation={[0, -angle, 0]}
            castShadow
            receiveShadow
          >
            <meshStandardMaterial color="#94a3b8" roughness={0.9} />
          </Box>
        );
      })}
    </group>
  );
}

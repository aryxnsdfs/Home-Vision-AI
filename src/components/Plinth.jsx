import React, { useMemo } from "react";

const SCALE = 0.18;

const roomBounds = (room) => ({
  x: (room.x - 12) * SCALE,
  z: (room.z - 10) * SCALE,
  width: room.width * SCALE,
  length: room.length * SCALE
});

export default function Plinth({ rooms = [] }) {
  if (!Array.isArray(rooms) || rooms.length === 0) return null;
  const bounds = useMemo(() => {
    const points = rooms.flatMap((room) => {
      const b = roomBounds(room);
      return [
        [b.x, b.z],
        [b.x + b.width, b.z + b.length]
      ];
    });
    const minX = Math.min(...points.map((point) => point[0])) - 1;
    const maxX = Math.max(...points.map((point) => point[0])) + 1;
    const minZ = Math.min(...points.map((point) => point[1])) - 1;
    const maxZ = Math.max(...points.map((point) => point[1])) + 1;
    return {
      x: (minX + maxX) / 2,
      z: (minZ + maxZ) / 2,
      width: maxX - minX,
      length: maxZ - minZ
    };
  }, [rooms]);

  return (
    <mesh receiveShadow position={[bounds.x, -0.16, bounds.z]} userData={{ hideInBlueprint: true }}>
      <boxGeometry args={[bounds.width, 0.2, bounds.length]} />
      <meshPhysicalMaterial color="#e0e0e0" roughness={0.1} clearcoat={1} clearcoatRoughness={0.06} />
    </mesh>
  );
}

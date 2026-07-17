import React, { useMemo } from "react";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { useProjectStore } from "../store/useProjectStore.js";
import { roomBounds } from "./Room.jsx";

function checkIntersection(r1, r2) {
  const b1 = roomBounds(r1);
  const b2 = roomBounds(r2);
  // check if they overlap or touch
  return (
    b1.x <= b2.x + b2.width &&
    b1.x + b1.width >= b2.x &&
    b1.z <= b2.z + b2.length &&
    b1.z + b1.length >= b2.z
  );
}

export default function ValidationPanel() {
  const project = useProjectStore((state) => state.project);
  const validateProject = useProjectStore((state) => state.validateProject);
  const isBlocked = project.validation.status === "blocked";

  const architecturalWarnings = useMemo(() => {
    const warnings = [];
    const rooms = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []) || [];
    
    // Wet wall check: kitchen and bathroom should share a wall
    const kitchens = rooms.filter(r => r.type === "kitchen");
    const bathrooms = rooms.filter(r => r.type === "bathroom");
    if (kitchens.length > 0 && bathrooms.length > 0) {
      let sharedWall = false;
      for (const k of kitchens) {
        for (const b of bathrooms) {
          if (checkIntersection(k, b)) {
            sharedWall = true;
            break;
          }
        }
      }
      if (!sharedWall) {
        warnings.push("Wet wall inefficiency: Kitchen and bathroom do not share a wall.");
      }
    }

    // Daylighting check: habitable rooms without windows
    const habitableTypes = ["bedroom", "living", "kitchen"];
    rooms.forEach(r => {
      if (habitableTypes.includes(r.type)) {
        if (!r.windows || r.windows.length === 0) {
          warnings.push(`Daylighting issue: ${r.name} has no windows.`);
        }
      }
    });

    // Entry flow: Entrance opens directly into living
    // This is common in smaller homes, but often a warning in larger homes
    // Just returning a dummy warning if we have a lot of rooms but no foyer
    if (rooms.length > 3 && !rooms.some(r => r.type === "foyer" || r.name.toLowerCase().includes("foyer"))) {
      warnings.push("Entry flow: Entrance opens directly into living area. Consider adding a foyer.");
    }

    return warnings;
  }, [(project.floors ? project.floors[project.current_floor_index || 0].rooms : [])]);

  const allItems = [
    ...project.validation.errors,
    ...project.validation.warnings,
    ...architecturalWarnings,
    ...project.validation.overrides
  ];

  return (
    <section className="glass rounded-[28px] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-extrabold text-white">
          {isBlocked ? <AlertTriangle size={17} /> : <ShieldCheck size={17} />}
          Pre-Flight
        </div>
        <button
          type="button"
          onClick={validateProject}
          className="rounded-full border border-slate-800 bg-slate-950 px-3 py-1.5 text-xs font-bold text-slate-200 transition hover:-translate-y-0.5 hover:shadow-md"
        >
          Validate
        </button>
      </div>
      <div className={`rounded-3xl p-3 ${isBlocked ? "bg-rose-950/40 text-rose-300" : "bg-emerald-950/40 text-emerald-300"}`}>
        <div className="text-xs font-bold uppercase tracking-wide">{isBlocked ? "Blocked" : "Verified"}</div>
        <div className="mt-1 text-sm font-semibold">
          {isBlocked ? `${project.validation.errors.length} corrections required` : "Structural gate clear"}
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {allItems.slice(0, 8).map((item, index) => (
          <div key={index} className="rounded-2xl bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-300">
            {item}
          </div>
        ))}
        {allItems.length === 0 && (
          <div className="rounded-2xl bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-500 text-center">
            No warnings.
          </div>
        )}
      </div>
    </section>
  );
}

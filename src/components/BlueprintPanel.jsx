import React from "react";
import { Building2, Grid2X2, Home, Ruler } from "lucide-react";
import { motion } from "framer-motion";
import { useProjectStore } from "../store/useProjectStore.js";

const Field = ({ label, value, suffix }) => (
  <div className="block">
    <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
      {label}
    </span>
    <div className="flex items-center rounded-2xl border border-slate-800 bg-slate-950 px-3 py-2 shadow-sm">
      <div className="w-full bg-transparent text-sm font-semibold text-white">
        {value}
      </div>
      {suffix ? <span className="text-xs font-semibold text-slate-400">{suffix}</span> : null}
    </div>
  </div>
);

export default function BlueprintPanel() {
  const project = useProjectStore((state) => state.project);
  const selectedRoomId = useProjectStore((state) => state.selectedRoomId);
  const selectedObject = useProjectStore((state) => state.selectedObject);
  const selectRoom = useProjectStore((state) => state.selectRoom);
  const selectedRoom = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).find((room) => room.id === selectedRoomId);

  return (
    <motion.section
      initial={{ opacity: 0, x: -14 }}
      animate={{ opacity: 1, x: 0 }}
      className="glass flex h-full min-h-0 flex-col rounded-[28px]"
    >
      <div className="border-b border-slate-800/80 p-4">
        <div className="flex items-center gap-2 text-sm font-extrabold text-white">
          <Grid2X2 size={17} />
          Blueprint
        </div>
      </div>

      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Plot Width" value={project.plot.width} suffix="ft" />
          <Field label="Plot Length" value={project.plot.length} suffix="ft" />
        </div>

        <div className="mt-4 rounded-3xl border border-slate-800 bg-slate-950/80 p-3 shadow-sm">
          <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
            <Home size={14} />
            Rooms
          </div>
          <div className="space-y-2">
            {(project.floors ? project.floors[project.current_floor_index || 0].rooms : []).map((room) => (
              <button
                type="button"
                key={room.id}
                onClick={() => selectRoom(room.id)}
                className={`flex w-full items-center justify-between rounded-2xl border px-3 py-2 text-left transition ${
                  room.id === selectedRoomId
                    ? "border-blue-900 bg-blue-950/50 text-blue-200"
                    : "border-slate-800 bg-slate-900/50 text-slate-300 hover:bg-slate-900"
                }`}
              >
                <span className="text-sm font-semibold">{room.name}</span>
                <span className="text-xs font-bold">
                  {room.width} x {room.length}
                </span>
              </button>
            ))}
          </div>
        </div>

        {selectedRoom ? (
          <div className="mt-4 rounded-3xl border border-slate-800 bg-slate-950/80 p-3 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
              <Ruler size={14} />
              Selected Room
            </div>
            <div className="mb-3 rounded-2xl border border-blue-900 bg-blue-950/40 px-3 py-2 text-xs font-bold capitalize text-blue-200">
              Selected: {selectedObject?.kind || "room"} · {selectedRoom.name}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Width" value={selectedRoom.width} suffix="ft" />
              <Field label="Length" value={selectedRoom.length} suffix="ft" />
              <Field label="Wall" value={selectedRoom.wallThicknessIn} suffix="in" />
              <label className="block">
                <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  Type
                </span>
                <div className="rounded-2xl border border-slate-800 bg-slate-900 px-3 py-2 text-sm font-semibold capitalize text-slate-300">
                  {selectedRoom.type}
                </div>
              </label>
              <Field label="Doors" value={selectedRoom.doors?.length || (selectedRoom.doorsCount ?? 1)} />
              <Field label="Windows" value={selectedRoom.windows?.length || (selectedRoom.windowsCount ?? 1)} />
            </div>
          </div>
        ) : null}

        <div className="mt-4 rounded-3xl border border-slate-800 bg-slate-950/80 p-3 shadow-sm">
          <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500">
            <Building2 size={14} />
            Structure
          </div>
          <div className="space-y-2 text-sm">
            {[
              ["Frame", project.building.structure],
              ["Walling", project.building.wallMaterial],
              ["Roof", project.building.roofing],
              ["Foundation", project.building.foundation]
            ].map(([label, value]) => (
              <div key={label} className="flex items-start justify-between gap-3 rounded-2xl bg-slate-900/70 px-3 py-2">
                <span className="text-xs font-semibold text-slate-500">{label}</span>
                <span className="text-right text-xs font-bold text-slate-200">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </motion.section>
  );
}

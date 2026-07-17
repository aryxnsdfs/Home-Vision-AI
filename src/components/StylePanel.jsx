import React, { useState } from "react";
import { Palette, Sparkles, Maximize } from "lucide-react";
import { motion } from "framer-motion";
import { useProjectStore } from "../store/useProjectStore.js";

export default function StylePanel() {
  const [prompt, setPrompt] = useState("Dark Italian marble floors, warm sunlight, neon green accents, matte concrete walls.");
  const project = useProjectStore((state) => state.project);
  const selectedRoomId = useProjectStore((state) => state.selectedRoomId);
  const selectedObject = useProjectStore((state) => state.selectedObject);
  const updateRoomDimensions = useProjectStore((state) => state.updateRoomDimensions);
  const applyStylePrompt = useProjectStore((state) => state.applyStylePrompt);
  const submitPrompt = () => {
    if (prompt.trim()) applyStylePrompt(prompt);
  };

  const selectedRoom = (project.floors ? project.floors[project.current_floor_index || 0].rooms : []).find(r => r.id === selectedRoomId);
  const showResizer = selectedRoom && selectedObject && (selectedObject.kind === 'room' || selectedObject.kind.startsWith('solid'));

  return (
    <motion.section
      initial={{ opacity: 0, x: 14 }}
      animate={{ opacity: 1, x: 0 }}
      className="glass rounded-[28px] p-4 flex flex-col gap-4"
    >
      {showResizer && (
        <div className="rounded-2xl bg-white/5 p-3 border border-slate-200 dark:border-slate-800">
          <div className="mb-2 flex items-center gap-2 text-xs font-bold text-slate-900 dark:text-white uppercase tracking-wider">
            <Maximize size={14} />
            Room Dimensions
          </div>
          <div className="space-y-3 mt-3">
            <div>
              <div className="flex justify-between text-[10px] font-bold text-slate-500 mb-1">
                <span>WIDTH (ft)</span>
                <span className="text-blue-500">{Math.round(selectedRoom.width * 3.28)}</span>
              </div>
              <input 
                type="range" min="1.5" max="10" step="0.1" 
                value={selectedRoom.width}
                onChange={e => updateRoomDimensions(selectedRoom.id, parseFloat(e.target.value), selectedRoom.length)}
                className="w-full accent-blue-500" 
              />
            </div>
            <div>
              <div className="flex justify-between text-[10px] font-bold text-slate-500 mb-1">
                <span>LENGTH (ft)</span>
                <span className="text-emerald-500">{Math.round(selectedRoom.length * 3.28)}</span>
              </div>
              <input 
                type="range" min="1.5" max="10" step="0.1" 
                value={selectedRoom.length}
                onChange={e => updateRoomDimensions(selectedRoom.id, selectedRoom.width, parseFloat(e.target.value))}
                className="w-full accent-emerald-500" 
              />
            </div>
          </div>
        </div>
      )}

      <div>
        <div className="mb-2 flex items-center gap-2 text-sm font-extrabold text-slate-950 dark:text-white">
          <Sparkles size={17} />
          AI Stylist
        </div>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submitPrompt();
            }
          }}
          rows={4}
          className="w-full resize-none rounded-3xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-900 outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10 dark:border-slate-800 dark:bg-slate-950 dark:text-white"
        />
        <button
          type="button"
          onClick={submitPrompt}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 py-3 text-sm font-bold text-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg dark:bg-white dark:text-slate-950"
        >
          <Palette size={16} />
          Apply Style
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        {[
          ["Floor", project.style.floorMaterial],
          ["Walls", project.style.wallFinish],
          ["Light", project.style.lighting],
          ["Scene", project.style.site]
        ].map(([label, value]) => (
          <div key={label} className="rounded-2xl bg-slate-100 px-3 py-2 dark:bg-slate-900">
            <div className="font-bold uppercase tracking-wide text-slate-500">{label}</div>
            <div className="mt-0.5 truncate font-semibold text-slate-800 dark:text-slate-200">{value.replaceAll("_", " ")}</div>
          </div>
        ))}
      </div>
    </motion.section>
  );
}

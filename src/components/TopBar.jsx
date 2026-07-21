import React from "react";
import { ShieldCheck, WandSparkles } from "lucide-react";
import { motion } from "framer-motion";
import { useProjectStore } from "../store/useProjectStore.js";

export default function TopBar() {
  const project = useProjectStore((state) => state.project);

  return (
    <motion.header
      initial={{ opacity: 0, y: -12 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex items-center justify-between gap-4 border-b border-slate-800/80 px-5 py-3"
    >
      <div className="flex min-w-0 items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-2xl bg-white text-slate-950 shadow-sm">
          <WandSparkles size={18} />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <div className="truncate text-sm font-extrabold tracking-tight text-white">
              Home Vision AI
            </div>
            <button
              onClick={() => useProjectStore.getState().startNewTemplate()}
              className="rounded bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300 hover:bg-slate-700 transition-colors"
            >
              New Template
            </button>
          </div>
          <div className="truncate text-xs font-medium text-slate-400">
            {project.location.city} · {project.building.typology} · {project.id}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div className="hidden items-center gap-2 rounded-full border border-emerald-900 bg-emerald-950/50 px-3 py-1.5 text-xs font-semibold text-emerald-300 sm:flex">
          <ShieldCheck size={15} />
          IS Rule Gate Active
        </div>
      </div>
    </motion.header>
  );
}

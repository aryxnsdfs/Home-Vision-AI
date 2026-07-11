import React from "react";
import { Activity, Banknote, CheckCircle2, CloudSun, Leaf, MapPin } from "lucide-react";
import { motion } from "framer-motion";
import { formatInr, useProjectStore } from "../store/useProjectStore.js";

const Metric = ({ icon: Icon, label, value, tone = "slate" }) => {
  const tones = {
    slate: "bg-slate-900 text-slate-200",
    green: "bg-emerald-950 text-emerald-300",
    blue: "bg-blue-950 text-blue-300",
    amber: "bg-amber-950 text-amber-300"
  };

  return (
    <div className="rounded-3xl border border-slate-800 bg-slate-950/80 p-3 shadow-sm">
      <div className={`mb-3 grid h-8 w-8 place-items-center rounded-2xl ${tones[tone]}`}>
        <Icon size={16} />
      </div>
      <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-extrabold tracking-tight text-white">{value}</div>
    </div>
  );
};

export default function MetricsPanel() {
  const project = useProjectStore((state) => state.project);

  return (
    <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <Metric icon={Banknote} label="Cost" value={formatInr(project.metrics.costInr)} tone="blue" />
      <Metric icon={CheckCircle2} label="Safety" value={project.metrics.structuralSafety} tone="green" />
      <Metric icon={Leaf} label="Carbon" value={`${project.metrics.carbonKg.toLocaleString("en-IN")} kg`} tone="slate" />
      <Metric icon={Activity} label="Vastu" value={project.metrics.vastu} tone="amber" />
      <div className="col-span-2 rounded-3xl border border-slate-800 bg-slate-950/80 p-3 shadow-sm xl:col-span-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-extrabold text-white">
            <MapPin size={16} />
            {project.location.city}, {project.location.state}
          </div>
          <div className="flex items-center gap-2 rounded-full bg-slate-900 px-3 py-1 text-xs font-bold text-slate-300">
            <CloudSun size={14} />
            Zone {project.location.seismicZone}
          </div>
        </div>
        <div className="mt-2 text-xs font-medium text-slate-400">
          {project.location.climate} · {project.location.costTier} · {project.location.multiplier}x pricing
        </div>
      </div>
    </motion.section>
  );
}

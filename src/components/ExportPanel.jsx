import React, { useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";
import { useProjectStore } from "../store/useProjectStore.js";

const downloadBlob = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

export default function ExportPanel() {
  const project = useProjectStore((state) => state.project);
  const captureScene = useProjectStore((state) => state.captureScene);
  const validateProject = useProjectStore((state) => state.validateProject);
  const [busy, setBusy] = useState(false);

  const handleExport = async () => {
    // Informational (non-blocking) warning when MEP layers are missing.
    const hasWiring = !!project?.rooms?.some(r => r.mep_nodes?.some(n => n.is_wiring));
    const hasPlumbing = !!project?.rooms?.some(r => r.mep_nodes?.some(n => n.is_plumbing));
    if (!hasWiring || !hasPlumbing) {
      useProjectStore.setState({ uiWarning: "Warning: This blueprint does not include Wiring and/or Water Supply layouts. For a complete engineering blueprint, please enable Add Wiring and Add Water Supply." });
      await new Promise((r) => setTimeout(r, 60)); // let the toast paint first
    }
    setBusy(true);
    try {
      validateProject();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const snapshot = captureScene();
      const latestProject = useProjectStore.getState().project;
      const [{ pdf }, { default: ArchitectReport }] = await Promise.all([
        import("@react-pdf/renderer"),
        import("../pdf/ArchitectReport.jsx")
      ]);
      const blob = await pdf(<ArchitectReport project={latestProject} snapshot={snapshot} />).toBlob();
      downloadBlob(blob, `${latestProject.id}-architect-export.pdf`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="glass rounded-[28px] p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-extrabold text-white">
        <FileText size={17} />
        Export
      </div>
      <button
        type="button"
        onClick={handleExport}
        disabled={busy}
        className="flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 px-4 py-3 text-sm font-bold text-white shadow-sm transition hover:-translate-y-0.5 hover:bg-emerald-500 hover:shadow-lg disabled:cursor-not-allowed disabled:opacity-70"
      >
        {busy ? <Loader2 size={17} className="animate-spin" /> : <Download size={17} />}
        {busy ? "Preparing PDF" : "Download Architect PDF"}
      </button>
      <div className="mt-3 rounded-2xl bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-400">
        Canvas snapshot + schedule generated client-side
      </div>
    </section>
  );
}

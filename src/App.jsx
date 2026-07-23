import React from "react";
import SceneCanvas from "./components/SceneCanvas.jsx";
import FloatingOverlay from "./components/FloatingOverlay.jsx";
import ProjectSetupModal from "./components/ProjectSetupModal.jsx";
import GenerationOverlay from "./components/GenerationOverlay.jsx";
import AnalysisModal from "./components/AnalysisModal.jsx";
import { useProjectStore } from "./store/useProjectStore";

export default function App() {
  const rooms = useProjectStore(s => (s.project.floors ? s.project.floors[s.project.current_floor_index || 0].rooms : []));
  const onboardingDone = useProjectStore(s => s.onboardingDone);
  const showSetupModal = useProjectStore(s => s.showSetupModal);
  const resultStale = useProjectStore(s => s.resultStale);
  const showOnboarding = (!onboardingDone && rooms.length === 0) || showSetupModal;

  // NOTE: cost presets/materials are derived client-side (PACKAGE_PRESETS) and
  // via the /recalculate-cost endpoint on demand — no need to pre-fetch on load
  // (that call hit a hardcoded host and only produced a console error).

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-slate-950 text-white">
      {showOnboarding && <ProjectSetupModal />}
      {!resultStale && <SceneCanvas />}
      {resultStale && <div className="fixed inset-0 z-0 bg-slate-950" aria-label="Waiting for a validated design" />}
      <FloatingOverlay />
      <GenerationOverlay />
      <AnalysisModal />
    </main>
  );
}

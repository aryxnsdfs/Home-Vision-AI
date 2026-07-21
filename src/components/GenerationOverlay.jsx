import React, { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useProjectStore } from "../store/useProjectStore";

const STAGES = [
  { id: 1, label: "Analyzing requirements" },
  { id: 2, label: "Defining plot boundary" },
  { id: 3, label: "Placing rooms" },
  { id: 4, label: "Architectural features" },
  { id: 5, label: "Furniture layout" },
  { id: 6, label: "Electrical plan" },
  { id: 7, label: "Plumbing plan" },
  { id: 8, label: "Materials & structure" },
  { id: 9, label: "Generating blueprints" },
];

function Dot({ active, done }) {
  return (
    <motion.span
      className="inline-block w-1.5 h-1.5 rounded-full"
      animate={{
        backgroundColor: done ? "#10b981" : active ? "#ffffff" : "rgba(255,255,255,0.15)",
        scale: active ? [1, 1.4, 1] : 1,
      }}
      transition={
        active
          ? { scale: { duration: 0.8, repeat: Infinity, ease: "easeInOut" }, backgroundColor: { duration: 0.2 } }
          : { duration: 0.25 }
      }
    />
  );
}

export default function GenerationOverlay() {
  const generationProgress = useProjectStore(s => s.generationProgress);
  const clearGenerationProgress = useProjectStore(s => s.clearGenerationProgress);

  const [mockStage, setMockStage] = useState(0);
  const timerRef = useRef(null);
  const ffRef = useRef(null);

  const visible = !!generationProgress;
  const finalizing = generationProgress?.finalizing ?? false;
  const meta = generationProgress?.meta ?? {};
  const capacity = generationProgress?.capacity;

  const isComplete = mockStage >= 9 && (finalizing || !generationProgress);

  useEffect(() => {
    if (!visible) return;
    setMockStage(0);
    let cur = 0;
    const advance = () => {
      cur += 1;
      setMockStage(cur);
      if (cur < 8) timerRef.current = setTimeout(advance, 1500);
    };
    timerRef.current = setTimeout(advance, 300);
    return () => clearTimeout(timerRef.current);
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!finalizing) return;
    clearTimeout(timerRef.current);
    let cur = mockStage;
    const ff = () => {
      cur += 1;
      setMockStage(cur);
      if (cur < 9) {
        ffRef.current = setTimeout(ff, 80);
      }
    };
    if (cur < 9) ffRef.current = setTimeout(ff, 80);
    else setMockStage(9);
    return () => clearTimeout(ffRef.current);
  }, [finalizing]); // eslint-disable-line react-hooks/exhaustive-deps

  const subtitle = meta.title
    ? meta.title
    : meta.prompt
    ? meta.prompt.slice(0, 72)
    : "Preparing your design";

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="gen-overlay"
          className="fixed inset-0 z-[300] flex flex-col items-center justify-center p-4"
          style={{ background: "#0a0a0a" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
        >
          {/* Ambient animated background */}
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            <motion.div
              className="absolute -top-40 -left-32 h-[30rem] w-[30rem] rounded-full blur-2xl"
              style={{ background: "radial-gradient(circle, rgba(16,185,129,0.30), transparent 70%)" }}
              animate={{ x: [0, 80, 0], y: [0, 50, 0], scale: [1, 1.2, 1] }}
              transition={{ duration: 9, repeat: Infinity, ease: "easeInOut" }}
            />
            <motion.div
              className="absolute -bottom-40 -right-32 h-[34rem] w-[34rem] rounded-full blur-2xl"
              style={{ background: "radial-gradient(circle, rgba(56,189,248,0.28), transparent 70%)" }}
              animate={{ x: [0, -70, 0], y: [0, -40, 0], scale: [1.15, 1, 1.15] }}
              transition={{ duration: 11, repeat: Infinity, ease: "easeInOut" }}
            />
            <motion.div
              className="absolute top-1/3 left-1/2 h-[26rem] w-[26rem] rounded-full blur-2xl"
              style={{ background: "radial-gradient(circle, rgba(168,85,247,0.26), transparent 70%)" }}
              animate={{ x: [-40, 60, -40], y: [0, -50, 0], scale: [1, 1.25, 1] }}
              transition={{ duration: 13, repeat: Infinity, ease: "easeInOut" }}
            />
            <motion.div
              className="absolute top-10 right-1/4 h-72 w-72 rounded-full blur-2xl"
              style={{ background: "radial-gradient(circle, rgba(251,191,36,0.22), transparent 70%)" }}
              animate={{ x: [0, -50, 0], y: [0, 60, 0], scale: [1.1, 0.9, 1.1] }}
              transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
            />
            <motion.div
              className="absolute bottom-16 left-1/4 h-72 w-72 rounded-full blur-2xl"
              style={{ background: "radial-gradient(circle, rgba(236,72,153,0.22), transparent 70%)" }}
              animate={{ x: [0, 50, 0], y: [0, -40, 0], scale: [0.9, 1.15, 0.9] }}
              transition={{ duration: 12, repeat: Infinity, ease: "easeInOut" }}
            />
          </div>

          {/* Main content card */}
          <div className="w-full max-w-sm px-6 relative z-10 my-auto">
            {/* Header */}
            <motion.div
              className="mb-8"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4 }}
            >
              {isComplete ? (
                <div className="flex items-center justify-between mb-1.5">
                  <p className="text-white text-xl font-semibold tracking-tight">
                    Your home is ready
                  </p>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    Complete
                  </span>
                </div>
              ) : (
                <p className="text-white text-xl font-semibold tracking-tight mb-1.5">
                  Generating your home
                </p>
              )}
            </motion.div>

            {capacity && (
              <motion.div
                className="mb-7 rounded-xl border border-white/10 bg-white/5 p-3"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
              >
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="font-medium text-white/70">Area budget</span>
                  <span className={capacity.fits ? "text-emerald-400" : "text-red-400"}>
                    {capacity.required_sqft?.toLocaleString()} / {capacity.available_sqft?.toLocaleString()} sq ft
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/10">
                  <motion.div
                    className={`h-full rounded-full ${capacity.fits ? "bg-emerald-400" : "bg-red-500"}`}
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(100, capacity.usage_percent || 0)}%` }}
                  />
                </div>
                <p className="mt-2 text-[11px] text-white/45">
                  {capacity.fits
                    ? `${Math.max(0, capacity.available_sqft - capacity.required_sqft).toLocaleString()} sq ft remains for flexibility.`
                    : `Needs ${(capacity.required_sqft - capacity.available_sqft).toLocaleString()} sq ft more. Consider another floor, a ${capacity.recommended_plot?.width}×${capacity.recommended_plot?.length} ft plot, or optimization.`}
                </p>
              </motion.div>
            )}

            {/* Stage list */}
            <div className="space-y-3.5">
              {STAGES.map((s, idx) => {
                const done   = isComplete || mockStage > s.id;
                const active = !isComplete && mockStage === s.id;
                const future = !isComplete && mockStage < s.id;

                return (
                  <motion.div
                    key={s.id}
                    className="flex items-center gap-3"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: future ? 0.2 : 1 }}
                    transition={{ duration: 0.3, delay: idx * 0.025 }}
                  >
                    <Dot active={active} done={done} />
                    <motion.span
                      className="text-sm font-medium"
                      animate={{
                        color: done
                          ? "rgba(255,255,255,0.45)"
                          : active
                          ? "rgba(255,255,255,1)"
                          : "rgba(255,255,255,0.2)",
                      }}
                      transition={{ duration: 0.25 }}
                    >
                      {s.label}
                    </motion.span>
                    {done && (
                      <motion.span
                        className="text-emerald-400 text-xs ml-auto font-semibold"
                        initial={{ opacity: 0, scale: 0.8 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 0.2 }}
                      >
                        ✓
                      </motion.span>
                    )}
                  </motion.div>
                );
              })}
            </div>

            {/* Progress bar — colorful gradient + moving shimmer */}
            <motion.div
              className="mt-8 h-1.5 bg-white/8 rounded-full overflow-hidden relative"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.4 }}
            >
              <motion.div
                className="h-full rounded-full relative overflow-hidden"
                style={{ background: "linear-gradient(90deg, #10b981, #38bdf8, #a855f7)" }}
                animate={{ width: `${(Math.min(mockStage, 9) / 9) * 100}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
              >
                <motion.div
                  className="absolute inset-y-0 w-1/3"
                  style={{ background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.6), transparent)" }}
                  animate={{ x: ["-120%", "320%"] }}
                  transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                />
              </motion.div>
            </motion.div>

            {/* "Show us" button rendered after generation is complete */}
            {isComplete && (
              <motion.button
                initial={{ opacity: 0, y: 14, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.35, ease: "easeOut" }}
                whileHover={{ scale: 1.02, boxShadow: "0 12px 28px -6px rgba(16, 185, 129, 0.4)" }}
                whileTap={{ scale: 0.98 }}
                onClick={() => {
                  clearGenerationProgress();
                  setMockStage(0);
                }}
                className="mt-8 w-full rounded-xl bg-gradient-to-r from-emerald-500 via-teal-500 to-emerald-500 bg-[length:200%_auto] py-3.5 px-6 font-semibold text-white shadow-xl shadow-emerald-500/25 transition-all cursor-pointer flex items-center justify-center gap-2.5 tracking-wide text-base border border-emerald-400/30"
              >
                <span>Show us</span>
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
              </motion.button>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

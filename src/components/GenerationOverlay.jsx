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
  const [showReady, setShowReady] = useState(false);
  const timerRef = useRef(null);
  const ffRef = useRef(null);

  const visible = !!generationProgress;
  const finalizing = generationProgress?.finalizing ?? false;
  const meta = generationProgress?.meta ?? {};

  useEffect(() => {
    if (!visible) return;
    setMockStage(0);
    setShowReady(false);
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
      } else {
        ffRef.current = setTimeout(() => setShowReady(true), 250);
        ffRef.current = setTimeout(() => {
          setShowReady(false);
          clearGenerationProgress();
          setMockStage(0);
        }, 1400);
      }
    };
    if (cur < 9) ffRef.current = setTimeout(ff, 80);
    else {
      ffRef.current = setTimeout(() => setShowReady(true), 250);
      ffRef.current = setTimeout(() => {
        setShowReady(false);
        clearGenerationProgress();
        setMockStage(0);
      }, 1400);
    }
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
          className="fixed inset-0 z-[300] flex flex-col items-center justify-center"
          style={{ background: "#0a0a0a" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
        >
          {/* Ambient animated background — colorful drifting glow blobs on pure
              black. No grid. Multiple hues drift + pulse for a lively feel. */}
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

          {/* Completion screen */}
          <AnimatePresence>
            {showReady && (
              <motion.div
                className="absolute inset-0 flex flex-col items-center justify-center z-10"
                style={{ background: "#0a0a0a" }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
              >
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.35 }}
                  className="text-center"
                >
                  <p className="text-white text-2xl font-semibold tracking-tight mb-1">
                    Your home is ready
                  </p>
                  <p className="text-white/30 text-sm">Design generation complete</p>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Main content */}
          {!showReady && (
            <div className="w-full max-w-sm px-6">
              {/* Header */}
              <motion.div
                className="mb-10"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4 }}
              >
                <p className="text-white text-xl font-semibold tracking-tight mb-1.5">
                  Generating your home
                </p>
                <p className="text-white/30 text-sm leading-snug line-clamp-2">
                  {subtitle}
                </p>
              </motion.div>

              {/* Stage list */}
              <div className="space-y-4">
                {STAGES.map((s, idx) => {
                  const done   = mockStage > s.id;
                  const active = mockStage === s.id;
                  const future = mockStage < s.id;

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
                        className="text-sm"
                        animate={{
                          color: done
                            ? "rgba(255,255,255,0.35)"
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
                          className="text-emerald-500 text-xs ml-auto"
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
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
                className="mt-10 h-1.5 bg-white/8 rounded-full overflow-hidden relative"
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
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

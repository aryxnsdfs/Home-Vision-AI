import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useProjectStore } from '../store/useProjectStore';
import { X, Check, ArrowRight, ChevronRight, Home, Building } from 'lucide-react';

export default function AnalysisModal() {
  const analysisResult = useProjectStore(state => state.analysisResult);
  const generateWithAI = useProjectStore(state => state.generateWithAI);
  const isAnalyzing = useProjectStore(state => state.isAnalyzing);
  const clearAnalysis = () => useProjectStore.setState({ analysisResult: null });

  const [answers, setAnswers] = useState({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (analysisResult) {
      // Pre-fill defaults
      const defaults = {};
      (analysisResult.questions || []).forEach(q => {
        defaults[q.key] = q.options[0];
      });
      setAnswers(defaults);
    }
  }, [analysisResult]);

  if (!analysisResult) return null;

  const handleGenerate = async () => {
    setLoading(true);
    // Grab state from project store to re-run generation
    // We don't have the original prompt/w/l here easily unless we stored it in analyzePrompt.
    // Wait, analyzePrompt doesn't store them in the store?
    // We should just store lastPrompt, lastWidth, lastLength in useProjectStore when analyzePrompt is called.
    const finalPrompt = state.lastPrompt || "";
    await generateWithAI(
      finalPrompt,
      state.lastWidth,
      state.lastLength,
      state.lastIndianOptions,
      state.lastColors,
      "Standard",
      "India",
      {},
      state.lastFloors,
      analysisResult.analysis_id,
      answers
    );
    clearAnalysis();
    setLoading(false);
  };

  const { questions, canonical_spec_preview: spec } = analysisResult;

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/85 backdrop-blur-xl p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="relative w-full max-w-3xl rounded-3xl bg-neutral-900/95 border border-white/10 shadow-2xl flex flex-col max-h-[92vh] overflow-hidden"
      >
        <div className="px-8 py-6 border-b border-white/[0.07] flex items-center justify-between shrink-0 bg-neutral-800/50">
          <div>
            <h2 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
              <Check className="text-emerald-400" size={20} /> Design Brief Analyzed
            </h2>
            <p className="text-neutral-400 text-sm mt-1">Please confirm a few details before generation.</p>
          </div>
          <button
            onClick={clearAnalysis}
            className="p-2 rounded-full hover:bg-white/10 text-neutral-400 hover:text-white transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        <div className="p-8 overflow-y-auto flex-1 custom-scrollbar flex flex-col gap-8">
          
          {/* Clarification Questions */}
          {questions && questions.length > 0 && (
            <div className="space-y-6">
              <h3 className="text-lg font-semibold text-white/90">Clarifications Needed</h3>
              <div className="grid gap-6 sm:grid-cols-2">
                {questions.map((q, i) => (
                  <div key={q.key} className="bg-white/5 border border-white/10 p-5 rounded-2xl">
                    <p className="text-sm font-medium text-white mb-3">{q.question}</p>
                    <div className="flex flex-col gap-2">
                      {q.options.map(opt => (
                        <label key={opt} className={`flex items-center gap-3 p-3 rounded-xl cursor-pointer transition-colors border ${answers[q.key] === opt ? 'bg-emerald-500/10 border-emerald-500/30' : 'hover:bg-white/5 border-transparent'}`}>
                          <input 
                            type="radio" 
                            name={q.key} 
                            value={opt} 
                            checked={answers[q.key] === opt} 
                            onChange={() => setAnswers(prev => ({...prev, [q.key]: opt}))}
                            className="hidden" 
                          />
                          <div className={`w-4 h-4 rounded-full border flex items-center justify-center ${answers[q.key] === opt ? 'border-emerald-500' : 'border-white/30'}`}>
                            {answers[q.key] === opt && <div className="w-2 h-2 rounded-full bg-emerald-500" />}
                          </div>
                          <span className="text-sm text-neutral-200">{opt}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* AI Interpretation Summary */}
          <div className="space-y-4">
            <h3 className="text-lg font-semibold text-white/90">AI Interpretation</h3>
            <div className="bg-neutral-950 rounded-2xl border border-white/5 p-6 space-y-4">
              <div className="flex justify-between items-center text-sm border-b border-white/10 pb-4">
                <span className="text-neutral-400">Total Rooms Detected</span>
                <span className="text-white font-mono font-medium">{spec?.target_rooms?.length || 0}</span>
              </div>
              <div className="flex justify-between items-center text-sm border-b border-white/10 pb-4">
                <span className="text-neutral-400">Estimated Configuration</span>
                <span className="text-white font-mono font-medium">{spec?.bhk || "?"} BHK</span>
              </div>
              <div className="text-sm">
                <span className="text-neutral-400 block mb-2">Extracted Rooms:</span>
                <div className="flex flex-wrap gap-2">
                  {spec?.target_rooms?.map((r, i) => (
                    <span key={i} className="px-3 py-1 rounded-full bg-white/10 text-neutral-300 text-xs font-medium">
                      {r.replace(/_/g, ' ')}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-6 border-t border-white/[0.07] bg-neutral-900 flex justify-end">
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="flex items-center gap-2 px-6 py-3 bg-white text-black font-semibold rounded-xl hover:bg-neutral-200 transition-colors disabled:opacity-50"
          >
            {loading ? (
              <div className="w-5 h-5 border-2 border-black/20 border-t-black rounded-full animate-spin" />
            ) : (
              <ArrowRight size={18} />
            )}
            Generate Villa
          </button>
        </div>
      </motion.div>
    </div>
  );
}

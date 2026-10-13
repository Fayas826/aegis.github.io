import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { FaGlobe, FaEye, FaDesktop, FaPause, FaPlay, FaTimes, FaShieldAlt, FaExternalLinkAlt } from "react-icons/fa";

export default function BrowserExecutionWidget() {
  const [progress, setProgress] = useState(null);
  const [previewTimestamp, setPreviewTimestamp] = useState(Date.now());
  const [isControlling, setIsControlling] = useState(false);

  // Poll or stream browser progress from backend API
  useEffect(() => {
    let isMounted = true;

    const fetchProgress = async () => {
      try {
        const host = window.location.hostname || "localhost";
        const port = window.location.port === "3000" || window.location.port === "5173" ? "8000" : window.location.port || "8000";
        const res = await fetch(`http://${host}:${port}/api/browser/live_progress`);
        if (res.ok) {
          const data = await res.json();
          if (isMounted) {
            setProgress(data);
            if (data.screenshot_url) {
              setPreviewTimestamp(Date.now());
            }
          }
        }
      } catch (err) {
        // Backend offline or non-polling interval
      }
    };

    fetchProgress();
    const interval = setInterval(fetchProgress, 2000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const sendControl = async (action) => {
    setIsControlling(true);
    try {
      const host = window.location.hostname || "localhost";
      const port = window.location.port === "3000" || window.location.port === "5173" ? "8000" : window.location.port || "8000";
      await fetch(`http://${host}:${port}/api/browser/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action })
      });
    } catch (e) {
      console.error("Control action failed:", e);
    } finally {
      setIsControlling(false);
    }
  };

  if (!progress || progress.stage === "IDLE") {
    return null;
  }

  const host = window.location.hostname || "localhost";
  const port = window.location.port === "3000" || window.location.port === "5173" ? "8000" : window.location.port || "8000";
  const previewUrl = `http://${host}:${port}/api/browser/preview?t=${previewTimestamp}`;
  const winState = progress.window_state || {};

  return (
    <motion.div
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 15 }}
      className="glass-panel p-3 rounded-md border border-cyan-500/30 bg-black/85 backdrop-blur-md shadow-[0_0_30px_rgba(0,255,255,0.15)] flex flex-col gap-2 font-mono text-[9px] relative overflow-hidden"
    >
      <div className="flex items-center justify-between border-b border-white/10 pb-1.5">
        <div className="flex items-center gap-2">
          <FaGlobe className="text-cyan-400 animate-pulse" />
          <span className="text-cyan-400 font-bold tracking-widest uppercase">
            LIVE_BROWSER // {progress.mode?.toUpperCase() || "VISIBLE"}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`px-1.5 py-0.5 rounded text-[8px] font-bold ${
            progress.is_pending_confirmation
              ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
              : "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30"
          }`}>
            {progress.stage}
          </span>
        </div>
      </div>

      {/* Target Site & Title */}
      <div className="flex flex-col gap-0.5 bg-white/5 p-1.5 rounded">
        <div className="flex items-center justify-between text-white/50 text-[8px]">
          <span>WEBSITE / URL:</span>
          <span className="text-cyan-300/80 truncate max-w-[180px]">{progress.website || "about:blank"}</span>
        </div>
        <div className="text-white/80 font-semibold truncate text-[9px]">
          {progress.page_title || "Connecting to portal..."}
        </div>
      </div>

      {/* Window Visibility Indicators */}
      <div className="grid grid-cols-4 gap-1 text-[7px] text-center uppercase tracking-wider">
        <div className={`p-1 rounded ${winState.WINDOW_CREATED ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "bg-white/5 text-white/40"}`}>
          CREATED
        </div>
        <div className={`p-1 rounded ${winState.WINDOW_VISIBLE ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "bg-white/5 text-white/40"}`}>
          VISIBLE
        </div>
        <div className={`p-1 rounded ${winState.FOREGROUND_CONFIRMED ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30" : "bg-white/5 text-white/40"}`}>
          FOREGROUND
        </div>
        <div className={`p-1 rounded ${winState.PAGE_STATE_VERIFIED ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "bg-white/5 text-white/40"}`}>
          VERIFIED
        </div>
      </div>

      {/* Latest Action & Verified Outcome */}
      <div className="flex flex-col gap-0.5 border-l-2 border-cyan-400/60 pl-2 py-0.5">
        <div className="text-white/60 text-[8px] uppercase">ACTION: {progress.action}</div>
        <div className="text-cyan-300 font-bold text-[8px] uppercase">OUTCOME: {progress.verified_outcome}</div>
      </div>

      {/* Live Preview / Screenshot */}
      <div className="relative rounded overflow-hidden border border-white/10 bg-black/60 h-28 flex items-center justify-center">
        <img
          src={previewUrl}
          alt="Desktop / Page Live Preview"
          className="w-full h-full object-cover"
          onError={(e) => {
            e.target.style.display = 'none';
          }}
        />
        <div className="absolute top-1 left-1.5 px-1 bg-black/70 rounded text-[7px] text-cyan-400 font-bold tracking-wider">
          LIVE_DESKTOP_PREVIEW
        </div>
      </div>

      {/* Pending Confirmation Safety Banner */}
      {progress.is_pending_confirmation && (
        <div className="p-2 rounded bg-amber-500/10 border border-amber-500/30 flex items-center gap-2 text-amber-300 text-[8px]">
          <FaShieldAlt className="text-amber-400 text-sm flex-shrink-0 animate-bounce" />
          <span>CONFIRMATION GATE: Awaiting operator inspection on Windows desktop screen. No payment committed.</span>
        </div>
      )}

      {/* Operator Interactive Controls */}
      <div className="flex items-center gap-1.5 pt-1 border-t border-white/10">
        <button
          onClick={() => sendControl("focus")}
          disabled={isControlling}
          className="flex-1 py-1 px-1.5 rounded bg-cyan-600/30 hover:bg-cyan-600/50 border border-cyan-500/40 text-cyan-300 text-[8px] font-bold flex items-center justify-center gap-1 transition-all"
        >
          <FaEye /> BRING TO FRONT
        </button>
        <button
          onClick={() => sendControl("pause")}
          disabled={isControlling}
          className="py-1 px-2 rounded bg-white/10 hover:bg-white/20 text-white/70 text-[8px] flex items-center justify-center transition-all"
          title="Pause Execution"
        >
          <FaPause />
        </button>
        <button
          onClick={() => sendControl("resume")}
          disabled={isControlling}
          className="py-1 px-2 rounded bg-white/10 hover:bg-white/20 text-white/70 text-[8px] flex items-center justify-center transition-all"
          title="Resume Execution"
        >
          <FaPlay />
        </button>
        <button
          onClick={() => sendControl("cancel")}
          disabled={isControlling}
          className="py-1 px-2 rounded bg-red-500/20 hover:bg-red-500/40 border border-red-500/30 text-red-300 text-[8px] flex items-center justify-center transition-all"
          title="Cancel Execution"
        >
          <FaTimes />
        </button>
      </div>
    </motion.div>
  );
}

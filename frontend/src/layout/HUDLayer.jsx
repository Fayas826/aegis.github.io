import React, { Suspense } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useAppState } from "@/core/contexts";

import VitalsMonitor from "@/features/hud/VitalsMonitor";
import IntelligenceBrief from "@/features/hud/IntelligenceBrief";
import TopMetrics from "@/features/hud/TopMetrics";
import WeatherWidget from "@/features/hud/WeatherWidget";
import TacticalThreatScanner from "@/features/scanners/TacticalThreatScanner";
import CommandExecutionPanel from "@/features/hud/CommandExecutionPanel";
import BrowserExecutionWidget from "@/features/hud/BrowserExecutionWidget";
import NetworkHeatmap from "@/features/simulations/NetworkHeatmap";
import Armory from "@/features/simulations/Armory";
import SentienceEvolution from "@/features/simulations/SentienceEvolution";

export default function HUDLayer() {
  const {
    status,
    threatLevel,
    isInitialized,
    healthState,
    worldState,
    agentState,
    actionResults,
    forgeLogs,
    weatherData,
    processLocalIntent,
    showArmory,
    systemPulse,
    themeColor,
    thermalData,
    isSingularityExpanded,
    setIsSingularityExpanded
  } = useAppState();

  return (
    <>
      {/* Left Interface Nodes */}
      <div className="absolute left-8 top-8 w-72 flex flex-col gap-5 pointer-events-none">
        <div className="pointer-events-auto">
          <VitalsMonitor status={status} threatLevel={threatLevel} />
        </div>
        <div className="pointer-events-auto">
          <IntelligenceBrief active={isInitialized} />
        </div>
      </div>

      {/* Armory / Sentience Node */}
      <AnimatePresence>
        {showArmory && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0, width: isSingularityExpanded ? "500px" : "18rem" }}
            exit={{ opacity: 0, y: 20 }}
            className="absolute left-8 bottom-8 pointer-events-auto z-110"
          >
            <div className="flex flex-col-reverse gap-2">
              <Suspense fallback={<div className="h-48 animate-pulse bg-white/5" />}>
                <Armory
                  status={status}
                  systemPulse={systemPulse}
                  themeColor={themeColor}
                  vitals={thermalData}
                  isExpanded={isSingularityExpanded}
                  onToggleSentience={() => setIsSingularityExpanded(prev => !prev)}
                  onDiagnostic={() => processLocalIntent("systems diagnostic")}
                />
              </Suspense>
              {isSingularityExpanded && (
                <motion.div initial={{ opacity: 0, scaleY: 0 }} animate={{ opacity: 1, scaleY: 1 }} className="rounded-sm border border-cyan-500/30 bg-black/80 p-4">
                  <SentienceEvolution />
                </motion.div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Right Interface Nodes */}
      <div className="absolute right-4 top-4 w-72 flex flex-col gap-3 items-end pointer-events-none">
        <div className="pointer-events-auto w-full">
          <TopMetrics status={status} healthState={healthState} worldState={worldState} agentState={agentState} onDiagnostic={() => processLocalIntent("systems diagnostic")} />
        </div>
        <div className="pointer-events-auto w-full stark-widget p-2 bg-black/40 border border-cyan-500/10">
          <WeatherWidget data={weatherData} />
        </div>
        <div className="pointer-events-auto w-full">
          <TacticalThreatScanner active={isInitialized} />
        </div>
        <div className="pointer-events-auto w-full">
          <BrowserExecutionWidget />
        </div>
        <div className="pointer-events-auto w-full">
          <CommandExecutionPanel actions={[...(actionResults || []), ...forgeLogs]} />
        </div>
        
        <div className="pointer-events-auto w-full mt-4 stark-widget p-4 bg-black/40 border border-cyan-500/10">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[8px] text-cyan-400 font-black tracking-[0.3em] uppercase">NEURAL_LATENCY</span>
            <div className="h-px bg-linear-to-r from-transparent via-cyan-500/20 to-transparent flex-1" />
          </div>
          <Suspense fallback={<div className="h-20 animate-pulse" />}>
            <NetworkHeatmap />
          </Suspense>
        </div>
      </div>
    </>
  );
}

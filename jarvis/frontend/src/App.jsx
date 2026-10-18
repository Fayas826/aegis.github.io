import { useState, useEffect } from "react";
import axios from "axios";
import { FaMicrophone, FaPaperPlane } from "react-icons/fa";
import { motion } from "framer-motion";


import ArcReactor from "./ArcReactor";
import GlowCard from "./GlowCard";
import HudOverlay from "./HudOverlay";
import Globe from "./Globe";
import StarField from "./Stars";

import reactorBg from "./assets/reactor.jpg";
import hudBg from "./assets/hud.jpg";
import gridBg from "./assets/grid.jpg";
import spaceBg from "./assets/space.jpg";

export default function App() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState("default");
  const [flash, setFlash] = useState(false);

  const playSound = (type) => {
    try {
      let sound;
      if (type === "reactor") sound = new Audio("/sounds/activate.wav");
      else if (type === "hud") sound = new Audio("/sounds/scan.wav");
      else sound = new Audio("/sounds/click.wav");
      
      // We set volume low to be safe, and play.
      sound.volume = 0.5;
      sound.play().catch(() => {
        // Browsers block autoplaying audio until the user interacts with the document.
        console.log("Audio play prevented or file not found.");
      });
    } catch (e) {
      console.log("Sound error", e);
    }
  };

  useEffect(() => {
    if (mode === "default") return;
    
    // Trigger visual flash
    setFlash(true);
    setTimeout(() => setFlash(false), 300);
    
    // Trigger audio
    playSound(mode);
  }, [mode]);

  const getBackground = () => {
    switch (mode) {
      case "reactor":
        return reactorBg;
      case "hud":
        return hudBg;
      case "grid":
        return gridBg;
      default:
        return spaceBg;
    }
  };

  const getBackgroundClass = () => {
    switch (mode) {
      case "reactor": return "animate-pulse";
      case "hud": return "animate-none"; // Avoiding spin so text remains readable
      case "grid": return "animate-none";
      case "loading": return "animate-pulse backdrop-blur-3xl"; // Add loading scan mode
      default: return "";
    }
  };

  const sendMessage = async () => {
    if (!input) return;

    const userMsg = { type: "user", text: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setMode("loading");

    try {
      const res = await axios.post("http://127.0.0.1:8000/jarvis", {
        message: input,
      });
      const botMsg = { type: "bot", text: res.data.response };
      setMessages((prev) => [...prev, botMsg]);
      setMode(res.data.mode || "default");
    } catch {
      setMessages((prev) => [
        ...prev,
        { type: "bot", text: "Connection error to core AI." },
      ]);
      setMode("grid"); // Fail gracefully to grid mode
    }

    setLoading(false);
  };

  const startVoice = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert("Speech recognition not supported in this browser.");
      return;
    }
    try {
      const recognition = new SpeechRecognition();
      recognition.onresult = (event) => {
        setInput(event.results[0][0].transcript);
      };
      recognition.start();
    } catch (err) {
      alert("Please allow Microphone permissions or use Chrome.");
    }
  };

  return (
    <div className="h-screen w-full flex text-white font-sans relative overflow-hidden bg-[#050510]">
      
      {/* Dynamic Background Image Layer */}
      <div 
        className={`absolute inset-0 z-0 ${getBackgroundClass()}`}
        style={{
          backgroundImage: `url(${getBackground()})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
          transition: "all 0.5s ease",
          opacity: mode === "default" ? 1 : 0.6 // Dim the custom images slightly for readability
        }}
      />
      
      {/* Screen flash effect */}
      {flash && (
        <div className="absolute inset-0 bg-blue-400/20 animate-pulse pointer-events-none z-50"></div>
      )}

      {/* 3D and Particle Overlays */}
      <div className="absolute inset-0 z-0 pointer-events-none">
        <StarField />
        <Globe />
        <ArcReactor />
        <HudOverlay />
      </div>

      {/* Sidebar */}
      <div className="w-64 bg-black/40 backdrop-blur-md border-r border-blue-500/20 p-6 flex flex-col z-10 pointer-events-auto">
        <h1 className="text-3xl font-bold mb-8 tracking-widest text-transparent bg-clip-text bg-linear-to-r from-blue-400 to-purple-500 drop-shadow-[0_0_10px_rgba(0,191,255,0.8)]">JARVIS ⚡</h1>

        <div className="space-y-4 flex-1">
          <button onClick={() => alert("Dashboard Module Active")} className="w-full text-left px-4 py-3 rounded-xl bg-blue-500/10 hover:bg-blue-500/20 border border-blue-500/20 transition-all glow">
            Dashboard
          </button>
          <button onClick={() => alert("Loading Market Data Engine...")} className="w-full text-left px-4 py-3 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all glow">
            Market Data
          </button>
          <button onClick={() => alert("Fetching historical logs...")} className="w-full text-left px-4 py-3 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all glow">
            History
          </button>
          <button onClick={() => alert("Settings Panel Locked")} className="w-full text-left px-4 py-3 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all glow">
            Settings
          </button>
        </div>
        
        <div className="text-xs text-blue-400/50 uppercase tracking-widest text-center mt-auto">
          Stark Industries v4.0
        </div>
      </div>

      {/* Chat Section */}
      <div className="flex-1 flex flex-col z-10 relative bg-black/10 backdrop-blur-sm">
        {/* Header */}
        <div className="px-8 py-5 border-b border-blue-500/20 bg-black/30 backdrop-blur-md flex justify-between items-center">
          <h2 className="text-xl font-light tracking-wider text-blue-100">AI ASSISTANT INTERFACE</h2>
          <div className="flex items-center gap-2">
             <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>
             <span className="text-sm text-green-400/80 uppercase">Online</span>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-8 space-y-6">
          {messages.map((msg, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, scale: 0.9, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ duration: 0.3 }}
              className={`max-w-2xl p-5 rounded-2xl border ${
                msg.type === "user"
                  ? "ml-auto bg-blue-600/20 border-blue-500/50 text-right backdrop-blur-md shadow-[0_0_15px_rgba(37,99,235,0.2)]"
                  : "bg-black/40 border-white/10 backdrop-blur-xl shadow-[0_0_15px_rgba(255,255,255,0.05)]"
              }`}
            >
              <p className="leading-relaxed tracking-wide text-gray-100">
                {msg.text}
              </p>
            </motion.div>
          ))}
          {loading && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-blue-400 animate-pulse font-mono flex items-center gap-2 mt-4"
            >
               <div className="w-4 h-4 rounded-full border-2 border-t-blue-400 border-r-transparent animate-spin"></div>
               Processing request...
            </motion.div>
          )}
        </div>

        {/* Input */}
        <div className="p-6 border-t border-blue-500/20 bg-black/40 backdrop-blur-lg">
          <div className="flex gap-4 items-center bg-white/5 border border-white/10 p-2 rounded-2xl focus-within:border-blue-500/50 focus-within:shadow-[0_0_20px_rgba(0,191,255,0.15)] transition-all">
            <button
              onClick={startVoice}
              className="w-12 h-12 rounded-xl bg-blue-500/20 border border-blue-400/50 
              shadow-[0_0_15px_rgba(0,191,255,0.3)] flex items-center justify-center hover:scale-105 hover:bg-blue-500/40 transition group cursor-pointer"
            >
              <FaMicrophone className="text-blue-300 group-hover:text-white" size={18} />
            </button>
            <input
              className="flex-1 bg-transparent border-none outline-none text-white px-2 placeholder:text-gray-500 font-light tracking-wide"
              placeholder="Ask Jarvis anything..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
            />
            <button
              onClick={sendMessage}
              className="px-6 py-3 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold tracking-wide transition-all glow disabled:opacity-50 flex items-center gap-2"
              disabled={loading || !input}
            >
              <FaPaperPlane size={14} /> Send
            </button>
          </div>
        </div>
      </div>

      {/* Right Panel */}
      <div className="w-80 bg-black/40 backdrop-blur-md border-l border-blue-500/20 p-6 flex flex-col gap-6 z-10 overflow-y-auto">
        <h2 className="text-lg font-light tracking-widest text-blue-200 border-b border-white/10 pb-2">SYSTEM STATUS</h2>
        
        <div className="space-y-4">
          <GlowCard title="🧠 AI Engine" desc="Multi-Agent Operations: Nominal. Planning logic active." />
          <GlowCard title="⚡ Network Core" desc="FastAPI WebSocket bridge: Engaged. Latency < 40ms." />
          <GlowCard title="🔐 Security" desc="End-to-End Encryption: Online. Protocol Stark-4." />
          <GlowCard title="📊 Market Analyst" desc="Awaiting command..." />
        </div>
      </div>
    </div>
  );
}

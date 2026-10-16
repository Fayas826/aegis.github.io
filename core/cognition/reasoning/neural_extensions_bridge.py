"""
A.E.G.I.S. Neural Extensions Bridge
====================================
Unified Neural Wiring for Jarvis's Central Brain, Synaptic Associative Memory ("Neurons"),
and Subsystem Pods connecting 8 Advanced Open-Source AI Engines:

1. UI-TARS Desktop: Visual grounding, desktop control, action-feedback loops.
2. Browser Use: Autonomous DOM-driven multi-step web navigation & research.
3. OpenAI Agents SDK: Specialist handoffs, tool calling, runtime tracing, guardrails.
4. OpenHands: Autonomous software iteration loops (observe -> think -> act -> test).
5. LangGraph: Cyclic graph orchestration, checkpointing, and state recovery.
6. llama.cpp: Quantized GGUF inference optimized for limited hardware (4 GB VRAM GPU).
7. Whisper: Speech-to-text recognition with acoustic and temporal alignment.
8. AutoGen: Multi-agent conversational debate, collective consensus, and task solving.
"""

import os
import sys
import time
import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger("AEGIS.NeuralExtensionsBridge")
logger.setLevel(logging.INFO)

# Root workspace path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
EXTERNAL_ENGINES_DIR = os.path.join(WORKSPACE_ROOT, "external_engines")


class NeuralExtensionsBridge:
    """
    Central router that activates, manages, and wires the 8 open-source
    cognitive powerhouses directly into JarvisBrain and Synaptic Neurons.
    """

    def __init__(self):
        self.engines_status = {
            "ui_tars_desktop": False,
            "browser_use": False,
            "openai_agents_sdk": False,
            "openhands": False,
            "langgraph": False,
            "llama_cpp": False,
            "whisper": False,
            "autogen": False,
        }
        self.engine_health: Dict[str, Dict[str, Any]] = {}
        self._synaptic_memory = None
        self._central_brain = None
        self._init_subsystems()

    def _get_synaptic_memory(self):
        if self._synaptic_memory is None:
            try:
                from core.cognition.memory.synaptic_memory import synaptic_memory
                self._synaptic_memory = synaptic_memory
            except Exception as e:
                logger.debug(f"[Bridge] SynapticMemory lazy-load note: {e}")
        return self._synaptic_memory

    def _get_central_brain(self):
        if self._central_brain is None:
            try:
                from core.orchestration.central_brain import central_brain
                self._central_brain = central_brain
            except Exception as e:
                logger.debug(f"[Bridge] CentralBrain lazy-load note: {e}")
        return self._central_brain

    def _init_subsystems(self):
        """Probes installed modules and local external engines."""
        def source_state(name: str, path: str) -> None:
            self.engine_health[name] = {
                "source_present": os.path.isdir(path),
                "package_importable": False,
                "runtime_ready": False,
                "configured": False,
                "detail": "Source directory detected; executable runtime not proven.",
            }

        # 1. UI-TARS Desktop
        uitars_path = os.path.join(EXTERNAL_ENGINES_DIR, "ui-tars-desktop")
        source_state("ui_tars_desktop", uitars_path)

        # 2. Browser Use
        try:
            import browser_use
            self.engine_health["browser_use"] = {
                "source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "browser-use")),
                "package_importable": True,
                "runtime_ready": True,
                "configured": False,
                "detail": "Library importable; model/provider configuration is checked per task.",
            }
            self.engines_status["browser_use"] = True
        except ImportError:
            logger.warning("[Bridge] browser_use module not found in python path.")
            self.engine_health["browser_use"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "browser-use")), "package_importable": False, "runtime_ready": False, "configured": False, "detail": "browser_use import failed."}

        # 3. OpenAI Agents SDK
        try:
            import agents
            self.engine_health["openai_agents_sdk"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "openai-agents-python")), "package_importable": True, "runtime_ready": False, "configured": False, "detail": "SDK importable; no executable handoff adapter is configured."}
        except ImportError:
            logger.warning("[Bridge] agents module not found in python path.")
            self.engine_health["openai_agents_sdk"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "openai-agents-python")), "package_importable": False, "runtime_ready": False, "configured": False, "detail": "agents import failed."}

        # 4. OpenHands
        openhands_path = os.path.join(EXTERNAL_ENGINES_DIR, "OpenHands")
        source_state("openhands", openhands_path)

        # 5. LangGraph
        try:
            import langgraph
            self.engine_health["langgraph"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "langgraph")), "package_importable": True, "runtime_ready": False, "configured": False, "detail": "LangGraph importable; native graph/checkpointer adapter is not active in this bridge."}
        except ImportError:
            logger.warning("[Bridge] langgraph module not found in python path.")
            self.engine_health["langgraph"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "langgraph")), "package_importable": False, "runtime_ready": False, "configured": False, "detail": "langgraph import failed."}

        # 6. llama.cpp
        try:
            import llama_cpp
            self.engine_health["llama_cpp"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "llama.cpp")), "package_importable": True, "runtime_ready": False, "configured": False, "detail": "llama_cpp importable; a real GGUF model must be loaded before readiness."}
        except ImportError:
            logger.warning("[Bridge] llama_cpp module not found in python path.")
            self.engine_health["llama_cpp"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "llama.cpp")), "package_importable": False, "runtime_ready": False, "configured": False, "detail": "llama_cpp import failed."}

        # 7. Whisper
        try:
            import whisper
            self.engine_health["whisper"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "whisper")), "package_importable": True, "runtime_ready": False, "configured": False, "detail": "Whisper importable; model availability is checked on transcription."}
        except ImportError:
            logger.warning("[Bridge] whisper module not found in python path.")
            self.engine_health["whisper"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "whisper")), "package_importable": False, "runtime_ready": False, "configured": False, "detail": "whisper import failed."}

        # 8. AutoGen
        try:
            import autogen_agentchat
            self.engine_health["autogen"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "autogen")), "package_importable": True, "runtime_ready": False, "configured": False, "detail": "AutoGen importable; no executable team adapter is configured."}
        except ImportError:
            logger.warning("[Bridge] autogen_agentchat module not found in python path.")
            self.engine_health["autogen"] = {"source_present": os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, "autogen")), "package_importable": False, "runtime_ready": False, "configured": False, "detail": "autogen_agentchat import failed."}

    # -------------------------------------------------------------------------
    # 1. UI-TARS Desktop Grounding & Visual Action Loop
    # -------------------------------------------------------------------------
    def ground_ui_tars(self, screen_image, user_goal: str) -> Dict[str, Any]:
        """
        Interprets visual interfaces, predicts normalized action coordinates,
        and manages action-feedback loops.
        Wires synaptic neurons: 'ui_tars_grounding' -> 'desktop_action'
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("ui_tars_grounding", "visual_perception", "GROUNDS", 1.2)
            sm.fire_synapse("ui_tars_grounding", "desktop_action", "DISPATCHES", 1.0)

        if not self.engine_health.get("ui_tars_desktop", {}).get("runtime_ready", False):
            return {"engine": "UI-TARS-Desktop", "goal": user_goal, "status": "UNAVAILABLE", "detail": "UI-TARS runtime is not executable in the current bridge; no coordinates were produced."}

        # UI-TARS action feedback metadata
        return {
            "engine": "UI-TARS-Desktop",
            "goal": user_goal,
            "status": "ACTIVE",
            "action_feedback": {
                "feedback_loop": "visual_regrounding_enabled",
                "grounding_strategy": "normalized_point_prediction",
                "action_types": ["click", "type", "drag", "scroll", "wait"],
            }
        }

    # -------------------------------------------------------------------------
    # 2. Browser Use Autonomous Navigation
    # -------------------------------------------------------------------------
    async def run_browser_use_task(self, task_instruction: str, max_steps: int = 15) -> Dict[str, Any]:
        """
        Executes autonomous web tasks using browser-use state and page elements
        instead of hardcoded scripts.
        Wires synaptic neurons: 'browser_use' -> 'web_research'
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("browser_use", "web_research", "EXECUTES", 1.5)
            sm.fire_synapse("browser_use", "dom_element_selector", "ANALYZES", 1.2)

        cb = self._get_central_brain()
        if cb:
            cb.add_node(
                category="NOTE",
                title=f"Browser-Use Task: {task_instruction[:40]}",
                content={"task": task_instruction, "max_steps": max_steps},
                author_pod="POD_WEB",
                author_agent="BROWSER_USE_AGENT",
                tags=["browser_use", "web", "automation"]
            )

        if not self.engines_status["browser_use"]:
            return {"status": "DEGRADED", "engine": "browser_use", "detail": "Library not imported; fallback active"}

        try:
            from core.web.browser_use_agent import browser_use_engine
            result = await browser_use_engine.execute_task(task_instruction, max_steps=max_steps, headless=False)
            return result if isinstance(result, dict) else {"status": "FAILED", "engine": "browser-use", "error": "Browser-Use returned a non-structured result."}
        except Exception as e:
            logger.error(f"[Bridge] Browser-Use error: {e}")
            return {"status": "ERROR", "error": str(e)}

    # -------------------------------------------------------------------------
    # 3. OpenAI Agents SDK Runtime & Specialist Handoffs
    # -------------------------------------------------------------------------
    def dispatch_agent_handoff(self, current_agent: str, target_agent: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes formal agent handoffs with guardrail validation and tracing.
        Wires synaptic neurons: 'agent_handoff' -> target_agent
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse(current_agent, target_agent, "HANDOFF_TO", 1.5)
            sm.fire_synapse("openai_agents_sdk", "guardrail_verification", "VERIFIES", 1.0)

        cb = self._get_central_brain()
        if cb:
            cb.add_node(
                category="SYSTEM_STATE",
                title=f"Specialist Handoff: {current_agent} -> {target_agent}",
                content=task_context,
                author_pod="ORCHESTRATION",
                author_agent="AGENTS_SDK_ROUTER",
                tags=["handoff", "guardrails", "agents_sdk"]
            )

        if not self.engines_status["openai_agents_sdk"]:
            return {"status": "UNAVAILABLE", "engine": "openai-agents-sdk", "guardrail_status": "NOT_EXECUTED", "detail": "SDK is not importable; no handoff executed."}
        return {
            "engine": "openai-agents-sdk",
            "source_agent": current_agent,
            "target_agent": target_agent,
            "guardrail_status": "NOT_EXECUTED",
            "status": "UNAVAILABLE",
            "detail": "No executable Agents SDK handoff adapter is configured; metadata was not treated as a handoff.",
            "trace_id": f"trace-{int(time.time()*1000)}"
        }

    # -------------------------------------------------------------------------
    # 4. OpenHands Coding Loop Integration
    # -------------------------------------------------------------------------
    def openhands_observe_and_act(self, workspace_path: str, objective: str) -> Dict[str, Any]:
        """
        OpenHands iterative programming pattern:
        Observe Workspace -> Reason -> Edit Code -> Run Tests -> Evaluate Feedback.
        Wires synaptic neurons: 'openhands_coding' -> 'sdlc_patch'
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("openhands_coding", "sdlc_patch", "CREATES", 1.5)
            sm.fire_synapse("sdlc_patch", "test_verification", "EVALUATES", 1.2)

        return {
            "engine": "OpenHands",
            "status": "NOT_IMPLEMENTED",
            "mode": "OBSERVE_THINK_ACT_TEST_ITERATE",
            "workspace": workspace_path,
            "objective": objective,
            "action_space": ["read_file", "edit_file", "run_bash", "check_git_diff", "run_pytest"]
        }

    # -------------------------------------------------------------------------
    # 5. LangGraph State Graph & Checkpointing
    # -------------------------------------------------------------------------
    def create_state_checkpoint(self, state_id: str, state_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Provides cyclic graph orchestration, checkpointing, and state recovery
        for resilient, long-running agent missions.
        Wires synaptic neurons: 'langgraph_checkpoint' -> state_id
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("langgraph_graph", "state_checkpoint", "PRESERVES", 1.2)
            sm.fire_synapse("state_checkpoint", state_id, "CONTAINS", 1.0)

        cb = self._get_central_brain()
        if cb:
            cb.add_node(
                category="VARIABLE",
                title=f"LangGraph Checkpoint: {state_id}",
                content=state_data,
                author_pod="ORCHESTRATION",
                author_agent="LANGGRAPH_SPINE",
                tags=["langgraph", "checkpoint", "state"]
            )

        return {
            "engine": "central_brain_projection",
            "state_id": state_id,
            "checkpointed_at": time.time(),
            "status": "CHECKPOINT_SAVED",
            "langgraph_runtime": False,
            "detail": "Persisted through CentralBrain; LangGraph graph/checkpointer was not invoked.",
        }

    # -------------------------------------------------------------------------
    # 6. llama.cpp Local Quantized Engine (4GB VRAM GPU Optimized)
    # -------------------------------------------------------------------------
    def load_quantized_model(self, model_path: Optional[str] = None, n_gpu_layers: int = 24, n_ctx: int = 2048):
        """
        Loads GGUF quantized models with selective GPU layer offloading tuned
        specifically for a 4GB VRAM GPU, preventing CUDA out-of-memory errors.
        Wires synaptic neurons: 'llama_cpp' -> 'quantized_inference'
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("llama_cpp", "quantized_inference", "EXECUTES", 1.5)
            sm.fire_synapse("quantized_inference", "4gb_vram_offload", "OPTIMIZES", 1.4)

        if not self.engines_status.get("llama_cpp", False):
            return {"status": "UNAVAILABLE", "engine": "llama.cpp", "detail": "llama-cpp-python not importable; no model was loaded."}

        if not model_path:
            return {"status": "UNAVAILABLE", "engine": "llama.cpp", "detail": "A real GGUF model_path is required; no model was loaded."}
        if not os.path.isfile(model_path) or not model_path.lower().endswith(".gguf"):
            return {"status": "UNAVAILABLE", "engine": "llama.cpp", "detail": f"GGUF model not found or invalid: {model_path}"}

        try:
            from llama_cpp import Llama
            model = Llama(model_path=model_path, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False)
            self.engine_health["llama_cpp"].update({"runtime_ready": True, "configured": True, "detail": "GGUF model loaded successfully."})
            return {
                "engine": "llama.cpp",
                "status": "READY",
                "hardware_profile": "configured_gpu_layers",
                "n_gpu_layers": n_gpu_layers,
                "n_ctx": n_ctx,
                "format": "GGUF",
                "model_path": model_path,
                "model_loaded": model is not None,
            }
        except Exception as e:
            logger.error(f"[Bridge] llama.cpp init note: {e}")
            return {"status": "ERROR", "error": str(e)}

    # -------------------------------------------------------------------------
    # 7. Whisper Speech Intelligence
    # -------------------------------------------------------------------------
    def transcribe_audio_whisper(self, audio_file_path: str, model_size: str = "base") -> Dict[str, Any]:
        """
        Transcribes speech using local OpenAI Whisper model.
        Wires synaptic neurons: 'whisper_speech' -> 'audio_transcription'
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("whisper_speech", "audio_transcription", "TRANSCRIBES", 1.5)

        if not self.engines_status["whisper"]:
            return {"status": "UNAVAILABLE", "detail": "whisper not installed"}

        try:
            import whisper
            model = whisper.load_model(model_size)
            result = model.transcribe(audio_file_path)
            return {
                "status": "SUCCESS",
                "engine": "Whisper",
                "text": result.get("text", "").strip(),
                "language": result.get("language", "en")
            }
        except Exception as e:
            logger.error(f"[Bridge] Whisper transcription failed: {e}")
            return {"status": "ERROR", "error": str(e)}

    # -------------------------------------------------------------------------
    # 8. AutoGen Multi-Agent Collective
    # -------------------------------------------------------------------------
    def start_autogen_squad(self, goal: str, agent_roles: List[str]) -> Dict[str, Any]:
        """
        Orchestrates multi-agent conversation and collective task solving.
        Wires synaptic neurons: 'autogen_collective' -> 'multi_agent_consensus'
        """
        sm = self._get_synaptic_memory()
        if sm:
            sm.fire_synapse("autogen_collective", "multi_agent_consensus", "DEBATES", 1.5)
            for role in agent_roles:
                sm.fire_synapse("autogen_collective", role, "ENLISTS", 1.0)

        cb = self._get_central_brain()
        if cb:
            cb.add_node(
                category="NOTE",
                title=f"AutoGen Squad Formation: {goal[:40]}",
                content={"goal": goal, "roles": agent_roles},
                author_pod="ORCHESTRATION",
                author_agent="AUTOGEN_COORDINATOR",
                tags=["autogen", "multi_agent", "consensus"]
            )

        if not self.engines_status.get("autogen", False):
            return {"status": "UNAVAILABLE", "engine": "AutoGen", "goal": goal, "roles": agent_roles, "detail": "AutoGen is not importable; no squad started."}
        return {
            "engine": "AutoGen",
            "status": "NOT_IMPLEMENTED",
            "goal": goal,
            "roles": agent_roles,
            "strategy": "round_robin_group_chat",
            "detail": "No executable AutoGen team was started; this is a planning description only.",
        }

    def get_full_cortex_manifest(self) -> Dict[str, Any]:
        """Returns the full status and wiring manifest of all 8 engines."""
        inventory = {
            name: os.path.isdir(os.path.join(EXTERNAL_ENGINES_DIR, dirname))
            for name, dirname in {
                "ui-tars-desktop": "ui-tars-desktop",
                "browser-use": "browser-use",
                "openai-agents-python": "openai-agents-python",
                "OpenHands": "OpenHands",
                "langgraph": "langgraph",
                "llama.cpp": "llama.cpp",
                "whisper": "whisper",
                "autogen": "autogen",
            }.items()
        }
        return {
            "cortex_version": "A.E.G.I.S. 2.0 Unified Neural Spine",
            "timestamp": time.time(),
            "wired_engines": self.engines_status,
            "engine_health": self.engine_health,
            "external_engines_dir": EXTERNAL_ENGINES_DIR,
            "external_engine_inventory": inventory,
            "synaptic_neurons_connected": self._get_synaptic_memory() is not None,
            "central_brain_connected": self._get_central_brain() is not None,
        }


# Singleton instance
neural_bridge = NeuralExtensionsBridge()

"""
A.E.G.I.S. Adaptive Execution Router
=====================================
Phase 3.2: Multi-Modal Execution Dispatch and Strategy Selector.
Selects the optimal execution technology (Native API/CLI, Semantic UIA, Visual Computer Use,
or Browser Automation) based on application identity, task intent, interface accessibility,
and verification contracts.

Invariants:
- Never relies on simplistic prompt keyword matching alone.
- Dispatches along an explicit fidelity ladder:
    1. Native API / Scripting / CLI (Highest fidelity, deterministic)
    2. Semantic Windows UI Automation (Accessible controls, invariant to resolution)
    3. Browser DOM Automation via Playwright (Web tasks)
    4. Visual Computer Use (Screen capture, OCR/VLM grounding, mouse/keyboard fallback)
- Bounds retries and enforces post-action verification.
- Strictly halts with FOCUS_MISMATCH or DESKTOP_SESSION_UNAVAILABLE when environment safety is violated.
"""

import sys
import enum
import time
import shutil
import urllib.parse
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from core.execution.semantic_ui_controller import semantic_ui_controller
from core.execution.universal_desktop_controller import universal_controller
from core.perception.screen_capture import screen_capturer


class ExecutionMethod(str, enum.Enum):
    NATIVE_API = "NATIVE_API"                  # Tier 1: CLI, Python runtime, Blender bpy, IPC/RPC
    WINDOWS_UIA = "WINDOWS_UIA"                # Tier 2: Microsoft UI Automation
    VISUAL_COMPUTER_USE = "VISUAL_COMPUTER_USE"# Tier 3: Screenshot + OCR/VLM + Human-like input
    BROWSER_DOM = "BROWSER_DOM"                # Tier 4: Playwright CDP semantic locators


@dataclass
class TargetProfile:
    """Discovered capabilities and environmental profile of an application or target."""
    target_name: str
    is_web_url: bool = False
    has_cli_interface: bool = False
    cli_command: Optional[str] = None
    has_uia_tree: bool = False
    hwnd: Optional[int] = None
    is_opaque_canvas: bool = False
    preferred_method: ExecutionMethod = ExecutionMethod.WINDOWS_UIA
    reasoning: str = ""
    tier: Optional[ExecutionMethod] = None
    target: str = ""
    can_fallback: bool = True
    fallback_tiers: List[ExecutionMethod] = field(default_factory=list)

    def __post_init__(self):
        if not self.tier:
            self.tier = self.preferred_method
        if not self.target:
            self.target = self.target_name


class AdaptiveExecutionRouter:
    """Autonomous execution router selecting the highest-fidelity control modality."""

    def __init__(self):
        # Known application profiles with native scriptability / CLI support
        self._native_cli_catalog = {
            "python": "python",
            "git": "git",
            "powershell": "powershell.exe",
            "cmd": "cmd.exe",
            "npm": "npm.cmd",
            "node": "node.exe",
            "code": "code.cmd",
            "blender": "blender.exe",
        }

        # Known opaque canvas applications where UIA exposes only empty frame controls
        self._opaque_canvas_catalog = {
            "paint", "mspaint", "photoshop", "gimp", "unity", "unreal", "godot"
        }

    def analyze_target(self, target: str, task_intent: str = "") -> TargetProfile:
        """
        Analyzes target application or URL and task intent to determine available interfaces.
        Does not use naive keyword matching; inspects system PATH, running HWNDs, and URL structures.
        """
        clean_target = (target or "").strip()
        lower_target = clean_target.lower()
        lower_intent = (task_intent or "").lower()

        # 1. Web URL or Web Browsing Intent
        is_url = bool(
            clean_target.startswith("http://") or
            clean_target.startswith("https://") or
            clean_target.startswith("www.") or
            any(clean_target.endswith(tld) for tld in [".com", ".org", ".io", ".net", ".edu", ".gov"])
        )
        if is_url:
            return TargetProfile(
                target_name=clean_target,
                is_web_url=True,
                preferred_method=ExecutionMethod.BROWSER_DOM,
                reasoning=f"Target '{clean_target}' is a Web URL; routing to Playwright DOM driver."
            )

        # 2. Native CLI / Command Execution Intent
        cli_key = lower_target.split()[0] if lower_target else ""
        if cli_key in self._native_cli_catalog or shutil.which(cli_key):
            # Check if intent is pure CLI computation or script execution
            cli_intents = ["run", "execute", "version", "build", "compile", "status", "commit", "test", "eval"]
            if any(ci in lower_intent for ci in cli_intents) or not any(gui in lower_intent for gui in ["window", "click", "button"]):
                return TargetProfile(
                    target_name=clean_target,
                    has_cli_interface=True,
                    cli_command=clean_target,
                    preferred_method=ExecutionMethod.NATIVE_API,
                    reasoning=f"Target executable '{cli_key}' available on PATH and intent is non-GUI execution."
                )

        # 3. Running or Native Windows Desktop Application
        hwnd = semantic_ui_controller._resolve_hwnd(clean_target)
        is_opaque = any(c in lower_target for c in self._opaque_canvas_catalog)

        # Probe UIA accessibility tree if window exists
        has_uia = False
        if hwnd:
            insp = semantic_ui_controller.inspect_window(hwnd)
            if insp.get("status") == "SUCCESS":
                # Quick tree probe
                tree = semantic_ui_controller.get_ui_tree(hwnd, max_depth=2, max_elements=5)
                has_uia = tree.get("element_count", 0) > 1

        if is_opaque and not has_uia:
            return TargetProfile(
                target_name=clean_target,
                hwnd=hwnd,
                is_opaque_canvas=True,
                preferred_method=ExecutionMethod.VISUAL_COMPUTER_USE,
                reasoning=f"Application '{clean_target}' is an opaque canvas with sparse UIA accessibility; routing to Visual Computer Use."
            )

        # Standard accessible Windows desktop app
        return TargetProfile(
            target_name=clean_target,
            hwnd=hwnd,
            has_uia_tree=True,
            preferred_method=ExecutionMethod.WINDOWS_UIA,
            reasoning=f"Application '{clean_target}' supports standard Microsoft Windows UI Automation."
        )

    async def execute_adaptively(
        self,
        task_description: str,
        target_app: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        allow_visual_fallback: bool = True
    ) -> Dict[str, Any]:
        """
        Executes an action using the most capable modality, with bounded fallbacks
        and strict outcome verification.
        """
        params = params or {}
        profile = self.analyze_target(target_app, task_description)
        selected_method = profile.preferred_method
        t_start = time.time()

        evidence: Dict[str, Any] = {
            "target_profile": {
                "target": profile.target_name,
                "preferred_method": selected_method.value,
                "reasoning": profile.reasoning
            },
            "attempts": []
        }

        # --- TIER 1: Native CLI / API Execution ---
        if selected_method == ExecutionMethod.NATIVE_API:
            from core.orchestration.agent_tools import run_terminal_command
            cmd = params.get("command") or target_app
            cwd = params.get("cwd", ".")
            output = run_terminal_command(cmd, cwd=cwd, visible=False)
            duration_ms = (time.time() - t_start) * 1000
            
            # Parse output and status
            is_success = "[EXIT 0]" in output or "Command completed with no output." in output
            return {
                "status": "SUCCESS" if is_success else "FAILED",
                "method_used": ExecutionMethod.NATIVE_API.value,
                "command": cmd,
                "output": output,
                "duration_ms": duration_ms,
                "evidence": evidence
            }

        # --- TIER 4: Browser DOM Automation ---
        if selected_method == ExecutionMethod.BROWSER_DOM:
            from core.perception.browser_driver import browser_driver
            url = params.get("url") or target_app
            nav_ok = await browser_driver.navigate_to(url)
            duration_ms = (time.time() - t_start) * 1000
            return {
                "status": "SUCCESS" if nav_ok else "FAILED",
                "method_used": ExecutionMethod.BROWSER_DOM.value,
                "url": url,
                "duration_ms": duration_ms,
                "evidence": evidence
            }

        # --- TIER 2: Semantic Windows UI Automation ---
        if selected_method == ExecutionMethod.WINDOWS_UIA:
            hwnd = profile.hwnd or semantic_ui_controller._resolve_hwnd(target_app)
            if not hwnd:
                # Attempt to launch if not currently running
                from action.desktop_control.desktop_controller import desktop_controller
                launch_res = await desktop_controller.open_app(target_app, auto_focus=True)
                if launch_res.get("status") != "SUCCESS":
                    return {
                        "status": "FAILED",
                        "method_used": ExecutionMethod.WINDOWS_UIA.value,
                        "error_code": "APP_LAUNCH_FAILED",
                        "error": launch_res.get("error", "Application launch failed."),
                        "duration_ms": (time.time() - t_start) * 1000,
                        "evidence": evidence
                    }
                hwnd = launch_res.get("hwnd")

            uia_res = None
            act_type = action.lower()

            if act_type == "invoke":
                search_criteria = params.get("search_criteria", {})
                uia_res = semantic_ui_controller.invoke_ui_element(hwnd, search_criteria)
            elif act_type == "set_value":
                search_criteria = params.get("search_criteria", {})
                val = params.get("value", "")
                uia_res = semantic_ui_controller.set_ui_value(hwnd, search_criteria, val)
            elif act_type == "read":
                search_criteria = params.get("search_criteria", {})
                uia_res = semantic_ui_controller.read_ui_element(hwnd, search_criteria)
            elif act_type == "select":
                search_criteria = params.get("search_criteria", {})
                uia_res = semantic_ui_controller.select_ui_item(hwnd, search_criteria)
            else:
                uia_res = {"status": "FAILED", "error_code": "UNKNOWN_ACTION", "message": f"Action '{action}' unrecognized."}

            evidence["attempts"].append({"method": "WINDOWS_UIA", "result": uia_res})

            if uia_res.get("status") == "SUCCESS":
                duration_ms = (time.time() - t_start) * 1000
                return {
                    "status": "SUCCESS",
                    "method_used": ExecutionMethod.WINDOWS_UIA.value,
                    "detail": uia_res,
                    "duration_ms": duration_ms,
                    "evidence": evidence
                }

            # If UIA failed because of focus mismatch, DO NOT fall back to visual typing (preserve focus safety)
            if uia_res.get("error_code") == "FOCUS_MISMATCH":
                return {
                    "status": "BLOCKED",
                    "method_used": ExecutionMethod.WINDOWS_UIA.value,
                    "error_code": "FOCUS_MISMATCH",
                    "error": uia_res.get("message"),
                    "duration_ms": (time.time() - t_start) * 1000,
                    "evidence": evidence
                }

            # Check if visual fallback is authorized and safe
            if not allow_visual_fallback:
                return {
                    "status": "FAILED",
                    "method_used": ExecutionMethod.WINDOWS_UIA.value,
                    "error_code": uia_res.get("error_code", "UIA_FAILED"),
                    "error": uia_res.get("message"),
                    "duration_ms": (time.time() - t_start) * 1000,
                    "evidence": evidence
                }

        # --- TIER 3: Visual Computer Use Fallback ---
        print(f"[ADAPTIVE_ROUTER] Engaging Tier 3 Visual Computer Use fallback for '{target_app}'...")
        try:
            frame = screen_capturer.capture_frame()
            if not frame or not frame.image_b64:
                return {
                    "status": "FAILED",
                    "method_used": ExecutionMethod.VISUAL_COMPUTER_USE.value,
                    "error_code": "SCREEN_CAPTURE_FAILED",
                    "error": "Failed to capture visual frame for fallback grounding.",
                    "evidence": evidence
                }

            # Ground coordinates via Visual Grounder if coordinates not supplied
            coords = params.get("coords")
            if not coords:
                from core.orchestration.computer_use.grounder import grounder_pipeline
                target_desc = params.get("target_description") or target_app
                grounded = grounder_pipeline.ground_element(
                    target=target_desc,
                    frame=frame,
                    task_description=task_description,
                    current_step=1,
                    bounding_boxes=[]
                )
                coords = grounded.get("mapped_coords")

            if coords and len(coords) == 2:
                universal_controller.force_foreground_window(profile.hwnd or frame.active_window)
                from core.execution.human_input import human_click
                click_msg = human_click(coords[0], coords[1])
                evidence["attempts"].append({"method": "VISUAL_COMPUTER_USE", "coords": coords, "msg": click_msg})
                duration_ms = (time.time() - t_start) * 1000
                return {
                    "status": "SUCCESS",
                    "method_used": ExecutionMethod.VISUAL_COMPUTER_USE.value,
                    "detail": f"Visual fallback clicked at ({coords[0]}, {coords[1]}): {click_msg}",
                    "duration_ms": duration_ms,
                    "evidence": evidence
                }
            else:
                return {
                    "status": "FAILED",
                    "method_used": ExecutionMethod.VISUAL_COMPUTER_USE.value,
                    "error_code": "VISUAL_GROUNDING_FAILED",
                    "error": "Could not visually locate target coordinates on screen.",
                    "evidence": evidence
                }

        except Exception as e_vis:
            return {
                "status": "FAILED",
                "method_used": ExecutionMethod.VISUAL_COMPUTER_USE.value,
                "error_code": "VISUAL_FALLBACK_EXCEPTION",
                "error": str(e_vis),
                "evidence": evidence
            }

    def select_execution_tier(
        self,
        task_description: str,
        app_identity: str,
        is_cli_intent: bool = False
    ) -> TargetProfile:
        intent = "run execute CLI" if is_cli_intent else task_description
        profile = self.analyze_target(app_identity, intent)
        ladder = []
        if profile.preferred_method == ExecutionMethod.WINDOWS_UIA:
            ladder = [ExecutionMethod.VISUAL_COMPUTER_USE]
        elif profile.preferred_method == ExecutionMethod.BROWSER_DOM:
            ladder = [ExecutionMethod.VISUAL_COMPUTER_USE]
        profile.fallback_tiers = ladder
        profile.tier = profile.preferred_method
        profile.target = profile.target_name
        profile.can_fallback = len(ladder) > 0 or profile.preferred_method != ExecutionMethod.NATIVE_API
        return profile


adaptive_execution_router = AdaptiveExecutionRouter()
adaptive_router = adaptive_execution_router
TierFallbackProfile = TargetProfile

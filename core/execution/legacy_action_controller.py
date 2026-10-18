"""Explicit compatibility controller for pre-universal task steps.

This module contains the old app-specific dispatcher that used to live inside
``core.orchestration.task_engine``. It is intentionally not the default path:
new task nodes go through ``core.execution.universal_dispatch`` and the
observe-plan-act-verify runtime. Callers must opt into this controller with
``execution_mode='legacy'`` or by explicitly supplying it to ``TaskEngine``.

The implementation is retained for migration compatibility only. Its results
must not be used as proof of a verified universal goal.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import urllib.parse
import uuid
import webbrowser
from typing import Any, Optional


async def _resolve_ui_coords(target_text: str):
    """Resolve a legacy natural-language UI target into screen coordinates."""

    target = (target_text or "").strip()
    if not target:
        return None
    try:
        from core.perception.screen_capture import screen_capturer
        from core.perception.gui_grounding import vision_grounder

        frame = await screen_capturer.capture_frame_async()
        element = await vision_grounder.find_target(frame, target)
    except Exception:
        return None

    if element is None:
        return None
    for attr in ("center", "coords", "position"):
        value = getattr(element, attr, None)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return [int(value[0]), int(value[1])]
            except Exception:
                continue
        if isinstance(value, dict) and "x" in value and "y" in value:
            try:
                return [int(value["x"]), int(value["y"])]
            except Exception:
                continue
    box = getattr(element, "bbox", None)
    if isinstance(box, (list, tuple)) and len(box) >= 4:
        try:
            return [int((box[0] + box[2]) / 2), int((box[1] + box[3]) / 2)]
        except Exception:
            return None
    if isinstance(box, dict) and all(k in box for k in ("x", "y", "width", "height")):
        try:
            return [int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)]
        except Exception:
            return None
    return None


def _desktop_controller() -> Any:
    try:
        from core.execution.universal_desktop_controller import UniversalDesktopController

        return UniversalDesktopController()
    except Exception:
        return None


async def default_action_controller(action_type: str, payload: dict):
    """Execute one explicitly legacy action.

    This function preserves the migration surface used by older planners. It
    is not imported by universal dispatch and is never selected implicitly for
    a universal action.
    """

    action = (action_type or "").upper()
    desktop_ctrl = _desktop_controller()

    if action == "APP_OPEN":
        app_name = payload.get("app_name", "").strip().lower()
        if desktop_ctrl:
            focused = desktop_ctrl.focus_application(app_name, minimize_others=False)
            if focused:
                return {"status": "SUCCESS", "message": f"Switched active focus to {app_name.title()}."}

        url_map = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "github": "https://www.github.com",
            "chatgpt": "https://chat.openai.com",
            "reddit": "https://www.reddit.com",
            "spotify": "https://open.spotify.com",
            "twitter": "https://twitter.com",
            "x": "https://x.com",
        }
        for key, url in url_map.items():
            if key in app_name:
                webbrowser.open(url)
                return {"status": "SUCCESS", "message": f"Opened {key.title()} in web browser."}

        sys_apps = {
            "notepad": "notepad.exe",
            "calc": "calc.exe",
            "calculator": "calc.exe",
            "explorer": "explorer.exe",
            "terminal": "cmd.exe",
            "cmd": "cmd.exe",
            "chrome": "chrome.exe",
            "code": "code",
            "vscode": "code",
        }
        command = sys_apps.get(app_name, "")
        if not command:
            return {"status": "FAILED", "error": f"Unknown legacy application: {app_name}"}
        try:
            subprocess.Popen([command], shell=False)
            await asyncio.sleep(0.8)
            if desktop_ctrl:
                desktop_ctrl.focus_application(app_name, minimize_others=False)
                desktop_ctrl.bypass_foreground_lock()
            return {"status": "SUCCESS", "message": f"Launched legacy application: {app_name}"}
        except Exception as exc:
            return {"status": "FAILED", "error": str(exc)}

    if action in {"AGENTIC_WEB_TASK", "MODEL_DRIVEN_WEB"}:
        directive = payload.get("directive", payload.get("query", ""))
        try:
            from core.web.model_driven_web_agent import model_driven_web_agent

            report = await model_driven_web_agent.execute_task(directive, headless=False)
            return {
                "status": report.get("status", "UNVERIFIED"),
                "message": f"Legacy model-driven web dispatch completed: {directive}",
                "report": report,
            }
        except Exception as exc:
            return {"status": "FAILED", "error": f"Legacy web execution failed: {exc}"}

    if action in {"BROWSER_NAVIGATE_OR_PLAY", "WEB_EXECUTION"}:
        app_name = payload.get("app_name", "").lower()
        query = payload.get("query", "trending")
        if "youtube" in app_name:
            try:
                from core.web_agent import aegis_web_agent

                await aegis_web_agent.initialize(headless=False)
                result = await aegis_web_agent.play_youtube_video(query)
                if result.get("status") == "SUCCESS":
                    return {"status": "SUCCESS", "message": f"Legacy YouTube flow completed for '{query}'.", "details": result}
            except Exception as exc:
                print(f"[LEGACY_TASK_CONTROLLER] YouTube agent warning: {exc}")
            target_url = (
                "https://www.youtube.com/feed/trending"
                if query in {"trending", "random", "any"}
                else f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
            )
            webbrowser.open(target_url)
            if desktop_ctrl:
                desktop_ctrl.focus_application("chrome", minimize_others=False)
            return {"status": "UNVERIFIED", "message": "Legacy browser navigation was issued without goal verification.", "url": target_url}

        if any(key in app_name for key in ("amazon", "shop", "ticket", "flight", "google_flights", "ticket_portal")):
            if "ticket" in app_name:
                site_url = os.getenv("AEGIS_BOOKING_SITE_URL", "").strip()
                if not site_url:
                    return {"status": "FAILED", "error": "SITE_URL_REQUIRED: legacy ticket workflows require an explicit authorized URL."}
            else:
                site_url = "https://www.google.com/travel/flights" if "flight" in app_name else "https://www.amazon.com"
            try:
                from core.web_agent import aegis_web_agent

                result = await aegis_web_agent.autonomous_search_and_select(site_url, query, query.split()[-1] if query else "")
                return {"status": result.get("status", "UNVERIFIED"), "message": f"Legacy web workflow ran at {site_url}.", "details": result}
            except Exception as exc:
                return {"status": "FAILED", "error": f"Legacy shopping workflow failed: {exc}"}

        target_url = payload.get("url")
        if not target_url:
            target_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
        webbrowser.open(target_url)
        return {"status": "UNVERIFIED", "message": "Legacy browser navigation was issued without goal verification.", "url": target_url}

    if action in {"VERIFIED_DESKTOP_TEXT_FILE", "DESKTOP_TEXT_FILE"}:
        from core.execution.verified_desktop_task import VerifiedDesktopTextTask
        from core.orchestration.agent_contract import ExecutionEnvironment, TaskContext

        allowed_roots = payload.get("allowed_roots", [])
        if isinstance(allowed_roots, str):
            allowed_roots = [allowed_roots]
        context = TaskContext(
            task_id=payload.get("task_id", str(uuid.uuid4())),
            goal=payload.get("goal", payload.get("description", "Verified desktop text-file task")),
            environment=ExecutionEnvironment.DESKTOP,
        )
        result = await VerifiedDesktopTextTask().run(
            app_command=payload.get("app_command", payload.get("app_name", "")),
            app_title=payload.get("app_title", payload.get("app_name", "")),
            content=payload.get("content", ""),
            destination=payload.get("destination", payload.get("path", "")),
            allowed_roots=allowed_roots,
            context=context,
            reopen_command=payload.get("reopen_command"),
            timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
            evidence_dir=payload.get("evidence_dir"),
        )
        return result.to_dict()

    if action in {"UI_AUTOMATION", "DESKTOP_CONTROL"}:
        sub_action = payload.get("action", "type")
        text_to_type = payload.get("text", "") or payload.get("target", "")
        target_window = payload.get("window", "")
        target_text = payload.get("target", "")
        if target_window and desktop_ctrl:
            desktop_ctrl.focus_application(target_window, minimize_others=False)
            await asyncio.sleep(0.4)
        try:
            import pyautogui
            import pyperclip

            if sub_action == "type":
                if not text_to_type:
                    return {"status": "FAILED", "error": "No text provided to type."}
                if target_window and desktop_ctrl:
                    desktop_ctrl.safe_type_into_window(target_window, text_to_type)
                else:
                    pyperclip.copy(text_to_type)
                    pyautogui.hotkey("ctrl", "v")
                    pyautogui.press("enter")
                return {"status": "SUCCESS", "message": f"Legacy typed input: '{text_to_type[:50]}'"}
            if sub_action == "press":
                key = payload.get("key", "enter")
                pyautogui.press(key)
                return {"status": "SUCCESS", "message": f"Legacy key '{key}' executed."}
            if sub_action in {"click", "double_click", "right_click", "move"}:
                coords = payload.get("coords")
                if not (coords and len(coords) >= 2):
                    coords = await _resolve_ui_coords(target_text)
                if not coords:
                    return {"status": "FAILED", "error": f"Could not locate '{target_text or 'target'}'; no click was performed."}
                from core.execution.human_input import human_click, human_move

                if sub_action == "move":
                    human_move(coords[0], coords[1])
                else:
                    human_click(coords[0], coords[1], clicks=2 if sub_action == "double_click" else 1, button="right" if sub_action == "right_click" else "left")
                return {"status": "SUCCESS", "message": "Legacy desktop input executed.", "coords": coords}
            if sub_action == "scroll":
                amount = int(payload.get("amount", 3) or 3)
                pyautogui.scroll(amount)
                return {"status": "SUCCESS", "message": f"Legacy scroll executed: {amount}."}
            return {"status": "FAILED", "error": f"Unsupported legacy UI action '{sub_action}'."}
        except Exception as exc:
            return {"status": "FAILED", "error": f"Legacy UI automation failed: {exc}"}

    if action == "WEB_SEARCH":
        from core.tools.web_search import web_search

        query = payload.get("query", "")
        if not query:
            return {"status": "FAILED", "error": "No search query provided."}
        results = web_search(query, max_results=5)
        if results and "error" not in results[0]:
            return {"status": "SUCCESS", "message": f"Legacy web search completed for '{query}'.", "results": results}
        return {"status": "FAILED", "error": results[0].get("error", "No results returned.") if results else "No results returned."}

    if action in {"COMPUTER_USE", "AUTONOMOUS_DESKTOP"}:
        task_prompt = payload.get("task", "") or payload.get("objective", "")
        try:
            from core.orchestration.computer_use_agent import computer_use_agent

            result = await computer_use_agent.execute_task(task_prompt)
            return {"status": result.get("status", "UNVERIFIED"), "message": "Legacy computer-use flow completed.", "details": result}
        except Exception as exc:
            return {"status": "FAILED", "error": f"Legacy computer-use failed: {exc}"}

    if action in {"VIBE_CODING", "VIBE_SANDBOX", "VIBE_GENERATE"}:
        try:
            from core.intelligence.vibe_sandbox import vibe_sandbox_engine

            result = vibe_sandbox_engine.generate_app(
                payload.get("prompt", "") or payload.get("task", "") or payload.get("concept", ""),
                app_id=payload.get("app_id", "vibe_app"),
            )
            return {"status": result.get("status", "FAILED"), "message": "Legacy Vibe flow completed.", "details": result}
        except Exception as exc:
            return {"status": "FAILED", "error": f"Legacy Vibe generation failed: {exc}"}

    if action == "AGENT_SWARM_DELEGATION":
        try:
            from core.orchestration.agent_loop import spawn_agent

            result = await spawn_agent(payload.get("agent_id", "AGENT-112"), payload.get("task", "") or payload.get("objective", ""))
            return {"status": "SUCCESS" if result.get("final_state") == "DONE" else "UNVERIFIED", "message": "Legacy swarm delegation completed.", "details": result}
        except Exception as exc:
            return {"status": "FAILED", "error": f"Legacy swarm delegation failed: {exc}"}

    return {"status": "FAILED", "action": action, "error": f"Unsupported legacy action '{action}'. Nothing was performed."}


__all__ = ["default_action_controller"]

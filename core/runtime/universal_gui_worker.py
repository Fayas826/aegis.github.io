"""Model-driven, cross-application desktop worker for AEGIS.

This is the desktop counterpart to the existing browser agent.  It sends a
fresh screenshot plus the fused accessibility/Set-of-Marks manifest to a
vision-capable model, executes only bounded actions through
``DesktopActionLayer``, and observes again after every action.

The worker intentionally does not claim that a model's final prose proves the
goal.  It returns ``VERIFIED`` only when a deterministic postcondition can be
checked; otherwise it returns ``UNVERIFIED`` with the last observation.
"""

from __future__ import annotations

import base64
import os
import shlex
import shutil
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from core.execution.universal_desktop_layer import DesktopActionLayer, DesktopActionResult
from core.perception.universal_desktop_layer import DesktopObservation, DesktopPerception


MAX_MODEL_STEPS = 20
DEFAULT_MODEL = "gemini-2.5-flash"


def _goal_target(goal: str, verbs: Sequence[str]) -> str:
    lower = goal.casefold()
    for verb in verbs:
        marker = f"{verb.casefold()} "
        if marker in lower:
            return goal[lower.index(marker) + len(marker):].strip(" .,:;\"'")
    return ""


def _verified_open_goal(goal: str, observation: DesktopObservation) -> bool:
    """Verify the narrow, observable ``open/launch app`` class of goals."""

    lowered = goal.casefold()
    if not any(word in lowered for word in ("open ", "launch ", "start ")):
        return False
    target = _goal_target(goal, ("open", "launch", "start"))
    if not target:
        return False
    target_token = target.casefold().split()[0]
    return target_token in (observation.active_window or "").casefold() or target_token in (observation.active_process or "").casefold()


def _observation_prompt(observation: DesktopObservation) -> str:
    manifest = observation.manifest.to_dict() if observation.manifest else None
    return (
        "FRESH DESKTOP OBSERVATION\n"
        f"status={observation.status} active_window={observation.active_window!r} "
        f"active_process={observation.active_process!r}\n"
        f"set_of_marks={manifest}\n"
        "Use element_id/Set-of-Marks targets when available. Coordinates are normalized 0-1000."
    )


@dataclass
class WorkerOutcome:
    status: str
    detail: str
    steps: int = 0
    final_observation: Optional[dict[str, Any]] = None
    actions: tuple[dict[str, Any], ...] = ()
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "status": self.status,
            "verification_status": self.status,
            "detail": self.detail,
            "steps": self.steps,
            "actions": list(self.actions),
            "final_observation": self.final_observation,
        }
        if self.error:
            result["error"] = self.error
        return result


class UniversalGUIWorker:
    """Execute model-selected actions against a real observed desktop."""

    def __init__(
        self,
        *,
        perception: Optional[DesktopPerception] = None,
        actions: Optional[DesktopActionLayer] = None,
        client: Any = None,
        model: Optional[str] = None,
        api_key_env: str = "GEMINI_API_KEY",
        confirmation: Optional[Callable[[str], bool]] = None,
        max_steps: int = MAX_MODEL_STEPS,
    ) -> None:
        self.perception = perception or DesktopPerception()
        self.actions = actions or DesktopActionLayer(self.perception)
        self.client = client
        self.model = model or os.getenv("AEGIS_GUI_AGENT_MODEL", DEFAULT_MODEL)
        self.api_key_env = api_key_env
        self.confirmation = confirmation
        self.max_steps = max(1, min(int(max_steps), MAX_MODEL_STEPS))

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        key = os.getenv(self.api_key_env, "").strip()
        if not key or key.upper() in {"YOUR_GEMINI_API_KEY", "CHANGEME"}:
            return None
        try:
            from google import genai

            self.client = genai.Client(api_key=key)
            return self.client
        except Exception:
            return None

    @staticmethod
    def _tool() -> Any:
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name="click_element",
                description="Click a visible clickable Set-of-Marks element by element_id.",
                parameters=types.Schema(type="OBJECT", properties={"element_id": types.Schema(type="STRING")}, required=["element_id"]),
            ),
            types.FunctionDeclaration(
                name="click_normalized",
                description="Click normalized display coordinates in the 0-1000 range.",
                parameters=types.Schema(type="OBJECT", properties={"x": types.Schema(type="INTEGER"), "y": types.Schema(type="INTEGER")}, required=["x", "y"]),
            ),
            types.FunctionDeclaration(
                name="type_text",
                description="Paste text into the currently focused field.",
                parameters=types.Schema(type="OBJECT", properties={"text": types.Schema(type="STRING")}, required=["text"]),
            ),
            types.FunctionDeclaration(
                name="hotkey",
                description="Send a bounded keyboard shortcut such as ctrl+l or alt+f4.",
                parameters=types.Schema(type="OBJECT", properties={"keys": types.Schema(type="ARRAY", items=types.Schema(type="STRING"))}, required=["keys"]),
            ),
            types.FunctionDeclaration(
                name="scroll",
                description="Scroll by a small bounded integer amount.",
                parameters=types.Schema(type="OBJECT", properties={"amount": types.Schema(type="INTEGER")}, required=["amount"]),
            ),
            types.FunctionDeclaration(
                name="launch_application",
                description="Launch one executable without a shell.",
                parameters=types.Schema(type="OBJECT", properties={"program": types.Schema(type="STRING"), "args": types.Schema(type="ARRAY", items=types.Schema(type="STRING"))}, required=["program"]),
            ),
            types.FunctionDeclaration(
                name="request_user_confirmation",
                description="Request confirmation before an irreversible action.",
                parameters=types.Schema(type="OBJECT", properties={"reason": types.Schema(type="STRING")}, required=["reason"]),
            ),
        ]
        return types.Tool(function_declarations=declarations)

    def _execute_call(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if name == "click_element":
            result = self.actions.click_element(args.get("element_id", ""))
        elif name == "click_normalized":
            result = self.actions.click_normalized(int(args.get("x", -1)) / 1000.0, int(args.get("y", -1)) / 1000.0)
        elif name == "type_text":
            result = self.actions.paste_text(args.get("text", ""))
        elif name == "hotkey":
            result = self.actions.hotkey(args.get("keys", []))
        elif name == "scroll":
            result = self.actions.scroll(int(args.get("amount", 0)))
        elif name == "launch_application":
            program = str(args.get("program", "")).strip()
            raw_args = args.get("args", [])
            if not isinstance(raw_args, Sequence) or isinstance(raw_args, (str, bytes)):
                raw_args = []
            result = self.actions.launch(program, [str(item) for item in raw_args])
        elif name == "request_user_confirmation":
            reason = str(args.get("reason", "Irreversible desktop action"))
            allowed = bool(self.confirmation and self.confirmation(reason))
            return {"status": "CONFIRMED" if allowed else "BLOCKED", "detail": reason if allowed else "Operator confirmation required."}
        else:
            return {"status": "BLOCKED", "detail": f"Unsupported model action: {name}"}
        if isinstance(result, DesktopActionResult):
            return result.to_dict()
        return {"status": "FAILED", "detail": "Action adapter returned an invalid result."}

    def _direct_launch_goal(self, goal: str) -> Optional[dict[str, Any]]:
        """Handle a low-risk app launch without requiring a vision model.

        This keeps simple commands usable when Gemini is not configured while
        still using the same observed launch adapter and postcondition check.
        All other goals continue through the model-driven loop.
        """

        match = re.match(r"^\s*(?:open|launch|start)\s+(?:the\s+)?(.+?)\s*$", goal, re.I)
        if not match:
            return None
        target = re.sub(r"\s+(?:app|application)$", "", match.group(1), flags=re.I).strip()
        if not target or any(char in target for char in "\r\n\x00") or len(target) > 260:
            return {"status": "BLOCKED", "verification_status": "BLOCKED", "error": "Invalid application target."}
        program = target
        args: list[str] = []
        try:
            from action.desktop_control.desktop_controller import desktop_controller

            resolved = desktop_controller.resolve_application_path(target)
            if resolved:
                program = resolved
        except Exception:
            resolved = shutil.which(target)
            if resolved:
                program = resolved
        action = self.actions.launch(program, args)
        observation = self.perception.observe()
        if action.status != "SUCCESS":
            return {
                "status": action.status,
                "verification_status": "UNVERIFIED",
                "error": action.error or action.detail,
                "action": action.to_dict(),
                "final_observation": observation.to_dict(),
            }
        verified = _verified_open_goal(goal, observation)
        return {
            "status": "VERIFIED" if verified else "UNVERIFIED",
            "verification_status": "VERIFIED" if verified else "UNVERIFIED",
            "detail": "Application launch verified by fresh active-window observation." if verified else "Application launched but active-window verification did not match.",
            "action": action.to_dict(),
            "final_observation": observation.to_dict(),
        }

    def run_task(self, goal: str, status_callback: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
        direct = self._direct_launch_goal(goal)
        if direct is not None:
            return direct
        client = self._get_client()
        if client is None:
            return WorkerOutcome("UNAVAILABLE", "Gemini vision client is not configured or importable.").to_dict()
        observation = self.perception.observe()
        if observation.status != "SUCCESS":
            return WorkerOutcome("UNAVAILABLE", "Desktop observation is unavailable.", final_observation=observation.to_dict(), error=observation.error).to_dict()

        try:
            from google.genai import types
        except Exception as exc:
            return WorkerOutcome("UNAVAILABLE", "google-genai is unavailable.", error=str(exc)).to_dict()

        image = None
        if observation.image_b64:
            try:
                image = types.Part.from_bytes(data=base64.b64decode(observation.image_b64), mime_type="image/png")
            except Exception:
                image = None
        contents: list[Any] = [types.Content(role="user", parts=[types.Part.from_text(text=f"USER GOAL: {goal}\n\n{_observation_prompt(observation)}")] + ([image] if image else []))]
        actions: list[dict[str, Any]] = []
        config = types.GenerateContentConfig(
            system_instruction=(
                "You are the AEGIS universal desktop planner. Observe the fresh screenshot and manifest, "
                "choose one bounded action at a time, and never claim completion without a later observation. "
                "Ask for confirmation before deletion, payment, sending, submission, or system changes."
            ),
            tools=[self._tool()],
            temperature=0.1,
        )

        for step in range(1, self.max_steps + 1):
            if status_callback:
                status_callback(f"desktop step {step}: observing and planning")
            try:
                response = client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as exc:
                return WorkerOutcome("UNVERIFIED", "Vision planner failed after desktop actions.", step - 1, observation.to_dict(), tuple(actions), str(exc)).to_dict()
            candidate = response.candidates[0] if getattr(response, "candidates", None) else None
            if candidate is None or getattr(candidate, "content", None) is None:
                return WorkerOutcome("UNVERIFIED", "Vision planner returned no usable content.", step - 1, observation.to_dict(), tuple(actions)).to_dict()
            contents.append(candidate.content)
            calls = [part.function_call for part in getattr(candidate.content, "parts", ()) if getattr(part, "function_call", None) is not None]
            if not calls:
                verified = _verified_open_goal(goal, observation)
                status = "VERIFIED" if verified else "UNVERIFIED"
                return WorkerOutcome(status, "Observable desktop goal verified." if verified else "Planner stopped without deterministic goal evidence.", step - 1, observation.to_dict(), tuple(actions)).to_dict()
            responses: list[Any] = []
            for call in calls:
                name = str(getattr(call, "name", ""))
                args = getattr(call, "args", {}) or {}
                if status_callback:
                    status_callback(f"desktop step {step}: {name}")
                result = self._execute_call(name, args)
                actions.append({"action": name, "result": result})
                if result.get("status") == "BLOCKED":
                    return WorkerOutcome("BLOCKED", result.get("detail", "Action blocked."), step, observation.to_dict(), tuple(actions)).to_dict()
                observation = self.perception.observe()
                if observation.status != "SUCCESS":
                    return WorkerOutcome("UNVERIFIED", "Post-action desktop observation unavailable.", step, observation.to_dict(), tuple(actions), observation.error).to_dict()
                responses.append(types.Part(function_response=types.FunctionResponse(name=name, response={"result": result, "observation": observation.to_dict()})))
                if observation.image_b64:
                    try:
                        responses.append(types.Part.from_bytes(data=base64.b64decode(observation.image_b64), mime_type="image/png"))
                    except Exception:
                        pass
            contents.append(types.Content(role="user", parts=responses))

        verified = _verified_open_goal(goal, observation)
        return WorkerOutcome("VERIFIED" if verified else "UNVERIFIED", "Step budget exhausted; final observation evaluated.", self.max_steps, observation.to_dict(), tuple(actions)).to_dict()


__all__ = ["UniversalGUIWorker", "WorkerOutcome"]

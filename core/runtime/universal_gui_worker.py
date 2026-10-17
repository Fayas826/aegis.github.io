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
import json
import os
import shlex
import shutil
import re
import time
import uuid
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
    observations: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "status": self.status,
            "verification_status": self.status,
            "detail": self.detail,
            "steps": self.steps,
            "actions": list(self.actions),
            "observations": list(self.observations),
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
        provider: Optional[str] = None,
        openai_request: Optional[Callable[[dict[str, Any]], Mapping[str, Any]]] = None,
        openai_key_env: str = "OPENAI_API_KEY",
        openai_model: Optional[str] = None,
        task_junction: Any = None,
    ) -> None:
        self.perception = perception or DesktopPerception()
        self.actions = actions or DesktopActionLayer(self.perception)
        self.client = client
        self.model = model or os.getenv("AEGIS_GUI_AGENT_MODEL", DEFAULT_MODEL)
        self.api_key_env = api_key_env
        self.confirmation = confirmation
        self.max_steps = max(1, min(int(max_steps), MAX_MODEL_STEPS))
        self.provider = (provider or os.getenv("AEGIS_GUI_PROVIDER", "auto")).strip().lower()
        self.openai_request = openai_request
        self.openai_key_env = openai_key_env
        self.openai_model = openai_model or os.getenv("AEGIS_GUI_OPENAI_MODEL", "gpt-4o")
        self.task_junction = task_junction
        self._active_task_id: Optional[str] = None
        self._active_junction: Any = None
        self._active_context: Any = None

    def _select_provider(self) -> tuple[Optional[str], Optional[str]]:
        """Select Gemini or ChatGPT without claiming an unconfigured provider."""

        gemini_key = os.getenv(self.api_key_env, "").strip()
        openai_key = os.getenv(self.openai_key_env, "").strip()
        has_gemini = self.client is not None or bool(gemini_key and gemini_key.upper() not in {"YOUR_GEMINI_API_KEY", "CHANGEME"})
        has_openai = self.openai_request is not None or bool(openai_key and openai_key.upper() not in {"YOUR_OPENAI_API_KEY", "CHANGEME"})
        if self.provider in {"openai", "chatgpt"}:
            return ("openai", None) if has_openai else (None, "ChatGPT/OpenAI provider is not configured.")
        if self.provider in {"gemini", "google"}:
            return ("gemini", None) if has_gemini else (None, "Gemini provider is not configured.")
        if has_gemini:
            return "gemini", None
        # A caller that explicitly supplied a Gemini key variable is asking
        # for that provider; do not silently consume a different ambient key.
        if self.api_key_env != "GEMINI_API_KEY" and self.openai_request is None:
            return None, "The requested Gemini provider is not configured."
        if has_openai:
            return "openai", None
        return None, "Neither Gemini nor ChatGPT/OpenAI is configured."

    def _neural_event(self, goal: str, provider: str, phase: str,
                      status: Optional[str] = None, details: Optional[Mapping[str, Any]] = None) -> None:
        """Persist execution activity through the existing neural spine."""

        try:
            from core.cognition.memory.synaptic_memory import synaptic_memory

            synaptic_memory.fire_synapse(f"{provider}_computer_use", "screen_perception", "GROUNDS", 1.5)
            synaptic_memory.fire_synapse("screen_perception", "universal_desktop_layer", "OBSERVES", 1.2)
            if phase in {"action", "completed"}:
                synaptic_memory.fire_synapse("universal_desktop_layer", "os_actuator", "EXECUTES", 1.5)
            if phase == "completed":
                synaptic_memory.fire_synapse("os_actuator", "verification_planner", "VERIFIES", 1.5)
        except Exception:
            pass
        try:
            from core.orchestration.central_brain import central_brain

            central_brain.add_node(
                category="ACTION" if phase != "completed" else "DELIVERABLE",
                title=f"Universal GUI {phase}: {goal[:48]}",
                content={"goal": goal, "provider": provider, "status": status, "details": dict(details or {}), "timestamp": time.time()},
                author_pod="POD_ACTUATION",
                author_agent="UNIVERSAL_GUI_WORKER",
                tags=["aegis", "universal_gui", provider, phase],
            )
        except Exception:
            pass

    def _start_junction(self, goal: str, task_context: Any = None) -> tuple[str, Any]:
        try:
            from core.orchestration.agent_contract import ExecutionEnvironment, TaskContext
            from core.orchestration.shared_task_junction import task_junction

            context = task_context or TaskContext(goal=goal, environment=ExecutionEnvironment.DESKTOP)
            junction = self.task_junction or task_junction
            junction.start(context, "universal_gui_worker", {"provider": self.provider, "goal": goal})
            return context.task_id, junction
        except Exception:
            return str(uuid.uuid4()), None

    def _finish_junction(self, task_id: str, junction: Any, outcome: Any) -> None:
        if junction is None:
            return
        data = outcome if isinstance(outcome, Mapping) else {"status": "UNVERIFIED", "detail": str(outcome)}
        raw_status = str(data.get("status") or "").upper()
        verification = str(data.get("verification_status") or "").upper()
        status = raw_status if raw_status in {"FAILED", "BLOCKED", "UNAVAILABLE", "CANCELLED", "TIMEOUT", "VERIFICATION_FAILED", "SAFETY_BLOCK"} else (verification or raw_status or "UNVERIFIED")
        if status == "SUCCESS" and not data.get("verified", False):
            status = "UNVERIFIED"
        evidence = {
            "provider": self.provider,
            "actions": data.get("actions", []),
            "observations": data.get("observations", []),
            "final_observation": data.get("final_observation"),
            "verification_status": status,
        }
        try:
            # Keep the task RUNNING while recording evidence; ``complete`` is
            # the single transition that writes the terminal result payload.
            junction.record_event(task_id, "universal_gui_result", agent="universal_gui_worker", evidence=evidence)
            junction.complete(task_id, status, data, evidence)
        except Exception:
            pass

    def _stop_status(self) -> Optional[str]:
        """Check cooperative cancellation and deadline state before model calls."""

        context = self._active_context
        if context is not None:
            if bool(getattr(context, "cancelled", False)):
                return "CANCELLED"
            try:
                if context.is_expired():
                    return "TIMEOUT"
            except Exception:
                pass
        junction = self._active_junction
        task_id = self._active_task_id
        if junction is not None and task_id:
            try:
                state = junction.get(task_id) or {}
                if state.get("status") == "CANCEL_REQUESTED":
                    return "CANCELLED"
            except Exception:
                pass
        return None

    def run_task(self, goal: str, status_callback: Optional[Callable[[str], None]] = None,
                 task_id: Optional[str] = None, task_context: Any = None) -> dict[str, Any]:
        """Run one universal GUI goal and persist its actual terminal outcome."""

        task_id_value, junction = self._start_junction(goal, task_context)
        self._active_task_id = task_id_value
        self._active_junction = junction
        self._active_context = task_context
        direct = self._direct_launch_goal(goal)
        if direct is not None:
            selected = "native"
            self.provider = selected
            self._neural_event(goal, selected, "started", "RUNNING", {"task_id": task_id_value})
            outcome = direct
            self._finish_junction(task_id_value, junction, outcome)
            self._neural_event(goal, selected, "completed", str(outcome.get("status", "UNVERIFIED")), {"task_id": task_id_value})
            return outcome

        provider, provider_error = self._select_provider()
        selected = provider or self.provider
        self._neural_event(goal, selected, "started", "RUNNING", {"task_id": task_id_value})
        if provider is None:
            outcome = WorkerOutcome("UNAVAILABLE", provider_error or "No GUI provider is configured.").to_dict()
        else:
            self.provider = provider
            try:
                outcome = self._run_task_core(goal, status_callback=status_callback)
            except Exception as exc:
                outcome = WorkerOutcome("FAILED", "Universal GUI worker failed.", error=str(exc)).to_dict()
        self._finish_junction(task_id_value, junction, outcome)
        self._neural_event(goal, selected, "completed", str(outcome.get("status", "UNVERIFIED")), {"task_id": task_id_value})
        self._active_task_id = None
        self._active_junction = None
        self._active_context = None
        return outcome

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
        # Only bypass the model for a simple launch command.  A compound goal
        # such as "open Notepad, type..., save..." must reach the planner;
        # otherwise the whole sentence would be treated as an executable path.
        if re.search(r"\b(?:and|then|type|write|save|close|reopen|verify|click|into|file)\b", target, re.I):
            return None
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
        action_before = getattr(action, "before", None)
        action_after = getattr(action, "after", None)
        if action.status != "SUCCESS":
            return {
                "status": action.status,
                "verification_status": "UNVERIFIED",
                "error": action.error or action.detail,
                "action": action.to_dict(),
                "observations": [action_before.to_dict() if action_before else None, action_after.to_dict() if action_after else None, observation.to_dict()],
                "final_observation": observation.to_dict(),
            }
        verified = _verified_open_goal(goal, observation)
        return {
            "status": "VERIFIED" if verified else "UNVERIFIED",
            "verification_status": "VERIFIED" if verified else "UNVERIFIED",
            "detail": "Application launch verified by fresh active-window observation." if verified else "Application launched but active-window verification did not match.",
            "action": action.to_dict(),
            "observations": [action_before.to_dict() if action_before else None, action_after.to_dict() if action_after else None, observation.to_dict()],
            "final_observation": observation.to_dict(),
        }

    def _run_task_core(self, goal: str, status_callback: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
        if self.provider == "openai":
            return self._run_openai(goal, status_callback=status_callback)

        client = self._get_client()
        if client is None:
            return WorkerOutcome("UNAVAILABLE", "Gemini vision client is not configured or importable.").to_dict()
        observation = self.perception.observe()
        if observation.status != "SUCCESS":
            return WorkerOutcome("UNAVAILABLE", "Desktop observation is unavailable.", final_observation=observation.to_dict(), error=observation.error).to_dict()
        observation_history: list[dict[str, Any]] = [observation.to_dict()]

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
            stop_status = self._stop_status()
            if stop_status:
                return WorkerOutcome(stop_status, f"Universal GUI task ended with {stop_status.lower()}.", step - 1, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
            if status_callback:
                status_callback(f"desktop step {step}: observing and planning")
            try:
                response = client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as exc:
                return WorkerOutcome("UNVERIFIED", "Vision planner failed after desktop actions.", step - 1, observation.to_dict(), tuple(actions), str(exc), tuple(observation_history)).to_dict()
            candidate = response.candidates[0] if getattr(response, "candidates", None) else None
            if candidate is None or getattr(candidate, "content", None) is None:
                return WorkerOutcome("UNVERIFIED", "Vision planner returned no usable content.", step - 1, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
            contents.append(candidate.content)
            calls = [part.function_call for part in getattr(candidate.content, "parts", ()) if getattr(part, "function_call", None) is not None]
            if not calls:
                verified = _verified_open_goal(goal, observation)
                status = "VERIFIED" if verified else "UNVERIFIED"
                return WorkerOutcome(status, "Observable desktop goal verified." if verified else "Planner stopped without deterministic goal evidence.", step - 1, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
            responses: list[Any] = []
            for call in calls:
                name = str(getattr(call, "name", ""))
                args = getattr(call, "args", {}) or {}
                if status_callback:
                    status_callback(f"desktop step {step}: {name}")
                result = self._execute_call(name, args)
                actions.append({"action": name, "result": result})
                self._neural_event(goal, self.provider, "action", str(result.get("status", "FAILED")), {"action": name, "step": step})
                if result.get("status") == "BLOCKED":
                    return WorkerOutcome("BLOCKED", result.get("detail", "Action blocked."), step, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
                observation = self.perception.observe()
                if observation.status != "SUCCESS":
                    observation_history.append(observation.to_dict())
                    return WorkerOutcome("UNVERIFIED", "Post-action desktop observation unavailable.", step, observation.to_dict(), tuple(actions), observation.error, tuple(observation_history)).to_dict()
                observation_history.append(observation.to_dict())
                responses.append(types.Part(function_response=types.FunctionResponse(name=name, response={"result": result, "observation": observation.to_dict()})))
                if observation.image_b64:
                    try:
                        responses.append(types.Part.from_bytes(data=base64.b64decode(observation.image_b64), mime_type="image/png"))
                    except Exception:
                        pass
            contents.append(types.Content(role="user", parts=responses))

        verified = _verified_open_goal(goal, observation)
        return WorkerOutcome("VERIFIED" if verified else "UNVERIFIED", "Step budget exhausted; final observation evaluated.", self.max_steps, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()

    @staticmethod
    def _openai_tools() -> list[dict[str, Any]]:
        """OpenAI Chat Completions tool schema matching the bounded executor."""

        def fn(name: str, description: str, properties: Mapping[str, Any], required: Sequence[str]):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": dict(properties), "required": list(required)},
                },
            }

        return [
            fn("click_element", "Click a visible clickable Set-of-Marks element by element_id.", {"element_id": {"type": "string"}}, ["element_id"]),
            fn("click_normalized", "Click normalized display coordinates from 0 to 1000.", {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
            fn("type_text", "Enter text into the currently focused field.", {"text": {"type": "string"}}, ["text"]),
            fn("hotkey", "Send a bounded keyboard shortcut.", {"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"]),
            fn("scroll", "Scroll by a small bounded integer amount.", {"amount": {"type": "integer"}}, ["amount"]),
            fn("launch_application", "Launch one executable without a shell.", {"program": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, ["program"]),
            fn("request_user_confirmation", "Request confirmation before an irreversible action.", {"reason": {"type": "string"}}, ["reason"]),
        ]

    def _openai_request(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        if self.openai_request is not None:
            result = self.openai_request(payload)
            return result if isinstance(result, Mapping) else {"error": "OpenAI adapter returned an invalid response."}
        import urllib.request

        key = os.getenv(self.openai_key_env, "").strip()
        if not key:
            return {"error": f"{self.openai_key_env} is not configured."}
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body if isinstance(body, Mapping) else {"error": "OpenAI returned a non-object response."}
        except Exception as exc:
            return {"error": str(exc)}

    def _run_openai(self, goal: str, status_callback: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
        observation = self.perception.observe()
        if observation.status != "SUCCESS":
            return WorkerOutcome("UNAVAILABLE", "Desktop observation is unavailable.", final_observation=observation.to_dict(), error=observation.error).to_dict()
        observation_history: list[dict[str, Any]] = [observation.to_dict()]
        image = (
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{observation.image_b64}"}}
            if observation.image_b64 else None
        )
        user_content: list[dict[str, Any]] = [{"type": "text", "text": f"USER GOAL: {goal}\n\n{_observation_prompt(observation)}"}]
        if image:
            user_content.append(image)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": (
                "You are the AEGIS universal desktop planner. Use one bounded tool at a time, "
                "trust only fresh observations, and never claim completion without deterministic evidence. "
                "Request confirmation before deletion, payment, sending, submission, or system changes."
            )},
            {"role": "user", "content": user_content},
        ]
        actions: list[dict[str, Any]] = []
        for step in range(1, self.max_steps + 1):
            stop_status = self._stop_status()
            if stop_status:
                return WorkerOutcome(stop_status, f"Universal GUI task ended with {stop_status.lower()}.", step - 1, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
            if status_callback:
                status_callback(f"desktop step {step}: {self.provider} planning")
            response = self._openai_request({
                "model": self.openai_model,
                "messages": messages,
                "tools": self._openai_tools(),
                "tool_choice": "auto",
                "temperature": 0.1,
            })
            if response.get("error"):
                return WorkerOutcome("FAILED", "ChatGPT/OpenAI planner request failed.", step - 1, observation.to_dict(), tuple(actions), str(response.get("error")), tuple(observation_history)).to_dict()
            choices = response.get("choices") or []
            message = choices[0].get("message") if choices and isinstance(choices[0], Mapping) else None
            if not isinstance(message, Mapping):
                return WorkerOutcome("UNVERIFIED", "ChatGPT/OpenAI returned no usable message.", step - 1, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                verified = _verified_open_goal(goal, observation)
                status = "VERIFIED" if verified else "UNVERIFIED"
                return WorkerOutcome(status, "Observable desktop goal verified." if verified else "Planner stopped without deterministic goal evidence.", step - 1, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
            messages.append(dict(message))
            for call in tool_calls:
                function = call.get("function", {}) if isinstance(call, Mapping) else {}
                name = str(function.get("name", ""))
                raw_args = function.get("arguments", {})
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                except (TypeError, ValueError):
                    args = {}
                if status_callback:
                    status_callback(f"desktop step {step}: {name}")
                result = self._execute_call(name, args)
                actions.append({"action": name, "result": result})
                self._neural_event(goal, self.provider, "action", str(result.get("status", "FAILED")), {"action": name, "step": step})
                result_status = str(result.get("status", "FAILED")).upper()
                if result_status == "BLOCKED":
                    return WorkerOutcome("BLOCKED", result.get("detail", "Action blocked."), step, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()
                observation = self.perception.observe()
                observation_history.append(observation.to_dict())
                if observation.status != "SUCCESS":
                    return WorkerOutcome("UNVERIFIED", "Post-action desktop observation unavailable.", step, observation.to_dict(), tuple(actions), observation.error, tuple(observation_history)).to_dict()
                tool_payload = {"result": result, "observation": observation.to_dict()}
                messages.append({"role": "tool", "tool_call_id": call.get("id", f"call_{step}"), "content": json.dumps(tool_payload, default=str)})
                if observation.image_b64:
                    messages.append({"role": "user", "content": [{"type": "text", "text": "Fresh post-action screenshot:"}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{observation.image_b64}"}}]})
        verified = _verified_open_goal(goal, observation)
        return WorkerOutcome("VERIFIED" if verified else "UNVERIFIED", "Step budget exhausted; final observation evaluated.", self.max_steps, observation.to_dict(), tuple(actions), observations=tuple(observation_history)).to_dict()


__all__ = ["UniversalGUIWorker", "WorkerOutcome"]

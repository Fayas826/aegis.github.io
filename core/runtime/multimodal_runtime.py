"""Portable AEGIS multimodal runtime adapter.

This module is deliberately an adapter, rather than another planner or an
alternative browser implementation.  Browser missions are handed to the
existing :class:`ModelDrivenWebAgent`, which in turn uses the unified browser
runtime and its fresh-page evidence.  Desktop missions are handed to an
injected worker so this module remains usable by the desktop shell, the
backend, and offline tests.

Google's SDK, microphone drivers, and screen-capture libraries are optional.
They are imported only when a live session is explicitly requested.  The
offline goal/verification path therefore works on Linux CI and on machines
without audio hardware or a Gemini key.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Deque, Dict, Mapping, Optional

from core.orchestration.verification_planner import OutcomeStatus, VerificationPlanner


UNIVERSAL_GUI_TOOL_NAME = "execute_universal_gui_task"
MAX_GUI_GOAL_LENGTH = 4000
DEFAULT_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview"


def _import_available(module_name: str) -> bool:
    """Return whether an optional module can actually be imported."""

    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _clean_goal(goal: Any) -> str:
    return goal.strip() if isinstance(goal, str) else ""


@dataclass(frozen=True)
class GeminiConfiguration:
    """Non-secret Gemini configuration used by the adapter."""

    key_env: str = "GEMINI_API_KEY"
    live_model: str = DEFAULT_LIVE_MODEL

    def validate(
        self,
        environ: Optional[Mapping[str, str]] = None,
        *,
        require_live: bool = False,
    ) -> Dict[str, Any]:
        """Validate configuration without ever returning the key value."""

        env = os.environ if environ is None else environ
        raw_key = str(env.get(self.key_env, "") or "").strip()
        placeholder = raw_key.upper() in {"YOUR_GEMINI_API_KEY", "CHANGEME", "NONE", "NULL"}
        key_present = bool(raw_key) and not placeholder
        missing = []
        if not key_present:
            missing.append(self.key_env)
        if not self.live_model.strip():
            missing.append("live_model")

        optional = {
            "google_genai": _import_available("google.genai"),
            "pyaudio": _import_available("pyaudio"),
        }
        if require_live and not optional["google_genai"]:
            missing.append("google.genai")
        status = "READY" if not missing else "UNAVAILABLE"
        return {
            "status": status,
            "key_env": self.key_env,
            "key_configured": key_present,
            "live_model": self.live_model,
            "optional_dependencies": optional,
            "missing": missing,
            "detail": "Gemini live configuration is available." if status == "READY"
            else "Gemini live configuration is incomplete.",
        }


def validate_gemini_configuration(
    environ: Optional[Mapping[str, str]] = None,
    *,
    key_env: str = "GEMINI_API_KEY",
    live_model: str = DEFAULT_LIVE_MODEL,
    require_live: bool = False,
) -> Dict[str, Any]:
    """Public secret-free Gemini configuration check."""

    return GeminiConfiguration(key_env=key_env, live_model=live_model).validate(
        environ, require_live=require_live
    )


def is_gemini_configured(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Small convenience predicate for callers that only need a boolean."""

    return bool(validate_gemini_configuration(environ)["key_configured"])


def universal_gui_tool_contract() -> Dict[str, Any]:
    """Return the bounded, SDK-neutral Gemini function contract.

    Keeping this as plain JSON makes it safe to inspect and test without
    importing ``google-genai``.  The live bridge converts it to SDK types when
    that dependency is present.
    """

    return {
        "function_declarations": [
            {
                "name": UNIVERSAL_GUI_TOOL_NAME,
                "description": (
                    "Delegate one bounded desktop or application GUI goal to the "
                    "authorized AEGIS worker. The worker performs safety checks "
                    "and reports evidence; never claim success from this call alone."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "goal": {
                            "type": "STRING",
                            "description": f"A concrete GUI goal, at most {MAX_GUI_GOAL_LENGTH} characters.",
                        }
                    },
                    "required": ["goal"],
                },
            }
        ]
    }


def validate_gui_goal(goal: Any) -> tuple[Optional[str], Optional[str]]:
    """Return ``(goal, error)`` for the bounded universal GUI contract."""

    normalized = _clean_goal(goal)
    if not normalized:
        return None, "A concrete GUI goal is required."
    if len(normalized) > MAX_GUI_GOAL_LENGTH:
        return None, f"GUI goals are limited to {MAX_GUI_GOAL_LENGTH} characters."
    return normalized, None


@dataclass
class RuntimeResult:
    """JSON-safe outcome envelope shared by browser, desktop, and live paths."""

    status: str
    operation: str
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = {"status": self.status, "operation": self.operation, "detail": self.detail}
        for key, value in self.data.items():
            # Preserve the adapter's terminal status; a worker's raw
            # ``status`` is useful evidence but must not overwrite it.
            result["worker_status" if key == "status" else key] = value
        return result


def _status_from_worker(value: Any) -> Optional[str]:
    """Extract a truthful public status from a worker result."""

    if isinstance(value, Mapping):
        from core.execution.universal_dispatch import execution_status
        status = execution_status(value)
    else:
        status = str(value or "").upper()
    if status in {"BLOCKED", "SAFETY_BLOCK", "SAFETY_BLOCKED", "BLOCKED_BY_CONSENT"}:
        return OutcomeStatus.BLOCKED.value
    if status in {"UNAVAILABLE", "UNCONFIGURED", "NOT_AVAILABLE"}:
        return OutcomeStatus.UNAVAILABLE.value
    if status in {"CLARIFICATION_REQUIRED", "CLARIFY", "AMBIGUOUS"}:
        return OutcomeStatus.CLARIFICATION_REQUIRED.value
    if status in {"VERIFIED", "VERIFIED_SUCCESS"}:
        return OutcomeStatus.VERIFIED.value
    if status in {"FAILED", "ERROR", "CANCELLED", "TIMEOUT", "VERIFICATION_FAILED", "PENDING_CONFIRMATION", "PERMISSION_REQUIRED"}:
        return status
    if status in {"UNVERIFIED", "SUCCESS", "COMPLETED"}:
        return OutcomeStatus.UNVERIFIED.value
    return None


def _result_data(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {"worker_result": value}


class MultimodalRuntimeAdapter:
    """Bridge voice/screen events to existing browser and desktop runtimes."""

    def __init__(
        self,
        *,
        browser_runtime: Any = None,
        browser_agent: Any = None,
        desktop_worker: Any = None,
        screen_capture: Optional[Callable[[], Any]] = None,
        configuration: Optional[GeminiConfiguration] = None,
        live_connector: Optional[Callable[..., Any]] = None,
        max_evidence_age_s: float = 30.0,
    ) -> None:
        self.browser_runtime = browser_runtime
        self.browser_agent = browser_agent
        self.desktop_worker = desktop_worker
        self.screen_capture = screen_capture
        self.configuration = configuration or GeminiConfiguration()
        self.live_connector = live_connector
        self.max_evidence_age_s = max(0.0, float(max_evidence_age_s))
        self.audio_out: Deque[bytes] = deque(maxlen=128)
        self._cancel_event = asyncio.Event()
        self._desktop_contexts: Dict[str, Any] = {}
        self._live_context: Any = None
        self._live_session: Any = None
        self._live_model: str = self.configuration.live_model

    # -- cancellation and barge-in -----------------------------------------

    def cancel(self, reason: str = "cancelled") -> None:
        """Cooperatively cancel the active mission and clear spoken audio."""

        del reason  # the result includes a stable status; callers can log detail
        self._cancel_event.set()
        for context in list(self._desktop_contexts.values()):
            context.cancelled = True
        self.audio_out.clear()

    def barge_in(self) -> Dict[str, Any]:
        """Stop pending playback immediately and request cooperative cancellation."""

        self.cancel("barge-in")
        return {"status": "INTERRUPTED", "audio_cleared": True}

    def reset_cancellation(self) -> None:
        self._cancel_event = asyncio.Event()

    def enqueue_audio(self, pcm_chunk: bytes) -> None:
        if pcm_chunk:
            self.audio_out.append(bytes(pcm_chunk))

    # -- goal dispatch ------------------------------------------------------

    @staticmethod
    def _is_browser_goal(goal: str) -> bool:
        lowered = goal.casefold()
        return (
            "http://" in lowered
            or "https://" in lowered
            or any(word in lowered for word in ("browser", "website", "web page", "search the web", "look up online"))
        )

    def _planner_clarification(self, goal: str) -> Optional[RuntimeResult]:
        plan = VerificationPlanner.plan(goal)
        if plan.is_ambiguous:
            plan_data = asdict(plan) if is_dataclass(plan) else plan
            return RuntimeResult(
                OutcomeStatus.CLARIFICATION_REQUIRED.value,
                "goal",
                plan.clarification or "Please specify an observable goal.",
                {"goal": goal, "verification_plan": plan_data},
            )
        return None

    async def execute_goal(
        self,
        goal: Any,
        *,
        surface: Optional[str] = None,
        headless: bool = False,
        max_steps: int = 8,
        task_context: Any = None,
    ) -> Dict[str, Any]:
        normalized, error = validate_gui_goal(goal)
        if error:
            return RuntimeResult(OutcomeStatus.CLARIFICATION_REQUIRED.value, "goal", error).to_dict()
        chosen = (surface or ("browser" if self._is_browser_goal(normalized) else "desktop")).casefold()
        if chosen in {"browser", "web"}:
            return await self.execute_browser_goal(normalized, headless=headless, max_steps=max_steps)
        if chosen in {"desktop", "gui", "voice"}:
            return await self.execute_desktop_goal(normalized, task_context=task_context, max_steps=max_steps)
        return RuntimeResult(OutcomeStatus.CLARIFICATION_REQUIRED.value, "goal", "Unknown execution surface.").to_dict()

    async def execute_browser_goal(
        self, goal: str, *, headless: bool = False, max_steps: int = 8
    ) -> Dict[str, Any]:
        """Run a browser mission through the existing web agent/runtime only."""

        normalized, error = validate_gui_goal(goal)
        if error:
            return RuntimeResult(OutcomeStatus.CLARIFICATION_REQUIRED.value, "browser", error).to_dict()
        clarification = self._planner_clarification(normalized)
        if clarification is not None:
            return clarification.to_dict()
        if self._cancel_event.is_set():
            return RuntimeResult(OutcomeStatus.UNVERIFIED.value, "browser", "Browser goal was cancelled.").to_dict()

        agent = self.browser_agent
        if agent is None:
            try:
                if os.getenv("AEGIS_BROWSER_AGENT", "").casefold() in {"gemini", "gemini-computer-use", "computer-use"}:
                    from core.web.gemini_computer_use_agent import GeminiComputerUseBrowserAgent
                    runtime = self.browser_runtime
                    if runtime is None:
                        from core.web.unified_browser_runtime import unified_browser_runtime
                        runtime = unified_browser_runtime
                    init = await runtime.initialize(headless=headless)
                    if init.get("status") not in {"LAUNCHED", "CONNECTED", "REUSED"}:
                        return RuntimeResult(OutcomeStatus.UNAVAILABLE.value, "browser", "Browser runtime could not initialize.", {"initialization": init}).to_dict()
                    agent = GeminiComputerUseBrowserAgent(browser_runtime=runtime, max_steps=max_steps)
                else:
                    from core.web.model_driven_web_agent import ModelDrivenWebAgent
                    agent = ModelDrivenWebAgent()
                    if self.browser_runtime is not None:
                        agent.browser_runtime = self.browser_runtime
                self.browser_agent = agent
            except Exception as exc:
                return RuntimeResult(OutcomeStatus.UNAVAILABLE.value, "browser", f"Browser agent unavailable: {exc}").to_dict()

        try:
            report = await agent.execute_task(normalized, headless=headless, max_steps=max_steps)
        except asyncio.CancelledError:
            return RuntimeResult(OutcomeStatus.UNVERIFIED.value, "browser", "Browser goal was cancelled.").to_dict()
        except Exception as exc:
            return RuntimeResult(OutcomeStatus.UNVERIFIED.value, "browser", f"Browser mission failed: {exc}").to_dict()

        data = _result_data(report)
        status = _status_from_worker(data) or OutcomeStatus.UNVERIFIED.value
        if status == OutcomeStatus.VERIFIED.value:
            # The web agent is the source of truth for page evidence.  Require
            # its evidence artifact, and reject explicitly stale injected data.
            evidence = data.get("evidence_screenshot") or data.get("evidence")
            if not evidence or not os.path.exists(str(evidence)):
                status = OutcomeStatus.UNVERIFIED.value
                data["verification_detail"] = "Verified status lacked a preserved evidence artifact."
            observation = data.get("final_observation") or data.get("observation")
            if isinstance(observation, Mapping):
                stamp = observation.get("observed_at", observation.get("timestamp"))
                if isinstance(stamp, (int, float)) and time.time() - float(stamp) > self.max_evidence_age_s:
                    status = OutcomeStatus.UNVERIFIED.value
                    data["verification_detail"] = "Final page evidence is stale."
        data["adapter_status"] = status
        return RuntimeResult(status, "browser", data.get("error", "Browser mission evaluated."), data).to_dict()

    async def execute_desktop_goal(self, goal: str, *, task_context: Any = None, max_steps: int = 20) -> Dict[str, Any]:
        """Dispatch a universal GUI goal through the injected authorized worker."""

        if self._cancel_event.is_set():
            return RuntimeResult("CANCELLED", "desktop", "Desktop goal was cancelled before dispatch.").to_dict()
        normalized, error = validate_gui_goal(goal)
        if error:
            return RuntimeResult("CLARIFICATION_REQUIRED", "desktop", error).to_dict()
        from core.orchestration.agent_contract import TaskContext
        context = task_context or TaskContext(goal=normalized, max_steps=max_steps)
        if self.desktop_worker is None:
            try:
                from core.runtime.universal_gui_worker import UniversalGUIWorker

                self.desktop_worker = UniversalGUIWorker(max_steps=40, cancel_check=lambda: self._cancel_event.is_set())
            except Exception as exc:
                return RuntimeResult(OutcomeStatus.UNAVAILABLE.value, "desktop", f"Universal desktop worker unavailable: {exc}").to_dict()
        worker = self.desktop_worker

        def invoke() -> Any:
            callback = lambda message: None
            method = getattr(worker, "run_task", worker)
            # Inspect before dispatch. A TypeError from *inside* a worker must
            # never trigger a second run and duplicate real desktop input.
            signature = inspect.signature(method)
            kwargs = {}
            if "status_callback" in signature.parameters:
                kwargs["status_callback"] = callback
            if "task_context" in signature.parameters:
                kwargs["task_context"] = context
            return method(normalized, **kwargs)

        self._desktop_contexts[context.task_id] = context
        try:
            outcome = await asyncio.to_thread(invoke)
        except asyncio.CancelledError:
            context.cancelled = True
            self.cancel()
            # to_thread cannot kill an input backend; worker cooperatively
            # stops before the next action and retains its desktop lease.
            raise
        except Exception as exc:
            return RuntimeResult("FAILED", "desktop", f"Desktop worker failed: {type(exc).__name__}").to_dict()
        finally:
            self._desktop_contexts.pop(context.task_id, None)

        data = _result_data(outcome)
        status = _status_from_worker(outcome)
        # A worker's plain SUCCESS/COMPLETED is execution evidence only.  It
        # cannot become VERIFIED until the worker supplies verification_status.
        if status is None:
            status = OutcomeStatus.UNVERIFIED.value
        if status == OutcomeStatus.VERIFIED.value and not (
            data.get("verification_status") or data.get("verified") is True
        ):
            status = OutcomeStatus.UNVERIFIED.value
        data["adapter_status"] = status
        return RuntimeResult(status, "desktop", "Desktop worker outcome evaluated.", data).to_dict()

    # -- optional Gemini Live bridge ----------------------------------------

    def live_capabilities(self) -> Dict[str, Any]:
        return {
            "google_genai": _import_available("google.genai"),
            "pyaudio": _import_available("pyaudio"),
            "screen_capture": self.screen_capture is not None,
        }

    @staticmethod
    def _sdk_tool(types_module: Any) -> Any:
        """Convert the plain contract into google-genai types when possible."""

        declaration = universal_gui_tool_contract()["function_declarations"][0]
        try:
            schema = types_module.Schema(
                type="OBJECT",
                properties={"goal": types_module.Schema(type="STRING", description=declaration["parameters"]["properties"]["goal"]["description"])},
                required=["goal"],
            )
            return types_module.Tool(function_declarations=[types_module.FunctionDeclaration(
                name=declaration["name"], description=declaration["description"], parameters=schema
            )])
        except Exception:
            return universal_gui_tool_contract()

    async def start_live(
        self,
        *,
        client: Any = None,
        session: Any = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start an optional Gemini Live session, without requiring it at import time."""

        self._live_model = model or self.configuration.live_model
        if session is not None:
            self._live_session = session
            return {"status": "READY", "live": True, "model": self._live_model}
        # An injected client/connector is intentionally testable without the
        # optional google-genai package.  The package is required only when the
        # adapter has to construct the client itself.
        config = self.configuration.validate(
            require_live=client is None and self.live_connector is None
        )
        injected_transport = client is not None or self.live_connector is not None
        if config["status"] != "READY" and not (
            injected_transport and config.get("missing") == [self.configuration.key_env]
        ):
            return {"status": "UNAVAILABLE", "live": False, "configuration": config}
        try:
            if client is None:
                genai = importlib.import_module("google.genai")
                client = genai.Client(api_key=os.environ.get(self.configuration.key_env, ""))
            connect = self.live_connector or client.aio.live.connect
            try:
                types_module = importlib.import_module("google.genai.types")
                live_config = types_module.LiveConnectConfig(
                    response_modalities=["AUDIO"], tools=[self._sdk_tool(types_module)]
                )
            except Exception:
                live_config = {"response_modalities": ["AUDIO"], "tools": [universal_gui_tool_contract()]}
            context = connect(model=self._live_model, config=live_config)
            self._live_context = context
            self._live_session = await context.__aenter__() if hasattr(context, "__aenter__") else context
            return {"status": "READY", "live": True, "model": self._live_model}
        except Exception as exc:
            self._live_context = None
            self._live_session = None
            return {"status": "UNAVAILABLE", "live": False, "detail": f"Gemini Live unavailable: {exc}"}

    async def stop_live(self) -> Dict[str, Any]:
        try:
            if self._live_context is not None and hasattr(self._live_context, "__aexit__"):
                await self._live_context.__aexit__(None, None, None)
        except Exception:
            pass
        self._live_context = None
        self._live_session = None
        self.audio_out.clear()
        return {"status": "STOPPED", "live": False}

    async def _send_live(self, payload: Any) -> bool:
        if self._live_session is None:
            return False
        result = self._live_session.send(input=payload, end_of_turn=False)
        if inspect.isawaitable(result):
            await result
        return True

    async def send_audio_chunk(self, pcm: bytes, *, mime_type: str = "audio/pcm") -> Dict[str, Any]:
        if not await self._send_live({"data": bytes(pcm), "mime_type": mime_type}):
            return {"status": "UNAVAILABLE", "detail": "Gemini Live session is not active."}
        return {"status": "SENT", "kind": "audio"}

    async def send_screen_frame(self, frame: bytes, *, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        if not await self._send_live({"data": bytes(frame), "mime_type": mime_type}):
            return {"status": "UNAVAILABLE", "detail": "Gemini Live session is not active."}
        return {"status": "SENT", "kind": "screen"}

    async def _send_tool_result(self, function_call: Any, result: Dict[str, Any]) -> None:
        """Acknowledge a Live tool call so the model can continue its turn."""

        if self._live_session is None:
            return
        name = getattr(function_call, "name", UNIVERSAL_GUI_TOOL_NAME)
        call_id = getattr(function_call, "id", None)
        payload: Any
        try:
            types_module = importlib.import_module("google.genai.types")
            response = types_module.LiveClientToolResponse(
                function_responses=[types_module.FunctionResponse(
                    name=name, id=call_id, response={"status": result.get("status"), "result": result}
                )]
            )
        except Exception:
            payload = {"function_responses": [{"name": name, "id": call_id, "response": result}]}
        else:
            payload = response
        sent = self._live_session.send(input=payload)
        if inspect.isawaitable(sent):
            await sent

    async def iter_live_events(self, session: Any = None) -> AsyncIterator[Dict[str, Any]]:
        """Yield audio/events and dispatch model GUI calls through the worker."""

        active = session or self._live_session
        if active is None:
            yield {"status": "UNAVAILABLE", "detail": "Gemini Live session is not active."}
            return
        try:
            async for response in active.receive():
                server = getattr(response, "server_content", None)
                if server is not None and getattr(server, "interrupted", False):
                    self.barge_in()
                    yield {"status": "INTERRUPTED", "kind": "barge_in"}
                turn = getattr(server, "model_turn", None) if server is not None else None
                if turn is not None:
                    for part in getattr(turn, "parts", ()) or ():
                        inline = getattr(part, "inline_data", None)
                        if inline is not None and getattr(inline, "data", None):
                            self.enqueue_audio(inline.data)
                            yield {"status": "AUDIO", "data": inline.data}
                tool_call = getattr(response, "tool_call", None)
                for function_call in getattr(tool_call, "function_calls", ()) if tool_call else ():
                    if getattr(function_call, "name", "") != UNIVERSAL_GUI_TOOL_NAME:
                        continue
                    args = getattr(function_call, "args", {}) or {}
                    result = await self.execute_desktop_goal(args.get("goal", ""))
                    await self._send_tool_result(function_call, result)
                    yield {"status": result["status"], "kind": "tool_result", "result": result}
        except asyncio.CancelledError:
            self.cancel("live receive cancelled")
            yield {"status": "UNVERIFIED", "detail": "Live receive cancelled."}


__all__ = [
    "DEFAULT_LIVE_MODEL",
    "GeminiConfiguration",
    "MAX_GUI_GOAL_LENGTH",
    "MultimodalRuntimeAdapter",
    "RuntimeResult",
    "UNIVERSAL_GUI_TOOL_NAME",
    "is_gemini_configured",
    "universal_gui_tool_contract",
    "validate_gemini_configuration",
    "validate_gui_goal",
]

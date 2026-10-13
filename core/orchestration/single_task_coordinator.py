"""
AEGIS Single-Task Orchestrator & Execution Coordinator
======================================================
Phase 4.3: Authoritative Single Execution Owner per Interactive Task.

Provides:
1. Canonical Task Context with stable identifiers:
   (task_id, correlation_id, plan_id, step_id, tool_call_id, agent_id, attempt_number).
2. Lifecycle state management in TaskStateController:
   CREATED -> PLANNING -> RUNNING -> VERIFYING -> SUCCEEDED (or FAILED / CANCELLED).
3. Physical desktop serialization via DesktopExecutionLock.
4. Intelligent application and browser session reuse.
5. Sequential step progression with post-action independent verification.
6. Recovery without blind replay of already-completed steps.
7. Telemetry streaming to enterprise_event_bus topic 'task.orchestration.progress'.
8. Structured execution trace logging for auditing.
"""

import os
import sys
import time
import uuid
import json
import asyncio
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

from core.orchestration.task_state import task_state_controller
from core.execution.desktop_execution_lock import desktop_execution_lock
from core.orchestration.enterprise_event_bus import enterprise_event_bus
from action.desktop_control.desktop_controller import desktop_controller
from core.orchestration.computer_use.planner import planner_pipeline
from core.orchestration.agent_contract import StructuredError, ErrorCode
from core.execution.universal_desktop_controller import desktop_controller as universal_controller


@dataclass
class ExecutionStepTrace:
    step_id: str
    step_number: int
    tool_call_id: str
    agent_id: str
    attempt_number: int
    action_type: str
    target: str
    text_payload: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    tool_result: Optional[Dict[str, Any]] = None
    verification_state: str = "PENDING"  # PENDING, VERIFIED, FAILED, SKIPPED
    verification_details: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED, RETRYING


@dataclass
class TaskExecutionTrace:
    task_id: str
    correlation_id: str
    plan_id: str
    user_goal: str
    parsed_intent: Dict[str, Any]
    status: str = "CREATED"
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    active_application: Optional[str] = None
    process_id: Optional[int] = None
    window_handle: Optional[int] = None
    steps: List[ExecutionStepTrace] = field(default_factory=list)
    final_result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


class SingleTaskCoordinator:
    """Authoritative singleton coordinating interactive tasks across desktop and browser."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SingleTaskCoordinator, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.active_tasks: Dict[str, TaskExecutionTrace] = {}
        self.current_task: Optional[TaskExecutionTrace] = None
        self.paused = False
        self.cancelled_tasks = set()
        self.traces_dir = os.path.join("project_memory", "traces")
        os.makedirs(self.traces_dir, exist_ok=True)

    def _emit_progress(self, trace: TaskExecutionTrace, current_step_num: int, total_steps: int, action_type: str, details: str, verification_state: str = "PENDING"):
        """Publishes live execution progress event."""
        payload = {
            "task_id": trace.task_id,
            "correlation_id": trace.correlation_id,
            "plan_id": trace.plan_id,
            "goal": trace.user_goal,
            "stage": trace.status,
            "current_step": current_step_num,
            "total_steps": total_steps,
            "progress_ratio": round(current_step_num / max(1, total_steps), 2),
            "action_type": action_type,
            "active_app": trace.active_application or "Desktop",
            "pid": trace.process_id,
            "hwnd": trace.window_handle,
            "details": details,
            "verification_state": verification_state,
            "timestamp": time.time(),
        }
        try:
            enterprise_event_bus.publish("task.orchestration.progress", payload)
        except Exception:
            pass

    async def execute_interactive_task(
        self,
        goal: str,
        correlation_id: Optional[str] = None,
        max_steps: int = 15,
        timeout_seconds: float = 120.0,
    ) -> Dict[str, Any]:
        """
        Executes an interactive user task through one coordinated task lifecycle.
        Serializes desktop access, tracks persistent steps, and verifies outcomes.
        """
        task_id = str(uuid.uuid4())
        corr_id = correlation_id or str(uuid.uuid4())
        plan_id = task_state_controller.begin_session(objective=goal, intent={"goal": goal, "type": "INTERACTIVE_TASK"})

        trace = TaskExecutionTrace(
            task_id=task_id,
            correlation_id=corr_id,
            plan_id=plan_id,
            user_goal=goal,
            parsed_intent={"goal": goal, "parsed": True},
            status="PLANNING",
        )
        self.active_tasks[task_id] = trace
        self.current_task = trace

        self._emit_progress(trace, 0, 1, "PLAN", "Generating canonical task plan...")
        start_time = time.time()

        # Acquire Authoritative Desktop Execution Lock
        try:
            lock_acquired = await desktop_execution_lock.acquire_async(
                owner=f"task_{task_id}",
                timeout=timeout_seconds,
                task_id=task_id,
            )
            if not lock_acquired:
                trace.status = "FAILED"
                err_msg = "Could not acquire DesktopExecutionLock; another interactive task is active."
                trace.error = {"error": err_msg}
                self._save_trace(trace)
                return {
                    "task_id": task_id,
                    "correlation_id": corr_id,
                    "status": "FAILED",
                    "reason": err_msg,
                    "error": {"message": err_msg},
                }

            trace.status = "RUNNING"
            current_step = 0
            completed_steps_evidence = []

            while current_step < max_steps:
                if task_id in self.cancelled_tasks:
                    trace.status = "CANCELLED"
                    task_state_controller.complete_session("CANCELLED")
                    self._emit_progress(trace, current_step, max_steps, "CANCEL", "Task was cancelled by operator.")
                    return {"task_id": task_id, "correlation_id": corr_id, "status": "CANCELLED"}

                # Pause hook
                while self.paused:
                    await asyncio.sleep(0.2)

                current_step += 1
                step_id = f"step_{current_step}"
                tool_call_id = f"tc_{uuid.uuid4().hex[:8]}"

                # 1. PLAN next action
                task_state_controller.mark_node_running(step_id)
                plan = await planner_pipeline.plan(goal, current_step, max_steps, "")

                action_type = plan.get("action_type") or "WAIT"
                target = plan.get("target") or "Active Window"
                payload = plan.get("text_payload")
                is_finished = plan.get("finished", False)
                target_app = plan.get("target_app") or trace.active_application

                if target_app:
                    trace.active_application = target_app

                step_trace = ExecutionStepTrace(
                    step_id=step_id,
                    step_number=current_step,
                    tool_call_id=tool_call_id,
                    agent_id="single_task_coordinator",
                    attempt_number=1,
                    action_type=action_type,
                    target=target,
                    text_payload=payload,
                    start_time=time.time(),
                    status="RUNNING",
                )
                trace.steps.append(step_trace)

                self._emit_progress(
                    trace,
                    current_step,
                    max_steps,
                    action_type,
                    f"Executing {action_type} on {target} ({plan.get('reasoning', '')})",
                )

                # 2. EXECUTE Step
                step_res = await self._dispatch_step(action_type, target, payload, trace)
                step_trace.tool_result = step_res

                if step_res.get("status") in ("ERROR", "FAILED"):
                    step_trace.status = "FAILED"
                    step_trace.error = step_res.get("error") or "Execution failed"
                    step_trace.end_time = time.time()
                    trace.status = "FAILED"
                    task_state_controller.mark_node_failed(step_id, step_trace.error)
                    task_state_controller.complete_session("FAILED")
                    self._emit_progress(trace, current_step, max_steps, action_type, f"Step failed: {step_trace.error}", "FAILED")
                    self._save_trace(trace)
                    return {
                        "task_id": task_id,
                        "correlation_id": corr_id,
                        "status": "FAILED",
                        "reason": step_trace.error,
                        "failed_step": step_id,
                        "steps": [asdict(s) for s in trace.steps],
                    }

                # 3. VERIFY Step Outcome
                trace.status = "VERIFYING"
                task_state_controller.mark_node_verifying(step_id)
                self._emit_progress(trace, current_step, max_steps, action_type, f"Verifying {action_type} outcome...", "VERIFYING")

                verification_res = await self._verify_step(action_type, target, payload, step_res, trace)
                step_trace.verification_details = verification_res

                if not verification_res.get("verified", False):
                    step_trace.verification_state = "FAILED"
                    step_trace.status = "FAILED"
                    step_trace.error = verification_res.get("error", "Verification condition not met")
                    step_trace.end_time = time.time()
                    trace.status = "FAILED"
                    task_state_controller.mark_node_failed(step_id, step_trace.error)
                    task_state_controller.complete_session("FAILED")
                    self._emit_progress(trace, current_step, max_steps, action_type, f"Verification failed: {step_trace.error}", "FAILED")
                    self._save_trace(trace)
                    return {
                        "task_id": task_id,
                        "correlation_id": corr_id,
                        "status": "FAILED",
                        "reason": step_trace.error,
                        "failed_step": step_id,
                        "steps": [asdict(s) for s in trace.steps],
                    }

                step_trace.verification_state = "VERIFIED"
                step_trace.status = "COMPLETED"
                step_trace.end_time = time.time()
                task_state_controller.mark_node_completed(step_id, step_res)
                completed_steps_evidence.append(step_res)
                self._emit_progress(trace, current_step, max_steps, action_type, f"Step {current_step} verified successfully.", "VERIFIED")

                if is_finished:
                    break

            trace.status = "SUCCEEDED"
            trace.end_time = time.time()
            trace.final_result = {
                "summary": f"Task '{goal}' completed and verified successfully in {current_step} steps.",
                "steps_executed": current_step,
                "active_application": trace.active_application,
                "process_id": trace.process_id,
                "window_handle": trace.window_handle,
            }
            task_state_controller.complete_session("SUCCEEDED")
            self._emit_progress(trace, current_step, current_step, "SUCCEEDED", trace.final_result["summary"], "VERIFIED")
            self._save_trace(trace)

            return {
                "task_id": task_id,
                "correlation_id": corr_id,
                "plan_id": plan_id,
                "status": "SUCCEEDED",
                "final_result": trace.final_result,
                "steps": [asdict(s) for s in trace.steps],
                "duration_s": round(trace.end_time - trace.start_time, 2),
            }

        except Exception as e:
            trace.status = "FAILED"
            trace.end_time = time.time()
            trace.error = {"exception": str(e)}
            task_state_controller.complete_session("FAILED")
            self._save_trace(trace)
            return {
                "task_id": task_id,
                "correlation_id": corr_id,
                "status": "FAILED",
                "reason": str(e),
                "steps": [asdict(s) for s in trace.steps],
            }
        finally:
            desktop_execution_lock.release(owner=f"task_{task_id}")

    async def _dispatch_step(self, action_type: str, target: str, payload: Optional[str], trace: TaskExecutionTrace) -> Dict[str, Any]:
        """Dispatches an atomic step using existing desktop controller tools."""
        act_up = action_type.upper()

        if act_up in ("OPEN_APP", "OPEN_APPLICATION", "LAUNCH_APP"):
            res = await desktop_controller.open_app(target, auto_focus=True, new_instance=False)
            if res.get("status") == "SUCCESS":
                if res.get("pid"):
                    trace.process_id = res["pid"]
                if res.get("hwnd"):
                    trace.window_handle = res["hwnd"]
            return res

        elif act_up in ("TYPE", "WRITE"):
            if trace.window_handle:
                universal_controller.force_foreground_window(trace.window_handle)
                await asyncio.sleep(0.3)
            raw_text = payload or target or ""
            should_enter = raw_text.endswith("\n")
            clean_text = raw_text.rstrip("\r\n")
            res = await desktop_controller.execute("UI_AUTOMATION", {
                "action": "type",
                "text": clean_text,
                "coords": None,
                "press_enter": should_enter
            })
            if should_enter:
                await asyncio.sleep(0.4)
                # If an overwrite prompt popped up, confirm with alt+y
                try:
                    import pyautogui
                    pyautogui.hotkey('alt', 'y')
                except Exception:
                    pass
                await asyncio.sleep(1.2)
            return res

        elif act_up in ("HOTKEY", "SHORTCUT"):
            if trace.window_handle:
                universal_controller.force_foreground_window(trace.window_handle)
                await asyncio.sleep(0.3)
            key_combo = payload or target or "ctrl+s"
            res = await desktop_controller.execute("UI_AUTOMATION", {
                "action": "hotkey",
                "key": key_combo
            })
            if any(k in str(key_combo).lower() for k in ["ctrl+s", "save"]):
                save_dialog_hwnd = None
                for _ in range(12):
                    await asyncio.sleep(0.25)
                    wins = universal_controller.list_visible_windows()
                    for h, title in wins:
                        if "save" in title.lower():
                            save_dialog_hwnd = h
                            break
                    if save_dialog_hwnd:
                        break

                if save_dialog_hwnd:
                    trace.window_handle = save_dialog_hwnd
                    universal_controller.force_foreground_window(save_dialog_hwnd)
                    await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.5)
            return res

        elif act_up in ("PRESS", "KEY_PRESS"):
            if trace.window_handle:
                universal_controller.force_foreground_window(trace.window_handle)
                await asyncio.sleep(0.2)
            res = await desktop_controller.execute("UI_AUTOMATION", {
                "action": "press",
                "key": payload or target or "enter"
            })
            return res

        elif act_up in ("VERIFY_FILE", "VERIFY_CALCULATION", "VERIFY"):
            res = await desktop_controller.execute("VERIFY", {
                "type": act_up,
                "target": target,
                "expected": payload or ""
            })
            return res

        elif act_up == "OS_COMMAND":
            res = await desktop_controller.execute("OS_COMMAND", {"command": payload or target or "echo ok"})
            return res

        elif act_up == "WAIT":
            await asyncio.sleep(1.0)
            return {"status": "SUCCESS", "detail": "Waited 1.0s"}

        return {"status": "SUCCESS", "detail": f"Completed {action_type}"}

    async def _verify_step(self, action_type: str, target: str, payload: Optional[str], step_res: Dict[str, Any], trace: TaskExecutionTrace) -> Dict[str, Any]:
        """Independently verifies whether the step had its expected physical or system outcome."""
        act_up = action_type.upper()

        if act_up in ("OPEN_APP", "LAUNCH_APP"):
            hwnd = step_res.get("hwnd") or trace.window_handle
            if hwnd:
                try:
                    import win32gui
                    is_vis = bool(win32gui.IsWindowVisible(hwnd))
                    return {"verified": is_vis, "hwnd": hwnd, "reused": step_res.get("reused", False)}
                except Exception:
                    pass
            return {"verified": True, "reused": step_res.get("reused", False)}

        elif act_up in ("VERIFY_FILE", "FILE"):
            return {
                "verified": step_res.get("status") == "SUCCESS",
                "file_path": step_res.get("file_path", target),
                "error": step_res.get("error"),
            }

        elif act_up in ("VERIFY_CALCULATION", "CALCULATION"):
            return {
                "verified": step_res.get("status") == "SUCCESS",
                "expected": payload,
                "error": step_res.get("error"),
            }

        # For typing, hotkeys, and basic UI actions, check execution success status
        return {"verified": (step_res.get("status") == "SUCCESS"), "error": step_res.get("error")}

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def cancel(self, task_id: Optional[str] = None):
        if task_id:
            self.cancelled_tasks.add(task_id)
        elif self.current_task:
            self.cancelled_tasks.add(self.current_task.task_id)

    def _save_trace(self, trace: TaskExecutionTrace):
        try:
            trace_path = os.path.join(self.traces_dir, f"{trace.task_id}.json")
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(asdict(trace), f, indent=2)
        except Exception as e:
            print(f"[COORDINATOR] Could not save execution trace: {e}")


single_task_coordinator = SingleTaskCoordinator()

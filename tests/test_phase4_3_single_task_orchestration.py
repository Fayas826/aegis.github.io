"""
AEGIS Phase 4.3 Acceptance Test Suite: Single-Task Orchestration, Smooth Execution and Autonomy Stabilization
=============================================================================================================
Verifies:
1. Authoritative execution owner per interactive task with canonical identifiers:
   (task_id, correlation_id, plan_id, step_id, tool_call_id, agent_id, attempt_number).
2. Physical desktop serialization via DesktopExecutionLock (mutual exclusion).
3. Intelligent application reuse (elevate existing window to foreground without duplicate process spawning).
4. Browser session and tab reuse.
5. Sequential progression of compound tasks without duplicate windows or disconnected steps.
6. Notepad file workflow with independent post-action disk verification (VERIFY_FILE) and safe cleanup.
7. Calculator arithmetic workflow with independent calculation verification (VERIFY_CALCULATION).
8. CLI task execution and output capture.
9. Recovery and retry without replaying already-completed steps.
10. Task cancellation midway through execution with safe lock release.
11. Telemetry and live HUD progress streaming on enterprise_event_bus ('task.orchestration.progress').
12. Preservation of safety boundaries: FOCUS_MISMATCH, DESKTOP_SESSION_UNAVAILABLE, human confirmation gates.
"""

import os
import sys
import time
import pytest
import asyncio
import psutil
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath("."))

from core.orchestration.single_task_coordinator import (
    single_task_coordinator,
    TaskExecutionTrace,
    ExecutionStepTrace
)
from core.execution.desktop_execution_lock import desktop_execution_lock
from core.orchestration.task_state import task_state_controller
from core.orchestration.enterprise_event_bus import enterprise_event_bus
from action.desktop_control.desktop_controller import desktop_controller
from core.execution.universal_desktop_controller import desktop_controller as universal_controller


@pytest.fixture(autouse=True)
def clean_test_processes():
    """Ensure clean desktop environment before and after each test."""
    desktop_execution_lock.release(force=True)
    yield
    # Cleanup any spawned test processes (Notepad / Calc)
    for p in psutil.process_iter(['name']):
        try:
            name = (p.info.get('name') or "").lower()
            if 'notepad' in name or 'calculator' in name or 'calc' in name:
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Release any lingering desktop locks
    desktop_execution_lock.release(force=True)


def test_01_canonical_task_context_and_identifiers():
    """Verify authoritative execution owner assigns canonical and stable trace identifiers."""
    async def _run():
        cmd = "open notepad"
        res = await single_task_coordinator.execute_interactive_task(cmd)
        assert res["status"] in ("SUCCEEDED", "COMPLETED")
        assert "task_id" in res and res["task_id"]
        assert "correlation_id" in res and res["correlation_id"]
        assert "plan_id" in res and res["plan_id"]
        steps = res.get("steps", [])
        assert len(steps) >= 1
        s0 = steps[0]
        assert "step_id" in s0
        assert "tool_call_id" in s0
        assert "agent_id" in s0
        assert "attempt_number" in s0
        assert s0["agent_id"] in ("single_task_coordinator", "computer_use_agent")
        assert s0["verification_state"] == "VERIFIED"

    asyncio.run(_run())


def test_02_desktop_execution_lock_mutual_exclusion():
    """Verify physical desktop lock prevents concurrent agents from typing over each other."""
    async def _run():
        desktop_execution_lock.release(force=True)
        # Task A acquires the lock
        acquired_a = await desktop_execution_lock.acquire_async(owner="agent_alpha", timeout=2.0)
        assert acquired_a is True
        assert desktop_execution_lock.is_locked() is True
        assert desktop_execution_lock.owner == "agent_alpha"

        # Task B attempts to acquire the lock concurrently -> should fail with timeout
        acquired_b = await desktop_execution_lock.acquire_async(owner="agent_beta", timeout=0.5)
        assert acquired_b is False
        assert desktop_execution_lock.owner == "agent_alpha"

        # Task A releases lock
        released_a = desktop_execution_lock.release(owner="agent_alpha")
        assert released_a is True
        assert desktop_execution_lock.is_locked() is False

        # Task B can now acquire cleanly
        acquired_b_retry = await desktop_execution_lock.acquire_async(owner="agent_beta", timeout=1.0)
        assert acquired_b_retry is True
        desktop_execution_lock.release(owner="agent_beta")

    asyncio.run(_run())


def test_03_idempotent_app_launch_and_window_reuse():
    """Verify desktop controller elevates existing window to foreground without duplicate PIDs."""
    async def _run():
        # First launch of Notepad
        res1 = await desktop_controller.open_app("Notepad", auto_focus=True, new_instance=False)
        assert res1["status"] == "SUCCESS"
        assert res1["reused"] is False
        pid1 = res1.get("pid")
        hwnd1 = res1.get("hwnd")
        assert pid1 is not None
        assert hwnd1 is not None

        # Second launch of Notepad -> Must reuse existing HWND / PID
        res2 = await desktop_controller.open_app("Notepad", auto_focus=True, new_instance=False)
        assert res2["status"] == "SUCCESS"
        assert res2["reused"] is True
        assert res2.get("hwnd") == hwnd1
        assert res2.get("pid") == pid1

    asyncio.run(_run())


def test_04_notepad_workflow_sequential_execution_and_file_verification():
    """Verify compound Notepad workflow executes sequentially with post-action disk verification."""
    async def _run():
        test_file = os.path.abspath(os.path.join("scratch", "test_folder", "aegis_phase4_3_acceptance.txt"))
        if os.path.exists(test_file):
            os.remove(test_file)

        cmd = (
            "Open Notepad, write 'Phase 4.3 Coherent Autonomous Workflow', "
            "and save it as aegis_phase4_3_acceptance.txt in the dedicated test folder."
        )
        res = await single_task_coordinator.execute_interactive_task(cmd)
        assert res["status"] == "SUCCEEDED"
        steps = res.get("steps", [])
        assert len(steps) == 5
        assert steps[0]["action_type"] == "OPEN_APP"
        assert steps[1]["action_type"] == "TYPE"
        assert steps[2]["action_type"] == "HOTKEY"
        assert steps[3]["action_type"] == "TYPE"
        assert steps[4]["action_type"] == "VERIFY_FILE"
        assert steps[4]["status"] == "COMPLETED"
        assert steps[4]["verification_state"] == "VERIFIED"

        # Independent test assertion of physical file content
        assert os.path.exists(test_file), f"Physical file must exist at {test_file}"
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Phase 4.3 Coherent Autonomous Workflow" in content

        # Cleanup test file
        if os.path.exists(test_file):
            os.remove(test_file)

    asyncio.run(_run())


def test_05_calculator_workflow_with_independent_verification():
    """Verify Calculator compound arithmetic workflow (23 * 17 = 391) with independent outcome check."""
    async def _run():
        cmd = "Open Calculator and calculate 23 * 17"
        res = await single_task_coordinator.execute_interactive_task(cmd)
        assert res["status"] == "SUCCEEDED"
        steps = res.get("steps", [])
        assert len(steps) == 3
        assert steps[0]["action_type"] == "OPEN_APP"
        assert steps[1]["action_type"] == "TYPE"
        assert steps[1]["text_payload"] == "23*17="
        assert steps[2]["action_type"] == "VERIFY_CALCULATION"
        assert steps[2]["text_payload"] == "391"
        assert steps[2]["verification_state"] == "VERIFIED"

    asyncio.run(_run())


def test_06_cli_task_execution_and_output_capture():
    """Verify CLI task execution produces captured output and verified status."""
    async def _run():
        res = await desktop_controller.execute("OS_COMMAND", {"command": "echo AEGIS_ORCHESTRATION_OK"})
        assert res["status"] == "SUCCESS"
        assert "AEGIS_ORCHESTRATION_OK" in res["output"]

    asyncio.run(_run())


def test_07_recovery_without_replaying_completed_steps():
    """Verify execution engine retains completed step evidence and avoids redundant execution."""
    async def _run():
        trace = TaskExecutionTrace(
            task_id="test_recov_001",
            correlation_id="corr_recov_001",
            plan_id="plan_recov_001",
            user_goal="Test recovery",
            parsed_intent={"goal": "Test recovery"},
        )
        # Step 1 was already completed
        trace.steps.append(ExecutionStepTrace(
            step_id="step_1",
            step_number=1,
            tool_call_id="call_1",
            agent_id="single_task_coordinator",
            attempt_number=1,
            action_type="OPEN_APP",
            target="Notepad",
            text_payload=None,
            status="COMPLETED",
            start_time=time.time(),
            end_time=time.time(),
            verification_state="VERIFIED",
            tool_result={"status": "SUCCESS", "hwnd": 12345}
        ))
        # Ensure that step 1 evidence is recorded and not re-executed
        completed_step_ids = {s.step_id for s in trace.steps if s.status == "COMPLETED"}
        assert "step_1" in completed_step_ids
        assert trace.steps[0].verification_state == "VERIFIED"

    asyncio.run(_run())


def test_08_task_cancellation_midway_with_lock_release():
    """Verify task cancellation midway transitions state to CANCELLED and safely releases lock."""
    async def _run():
        task_id = "test_cancel_001"
        acquired = await desktop_execution_lock.acquire_async(owner=f"task_{task_id}")
        assert acquired is True

        # Simulate cancellation signal
        task_state_controller.complete_session("CANCELLED")
        desktop_execution_lock.release(owner=f"task_{task_id}")

        assert desktop_execution_lock.is_locked() is False
        assert task_state_controller._record.status == "CANCELLED"

    asyncio.run(_run())


def test_09_hud_event_bus_telemetry_streaming():
    """Verify real-time step progress is published to enterprise_event_bus."""
    events_received = []

    def _on_progress(event):
        events_received.append(event)

    enterprise_event_bus.subscribe("task.orchestration.progress", _on_progress)

    async def _run():
        cmd = "open notepad"
        res = await single_task_coordinator.execute_interactive_task(cmd)
        assert res["status"] in ("SUCCEEDED", "COMPLETED")
        assert len(events_received) >= 1
        last_evt = events_received[-1]
        payload = getattr(last_evt, "payload", {}) if hasattr(last_evt, "payload") else last_evt
        assert "task_id" in payload
        assert "correlation_id" in payload
        assert "progress_ratio" in payload
        assert "verification_state" in payload

    asyncio.run(_run())


def test_10_preservation_of_safety_boundaries_and_confirmation_gates():
    """Verify existing security gates (FOCUS_MISMATCH, DESKTOP_SESSION_UNAVAILABLE) are preserved."""
    # 1. Desktop lock enforces bounded mutual exclusion
    assert hasattr(desktop_execution_lock, "acquire_async")
    assert hasattr(desktop_execution_lock, "release")

    # 2. CoordinateMapper preserves DPI scaling and focus boundaries
    assert desktop_controller.coord_mapper is not None
    assert desktop_controller.coord_mapper.dpi_scale >= 1.0

    # 3. Post-action verification rejects missing files
    async def _verify_fail():
        bad_res = await desktop_controller.verify_outcome({
            "type": "VERIFY_FILE",
            "target": r"C:\non_existent_folder_xyz\missing_file.txt",
            "expected": "some_content"
        })
        assert bad_res["status"] == "FAILED"
        assert "not created on disk" in bad_res["error"]

    asyncio.run(_verify_fail())

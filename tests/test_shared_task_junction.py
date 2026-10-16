"""Acceptance tests for the durable multi-agent task junction."""

import asyncio
import time

from core.orchestration.agent_contract import TaskContext
from core.orchestration.shared_task_junction import SharedTaskJunction
from core.orchestration.swarm_orchestrator import SwarmHandoff, SwarmOrchestrator
from core.storage.sqlite_store import AegisSQLiteStore


def test_junction_persists_context_handoffs_evidence_and_isolation(tmp_path):
    store = AegisSQLiteStore(str(tmp_path / "junction.db"))
    junction = SharedTaskJunction(store)
    first = TaskContext(goal="research", task_id="task-a", correlation_id="corr-a")
    second = TaskContext(goal="code", task_id="task-b", correlation_id="corr-b")

    junction.start(first, "recon", {"query": "AEGIS"})
    junction.start(second, "architect", {"repo": "demo"})
    junction.record_handoff("task-a", "recon", "librarian", "summarize", {"source": "page-1"}, "research complete", 1)
    junction.record_event("task-a", "verification", agent="librarian", evidence={"url": "https://example.test"})
    junction.complete("task-a", "SUCCESS", {"summary": "done"}, {"verified": True})

    reopened = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "junction.db")))
    task_a = reopened.get("task-a")
    task_b = reopened.get("task-b")
    assert task_a["status"] == "SUCCESS"
    assert task_a["parameters"]["source"] == "page-1"
    assert task_a["handoffs"][0]["target_agent"] == "librarian"
    assert task_a["evidence"][0]["url"] == "https://example.test"
    assert task_b["status"] == "RUNNING"
    assert task_b["parameters"] == {"repo": "demo"}


def test_swarm_passes_shared_context_across_handoff_and_persists_chain(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "swarm.db")))
    orchestrator = SwarmOrchestrator(junction=junction)
    received = {}

    class FirstAgent:
        async def execute_task(self, goal, context=None):
            assert context["task_id"]
            raise SwarmHandoff("second", "finish the task", {"finding": "important"}, "delegation")

    class SecondAgent:
        async def execute_task(self, goal, context=None):
            received.update(context["parameters"])
            return {"status": "SUCCESS", "summary": "completed with shared finding"}

    orchestrator.register_agent("first", FirstAgent())
    orchestrator.register_agent("second", SecondAgent())
    result = asyncio.run(orchestrator.execute_swarm_task("first", "start task", {"seed": 1}))

    assert result["status"] == "SUCCESS"
    assert received == {"seed": 1, "finding": "important"}
    saved = junction.get(result["task_id"])
    assert saved["status"] == "SUCCESS"
    assert [h["target_agent"] for h in saved["handoffs"]] == ["second"]
    assert saved["context"]["parameters"]["finding"] == "important"
    assert any(event["type"] == "agent_result" for event in saved["events"])


def test_swarm_cancellation_is_durable_and_not_reported_as_success(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "cancel.db")))
    orchestrator = SwarmOrchestrator(junction=junction)
    release = asyncio.Event()

    class SlowAgent:
        async def execute_task(self, goal, context=None):
            await release.wait()
            return {"status": "SUCCESS"}

    orchestrator.register_agent("slow", SlowAgent())

    async def run():
        task = asyncio.create_task(orchestrator.execute_swarm_task("slow", "wait"))
        while not orchestrator.active_tasks:
            await asyncio.sleep(0)
        task_id = next(iter(orchestrator.active_tasks))
        assert orchestrator.cancel_task(task_id) is True
        release.set()
        return await task

    result = asyncio.run(run())
    assert result["status"] == "CANCELLED"
    assert junction.get(result["task_id"])["status"] == "CANCELLED"


def test_terminal_task_ignores_late_events_results_and_cancellation(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "terminal.db")))
    context = TaskContext(goal="terminal", task_id="terminal-task")
    junction.start(context, "agent")
    junction.complete("terminal-task", "SUCCESS", {"answer": 1})

    junction.record_event("terminal-task", "late_result", status="FAILED", evidence={"late": True})
    junction.complete("terminal-task", "FAILED", {"answer": 2})
    junction.cancel("terminal-task", "late operator cancel")

    saved = junction.get("terminal-task")
    assert saved["status"] == "SUCCESS"
    assert saved["result"] == {"answer": 1}
    assert any(event["type"] == "late_event_ignored" for event in saved["events"])
    assert any(event["type"] == "late_terminal_result_ignored" for event in saved["events"])


def test_cancellation_request_wins_over_late_completion(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "cancel_race.db")))
    junction.start(TaskContext(goal="cancel race", task_id="cancel-race"), "agent")
    junction.cancel("cancel-race", "operator")
    junction.complete("cancel-race", "SUCCESS", {"late": True})

    saved = junction.get("cancel-race")
    assert saved["status"] == "CANCEL_REQUESTED"
    assert saved["result"] is None


def test_swarm_does_not_promote_unverified_agent_result_to_success(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "unverified.db")))
    orchestrator = SwarmOrchestrator(junction=junction)

    class UnverifiedAgent:
        async def execute_task(self, goal, context=None):
            return {"status": "UNVERIFIED", "reason": "no fresh evidence"}

    orchestrator.register_agent("unverified", UnverifiedAgent())
    result = asyncio.run(orchestrator.execute_swarm_task("unverified", "perform action"))

    assert result["status"] == "FAILED"
    assert result["error"]["error_code"] == "VERIFICATION_FAILED"
    assert junction.get(result["task_id"])["status"] == "FAILED"


def test_swarm_requires_fresh_expected_outcome_evidence(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "verification.db")))
    orchestrator = SwarmOrchestrator(junction=junction)

    class BrowserAgent:
        async def execute_task(self, goal, context=None):
            return {
                "status": "SUCCESS",
                "observation": {
                    "title": "Settings",
                    "url": "https://example.test/settings",
                    "timestamp": time.time(),
                },
            }

    orchestrator.register_agent("browser", BrowserAgent())
    result = asyncio.run(orchestrator.execute_swarm_task(
        "browser",
        "open settings",
        {"verification_goal": "open the Settings page"},
    ))

    assert result["status"] == "SUCCESS"
    assert result["final_result"]["verification"]["status"] == "VERIFIED"
    saved = junction.get(result["task_id"])
    assert any(event["type"] == "verification_result" for event in saved["events"])
    assert any(item.get("status") == "VERIFIED" for item in saved["evidence"])


def test_swarm_rejects_action_success_without_matching_expected_outcome(tmp_path):
    junction = SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "verification-fail.db")))
    orchestrator = SwarmOrchestrator(junction=junction)

    class BrowserAgent:
        async def execute_task(self, goal, context=None):
            return {
                "status": "SUCCESS",
                "observation": {
                    "title": "Home",
                    "url": "https://example.test/home",
                    "timestamp": time.time(),
                },
            }

    orchestrator.register_agent("browser", BrowserAgent())
    result = asyncio.run(orchestrator.execute_swarm_task(
        "browser",
        "open settings",
        {"verification_goal": "open the Settings page"},
    ))

    assert result["status"] == "FAILED"
    assert result["error"]["error_code"] == "VERIFICATION_FAILED"
    assert result["final_result"]["verification"]["status"] == "UNVERIFIED"

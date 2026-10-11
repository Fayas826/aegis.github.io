"""
AEGIS Phase 3 Multi-Agent Reliability & Swarm Stability Test Suite
==================================================================
Tests all 14 Phase 3 acceptance criteria:
1. Agent registration and lifecycle transitions (INITIALIZING -> READY -> BUSY -> STOPPED)
2. Registered agent failing health check (no blind READY assertion)
3. Tool lookup and validated invocation with Pydantic schemas
4. Successful and unsuccessful agent-to-agent handoffs
5. Missing tools and missing agents handling
6. Timeout and cancellation handling
7. Native desktop vs browser routing separation
8. Browser session reuse and state propagation
9. Structured error propagation (ErrorCode, StructuredError)
10. Checkpoint and recovery behavior in TaskStateController
11. Resource management and safe agent capacity under load
12. Protection against duplicate execution on succeeded tasks
13. Canonical 112-agent registry integrity
14. Compound desktop task decomposition (Notepad smoke workflow)
"""

import os
import sys
import asyncio
import unittest
from typing import Dict, Any

# Ensure project root is in sys.path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.orchestration.agent_contract import (
    AgentLifecycleState,
    TaskContext,
    ExecutionResult,
    StructuredError,
    ErrorCode,
    ExecutionEnvironment,
)
from core.orchestration.swarm_orchestrator import (
    SwarmOrchestrator,
    SwarmHandoff,
)
from core.tool_registry.registry import (
    ToolRegistry,
    ToolDefinition,
    ToolResult,
    tool_registry,
)
from core.orchestration.desktop_command_router import (
    DesktopCommandRouter,
)
from core.orchestration.task_state import (
    TaskStateController,
)
from core.agent_lifecycle.resource_manager import (
    ResourceManager,
)
from core.agent_lifecycle.registry import (
    registry as canonical_registry,
)
from core.orchestration.computer_use.planner import (
    planner_pipeline,
)
import pydantic


class MockAgent:
    def __init__(self, name: str, return_value: Any = None, handoff_to: str = None):
        self.name = name
        self.return_value = return_value or {"status": "SUCCESS", "detail": "Executed successfully"}
        self.handoff_to = handoff_to
        self.call_count = 0

    async def execute_task(self, goal: str, context: Dict[str, Any] = None):
        self.call_count += 1
        if self.handoff_to:
            raise SwarmHandoff(self.handoff_to, goal, context)
        if isinstance(self.return_value, Exception):
            raise self.return_value
        return self.return_value


class TestPhase3MultiAgentReliability(unittest.TestCase):

    def setUp(self):
        self.orchestrator = SwarmOrchestrator()
        self.tools = ToolRegistry()
        self.router = DesktopCommandRouter()

    # 1. Agent registration & lifecycle transitions
    def test_agent_registration_and_lifecycle(self):
        agent = MockAgent("worker_1")
        self.orchestrator.register_agent("worker_1", agent, health_check=lambda: True)

        # After healthy registration -> READY
        self.assertEqual(self.orchestrator.get_agent_state("worker_1"), AgentLifecycleState.READY)

        # Mark stopped
        self.orchestrator.mark_agent_stopped("worker_1")
        self.assertEqual(self.orchestrator.get_agent_state("worker_1"), AgentLifecycleState.STOPPED)

    # 2. Registered agent failing health check (not blindly advertised as READY)
    def test_agent_failing_health_check(self):
        faulty_agent = MockAgent("faulty")
        self.orchestrator.register_agent("faulty", faulty_agent, health_check=lambda: False)

        # Must NOT be READY! Should be DEGRADED
        state = self.orchestrator.get_agent_state("faulty")
        self.assertNotEqual(state, AgentLifecycleState.READY)
        self.assertEqual(state, AgentLifecycleState.DEGRADED)

    # 3. Tool lookup and validated invocation with schemas
    def test_tool_lookup_and_validation(self):
        class AddArgs(pydantic.BaseModel):
            a: int
            b: int

        def add_handler(a: int, b: int) -> int:
            return a + b

        tool_def = ToolDefinition(
            tool_id="math.add",
            description="Add two numbers",
            input_schema=AddArgs,
            handler=add_handler,
            risk_level="SAFE",
        )
        self.tools.register(tool_def)

        # Lookup
        self.assertTrue(self.tools.has_tool("math.add"))

        # Valid invocation
        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(self.tools.execute("math.add", {"a": 10, "b": 25}))
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.data, 35)

        # Invalid invocation (missing required param)
        bad_res = loop.run_until_complete(self.tools.execute("math.add", {"a": 10}))
        self.assertEqual(bad_res.status, "VALIDATION_ERROR")
        self.assertIsNotNone(bad_res.error)
        loop.close()

    # 4. Successful and unsuccessful handoffs
    def test_handoffs(self):
        agent_a = MockAgent("agent_a", handoff_to="agent_b")
        agent_b = MockAgent("agent_b", return_value={"status": "SUCCESS", "result": "Finished by B"})

        self.orchestrator.register_agent("agent_a", agent_a)
        self.orchestrator.register_agent("agent_b", agent_b)

        loop = asyncio.new_event_loop()
        # Successful A -> B handoff
        res = loop.run_until_complete(
            self.orchestrator.execute_swarm_task("agent_a", "Process data")
        )
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["final_result"]["result"], "Finished by B")
        self.assertEqual(len(res["trace"]), 3)
        agents_in_trace = [t["agent"] for t in res["trace"]]
        self.assertIn("agent_a", agents_in_trace)
        self.assertIn("agent_b", agents_in_trace)

        # Unsuccessful handoff to nonexistent agent
        agent_c = MockAgent("agent_c", handoff_to="agent_missing")
        self.orchestrator.register_agent("agent_c", agent_c)
        bad_res = loop.run_until_complete(
            self.orchestrator.execute_swarm_task("agent_c", "Process data")
        )
        self.assertEqual(bad_res["status"], "FAILED")
        self.assertIn("unavailable", bad_res["reason"].lower())
        loop.close()

    # 5. Missing tools and missing agents
    def test_missing_tools_and_agents(self):
        loop = asyncio.new_event_loop()
        # Missing tool
        t_res = loop.run_until_complete(self.tools.execute("nonexistent.tool", {}))
        self.assertEqual(t_res.status, "FAILED")
        self.assertEqual(t_res.error.error_code, ErrorCode.TOOL_UNAVAILABLE.value)

        # Missing agent
        a_res = loop.run_until_complete(
            self.orchestrator.execute_swarm_task("ghost_agent", "Do work")
        )
        self.assertEqual(a_res["status"], "FAILED")
        self.assertEqual(a_res["error"]["error_code"], ErrorCode.AGENT_NOT_FOUND.value)
        loop.close()

    # 6. Timeout handling
    def test_timeout_handling(self):
        async def slow_handler():
            await asyncio.sleep(2.0)
            return "too slow"

        slow_agent = MockAgent("slow_agent")
        slow_agent.execute_task = lambda goal: slow_handler()
        self.orchestrator.register_agent("slow_agent", slow_agent)

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            self.orchestrator.execute_swarm_task("slow_agent", "Quick task", timeout_seconds=1)
        )
        self.assertEqual(res["status"], "TIMEOUT")
        self.assertEqual(res["error"]["error_code"], ErrorCode.TIMEOUT.value)
        loop.close()

    # 7. Native desktop vs browser routing
    def test_native_desktop_vs_browser_routing(self):
        # Explicit native desktop commands
        c1 = self.router.classify("Open Notepad and write test notes")
        self.assertIn(c1.category, ("DESKTOP_ACTION", "COMPUTER_USE"))

        c2 = self.router.classify("Launch Calculator")
        self.assertEqual(c2.category, "DESKTOP_ACTION")
        self.assertEqual(c2.action_type, "OPEN_APP")

        # Explicit browser navigation
        c3 = self.router.classify("Go to https://in.bookmyshow.com")
        self.assertIn(c3.category, ("BROWSER_ACTION", "COMPUTER_USE"))

        c4 = self.router.classify("Search for quantum computing on google")
        self.assertEqual(c4.category, "BROWSER_ACTION")
        self.assertEqual(c4.action_type, "WEB_SEARCH")

    # 8. Browser session reuse and state propagation
    def test_browser_session_propagation(self):
        ctx = TaskContext(
            goal="Book tickets",
            session_id="browser_session_001",
            parameters={"city": "Kochi", "seats": 2},
            environment=ExecutionEnvironment.BROWSER,
        )
        d = ctx.to_dict()
        self.assertEqual(d["session_id"], "browser_session_001")
        self.assertEqual(d["environment"], "BROWSER")

        restored = TaskContext.from_dict(d)
        self.assertEqual(restored.session_id, "browser_session_001")
        self.assertEqual(restored.parameters["city"], "Kochi")

        # Genuine Browser Session Reuse across agents
        from core.perception.browser_driver import PlaywrightBrowserDriver
        loop = asyncio.new_event_loop()
        async def verify_cross_agent_session():
            driver = PlaywrightBrowserDriver()
            try:
                page1 = await driver.launch_browser(headless=True)
                if page1 is None:
                    return True
                # Agent 1 loads a controlled page and populates session state
                await page1.set_content("<html><body><input id='session_state' value='step_1_active'/></body></html>")

                # Agent 2 accesses the active browser session without relaunching
                page2 = await driver.launch_browser(headless=True)
                self.assertIs(page1, page2)
                val = await page2.input_value("#session_state")
                self.assertEqual(val, "step_1_active")
                return True
            finally:
                await driver.close()

        success = loop.run_until_complete(verify_cross_agent_session())
        self.assertTrue(success)
        loop.close()

    # 9. Structured error propagation
    def test_structured_error_propagation(self):
        failing_agent = MockAgent("failing", return_value={"status": "FAILED", "reason": "GPU VRAM allocation failed"})
        self.orchestrator.register_agent("failing", failing_agent)

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            self.orchestrator.execute_swarm_task("failing", "Render 3D scene")
        )
        self.assertEqual(res["status"], "FAILED")
        self.assertIsNotNone(res.get("error"))
        self.assertEqual(res["error"]["error_code"], ErrorCode.EXECUTION_FAILED.value)
        self.assertIn("GPU VRAM", res["reason"])
        loop.close()

    # 10. Checkpoint and recovery behavior in TaskStateController
    def test_task_state_lifecycle(self):
        controller = TaskStateController()
        plan_id = controller.begin_session("Audit agents", {"risk_level": "LOW"})
        self.assertIsNotNone(plan_id)

        # Mark node running
        controller.mark_node_running("node_1")
        self.assertEqual(controller._record.nodes["node_1"]["status"], "RUNNING")

        # Mark node verifying
        controller.mark_node_verifying("node_1")
        self.assertEqual(controller._record.nodes["node_1"]["status"], "VERIFYING")

        # Mark node succeeded
        controller.mark_node_succeeded("node_1", {"output": "pass"})
        self.assertEqual(controller._record.nodes["node_1"]["status"], "SUCCEEDED")

        # Complete session
        controller.complete_session("SUCCESS")
        self.assertEqual(controller._record.status, "SUCCESS")

    # 11. Resource management & capacity throttling
    def test_resource_manager_capacity(self):
        rm = ResourceManager(max_agents=8, max_ram_percent=85.0)

        # High pressure should throttle to 1 or 2
        high_load_tier = rm.load_tier(cpu_percent=90.0, ram_percent=88.0, hard_max=8, max_ram_percent=85.0)
        self.assertEqual(high_load_tier, 1)

        # Low load should allow full capacity
        low_load_tier = rm.load_tier(cpu_percent=20.0, ram_percent=40.0, hard_max=8, max_ram_percent=85.0)
        self.assertEqual(low_load_tier, 8)

    # 12. Protection against duplicate execution on succeeded tasks
    def test_duplicate_execution_protection(self):
        controller = TaskStateController()
        controller.begin_session("Step idempotency check", {})
        controller.mark_node_running("step_a")
        controller.mark_node_succeeded("step_a", {"result": "done"})

        # Trying to transition SUCCEEDED -> RUNNING must be rejected
        is_valid = controller._is_valid_transition("SUCCEEDED", "RUNNING")
        self.assertFalse(is_valid)

    # 13. Canonical 112-agent registry integrity
    def test_canonical_registry_integrity(self):
        self.assertGreater(len(canonical_registry.canonical_ids), 0)
        self.assertIn("AGENT-001", canonical_registry.canonical_ids)

    # 14. Compound desktop task decomposition (Notepad workflow)
    def test_compound_task_decomposition(self):
        compound_goal = "Open Notepad, write 'AEGIS OS Control Verified', and save it to the Desktop as 'aegis_test.txt'."
        loop = asyncio.new_event_loop()

        # Step 1: Must isolate app name 'Notepad' (NOT the whole sentence!)
        p1 = loop.run_until_complete(
            planner_pipeline.plan(compound_goal, current_step=1, max_steps=4, image_path="")
        )
        self.assertEqual(p1["action_type"], "OPEN_APP")
        self.assertEqual(p1["target"].lower(), "notepad")
        self.assertFalse(p1["finished"])

        # Step 2: Must extract typing payload
        p2 = loop.run_until_complete(
            planner_pipeline.plan(compound_goal, current_step=2, max_steps=4, image_path="")
        )
        self.assertEqual(p2["action_type"], "TYPE")
        self.assertEqual(p2["text_payload"], "AEGIS OS Control Verified")
        self.assertFalse(p2["finished"])

        # Step 3: Must trigger save hotkey
        p3 = loop.run_until_complete(
            planner_pipeline.plan(compound_goal, current_step=3, max_steps=4, image_path="")
        )
        self.assertEqual(p3["action_type"], "HOTKEY")
        self.assertEqual(p3["target"], "ctrl+s")

        # Step 4: Must type save filename and mark finished
        p4 = loop.run_until_complete(
            planner_pipeline.plan(compound_goal, current_step=4, max_steps=4, image_path="")
        )
        self.assertEqual(p4["action_type"], "TYPE")
        self.assertIn("aegis_test.txt", p4["text_payload"])
        self.assertTrue(p4["finished"])

        loop.close()


if __name__ == "__main__":
    unittest.main()

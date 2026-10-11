"""
AEGIS Swarm Orchestrator
=========================
Coordinates multi-agent execution, dynamic Agent-to-Agent (A2A) handoffs,
lifecycle states (INITIALIZING, READY, BUSY, DEGRADED, FAILED, STOPPED),
health-gated registration, and structured error propagation.
"""

import asyncio
import time
import traceback
from typing import Dict, Any, Optional, Callable, List

from core.orchestration.agent_contract import (
    AgentLifecycleState,
    TaskContext,
    ExecutionResult,
    StructuredError,
    ErrorCode,
    SwarmHandoffEnvelope,
)


class SwarmHandoff(Exception):
    """
    Exception raised when an agent delegates a task to another agent.
    Safely interrupts the current agent and triggers the swarm router
    to continue execution under the target agent.
    """
    def __init__(self, target_agent: str, goal: str, context: Optional[Dict[str, Any]] = None, reason: str = "Delegation"):
        self.target_agent = target_agent.lower()
        self.goal = goal
        self.context = context or {}
        self.reason = reason
        super().__init__(f"Swarm Handoff to {target_agent}: {goal}")


class SwarmOrchestrator:
    """
    Coordinates AEGIS Swarm Agents with explicit lifecycle management,
    evidence-based results, bounded handoffs, and resource-safe execution.
    """
    def __init__(self):
        self.registry: Dict[str, Any] = {}
        self.agent_states: Dict[str, AgentLifecycleState] = {}
        self.agent_status: Dict[str, str] = {}  # Backward compatibility
        self.agent_health_checks: Dict[str, Callable[[], bool]] = {}
        self.active_tasks: Dict[str, TaskContext] = {}
        self.max_handoff_depth: int = 5

    def register_agent(
        self,
        name: str,
        agent_instance: Any,
        health_check: Optional[Callable[[], bool]] = None,
    ):
        """
        Registers an agent with explicit INITIALIZING state,
        then evaluates its health before advertising READY.
        """
        agent_key = name.lower()
        self.registry[agent_key] = agent_instance
        self.agent_states[agent_key] = AgentLifecycleState.INITIALIZING
        self.agent_status[agent_key] = AgentLifecycleState.INITIALIZING.value

        if health_check:
            self.agent_health_checks[agent_key] = health_check

        # Evidence-gated health verification
        is_healthy = self.check_agent_health(agent_key)
        if is_healthy:
            self.agent_states[agent_key] = AgentLifecycleState.READY
            self.agent_status[agent_key] = AgentLifecycleState.READY.value
            print(f"[SWARM] Registered Agent: {name} (State: READY)")
        else:
            self.agent_states[agent_key] = AgentLifecycleState.DEGRADED
            self.agent_status[agent_key] = AgentLifecycleState.DEGRADED.value
            print(f"[SWARM] Registered Agent: {name} with warnings (State: DEGRADED)")

    def mark_agent_failed(self, name: str, reason: str):
        agent_key = name.lower()
        self.agent_states[agent_key] = AgentLifecycleState.FAILED
        self.agent_status[agent_key] = f"FAILED: {reason}"
        print(f"[SWARM] Agent '{name}' marked FAILED: {reason}")

    def mark_agent_stopped(self, name: str):
        agent_key = name.lower()
        self.agent_states[agent_key] = AgentLifecycleState.STOPPED
        self.agent_status[agent_key] = AgentLifecycleState.STOPPED.value
        print(f"[SWARM] Agent '{name}' marked STOPPED.")

    def check_agent_health(self, name: str) -> bool:
        """Determines readiness through actual dependency checks, not blind assertion."""
        agent_key = name.lower()
        agent = self.registry.get(agent_key)
        if not agent:
            return False

        custom_checker = self.agent_health_checks.get(agent_key)
        if custom_checker:
            try:
                return bool(custom_checker())
            except Exception as e:
                print(f"[SWARM_HEALTH] Custom health check failed for '{name}': {e}")
                return False

        # Built-in capability checks
        if agent_key == "computer_use":
            try:
                from core.perception.screen_capture import screen_capturer
                return hasattr(screen_capturer, "capture_frame")
            except Exception:
                return False

        if agent_key == "vision":
            return hasattr(agent, "execute_visual_task") or hasattr(agent, "execute_task")

        if agent_key == "ticket_booking":
            return hasattr(agent, "book_from_command") or hasattr(agent, "book_movie_tickets")

        return True

    def get_agent_state(self, name: str) -> AgentLifecycleState:
        return self.agent_states.get(name.lower(), AgentLifecycleState.FAILED)

    def get_swarm_status(self) -> Dict[str, str]:
        """Backward-compatible mapping of agent statuses."""
        return {k: self.agent_states.get(k, AgentLifecycleState.FAILED).value for k in self.registry}

    async def execute_swarm_task(
        self,
        initial_agent_name: str,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Executes a task through the swarm with:
        1. Explicit correlation ID and TaskContext
        2. Bounded handoff loop protection (max depth 5)
        3. Execution timeouts
        4. Evidence-based result verification (never reports false success)
        """
        start_time = time.time()
        task_ctx = TaskContext(
            goal=goal,
            parameters=context or {},
            deadline=(time.time() + timeout_seconds) if timeout_seconds else None,
        )
        self.active_tasks[task_ctx.task_id] = task_ctx

        current_agent_name = initial_agent_name.lower()
        current_goal = goal
        current_context = dict(context or {})

        execution_trace: List[Dict[str, Any]] = []
        visited_agents: List[str] = []
        handoff_depth = 0

        while True:
            agent = self.registry.get(current_agent_name)
            agent_state = self.agent_states.get(current_agent_name, AgentLifecycleState.FAILED)

            # 1. Agent Availability & State Check
            if not agent or agent_state in (AgentLifecycleState.FAILED, AgentLifecycleState.STOPPED):
                status_msg = self.agent_status.get(current_agent_name, "Not Registered")
                duration_ms = (time.time() - start_time) * 1000
                err = StructuredError(
                    error_code=ErrorCode.AGENT_NOT_FOUND.value,
                    message=f"Agent '{current_agent_name}' unavailable. State: {agent_state.value}. Status: {status_msg}",
                    details={"target_agent": current_agent_name, "trace": execution_trace},
                )
                return {
                    "task_id": task_ctx.task_id,
                    "correlation_id": task_ctx.correlation_id,
                    "status": "FAILED",
                    "error": err.to_dict(),
                    "reason": err.message,
                    "trace": execution_trace,
                    "duration_ms": duration_ms,
                }

            visited_agents.append(current_agent_name)
            handoff_depth += 1

            # 2. Handoff Loop & Depth Guard
            if handoff_depth > self.max_handoff_depth or visited_agents.count(current_agent_name) > 2:
                duration_ms = (time.time() - start_time) * 1000
                err = StructuredError(
                    error_code=ErrorCode.HANDOFF_LOOP_DETECTED.value,
                    message=f"Swarm handoff limit exceeded or ping-pong loop detected on '{current_agent_name}'.",
                    details={"visited_agents": visited_agents, "depth": handoff_depth},
                )
                return {
                    "task_id": task_ctx.task_id,
                    "correlation_id": task_ctx.correlation_id,
                    "status": "FAILED",
                    "error": err.to_dict(),
                    "reason": err.message,
                    "trace": execution_trace,
                    "duration_ms": duration_ms,
                }

            # 3. Mark BUSY
            prev_state = self.agent_states[current_agent_name]
            self.agent_states[current_agent_name] = AgentLifecycleState.BUSY
            print(f"[SWARM] Dispatching goal to {current_agent_name.upper()} (Step {handoff_depth}): '{current_goal[:80]}'")
            execution_trace.append({"agent": current_agent_name, "goal": current_goal, "step": handoff_depth})

            try:
                # 4. Resolve Execution Interface
                exec_future = None
                if hasattr(agent, "execute_task"):
                    exec_future = agent.execute_task(current_goal)
                elif hasattr(agent, "execute_visual_task"):
                    exec_future = agent.execute_visual_task(goal=current_goal)
                elif hasattr(agent, "book_from_command"):
                    exec_future = agent.book_from_command(current_goal)
                elif hasattr(agent, "run"):
                    exec_future = agent.run(current_goal)
                elif callable(agent):
                    exec_future = agent(current_goal)
                else:
                    duration_ms = (time.time() - start_time) * 1000
                    err = StructuredError(
                        error_code=ErrorCode.TOOL_UNAVAILABLE.value,
                        message=f"Agent '{current_agent_name}' lacks a recognized execution interface.",
                        details={"agent_type": type(agent).__name__},
                    )
                    self.agent_states[current_agent_name] = AgentLifecycleState.DEGRADED
                    return {
                        "task_id": task_ctx.task_id,
                        "correlation_id": task_ctx.correlation_id,
                        "status": "FAILED",
                        "error": err.to_dict(),
                        "reason": err.message,
                        "trace": execution_trace,
                        "duration_ms": duration_ms,
                    }

                # 5. Execute under timeout
                if task_ctx.deadline:
                    remaining_timeout = max(1.0, task_ctx.deadline - time.time())
                    result = await asyncio.wait_for(exec_future, timeout=remaining_timeout)
                else:
                    result = await exec_future

                # Revert state back to READY
                self.agent_states[current_agent_name] = AgentLifecycleState.READY

                # 6. Honest Status Check — Never mask failure as success
                is_failed = False
                error_info = None
                if isinstance(result, dict):
                    status_str = str(result.get("status", "")).upper()
                    if status_str in ("FAILED", "ERROR", "REJECTED", "BLOCKED"):
                        is_failed = True
                        error_info = result.get("error") or result.get("reason") or "Underlying agent reported failure."
                    elif status_str == "SUCCESS":
                        is_failed = False
                    elif result.get("success") is False:
                        is_failed = True
                        error_info = result.get("error") or "Operation unsuccessful."
                elif isinstance(result, str) and result.lower().startswith("error:"):
                    is_failed = True
                    error_info = result

                duration_ms = (time.time() - start_time) * 1000
                execution_trace.append({"agent": current_agent_name, "result": result, "status": "FAILED" if is_failed else "SUCCESS"})

                if is_failed:
                    err = StructuredError(
                        error_code=ErrorCode.EXECUTION_FAILED.value,
                        message=str(error_info),
                        details={"agent_result": result},
                        recoverable=True,
                    )
                    return {
                        "task_id": task_ctx.task_id,
                        "correlation_id": task_ctx.correlation_id,
                        "status": "FAILED",
                        "final_result": result,
                        "error": err.to_dict(),
                        "reason": str(error_info),
                        "trace": execution_trace,
                        "duration_ms": duration_ms,
                    }

                return {
                    "task_id": task_ctx.task_id,
                    "correlation_id": task_ctx.correlation_id,
                    "status": "SUCCESS",
                    "final_result": result,
                    "trace": execution_trace,
                    "duration_ms": duration_ms,
                }

            except SwarmHandoff as handoff:
                # Agent safely requested handoff
                self.agent_states[current_agent_name] = AgentLifecycleState.READY
                print(f"[SWARM] HANDOFF: {current_agent_name} -> {handoff.target_agent} ('{handoff.reason}')")
                current_agent_name = handoff.target_agent.lower()
                current_goal = handoff.goal
                current_context.update(handoff.context)
                # Next loop iteration continues with new agent

            except asyncio.TimeoutError:
                self.agent_states[current_agent_name] = AgentLifecycleState.READY
                duration_ms = (time.time() - start_time) * 1000
                err = StructuredError(
                    error_code=ErrorCode.TIMEOUT.value,
                    message=f"Agent '{current_agent_name}' execution timed out.",
                    details={"elapsed_ms": duration_ms},
                    recoverable=True,
                )
                return {
                    "task_id": task_ctx.task_id,
                    "correlation_id": task_ctx.correlation_id,
                    "status": "TIMEOUT",
                    "error": err.to_dict(),
                    "reason": err.message,
                    "trace": execution_trace,
                    "duration_ms": duration_ms,
                }

            except Exception as e:
                self.agent_states[current_agent_name] = AgentLifecycleState.DEGRADED
                duration_ms = (time.time() - start_time) * 1000
                print(f"[SWARM] Agent '{current_agent_name}' unhandled exception: {e}")
                traceback.print_exc()
                err = StructuredError(
                    error_code=ErrorCode.EXECUTION_FAILED.value,
                    message=str(e),
                    details={"exception_type": type(e).__name__},
                    recoverable=True,
                )
                return {
                    "task_id": task_ctx.task_id,
                    "correlation_id": task_ctx.correlation_id,
                    "status": "FAILED",
                    "error": err.to_dict(),
                    "reason": str(e),
                    "trace": execution_trace,
                    "duration_ms": duration_ms,
                }
            finally:
                if task_ctx.task_id in self.active_tasks:
                    del self.active_tasks[task_ctx.task_id]


swarm_orchestrator = SwarmOrchestrator()


def _initialize_swarm():
    try:
        from core.orchestration.computer_use_agent import computer_use_agent
        swarm_orchestrator.register_agent("computer_use", computer_use_agent)
    except Exception as e:
        print("[SWARM_INIT_ERROR] computer_use failed:")
        traceback.print_exc()
        swarm_orchestrator.mark_agent_failed("computer_use", str(e))

    try:
        from core.perception.multimodal_vision_agent import multimodal_vision_agent
        swarm_orchestrator.register_agent("vision", multimodal_vision_agent)
    except Exception as e:
        print("[SWARM_INIT_ERROR] vision failed:")
        traceback.print_exc()
        swarm_orchestrator.mark_agent_failed("vision", str(e))

    try:
        from core.ticket_booking_agent import jarvis_ticket_booker
        swarm_orchestrator.register_agent("ticket_booking", jarvis_ticket_booker)
    except Exception as e:
        print("[SWARM_INIT_ERROR] ticket_booking failed:")
        traceback.print_exc()
        swarm_orchestrator.mark_agent_failed("ticket_booking", str(e))


_initialize_swarm()

# AEGIS Agent Lifecycle Matrix & Execution Contract

**Specification Version:** 3.0  
**Module Reference:** `core/orchestration/agent_contract.py` & `core/orchestration/swarm_orchestrator.py`  

---

## 1. Lifecycle State Machine

Every agent registered in the AEGIS multi-agent system must transition through explicit lifecycle states:

```
               [Registration Request]
                         │
                         ▼
                  ┌──────────────┐
                  │ INITIALIZING │
                  └──────┬───────┘
                         │
               (Health Check Evaluated)
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
     [Unhealthy]     [Warnings]      [Healthy]
         │               │               │
         ▼               ▼               ▼
    ┌─────────┐    ┌──────────┐    ┌─────────┐
    │ FAILED  │    │ DEGRADED │    │  READY  │◄─────────┐
    └─────────┘    └──────────┘    └────┬────┘          │
                                        │ (Task Arrives)│ (Task Complete)
                                        ▼               │
                                   ┌─────────┐          │
                                   │  BUSY   ├──────────┘
                                   └────┬────┘
                                        │
                               (Stop / Teardown)
                                        │
                                        ▼
                                   ┌─────────┐
                                   │ STOPPED │
                                   └─────────┘
```

---

## 2. State Definitions & Transitions

| State | Definition | Entry Trigger | Exit Trigger | Permitted Next States |
|---|---|---|---|---|
| `INITIALIZING` | Agent is instantiated; dependencies & models are being resolved. | `register_agent(name, instance)` called. | Health check evaluation completes. | `READY`, `DEGRADED`, `FAILED` |
| `READY` | All required capabilities and health checks passed. Available for work. | Health check returns `healthy: True`. | Task dispatched to agent, or stop requested. | `BUSY`, `DEGRADED`, `STOPPED` |
| `BUSY` | Currently executing an active task or sub-plan. | Task dispatched by swarm orchestrator. | Task execution finishes (success or failure). | `READY`, `DEGRADED`, `FAILED` |
| `DEGRADED` | Non-fatal dependency missing (e.g., local model offline, falling back to cloud). | Optional dependency check returns warnings. | Recovered, task dispatched, or stopped. | `READY`, `BUSY`, `FAILED`, `STOPPED` |
| `FAILED` | Critical dependency missing; unable to perform tasks. | Required dependency check fails. | Agent re-initialized or reconfigured. | `INITIALIZING`, `STOPPED` |
| `STOPPED` | Explicitly shut down or deactivated. Will not receive tasks. | `stop_agent(name)` or system shutdown. | Agent re-registration. | `INITIALIZING` |

---

## 3. Health Gating Requirements

An agent must **never** be advertised as `READY` solely because registration succeeded. The orchestrator enforces the following validation sequence:

1. **Existence Check:** Verifies instance has callable execution methods (`execute_task`, `execute_visual_task`, `book_from_command`, `run`, or `__call__`).
2. **Capability Verification:** Inspects advertised capabilities (e.g., computer use, vision, browser).
3. **Dependency Probe:**
   - If agent exposes `check_health()` or `is_healthy()`, evaluates the returned health status.
   - If critical dependencies (e.g., display server, API keys, GPU backend) are missing, sets status to `FAILED`.
   - If secondary fallbacks are active (e.g., local model offline, cloud fallback ready), sets status to `DEGRADED`.
   - Only when all mandatory requirements are satisfied is `READY` assigned.

---

## 4. Error Code Matrix (`StructuredError`)

When a handoff, tool invocation, or task fails, it must return a typed `StructuredError`:

| Error Code | Meaning | Recoverable | Recommended Strategy |
|---|---|---|---|
| `AGENT_NOT_FOUND` | Requested agent name is not in the swarm registry. | No | Fail fast; report missing capability to planner. |
| `TOOL_NOT_FOUND` | Requested tool ID is not registered in `core/tool_registry/`. | No | Provide available tool catalog to planner. |
| `INITIALIZATION_FAILED` | Agent or tool failed to initialize required resources. | Yes | Degrade to fallback provider or restart agent. |
| `VALIDATION_FAILED` | Arguments did not match the tool's Pydantic schema. | Yes | Re-prompt caller with exact validation errors. |
| `PERMISSION_DENIED` | Action violates safety perimeter or lacks user approval. | Yes | Request user confirmation via `PENDING_CONFIRMATION`. |
| `TIMEOUT` | Task or tool execution exceeded allotted deadline. | Yes | Bounded retry or graceful degradation. |
| `EXECUTION_FAILED` | Internal tool or model execution raised an error. | Yes | Re-ground target, switch backend, or retry once. |
| `HANDOFF_CYCLE_DETECTED` | Task ping-ponged between agents or exceeded max depth (5). | No | Terminate handoff loop; return partial failure report. |
| `RECOVERY_EXHAUSTED` | All recovery strategies (switch backend, re-ground) failed. | No | Escalate to user or halt with diagnostic trace. |

---

## 5. Swarm Handoff Envelope Specification

Every task handoff produces or consumes a `SwarmHandoffEnvelope`:

```python
class SwarmHandoffEnvelope(BaseModel):
    task_id: str                      # Unique task UUID
    correlation_id: str               # End-to-end tracing ID
    source_agent: str                 # Calling agent name
    target_agent: str                 # Destination agent name
    user_goal: str                    # Original user intent
    current_goal: str                 # Decomposed sub-goal
    handoff_depth: int = 1            # Recursion depth (max 5)
    execution_environment: str        # "DESKTOP" | "BROWSER" | "CLI" | "API"
    structured_params: Dict[str, Any] # Typed task parameters
    created_at: float                 # Dispatch timestamp
    deadline_epoch: Optional[float]   # Hard timeout deadline
    cancellation_requested: bool      # Async cancellation signal
```

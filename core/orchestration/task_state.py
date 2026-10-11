import os
import time
import uuid
import threading
from typing import Dict, Any, List, Optional

from core.orchestration.lifecycle_record import TaskLifecycleRecord
from core.orchestration.persistence import atomic_write, safe_load
from core.orchestration.episodic_memory import write_episodic_memory, retrieve_episodic_history, find_similar_episodes
from core.orchestration.task_variables import substitute_variables

PROJECT_MEMORY_DIR = "project_memory"
_LIFECYCLE_DIR = os.path.join(PROJECT_MEMORY_DIR, "lifecycle")

_VALID_TRANSITIONS = {
    "CREATED":    {"PLANNING", "RUNNING", "PENDING", "CANCELLED"},
    "PLANNING":   {"RUNNING", "PENDING", "FAILED", "CANCELLED"},
    "PENDING":    {"RUNNING", "PLANNING", "CANCELLED"},
    "RUNNING":    {"VERIFYING", "SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED", "RETRYING", "RECOVERY"},
    "VERIFYING":  {"SUCCEEDED", "COMPLETED", "FAILED", "RETRYING", "RECOVERY"},
    "RETRYING":   {"RUNNING", "FAILED", "CANCELLED"},
    "RECOVERY":   {"RUNNING", "FAILED", "CANCELLED"},
    "SUCCEEDED":  set(),
    "COMPLETED":  set(),
    "FAILED":     {"RETRYING", "RECOVERY", "CANCELLED"},
    "CANCELLED":  set(),
}

_LOCK = threading.Lock()

class TaskStateController:
    """
    Phase 36.4 — Full Persistent Task Lifecycle Controller.
    Refactored into facade module over persistence, variables, and episodic memory.
    """
    def __init__(self):
        os.makedirs(PROJECT_MEMORY_DIR, exist_ok=True)
        os.makedirs(_LIFECYCLE_DIR, exist_ok=True)
        self._session_path = os.path.join(PROJECT_MEMORY_DIR, "active_session.json")
        self._plan_path    = os.path.join(PROJECT_MEMORY_DIR, "active_plan.json")
        self._state_path   = os.path.join(PROJECT_MEMORY_DIR, "project_state.json")
        self._completed_path = os.path.join(PROJECT_MEMORY_DIR, "completed_tasks.json")
        self._failed_path    = os.path.join(PROJECT_MEMORY_DIR, "failed_tasks.json")
        self._episodic_path  = os.path.join(PROJECT_MEMORY_DIR, "episodic_memory.json")
        self._record: Optional[TaskLifecycleRecord] = None
        self._lock = _LOCK
        self._load_session()

    def _load_session(self):
        data = safe_load(self._session_path, None)
        if data:
            self._record = TaskLifecycleRecord.from_dict(data)
            if self._record.status == "RUNNING":
                self._record.status = "INTERRUPTED"
                self._record.updated_at = time.time()
                self._persist_session()
                print(f"[TASK_STATE] Interrupted session detected: {self._record.plan_id}")

    def _persist_session(self):
        if self._record:
            atomic_write(self._session_path, self._record.to_dict())

    def begin_session(self, objective: str, intent: Dict[str, Any]) -> str:
        with self._lock:
            plan_id = str(uuid.uuid4())
            self._record = TaskLifecycleRecord(plan_id=plan_id, objective=objective, intent=intent)
            self._record.status = "PLANNING"
            self._record.updated_at = time.time()
            self._persist_session()
            self._legacy_update({"current_objective": objective, "status": "PLANNING", "active_task_id": None})
            print(f"[TASK_STATE] Session started: {plan_id}")
            return plan_id

    def get_interrupted_session(self) -> Optional[TaskLifecycleRecord]:
        with self._lock:
            if self._record and self._record.status == "INTERRUPTED":
                return self._record
            return None

    def recover_session(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._record or self._record.status != "INTERRUPTED":
                return None
            original_risk = self._record.intent.get("risk_level", "LOW")
            recovered_node = None
            plan = safe_load(self._plan_path, {}).get("tasks", [])
            for task in plan:
                node_id = task.get("task_id")
                node_state = self._record.nodes.get(node_id, {})
                node_status = node_state.get("status", task.get("status", "PENDING"))
                if node_status == "RUNNING":
                    task["status"] = "PENDING"
                    node_state["status"] = "PENDING"
                    node_state["recovery_ts"] = time.time()
                    node_state["recovered"] = True
                    self._record.nodes[node_id] = node_state
                    if task.get("risk_level", "LOW") != original_risk:
                        task["risk_level"] = original_risk
                    recovered_node = task
                    break
                elif node_status == "COMPLETED":
                    task["status"] = "COMPLETED"
            atomic_write(self._plan_path, {"tasks": plan})
            self._record.status = "RECOVERING"
            self._record.active_node_id = None
            self._record.updated_at = time.time()
            self._persist_session()
            return recovered_node

    def mark_node_running(self, node_id: str):
        with self._lock:
            if self._record:
                self._record.active_node_id = node_id
                self._record.status = "RUNNING"
                node = self._record.nodes.setdefault(node_id, {})
                node["status"] = "RUNNING"
                node["started_at"] = time.time()
                self._record.updated_at = time.time()
                self._persist_session()
                self._legacy_update({"status": "RUNNING", "active_task_id": node_id})

    def mark_node_verifying(self, node_id: str):
        with self._lock:
            if self._record:
                node = self._record.nodes.setdefault(node_id, {})
                current = node.get("status", "RUNNING")
                if not self._is_valid_transition(current, "VERIFYING"):
                    print(f"[TASK_STATE] Invalid transition {current} → VERIFYING for {node_id}")
                    return
                node["status"] = "VERIFYING"
                node["verifying_at"] = time.time()
                self._record.updated_at = time.time()
                self._persist_session()

    def mark_node_succeeded(self, node_id: str, result: Dict[str, Any] = None):
        return self.mark_node_completed(node_id, result)

    def mark_node_completed(self, node_id: str, result: Dict[str, Any] = None):
        with self._lock:
            if self._record:
                node = self._record.nodes.setdefault(node_id, {})
                current = node.get("status", "RUNNING")
                if not self._is_valid_transition(current, "COMPLETED") and not self._is_valid_transition(current, "SUCCEEDED"):
                    print(f"[TASK_STATE] Invalid transition {current} → COMPLETED for {node_id}")
                    return
                node["status"] = "SUCCEEDED"
                node["completed_at"] = time.time()
                node["result"] = result or {}
                if self._record.active_node_id == node_id:
                    self._record.active_node_id = None
                self._record.updated_at = time.time()
                self._persist_session()

    def mark_node_failed(self, node_id: str, error: str, error_type: str = "UNKNOWN"):
        with self._lock:
            if self._record:
                node = self._record.nodes.setdefault(node_id, {})
                node["status"] = "FAILED"
                node["failed_at"] = time.time()
                node["last_error"] = error
                node["error_type"] = error_type
                self._record.error_history.append({"node_id": node_id, "error": error, "error_type": error_type, "ts": time.time()})
                self._record.updated_at = time.time()
                self._persist_session()

    def mark_node_retrying(self, node_id: str, retry_count: int):
        with self._lock:
            if self._record:
                node = self._record.nodes.setdefault(node_id, {})
                node["status"] = "RETRYING"
                node["retry_count"] = retry_count
                node["retry_at"] = time.time()
                self._record.updated_at = time.time()
                self._persist_session()

    def record_tool_use(self, node_id: str, tool: str, result: str, latency_ms: float):
        with self._lock:
            if self._record:
                self._record.tool_history.append({"node_id": node_id, "tool": tool, "result": result, "latency_ms": latency_ms, "ts": time.time()})
                self._record.updated_at = time.time()
                self._persist_session()

    def record_recovery(self, node_id: str, strategy: str, outcome: str):
        with self._lock:
            if self._record:
                self._record.recovery_history.append({"node_id": node_id, "strategy": strategy, "outcome": outcome, "ts": time.time()})
                self._record.updated_at = time.time()
                self._persist_session()

    def _is_valid_transition(self, from_status: str, to_status: str) -> bool:
        return to_status in _VALID_TRANSITIONS.get(from_status, set())

    def complete_session(self, final_result: str = "SUCCESS"):
        with self._lock:
            if self._record:
                self._record.status = final_result
                self._record.updated_at = time.time()
                self._persist_session()
                write_episodic_memory(self._record, self._episodic_path)
                self._legacy_update({"status": "STANDBY", "active_task_id": None})
                print(f"[TASK_STATE] Session {self._record.plan_id} completed: {final_result}")

    def retrieve_episodic_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        return retrieve_episodic_history(self._episodic_path, limit)

    def find_similar_episodes(self, objective: str, limit: int = 3) -> List[Dict[str, Any]]:
        return find_similar_episodes(self._episodic_path, objective, limit)

    def save_active_plan(self, tasks: List[Dict[str, Any]]):
        atomic_write(self._plan_path, {"tasks": tasks})

    def load_active_plan(self) -> List[Dict[str, Any]]:
        return safe_load(self._plan_path, {}).get("tasks", [])

    def register_completed_task(self, task: Dict[str, Any]):
        completed = safe_load(self._completed_path, [])
        completed.append({**task, "registered_at": time.time()})
        atomic_write(self._completed_path, completed)

    def register_failed_task(self, task: Dict[str, Any]):
        failed = safe_load(self._failed_path, [])
        failed.append({**task, "registered_at": time.time()})
        atomic_write(self._failed_path, failed)

    def get_state(self) -> Dict[str, Any]:
        data = safe_load(self._state_path, {"current_objective": "None", "status": "STANDBY", "active_task_id": None})
        if self._record:
            data["variables"] = self._record.variables
        return data

    def update_state(self, updates: Dict[str, Any]):
        self._legacy_update(updates)

    def _legacy_update(self, updates: Dict[str, Any]):
        state = safe_load(self._state_path, {"current_objective": "None", "status": "STANDBY"})
        state.update(updates)
        atomic_write(self._state_path, state)

    def set_variable(self, name: str, value: Any):
        with self._lock:
            if self._record:
                self._record.variables[name] = value
                self._persist_session()
            state = self.get_state()
            state.setdefault("variables", {})[name] = value
            atomic_write(self._state_path, state)

    def substitute_variables(self, task_node: Dict[str, Any]) -> Dict[str, Any]:
        return substitute_variables(task_node, self._record, self._state_path)

task_state_controller = TaskStateController()

"""Durable task-scoped context shared by AEGIS agents.

This is a coordination facade over the existing :class:`AegisSQLiteStore`.
It is intentionally not a second memory system: one task document contains
the canonical execution context, handoff chain, evidence events, cancellation
state, and terminal result. Other stores remain projections for learning and
telemetry.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Dict, Optional

from core.orchestration.agent_contract import TaskContext
from core.storage.sqlite_store import AegisSQLiteStore, sovereign_db


class SharedTaskJunction:
    """Persist and retrieve one shared context per task ID."""

    TABLE = "tasks"
    TERMINAL_STATUSES = {
        "SUCCESS", "VERIFIED", "FAILED", "BLOCKED", "UNAVAILABLE",
        "TIMEOUT", "CANCELLED", "CONFIRMED",
    }

    def __init__(self, store: Optional[AegisSQLiteStore] = None):
        self.store = store or sovereign_db
        self._lock = threading.RLock()

    @staticmethod
    def _now() -> float:
        return time.time()

    def _read(self, task_id: str) -> Optional[Dict[str, Any]]:
        rows = self.store.find(self.TABLE, {"task_id": task_id}, limit=1)
        return copy.deepcopy(rows[0]) if rows else None

    def _write(self, document: Dict[str, Any]) -> Dict[str, Any]:
        document = copy.deepcopy(document)
        document["updated_at"] = self._now()
        self.store.upsert(self.TABLE, document, "task_id")
        return document

    def start(self, context: TaskContext, initial_agent: str,
              parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create or resume a task document without overwriting a live task."""
        with self._lock:
            current = self._read(context.task_id)
            if current:
                # A task ID is an idempotency key. A late retry must not erase
                # a running or terminal execution history.
                return current
            now = self._now()
            document = {
                "task_id": context.task_id,
                "correlation_id": context.correlation_id,
                "session_id": context.session_id,
                "goal": context.goal,
                "context": context.to_dict(),
                "parameters": copy.deepcopy(parameters or context.parameters),
                "status": "RUNNING",
                "agents": [initial_agent.lower()],
                "handoffs": [],
                "events": [{"type": "task_started", "agent": initial_agent.lower(), "ts": now}],
                "evidence": [],
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            return self._write(document)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._read(task_id)

    def record_event(self, task_id: str, event_type: str, *,
                     agent: Optional[str] = None,
                     evidence: Optional[Dict[str, Any]] = None,
                     context: Optional[Dict[str, Any]] = None,
                     status: Optional[str] = None,
                     **details: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            document = self._read(task_id)
            if document is None:
                return None
            if document.get("status") in self.TERMINAL_STATUSES:
                # Preserve the late event for diagnosis, but never allow it to
                # mutate terminal status, result, or terminal evidence.
                document.setdefault("events", []).append({
                    "type": "late_event_ignored",
                    "ignored_event_type": event_type,
                    "reason": "task_already_terminal",
                    "ts": self._now(),
                })
                return self._write(document)
            cancellation_requested = document.get("status") == "CANCEL_REQUESTED"
            event = {"type": event_type, "ts": self._now(), **details}
            if agent:
                event["agent"] = agent.lower()
                agents = document.setdefault("agents", [])
                if agent.lower() not in agents:
                    agents.append(agent.lower())
            document.setdefault("events", []).append(event)
            if evidence:
                document.setdefault("evidence", []).append(copy.deepcopy(evidence))
            if context:
                document.setdefault("context", {}).setdefault("parameters", {}).update(copy.deepcopy(context))
                document.setdefault("parameters", {}).update(copy.deepcopy(context))
            if status and not cancellation_requested:
                document["status"] = status
            return self._write(document)

    def record_handoff(self, task_id: str, source_agent: str, target_agent: str,
                       goal: str, context: Dict[str, Any], reason: str,
                       depth: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            document = self._read(task_id)
            if document is None:
                return None
            ts = self._now()
            handoff = {
                "source_agent": source_agent.lower(),
                "target_agent": target_agent.lower(),
                "goal": goal,
                "context": copy.deepcopy(context),
                "reason": reason,
                "depth": depth,
                "ts": ts,
            }
            document.setdefault("handoffs", []).append(handoff)
            document.setdefault("events", []).append({
                "type": "agent_handoff",
                "source_agent": source_agent.lower(),
                "target_agent": target_agent.lower(),
                "reason": reason,
                "depth": depth,
                "ts": ts,
            })
            document.setdefault("agents", [])
            for name in (source_agent, target_agent):
                if name.lower() not in document["agents"]:
                    document["agents"].append(name.lower())
            document.setdefault("context", {}).setdefault("parameters", {}).update(copy.deepcopy(context))
            document.setdefault("parameters", {}).update(copy.deepcopy(context))
            return self._write(document)

    def complete(self, task_id: str, status: str, result: Any = None,
                 evidence: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            document = self._read(task_id)
            if document is None:
                return None
            if document.get("status") in self.TERMINAL_STATUSES:
                document.setdefault("events", []).append({
                    "type": "late_terminal_result_ignored",
                    "attempted_status": status,
                    "reason": "task_already_terminal",
                    "ts": self._now(),
                })
                return self._write(document)
            if document.get("status") == "CANCEL_REQUESTED" and status != "CANCELLED":
                document.setdefault("events", []).append({
                    "type": "late_terminal_result_ignored",
                    "attempted_status": status,
                    "reason": "cancellation_requested",
                    "ts": self._now(),
                })
                return self._write(document)
            document["status"] = status
            document["result"] = copy.deepcopy(result)
            if evidence:
                document.setdefault("evidence", []).append(copy.deepcopy(evidence))
            document.setdefault("events", []).append({
                "type": "task_completed", "status": status, "ts": self._now(),
            })
            return self._write(document)

    def cancel(self, task_id: str, reason: str = "operator") -> Optional[Dict[str, Any]]:
        """Request cooperative cancellation without pretending it is complete."""
        current = self.get(task_id)
        if current and current.get("status") in self.TERMINAL_STATUSES:
            return current
        return self.record_event(task_id, "cancel_requested", status="CANCEL_REQUESTED", reason=reason)


task_junction = SharedTaskJunction()

"""
AEGIS Agent Contract & Shared Execution Models
===============================================
Defines typed, standard interfaces for agent execution, handoffs,
lifecycle tracking, structured errors, and verification evidence.
"""

import time
import uuid
from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict


class AgentLifecycleState(str, Enum):
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    BUSY = "BUSY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class ExecutionEnvironment(str, Enum):
    DESKTOP = "DESKTOP"
    BROWSER = "BROWSER"
    TERMINAL = "TERMINAL"
    HYBRID = "HYBRID"
    INTERNAL = "INTERNAL"


class ErrorCode(str, Enum):
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    ACTION_REJECTED = "ACTION_REJECTED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    HANDOFF_LOOP_DETECTED = "HANDOFF_LOOP_DETECTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class StructuredError:
    error_code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    recoverable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredError":
        return cls(
            error_code=data.get("error_code", ErrorCode.UNKNOWN.value),
            message=data.get("message", "Unknown error"),
            details=data.get("details", {}),
            recoverable=data.get("recoverable", False),
        )


@dataclass
class TaskContext:
    goal: str
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    environment: ExecutionEnvironment = ExecutionEnvironment.DESKTOP
    deadline: Optional[float] = None
    cancelled: bool = False
    step_index: int = 0
    max_steps: int = 25
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        if self.deadline and time.time() > self.deadline:
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "parameters": self.parameters,
            "environment": self.environment.value if isinstance(self.environment, ExecutionEnvironment) else str(self.environment),
            "deadline": self.deadline,
            "cancelled": self.cancelled,
            "step_index": self.step_index,
            "max_steps": self.max_steps,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskContext":
        env_raw = data.get("environment", ExecutionEnvironment.DESKTOP.value)
        try:
            env = ExecutionEnvironment(env_raw)
        except ValueError:
            env = ExecutionEnvironment.DESKTOP

        return cls(
            goal=data.get("goal", ""),
            task_id=data.get("task_id", str(uuid.uuid4())),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            session_id=data.get("session_id"),
            parameters=data.get("parameters", {}),
            environment=env,
            deadline=data.get("deadline"),
            cancelled=data.get("cancelled", False),
            step_index=data.get("step_index", 0),
            max_steps=data.get("max_steps", 25),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class ExecutionResult:
    task_id: str
    status: str  # "SUCCESS", "FAILED", "BLOCKED", "TIMEOUT", "CANCELLED"
    agent_id: str
    output: Any = None
    error: Optional[StructuredError] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    steps_executed: int = 0
    duration_ms: float = 0.0

    @property
    def is_success(self) -> bool:
        return self.status == "SUCCESS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "agent_id": self.agent_id,
            "output": self.output,
            "error": self.error.to_dict() if self.error else None,
            "evidence": self.evidence,
            "steps_executed": self.steps_executed,
            "duration_ms": self.duration_ms,
        }


@dataclass
class SwarmHandoffEnvelope:
    source_agent: str
    target_agent: str
    goal: str
    context: TaskContext
    reason: str = "Delegation"
    handoff_depth: int = 1
    max_depth: int = 5
    visited_agents: List[str] = field(default_factory=list)

    def can_handoff(self) -> bool:
        if self.handoff_depth >= self.max_depth:
            return False
        if self.visited_agents.count(self.target_agent.lower()) >= 2:
            return False
        return True

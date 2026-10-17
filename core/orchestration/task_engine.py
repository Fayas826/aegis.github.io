
import uuid
import time
import asyncio
import os
import json
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from core.cognition.reasoning.skills.skill_system import skill_registry
from core.reliability.reliability_engine import reliability_engine
from infrastructure.config.settings import TASK_HISTORY_PATH
from core.execution.universal_dispatch import (
    UNIVERSAL_ACTION_TYPES,
    execute_universal_goal,
    goal_for_action,
    universal_mode,
)

@dataclass
class TaskStep:
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    action_type: str = "" # "OS_COMMAND", "APP_OPEN", "SKILL", "UI_AUTOMATION"
    payload: Dict = field(default_factory=dict)
    status: str = "PENDING"
    result: Optional[Dict] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    rollback_payload: Optional[Dict] = None # Logic to undo this step

@dataclass
class Task:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original_intent: str = ""
    plan_description: str = ""
    steps: List[TaskStep] = field(default_factory=list)
    status: str = "PENDING"
    context: Dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

class TaskEngine:
    """🚀 O.M.E.G.A. TASK_ENGINE: Intelligent Execution & Rollback Layer."""
    
    def __init__(self, controller_callback: Callable):
        self.controller = controller_callback
        self.active_tasks: Dict[str, Task] = {}
        self.history_path = str(TASK_HISTORY_PATH)
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)

    async def execute_task(self, task: Task):
        print(f"[TASK_ENGINE] Executing Task: {task.task_id}")
        task.status = "EXECUTING"
        self.active_tasks[task.task_id] = task

        executed_steps = []
        for step in task.steps:
            if task.status == "CANCELLED":
                break
                
            success = await self._execute_step(task, step)
            if success:
                executed_steps.append(step)
            else:
                task.status = "FAILED"
                print("[TASK_ENGINE] Task FAILED. Initiating rollback...")
                reliability_engine.analyze_failure(
                    "TASK_ENGINE",
                    step.error or "STEP_EXECUTION_FAILED",
                    {"task_id": task.task_id, "step": step.description, "action_type": step.action_type},
                )
                await self._rollback_task(executed_steps)
                break
        else:
            task.status = "SUCCESS"
            task.completed_at = time.time()

        self._archive_task(task)
        self.active_tasks.pop(task.task_id, None)
        return task

    async def _execute_step(self, task: Task, step: TaskStep) -> bool:
        if not self._is_step_safe(step):
            step.status = "FAILED"
            step.error = "SAFETY_VIOLATION"
            return False

        step.status = "EXECUTING"
        step.started_at = time.time()
        
        while step.retry_count <= step.max_retries:
            try:
                # 🧩 SKILL INTEGRATION
                if step.action_type == "SKILL":
                    skill_name = step.payload.get("skill_name")
                    skill = skill_registry.get_skill(skill_name)
                    if skill:
                        result = await skill.execute(step.payload, self.controller)
                    else:
                        raise Exception(f"Skill not found: {skill_name}")
                else:
                    result = await self._dispatch_step(step)
                
                if result.get("status") in {"SUCCESS", "VERIFIED"}:
                    step.status = "SUCCESS"
                    step.result = result
                    step.ended_at = time.time()
                    return True
                else:
                    raise Exception(result.get("error", "Execution fail"))
                    
            except Exception as e:
                step.retry_count += 1
                step.error = str(e)
                if step.retry_count > step.max_retries:
                    step.status = "FAILED"
                    return False
                await asyncio.sleep(1)
        return False

    async def _dispatch_step(self, step: TaskStep) -> Dict:
        """Use universal execution by default, with a per-step legacy escape hatch.

        The old controller remains intact and is selected with
        ``payload.execution_mode = 'legacy'``.  This lets existing workflows
        migrate incrementally without making app-specific actions disappear.
        """

        if step.action_type in UNIVERSAL_ACTION_TYPES and universal_mode(step.payload) != "legacy":
            surface = step.payload.get("surface") if isinstance(step.payload, dict) else None
            goal = goal_for_action(step.action_type, step.payload, step.description)
            return await execute_universal_goal(goal, surface=surface)
        return await self.controller(step.action_type, step.payload)

    async def _rollback_task(self, steps: List[TaskStep]):
        """Undoes successful steps in reverse order."""
        for step in reversed(steps):
            if step.rollback_payload:
                print(f"[ROLLBACK] Undoing: {step.description}")
                await self.controller(step.rollback_payload.get("action_type"), step.rollback_payload.get("payload"))

    def _is_step_safe(self, step: TaskStep) -> bool:
        # Enforce Hard Safety Rules
        action = step.action_type
        payload = step.payload
        if action == "FILE_OP":
            path = payload.get("path", "").lower()
            if any(p in path for p in ["core/", "system/", "config/"]): return False
            if payload.get("operation") == "delete" and "data/temp" not in path: return False
        if action == "OS_COMMAND":
            cmd = payload.get("command", "").lower()
            if any(t in cmd for t in ["rm -rf", "del /s", "format", "reg delete"]): return False
        return True

    def _archive_task(self, task: Task):
        try:
            history = []
            if os.path.exists(self.history_path):
                with open(self.history_path, 'r') as f: history = json.load(f)
            
            history.append({
                "id": task.task_id,
                "intent": task.original_intent,
                "status": task.status,
                "duration": (task.completed_at - task.created_at) if task.completed_at else 0,
                "timestamp": datetime.now().isoformat()
            })
            with open(self.history_path, 'w') as f: json.dump(history[-100:], f, indent=4)
        except: pass

# Global instance
task_engine = None

def init_task_engine(controller):
    global task_engine
    task_engine = TaskEngine(controller)
    return task_engine

def get_task_engine():
    return task_engine

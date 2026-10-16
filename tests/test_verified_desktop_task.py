"""Offline state-machine tests for the verified desktop text workflow."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from core.execution.verified_desktop_task import VerifiedDesktopTextTask
from core.orchestration.agent_contract import TaskContext


class MemoryJunction:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    def start(self, context, initial_agent, parameters=None):
        self.documents[context.task_id] = {
            "task_id": context.task_id,
            "status": "RUNNING",
            "events": [],
            "evidence": [],
            "parameters": parameters or {},
        }
        return self.documents[context.task_id]

    def get(self, task_id):
        return self.documents.get(task_id)

    def record_event(self, task_id, event_type, evidence=None, **details):
        document = self.documents[task_id]
        document["events"].append({"type": event_type, **details})
        if evidence:
            document["evidence"].append(evidence)
        return document

    def complete(self, task_id, status, result=None, evidence=None):
        document = self.documents[task_id]
        document["status"] = status
        document["result"] = result
        if evidence:
            document["evidence"].append(evidence)
        return document


class FakeProcess:
    def __init__(self) -> None:
        self.closed = False

    def poll(self):
        return 0 if self.closed else None


class FakeController:
    def __init__(self) -> None:
        self.focus_calls: list[str] = []
        self.screenshots: list[str] = []

    def focus_application(self, title, minimize_others=False):
        self.focus_calls.append(title)
        return 100 + len(self.focus_calls)

    def take_screenshot_safely(self, path):
        self.screenshots.append(path)
        Path(path).write_bytes(b"fake-evidence")
        return True


class FakeInput:
    def __init__(self, content: str, altered: bool = False) -> None:
        self.content = content
        self.altered = altered
        self.last_paste = ""
        self.save_requested = False
        self.target: Path | None = None
        self.events: list[tuple[str, tuple[str, ...]]] = []

    def hotkey(self, *keys):
        self.events.append(("hotkey", keys))
        if keys == ("ctrl", "s"):
            self.save_requested = True

    def paste(self, text):
        self.events.append(("paste", (text,)))
        self.last_paste = text
        if self.save_requested:
            self.target = Path(text)

    def press(self, key):
        self.events.append(("press", (key,)))
        if key == "enter" and self.target is not None:
            value = "different" if self.altered else self.content
            self.target.write_text(value, encoding="utf-8")


def _task(tmp_path, input_adapter, junction=None, *, platform="win32"):
    controller = FakeController()
    processes: list[FakeProcess] = []
    junction = junction or MemoryJunction()

    def launch(_command):
        process = FakeProcess()
        processes.append(process)
        return process

    async def no_sleep(_seconds):
        return None

    runner = VerifiedDesktopTextTask(
        controller=controller,
        input_adapter=input_adapter,
        launcher=launch,
        junction=junction,
        observer=lambda: {"title": "Notepad", "timestamp": 1.0},
        screenshot=controller.take_screenshot_safely,
        sleep=no_sleep,
        platform_name=platform,
    )
    target = tmp_path / "aegis_desktop_test.txt"
    return runner, target, processes


def test_success_requires_save_reopen_and_independent_readback(tmp_path):
    content = "AEGIS desktop autonomy test"
    junction = MemoryJunction()
    runner, target, processes = _task(tmp_path, FakeInput(content), junction)

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content=content,
            destination=target,
            allowed_roots=[tmp_path],
            context=TaskContext(goal="save and verify a text file"),
            evidence_dir=tmp_path / "evidence",
        )
    )

    assert result.status == "VERIFIED"
    assert target.read_text(encoding="utf-8") == content
    assert result.evidence["verification"]["method"] == "independent_file_readback_after_reopen"
    assert len(processes) == 2
    assert junction.documents[result.task_id]["status"] == "VERIFIED"
    assert junction.documents[result.task_id]["evidence"]


def test_content_mismatch_is_unverified_not_success(tmp_path):
    content = "expected content"
    runner, target, _ = _task(tmp_path, FakeInput(content, altered=True))

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content=content,
            destination=target,
            allowed_roots=[tmp_path],
        )
    )

    assert result.status == "UNVERIFIED"
    assert "differs" in result.message


def test_destination_outside_allowed_root_is_blocked_before_launch(tmp_path):
    input_adapter = FakeInput("content")
    runner, target, processes = _task(tmp_path, input_adapter)

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content="content",
            destination=target,
            allowed_roots=[tmp_path / "different-root"],
        )
    )

    assert result.status == "BLOCKED"
    assert processes == []


def test_non_windows_desktop_is_explicitly_unavailable(tmp_path):
    runner, target, processes = _task(tmp_path, FakeInput("content"), platform="linux")

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content="content",
            destination=target,
            allowed_roots=[tmp_path],
        )
    )

    assert result.status == "UNAVAILABLE"
    assert processes == []


def test_existing_destination_is_blocked_without_explicit_overwrite(tmp_path):
    target = tmp_path / "aegis_desktop_test.txt"
    target.write_text("pre-existing", encoding="utf-8")
    runner, _, processes = _task(tmp_path, FakeInput("content"))

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content="content",
            destination=target,
            allowed_roots=[tmp_path],
        )
    )

    assert result.status == "BLOCKED"
    assert target.read_text(encoding="utf-8") == "pre-existing"
    assert processes == []


def test_cancelled_context_does_not_launch_application(tmp_path):
    context = TaskContext(goal="cancelled desktop task", cancelled=True)
    runner, target, processes = _task(tmp_path, FakeInput("content"))

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content="content",
            destination=target,
            allowed_roots=[tmp_path],
            context=context,
        )
    )

    assert result.status == "CANCELLED"
    assert processes == []


def test_expired_context_is_timeout_not_cancellation(tmp_path):
    context = TaskContext(goal="expired desktop task", deadline=time.time() - 1)
    runner, target, processes = _task(tmp_path, FakeInput("content"))

    result = asyncio.run(
        runner.run(
            app_command=["notepad.exe"],
            app_title="Notepad",
            content="content",
            destination=target,
            allowed_roots=[tmp_path],
            context=context,
        )
    )

    assert result.status == "TIMEOUT"
    assert processes == []

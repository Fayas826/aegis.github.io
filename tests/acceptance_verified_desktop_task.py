"""Real Windows acceptance for the generic verified desktop text workflow.

Run from the repository root with:

    python tests/acceptance_verified_desktop_task.py

The run is bounded to ``data/temp/aegis_desktop_acceptance`` and writes
optional screenshot evidence under ``data/evidence``.  On non-Windows hosts
the workflow returns ``UNAVAILABLE`` instead of pretending a desktop exists.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution.verified_desktop_task import VerifiedDesktopTextTask
from core.orchestration.agent_contract import ExecutionEnvironment, TaskContext


async def main() -> int:
    root = ROOT
    allowed_root = root / "data" / "temp" / "aegis_desktop_acceptance"
    target = allowed_root / "aegis_desktop_test.txt"
    result = await VerifiedDesktopTextTask().run(
        app_command=["notepad.exe"],
        app_title="Notepad",
        content="AEGIS desktop autonomy test",
        destination=target,
        allowed_roots=[allowed_root],
        context=TaskContext(
            goal="Open Notepad, save a text file, reopen it, and verify its contents",
            environment=ExecutionEnvironment.DESKTOP,
        ),
        evidence_dir=root / "data" / "evidence",
        overwrite=True,
    )
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0 if result.status == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

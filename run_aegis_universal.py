#!/usr/bin/env python3
"""Run one AEGIS universal desktop/browser goal.

Examples:
    python run_aegis_universal.py "open Chrome"
    python run_aegis_universal.py "search the web for Python 3.13 release notes"
    python run_aegis_universal.py --voice

This entrypoint uses the observe-plan-act-verify runtime directly rather than
the legacy app-name dispatch table. It still respects the worker's safety,
confirmation, authorization, and verification boundaries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser(description="AEGIS universal observe-plan-act-verify runner")
    parser.add_argument("goal", nargs="*", help="A concrete desktop or browser goal")
    parser.add_argument("--voice", action="store_true", help="Start optional Gemini Live voice/screen session")
    parser.add_argument("--headless", action="store_true", help="Use a headless browser where supported")
    args = parser.parse_args()

    if args.voice:
        from core.runtime.gemini_live_voice import GeminiLiveVoiceSession

        result = await GeminiLiveVoiceSession().run()
    else:
        goal = " ".join(args.goal).strip()
        if not goal:
            parser.error("a goal is required unless --voice is used")
        from core.runtime.multimodal_runtime import MultimodalRuntimeAdapter

        result = await MultimodalRuntimeAdapter().execute_goal(goal, headless=args.headless)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") in {"VERIFIED", "READY", "STOPPED"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

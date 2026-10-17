# Phase 4.5H validation — in progress

## Scope of this working step

The existing universal perception/action layers, TaskEngine dispatch,
multimodal adapter, GUI worker, VerificationPlanner and SharedTaskJunction were
hardened in place. No alternate desktop framework or application-specific
save/calculation workflow was added. The canonical agent roster and frontend
source were not edited by this step.

- Native window identity, display origin, screenshot MIME and UIA state are
  retained across fresh decisions. Coordinate actions require pixel-exact
  grounding; semantic actions still require stable window/control identity.
- General application discovery and exact window focusing replace guessing
  application aliases. Ambiguous discoveries fail closed.
- Gemini and OpenAI share one bounded action schema and worker loop. Only one
  action may execute per observation; batches, repeated ineffective actions,
  stale targets and exhausted recovery budgets stop rather than replaying input.
- TaskEngine forwards original intent separately from the current step. Its
  host-owned task context carries allowed roots, verification and permissions;
  planner node payloads cannot grant those privileges or extend host deadlines.
- Cancellation, deadlines, pending confirmation and nested negative verdicts
  remain negative across adapter, registry and TaskEngine envelopes.
- Compound verification uses independent host-owned predicates. Bounded file
  readback includes a pre-action identity baseline; a pre-existing matching file
  cannot prove a save. Reopening requires observed document disappearance from
  the window inventory before reappearance, not merely a focus switch.
- Images are retained only in an explicitly authorized evidence directory.
  Junction receipts retain actual proposed actions/results and verification.

## Checks performed

Focused offline command:

```bash
python -m pytest -q tests/test_universal_runtime_safety.py tests/test_universal_desktop_layer.py tests/test_universal_gui_worker.py tests/test_multimodal_runtime.py tests/test_verification_planner.py tests/test_shared_task_junction.py
```

Result after the final authority/provider/lifecycle regressions: **68 passed in 25.85 seconds**.
These tests use injected providers and input backends; they are not evidence of
live application operation.

- `python -m py_compile` on the affected runtime, perception, execution,
  verification, TaskEngine and registry modules **passed** with the final tests.
- `npm test -- --runInBand`: **3 passed**. Redis connection-refused errors and
  Jest force-exit/open-handle warnings are still present.
- `npm run build`: **passed** (TypeScript).
- `npm --prefix frontend run build`: **passed**; large-chunk warnings remain.
- The exact Python file list from `Makefile` timed out at 120 seconds. A verbose
  retry collected 215 tests and subsequently crashed with a Windows access
  violation in Torch/Transformers model materialization while the vision suite
  was running. Background self-healing/voice initialization loaded a local
  emotion model, and the vision test also reached an existing cloud fallback.
  Therefore the declared complete suite is **not passed** and is not reliably
  isolated/offline. It was not repeatedly rerun after the crash.
- `make` is not installed on this host; individual declared stages were used.
- Docker CLI is installed but its Linux daemon is unavailable. The gateway
  Dockerfile now pins Node 22.17.0, uses `npm ci`, includes the lockfile and copies
  only source/public assets. **A container build was not verified.** It remains
  a gateway image, not a Windows desktop environment or full-project image.

## Live acceptance prerequisites and receipts

Read-only native discovery found installed text-editor and calculator entries
and File Explorer/window entries. This proves inventory access, not successful
launch, focus, input or task completion.

At the last check, `GEMINI_API_KEY`, `GOOGLE_API_KEY` and `OPENAI_API_KEY` were
all absent. No secret values were read into this document. Three compound goals
were submitted at the real TaskEngine step-dispatch seam; each returned
`UNAVAILABLE` with **zero actions**:

- Text editor: create/save/close/reopen/readback. Junction task ID
  `e318c900-7a13-4cfe-91d1-405e97a87fdb`.
- Calculator: calculate and verify a result. Junction task ID
  `89a4cf50-9fb8-4b81-bd18-503ea76719c5`.
- File manager: navigate and verify an authorized folder. Junction task ID
  `4588b4d0-20da-4c53-ae23-9ebdc7a32e72`.

These local junction IDs are diagnostic pointers, not portable evidence files.
No document was created manually to manufacture GUI acceptance evidence.
Three-category model-driven acceptance is **NOT RUN / BLOCKED**, not passed.

## Remaining work before claiming Phase 4.5H complete

1. Configure one supported provider securely in the runner's environment.
2. Supply host-owned task verification contracts and explicitly allowed roots.
   Run reversible model-selected tasks across at least three actual application
   categories, including the complete document workflow, with independent
   saved-file readback and reopening evidence.
3. Review/migrate older direct chat/voice fast paths that still bypass TaskEngine;
   this working step does not claim every UI/voice entry uses the canonical loop.
4. Fully isolate the declared offline tests from hardware, self-healing and cloud
   fallback. Validate the complete suite on a clean Linux machine.
5. Validate clean container builds. The existing Python dependency set and
   separate backend image have not been proven reproducible by this step.

Safety is bounded, not universal: native title/window evidence cannot prove
canonical paths inside every application, UIA can be absent or incomplete,
generic clipboard pastes may need deferred restoration, and heuristics are not
a sandbox. Sensitive/protected surfaces require operator involvement; hosts
must supply their action policy where stronger authorization is required.

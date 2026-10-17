# Local desktop inference and circuit-breaker repair

## What was wrong

The GUI worker selected only Gemini/OpenAI even though the existing local gateway
and installed Ollama models were present. Local application weights do not need
a cloud API key. The local path now extends `core/model_gateway/local_provider.py`
and joins the existing worker; it is not a parallel desktop execution runtime.

Additional reproduced defects:

- The breaker did not reset consecutive failures on successful `call()`, allowed
  multiple concurrent HALF_OPEN probes, and treated creation of a coroutine as a
  successful operation. Its default trip notification also imported other app
  subsystems into a failure path.
- The local server was configured with `OLLAMA_HOST=0.0.0.0`. That is a bind
  address, not a safe client destination. The client now maps wildcard binds to
  literal loopback and applies the normal Ollama port when omitted.
- SQLite task lookup filtered only the newest `limit*5` candidates. Older task
  IDs disappeared from cancellation and could be duplicated by upsert. A
  failing regression reproduced this; lookup now filters before limiting results.
- Native UIA reads can stall inside COM despite a traversal-node budget. Native
  scans now have a bounded read-only daemon boundary, at most one outstanding
  scan, and no reuse of late controls. A stalled read does not authorize input.
- Plain `.exe` display-name aliases did not resolve packaged applications. The
  action layer now normalizes only simple display aliases against real inventory,
  not against an application-specific command map.
- After an action already satisfied independent goal predicates, the worker
  could ask for another action unnecessarily. It now stops at those predicates,
  or returns immediately when fresh evidence establishes an already-satisfied goal.

## Boundaries

`probe_desktop()` inspects `/api/tags` and `/api/show`; AVAILABLE means reachable
installed metadata, **not proven inference**. `plan_desktop()` requests one bounded
JSON response at `/api/generate`, validates model identity/completion, and runs
transport/decoding failures through the existing per-model circuit breaker.

The client uses literal loopback, no proxy environment, no redirects, no model
downloads, no cloud fallback, no RAG lookup, and no prompt/image logging. Local
plans must match the worker's one-action contract and pass its normal action,
permission, cancellation, confirmation, freshness and verification checks.
Text models cannot emit visual-coordinate actions. Window-only evidence allows
window operations but cannot authorize control clicks, typing or hotkeys.

Breakers use a monotonic clock, protected state transitions, one HALF_OPEN probe,
and `call_async()` for awaited work. Manual `can_execute()` callers must pair an
admitted operation with `record_success()`/`record_failure()` in the same owner.
A breaker **does not kill** an already-running native operation; transport and
UIA deadlines remain separate. Optional trip callbacks are explicitly injected
and must be bounded/nonblocking.

## Configuration

Use an installed model ID; the runtime never pulls missing weights. For example:

```powershell
$env:AEGIS_GUI_PROVIDER = "local"
$env:AEGIS_GUI_LOCAL_MODEL = "aegis-sovereign:latest"
$env:AEGIS_LOCAL_FIRST = "true"
python run_aegis_universal.py "Open Calculator"
```

The model above was found installed in this workspace's runner environment.
Another machine must supply its own installed model. `OLLAMA_HOST` defaults to
`http://127.0.0.1:11434`. Explicit missing models fail closed. The CLI consumes
process environment settings and does not load `.env` automatically.

## Actual verification

Focused offline command:

```bash
python -m pytest -q tests/test_circuit_breaker.py tests/test_local_desktop_provider.py tests/test_local_desktop_worker.py tests/test_storage_task_lookup.py tests/test_universal_gui_worker.py tests/test_universal_runtime_safety.py tests/test_universal_desktop_layer.py tests/test_multimodal_runtime.py tests/test_verification_planner.py tests/test_shared_task_junction.py
```

- **191 tests passed in 39.02 seconds.** HTTP/provider/input tests use mocks; GUI
  tests are not claimed as live multi-application acceptance.
- Live `aegis-sovereign:latest` inference returned the requested valid action
  JSON from a synthetic native-control prompt. No desktop action was sent by
  that inference smoke check.
- Live Moondream metadata advertised vision, but synthetic-image generation
  failed its bounded request. No working Moondream vision inference is claimed.
- Native UIA initially appeared unavailable because a real bounded traversal of
  the active Windows tree takes about **13.78 seconds** and returns roughly
  3,084 controls on this machine. The default bounded read budget is now 15
  seconds (hard cap 20), still with one outstanding daemon scan and late-result
  discard. This is a latency tradeoff, not an unbounded wait; control actions
  still require a fresh successful scan.
- An earlier guarded GUI attempt timed out. The runner was confirmed absent,
  cancellation was persisted after the task-lookup repair, and its action effect
  remains UNVERIFIED. No automatic replay was made while that runner was uncertain.
- A subsequent local-model Calculator launch reported SUCCESS and fresh window
  verification matched, but the model's unnecessary next focus proposal was
  denied by the host policy, leaving that attempt BLOCKED. This exposed the
  stop-after-postcondition bug fixed above.
- A final **read-only** TaskEngine run returned VERIFIED for the already-open
  Calculator with **zero inputs**, proving the fresh independent state check.
  It does not pretend a new model invocation or application launch happened in
  that read-only run.
- The real document save/reopen attempt is **not verified**. The first run
  stopped before input because the default vision model was unavailable. The
  explicit local-text run generated one invalid placeholder action, then typed
  into the already-focused existing Notepad document before the mission failed.
  A fresh observation identified that document and a single bounded Ctrl+Z
  reversed that known type action; the document was not saved or closed by
  AEGIS. A new guard now blocks `type_text` for create/save/reopen goals while
  an existing filename is focused unless the host explicitly sets
  `allow_existing_document_input`. The intended workflow remains pending until
  it starts from a fresh/untitled editor and the local model produces a valid
  bounded sequence.
- Subsequent bounded local-model window acceptance through the canonical
  TaskEngine path verified four application categories: Notepad (text editor,
  task `69751a65-dee8-48e4-98b7-d2e16bddabc3`), Calculator (calculator,
  task `b454dc36-83c0-4364-995a-3dfd2ebfc432`), Paint (graphics editor,
  task `c170d695-eebb-497c-94f2-e36819aca742`), and File Explorer (Windows
  shell/file manager, task `98e60e9b-4297-457f-85c8-2afc320cda13`). Each used
  `aegis-sovereign:latest`; all final results were `VERIFIED`. Some receipts
  include an intermediate blocked focus proposal followed by a safe replan and
  successful launch; the worker did not replay an uncertain input.

Private junction pointers for diagnosis: timed-out attempt
`c854b61c-32d6-466f-83c4-00879ea97492` (CANCELLED, effect unverified), guarded launch
`71fc0dd8-ab9f-43c6-aac0-773a6bded97d`, and read-only verification
`45b7b865-eb2f-4bee-b706-dac5d5fb0e10`. Raw screenshots/prompts/memory are not copied
into this document or committed.

Full three-category operation and document save/close/reopen/readback remain
pending. The existing broad-suite Torch crash, older UI/voice bypasses and
unverified clean Linux/container build described in `phase-4.5h-validation.md`
are not represented as fixed by these local runtime checks.

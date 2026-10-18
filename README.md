# AEGIS

AEGIS is a Windows-first AI automation workspace with Python voice/vision and
desktop execution services, a TypeScript enterprise gateway, and a React HUD.
It is not an unrestricted universal agent: browser authentication, CAPTCHA/MFA,
protected Windows surfaces, external providers, and hardware capabilities remain
explicit prerequisites.

## Runtime

- Python: 3.11.9 (`.python-version`)
- Node: 22.17.0 (`.nvmrc`)
- Python backend: FastAPI on port 8000
- Node gateway: Express on port 4000
- Frontend: Vite under `frontend/`

## Setup

1. Copy `.env.example` to `.env` and replace placeholder secrets. Never commit
   `.env`, API keys, browser cookies, or generated audio/screenshots.
2. Install Node dependencies with the lockfile:

   ```bash
   npm ci
   ```

3. Install Python backend dependencies in a project-local virtual environment.
   The exact direct runtime versions are kept in `backend/requirements.lock`:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r backend/requirements.lock
   ```

## Run

Build and run the Node gateway:

```bash
npm run build
npm start
```

Run the Python backend in another terminal:

```bash
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

The Windows desktop/voice runtime is a separate environment because it needs an
interactive Windows session, native input drivers, and optional audio/UI
automation packages:

```powershell
py -3.11 -m venv .venv-aegis
.\.venv-aegis\Scripts\python -m pip install -r aegis\requirements.lock
```

Do not install the AEGIS desktop lock into the Linux backend container.

Run the HUD during development:

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

Realtime cloud voice is opt-in. Keep the privacy defaults unless cloud voice
has been explicitly authorized:

```text
AEGIS_LOCAL_FIRST=false
AEGIS_VOICE_TRANSPORT=realtime
OPENAI_API_KEY=...
```

Without both settings and a valid server-side key, the realtime route returns an
explicit unavailable response and does not make a cloud call.

## Tests and build

The single supported offline validation entry point is:

```bash
make test
```

It runs the deterministic Python voice/vision/computer-use/realtime-contract
tests, the isolated Node Jest suite, and the TypeScript build. The tests do not
claim that a real microphone, speaker, cloud model, authenticated browser, or
Redis/Mongo service is available.

Windows users can run the same stages directly:

```powershell
python -m pytest tests/smoke/test_phase_r_voice.py tests/smoke/test_phase_o_computer_use.py tests/smoke/test_phase1_hybrid_brain.py tests/test_phase4_5_vision_integrity.py tests/test_screen_endpoint_truthfulness.py tests/test_tts_style_controls.py tests/smoke/test_phase_s_realtime.py -q
npm test -- --runInBand
npm run build
```

## Safety and real-world execution

- Consequential actions remain confirmation-gated.
- CAPTCHA, MFA, login prompts, anti-bot challenges, payment, and purchases
  require user participation or explicit confirmation.
- A screenshot failure is reported as `VISION_UNAVAILABLE`; stale or blank
  fallback images are not treated as current evidence.
- Real browser and desktop success requires post-action observation and
  verification; an issued click or navigation command is not proof of success.

## Current validation boundary

The repository contains working simulated acceptance coverage for voice session
state, VAD, barge-in, hybrid orchestration, browser contracts, screenshot
truthfulness, and local TTS style processing. A live hardware voice session and
arbitrary authenticated web automation still require machine-specific setup and
must be reported separately from offline test results.

The root Dockerfile currently builds the Node gateway only. The Python backend,
GPU/audio drivers, browser binaries, Redis, and MongoDB are separate runtime
dependencies and are not silently represented as fully containerized support.

Build and deploy the gateway with the pinned Node image and committed lockfile:

```bash
docker build -t aegis-gateway .
docker run --rm --env-file .env -p 4000:4000 aegis-gateway
```

Only the Node source and public assets enter this image; workspace memory,
screenshots and local configuration are not copied. Browser downloads are
disabled during this gateway build. Windows desktop input requires an
interactive authorized Windows session and is not available in a Linux container.

Build the Python API image separately from the repository root:

```bash
docker build -f backend/Dockerfile -t aegis-api .
docker run --rm --env-file .env -p 8000:8000 aegis-api
```

Or start the API service through Compose:

```bash
docker compose up --build api
```

The root `Dockerfile` remains the Node gateway image. `package-lock.json`,
`backend/requirements.lock`, and `aegis/requirements.lock` are the committed
dependency lockfiles for the three supported runtime surfaces. A Linux
container cannot control the host Windows desktop, capture its microphone, or
use Windows UI Automation.

## Universal desktop runtime (Phase 4.5H work in progress)

TaskEngine's universal steps now forward a structured `TaskContext` through the
multimodal adapter to the existing GUI worker. Original intent and current step
remain separate. A failed or unverified mission is not blindly replayed, and
`CANCELLED`, `TIMEOUT`, `PENDING_CONFIRMATION` and nested failures stay negative.
The registry entry is `desktop.universal_goal`; the explicit legacy escape hatch
remains `payload.execution_mode = "legacy"`.

Local Ollama, Gemini and OpenAI share bounded actions and fresh-observation
grounding. **Cloud keys are not required for installed local models.** Automatic
selection prefers an installed local model; `AEGIS_GUI_PROVIDER=local` makes the
local boundary explicit. No model is pulled or downloaded by the desktop worker.
`AEGIS_GUI_LOCAL_MODEL` selects an exact installed model ID. `OLLAMA_HOST` or
`OLLAMA_BASE_URL` must identify loopback; a server wildcard bind is mapped to
loopback rather than used as a destination. Proxies and redirects are disabled.

For example, on Windows with your existing sovereign model installed:

```powershell
$env:AEGIS_GUI_PROVIDER = "local"
$env:AEGIS_GUI_LOCAL_MODEL = "aegis-sovereign:latest"
$env:AEGIS_LOCAL_FIRST = "true"
python run_aegis_universal.py "Open Calculator"
```

Text models use fresh native accessibility/window evidence, not imagined visual
coordinates. Window inventory alone permits window operations, not typing or
clicking controls. Vision metadata does not establish working inference: the
latest Moondream synthetic-image check timed out, so choose a verified working
model explicitly. `GEMINI_API_KEY`/`OPENAI_API_KEY` are needed only for explicit
cloud choices (`AEGIS_GUI_PROVIDER=gemini`/`openai`). The CLI does not automatically
load `.env`. Never commit keys or put them in goals. A selected-provider failure
does not silently switch providers, execute a script, or authorize cloud fallback.

```bash
python run_aegis_universal.py "Open the editor and prepare a short draft"
python -m pytest -q tests/test_universal_runtime_safety.py tests/test_universal_desktop_layer.py tests/test_universal_gui_worker.py tests/test_multimodal_runtime.py tests/test_verification_planner.py tests/test_shared_task_junction.py
```

Compound goals remain `UNVERIFIED` without independent caller-owned predicates
in `TaskContext.parameters.verification`. File readback requires explicit
`allowed_roots`, bounded UTF-8 content and a before-action identity baseline;
an already-existing matching file does not prove a save. `require_reopen` also
requires disappearance from the window inventory before reappearance, not just
switching focus. This is native title/inventory evidence, not proof of a full
canonical file path inside arbitrary applications.

Offline mocks are not live acceptance. Three-category model-driven acceptance
and a real multi-step save/close/reopen/readback are still pending. Some older
chat/voice fast paths bypass TaskEngine and have not been migrated in this step.
See `docs/phase-4.5h-validation.md` for actual checks and remaining blockers.
The local-model/circuit-breaker follow-up is in `docs/local-desktop-runtime.md`.

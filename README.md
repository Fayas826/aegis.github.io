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

3. Install Python dependencies in a project-local virtual environment:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r backend/requirements.txt
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

Run the HUD during development:

```bash
npm --prefix frontend install
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

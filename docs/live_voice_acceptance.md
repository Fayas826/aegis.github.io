# Live voice acceptance procedure

The repository's automated voice tests use injected microphone, STT, backend,
and TTS fakes. They prove lifecycle and cancellation behavior, not human speech
recognition on this machine. Use this procedure for the hardware-backed gate.

## Prerequisites

- Windows interactive, unlocked desktop session
- Working microphone and speakers visible to `sounddevice`
- Local Whisper/Vosk model already cached, or an explicitly authorized network
  connection for first-time model download
- Python dependencies installed from `backend/requirements.txt` plus the
  voice/audio dependencies used by the selected provider
- A running authenticated AEGIS Python backend on `127.0.0.1:8000`
- Keep `AEGIS_LOCAL_FIRST=true` for a local-only run; use realtime cloud voice
  only when `AEGIS_LOCAL_FIRST=false`, `AEGIS_VOICE_TRANSPORT=realtime`, and a
  server-side key are intentionally configured

## Preflight

```powershell
python -c "import sounddevice as sd; print(sd.query_devices())"
python -c "from core.voice.whisper_stt import whisper_stt; print(whisper_stt.status()); print('available=', whisper_stt.is_available())"
python -m pytest tests/smoke/test_phase_r_voice.py tests/smoke/test_phase_s_realtime.py -q
```

The first command must list an input and an output device. The second must
report an available STT model before a live result can be claimed.

## Run and observe

```powershell
python sentinel/voice_session_engine.py
```

Record evidence for one complete turn:

1. AEGIS starts in `LISTENING` after its greeting.
2. Speak a harmless command such as `open notepad`.
3. Confirm the observed state sequence includes capture, transcription,
   execution, speaking, and return to listening.
4. Interrupt a long response with `stop that`.
5. Confirm audio stops, a `BARGE_IN` event is recorded, and a second utterance
   is accepted without a second listener or duplicate session.
6. Disconnect or disable the provider/device and confirm a structured failure
   returns the session to a safe listening/error state.

Record time to first transcript, time to first audio, interruption latency,
provider name, model name, and the final session metrics. Do not record API
keys, authentication cookies, or private audio in the repository.

Until these steps are completed on the target machine, the supported claim is
**simulated voice lifecycle verified; live voice-to-voice unverified**.

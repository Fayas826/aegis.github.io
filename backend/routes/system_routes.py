import os
import time
import asyncio
import io
import psutil
from fastapi import APIRouter, Depends
from fastapi.responses import Response, JSONResponse
import pyautogui
from backend.middleware.auth import require_auth
from action.desktop_control.bio_link import bio_link
from core.cognition.reasoning.shared_state import NEURAL_INSIGHT_CACHE, NEURAL_LOCK

router = APIRouter()

@router.get("/bio/vitals")
async def get_bio_vitals():
    return bio_link.get_vitals()

@router.get("/resonance/vitals")
async def resonance_vitals():
    return {
        "cpu_load": f"{psutil.cpu_percent()}%",
        "ram_load": f"{psutil.virtual_memory().percent}%",
        "gpu": 0,
        "latency": "14ms",
        "network": {"sent": "0MB", "recv": "0MB"},
        "tasks": [],
    }

@router.post("/vision/focal_plane")
async def vision_focal_plane():
    with NEURAL_LOCK:
        insight = NEURAL_INSIGHT_CACHE.get("visual", "Scanning focal plane...")
    return {"status": "SUCCESS", "insight": insight}

@router.get("/iot/state")
async def get_iot_state():
    return {
        "status": "ONLINE",
        "devices": 4
    }

@router.get("/system/screenshot", dependencies=[Depends(require_auth)])
async def get_system_screenshot():
    try:
        capture_timeout_s = max(0.1, float(os.getenv("AEGIS_SCREENSHOT_TIMEOUT_S", "5")))
    except ValueError:
        capture_timeout_s = 5.0

    try:
        # Keep the blocking desktop call off the async event loop and bound its
        # wait. The response is built from this capture only; no shared path or
        # prior frame can be mistaken for current evidence.
        screenshot = await asyncio.wait_for(
            asyncio.to_thread(pyautogui.screenshot),
            timeout=capture_timeout_s,
        )
        # Downscale slightly for web load performance
        screenshot.thumbnail((1280, 720))
        buffer = io.BytesIO()
        screenshot.save(buffer, "PNG")
        captured_at_ns = time.time_ns()
    except Exception as capture_error:
        # Never serve a stale frame or a fabricated blank image as if it were
        # the current desktop.  Callers need an explicit, machine-readable
        # failure so vision and automation can stop safely.
        if isinstance(capture_error, asyncio.TimeoutError):
            detail = f"Screen capture timed out after {capture_timeout_s:.2f}s"
        else:
            detail = str(capture_error)
        return JSONResponse(
            status_code=503,
            content={
                "status": "VISION_UNAVAILABLE",
                "verified": False,
                "error_code": "DESKTOP_SESSION_UNAVAILABLE",
                "detail": detail,
            },
        )
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Aegis-Capture-Source": "pyautogui.desktop",
            "X-Aegis-Captured-At-Ns": str(captured_at_ns),
        },
    )

# Audio Cache to deliver <5ms replay latency for frequent phrases
_TTS_AUDIO_CACHE = {}

@router.get("/tts/speak")
@router.post("/tts/speak")
async def tts_speak(text: str = "", voice: str = "", style: str = ""):
    """
    🎙️ High-Fidelity Human Neural Speech Engine
    Streams Microsoft Azure Neural audio (no robotic artifacts).
    The active language + voice style from Settings are applied here, and a
    broken locale voice falls back to another voice of the *same* language
    before ever dropping to the local English voice.
    """
    from fastapi.responses import Response
    if not text or not text.strip():
        return Response(content=b"", media_type="audio/mpeg")

    try:
        from core import user_settings
        candidates = user_settings.voice_candidates(voice)
        tuning = user_settings.get_voice_tuning(style)
    except Exception:
        candidates = (voice or "en-GB-RyanNeural",)
        tuning = {"pitch": "-8Hz", "rate": "-12%"}

    # Final safety boundary: sanitize JSON/code/internal markers before speech.
    try:
        from core.orchestration.response_composer import response_composer
        clean_text = response_composer.prepare_speech(text)[:900]
    except Exception:
        clean_text = text.strip()[:900]
    if not clean_text:
        clean_text = "Task processed successfully."
    cache_key = f"{candidates[0]}|{tuning.get('pitch')}|{tuning.get('rate')}|{clean_text}"
    if cache_key in _TTS_AUDIO_CACHE:
        val = _TTS_AUDIO_CACHE[cache_key]
        if isinstance(val, tuple):
            cached_bytes, cached_media = val
        else:
            cached_bytes, cached_media = val, "audio/mpeg"
        return Response(content=cached_bytes, media_type=cached_media)

    # 0. Kokoro-82M Local Neural Offline Synthesizer + BERT Emotional Vocal Prosody
    try:
        from core.voice.council_voice_matrix import council_voice
        from core.voice.streaming_speech_synthesizer import streaming_speech
        if council_voice and council_voice._kokoro:
            import io
            import soundfile as sf
            
            # Analyze emotional vector with BERT to modulate pitch and speed dynamically (ChatGPT Voice feel)
            prosody = streaming_speech.get_emotional_prosody(clean_text)
            speed = prosody.get("speed", 1.0)
            
            persona = council_voice.get_persona(voice or "AEGIS_PRIME")
            kokoro_voice_name = persona.get("kokoro_voice", "af_heart")
            samples, sr = council_voice._kokoro.create(clean_text, voice=kokoro_voice_name, speed=speed)
            buf = io.BytesIO()
            sf.write(buf, samples, sr, format="WAV")
            audio_bytes = buf.getvalue()
            if audio_bytes:
                if len(_TTS_AUDIO_CACHE) > 100:
                    _TTS_AUDIO_CACHE.clear()
                _TTS_AUDIO_CACHE[cache_key] = (audio_bytes, "audio/wav")
                return Response(content=audio_bytes, media_type="audio/wav")
    except Exception as e_kokoro:
        print(f"[TTS_KOKORO_NOTE] Local Kokoro deferred ({e_kokoro}); falling back to edge-tts.")

    last_error = None
    for candidate in candidates:
        try:
            import edge_tts
            communicate = edge_tts.Communicate(
                clean_text,
                candidate,
                pitch=tuning.get("pitch", "+0Hz"),
                rate=tuning.get("rate", "+0%"),
            )
            audio_buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buffer.extend(chunk["data"])
            audio_bytes = bytes(audio_buffer)
            if not audio_bytes:
                raise RuntimeError("voice returned no audio")
            if len(_TTS_AUDIO_CACHE) > 100:
                _TTS_AUDIO_CACHE.clear()
            _TTS_AUDIO_CACHE[cache_key] = (audio_bytes, "audio/mpeg")
            return Response(content=audio_bytes, media_type="audio/mpeg")
        except Exception as e:
            last_error = e
            print(f"[TTS_VOICE_SKIP] {candidate} unusable ({type(e).__name__}); trying next voice.")

    print(f"[TTS_NEURAL_WARN] All cloud voices failed ({last_error}). Attempting local acoustic fallback...")
    try:
        import pyttsx3
        import io
        import tempfile
        temp_path = tempfile.mktemp(suffix=".wav")
        engine = pyttsx3.init()
        engine.setProperty('rate', 175)
        engine.save_to_file(clean_text, temp_path)
        engine.runAndWait()
        with open(temp_path, "rb") as fh:
            wav_bytes = fh.read()
        try:
            os.remove(temp_path)
        except Exception:
            pass
        _TTS_AUDIO_CACHE[cache_key] = (wav_bytes, "audio/wav")
        return Response(content=wav_bytes, media_type="audio/wav")
    except Exception as elocal:
        print(f"[TTS_FAIL] Local voice fallback also failed: {elocal}")
        return Response(content=b"", media_type="audio/mpeg", status_code=500)

# ─── Native window control (frameless shell) ─────────────────────────────────
# The HUD is frameless, so AEGIS renders its own minimise / maximise / close
# buttons. These endpoints drive the real native window over HTTP. The desktop
# shell runs the backend in a daemon thread inside the same process that owns
# the window, so this works even when the pywebview JS bridge has not loaded.
def _window_action(action):
    from core.runtime import window_control
    handlers = {
        "minimize": window_control.minimize,
        "maximize": window_control.toggle_maximize,
        "hide": window_control.hide,
        "show": window_control.show,
    }
    handler = handlers.get(action)
    if handler is None:
        return JSONResponse({"status": "ERROR", "error": f"unknown action: {action}"}, status_code=400)
    ok = bool(handler())
    from core.runtime import window_control
    detail = None
    if not ok:
        if not window_control.has_window():
            detail = "No native window attached (opened in a browser or dev server)."
        else:
            err = window_control.last_error()
            detail = f"Native call failed: {err}" if err else "Native call failed."
    return JSONResponse({
        "status": "OK" if ok else "UNAVAILABLE",
        "action": action,
        "applied": ok,
        "maximized": window_control.is_maximized(),
        "detail": detail,
    })


@router.post("/window/{action}", dependencies=[Depends(require_auth)])
async def window_action(action: str):
    """Drive the native window: minimize | maximize | hide | show."""
    return _window_action(action.lower())


@router.get("/window/state")
async def window_state():
    """Reports whether native window control is available right now."""
    from core.runtime import window_control
    return {
        "status": "OK",
        "native_window": window_control.has_window(),
        "maximized": window_control.is_maximized(),
    }


@router.get("/capabilities/permissions")
async def effective_permissions():
    """Effective consent state, used by the UI to enable or disable actions."""
    from core.user_settings import get_permissions, permission_enabled
    return {
        "status": "OK",
        "permissions": get_permissions(),
        "enforced": {
            "filesystem_enforced": bool(permission_enabled("filesystem")),
            "network_enforced": bool(permission_enabled("network")),
            "screen_capture_enforced": bool(permission_enabled("screen_capture")),
            "microphone_enforced": bool(permission_enabled("microphone")),
            "camera_enforced": bool(permission_enabled("camera")),
        },
    }

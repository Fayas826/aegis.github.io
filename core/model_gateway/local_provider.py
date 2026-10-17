import json
import threading
import subprocess
from typing import Optional, Dict, Any
from core.model_gateway.base_provider import BaseLLMProvider

import os
TARGET_CORE_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_URL = "http://localhost:11434/api/generate"

# local vision candidates, in preference order
LOCAL_VISION_MODELS = ("moondream:latest", "llava:latest")


class _DesktopUnavailable(Exception):
    """Only fixed, public-safe messages may be used with this exception."""


class _DesktopCancelled(Exception):
    pass


def _desktop_origin():
    """Accept an origin, never an arbitrary URL or a remotely resolved host."""
    import ipaddress
    from urllib.parse import urlsplit

    configured_host = os.getenv("OLLAMA_HOST")
    raw = configured_host or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
    raw = raw.strip()
    if not raw or any(ord(c) <= 32 for c in raw) or "\\" in raw:
        raise _DesktopUnavailable("Ollama requires a valid loopback origin.")
    if "://" not in raw:
        raw = "http://" + raw
    try:
        url = urlsplit(raw)
        host, port = url.hostname, url.port
        if (url.scheme not in {"http", "https"} or not host or url.username is not None
                or url.password is not None or url.path not in {"", "/"}
                or url.query or url.fragment or "?" in raw or "#" in raw):
            raise ValueError
        # OLLAMA_HOST is also the server's bind setting. Never connect/send data
        # to wildcard addresses: translate those settings to literal loopback.
        mapped = {"localhost": "127.0.0.1"}
        if configured_host:
            mapped.update({"0.0.0.0": "127.0.0.1", "::": "::1"})
        address = ipaddress.ip_address(mapped.get(host.lower(), host))
        if not address.is_loopback or "%" in host or port == 0:
            raise ValueError
        host = "[{}]".format(address) if address.version == 6 else str(address)
        return "{}://{}:{}".format(url.scheme, host, port if port is not None else 11434)
    except ValueError:
        raise _DesktopUnavailable("Ollama requires a valid loopback origin.") from None


def _desktop_http_client(timeout):
    """Lazy, injectable HTTP boundary. No proxies, redirects, retries or servers."""
    import httpx
    return httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)


def _desktop_timeout(timeout, maximum):
    import math
    value = float(timeout)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid desktop timeout.")
    return min(value, maximum)


def _desktop_remaining(deadline):
    import time
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Desktop request deadline reached.")
    return remaining


def _desktop_cancel(cancel_check):
    if cancel_check is not None and cancel_check():
        raise _DesktopCancelled()


def _desktop_model_key(model):
    # Only the implicit :latest tag is equivalent; never strip other tags,
    # namespaces, registry prefixes, or change case.
    if not isinstance(model, str) or not model or model != model.strip():
        raise ValueError("Invalid Ollama model identity.")
    return model[:-7] if model.endswith(":latest") else model


def _desktop_json(response):
    response.raise_for_status()  # Includes redirects, which must not be followed.
    value = response.json()
    if not isinstance(value, dict) or value.get("error"):
        raise ValueError("Invalid Ollama response.")
    return value


def _desktop_select(client, origin, deadline, model, cancel_check=None):
    _desktop_cancel(cancel_check)
    tags = _desktop_json(client.get(origin + "/api/tags", timeout=_desktop_remaining(deadline)))
    _desktop_cancel(cancel_check)
    _desktop_remaining(deadline)
    inventory = tags.get("models")
    if not isinstance(inventory, list):
        raise ValueError("Invalid Ollama inventory.")
    installed = {}
    for entry in inventory:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Ollama inventory.")
        name = entry.get("name") or entry.get("model")
        installed[_desktop_model_key(name)] = name
    explicit = model
    if explicit is None:
        explicit = os.getenv("AEGIS_GUI_LOCAL_MODEL") or os.getenv("OLLAMA_VISION_MODEL")

    def capabilities(selected):
        _desktop_cancel(cancel_check)
        show = _desktop_json(client.post(origin + "/api/show", json={"model": selected},
                                         timeout=_desktop_remaining(deadline)))
        _desktop_cancel(cancel_check)
        _desktop_remaining(deadline)
        for field in ("model", "name"):
            if field in show and _desktop_model_key(show[field]) != _desktop_model_key(selected):
                raise ValueError("Ollama model identity mismatch.")
        caps = show.get("capabilities")
        if not isinstance(caps, list) or any(not isinstance(cap, str) for cap in caps):
            raise ValueError("Invalid Ollama capabilities.")
        # Do not infer vision from a model's name or assume older servers support it.
        return set(caps)

    def verified(selected):
        caps = capabilities(selected)
        if not caps.intersection({"completion", "vision"}):
            raise _DesktopUnavailable("Installed model does not advertise generation capabilities.")
        return selected, "vision" in caps

    if explicit is not None:
        selected = installed.get(_desktop_model_key(explicit))
        if selected is None:
            raise _DesktopUnavailable("Requested local desktop model is not installed.")
        return verified(selected)
    for candidate in LOCAL_VISION_MODELS:
        selected = installed.get(_desktop_model_key(candidate))
        if selected and "vision" in capabilities(selected):
            return selected, True
    selected = installed.get(_desktop_model_key(os.getenv("OLLAMA_MODEL") or TARGET_CORE_MODEL))
    if selected is None:
        raise _DesktopUnavailable("No preferred vision or configured text model is installed.")
    return verified(selected)


def probe_desktop(timeout=1.5, model=None):
    """Inspect installed Ollama metadata only; AVAILABLE does not certify inference.

    Model precedence: argument, AEGIS_GUI_LOCAL_MODEL, OLLAMA_VISION_MODEL,
    verified moondream/llava, then OLLAMA_MODEL (default qwen2.5:1.5b).
    The timeout is a shared metadata-request budget capped at five seconds.
    """
    import time
    result = {"status": "UNAVAILABLE", "provider": "ollama", "model": None,
              "supports_vision": False, "detail": "Local Ollama inventory or capabilities unavailable."}
    try:
        budget = _desktop_timeout(timeout, 5.0)
        origin = _desktop_origin()
        with _desktop_http_client(budget) as client:
            selected, vision = _desktop_select(client, origin, time.monotonic() + budget, model)
        result.update(status="AVAILABLE", model=selected, supports_vision=vision,
                      detail="Installed model metadata verified; inference has not been tested.")
    except _DesktopUnavailable as exc:
        result["detail"] = str(exc)
    except Exception:
        pass  # Never expose transport exceptions, URLs, response bodies or credentials.
    return result


def plan_desktop(prompt, system_prompt, *, image_b64='', image_mime='image/png',
                 timeout=30.0, model=None, cancel_check=None):
    """Return a local JSON plan for UniversalGUIWorker; never execute actions.

    A single deadline (maximum 60s) covers metadata and generation. Cancellation
    is cooperative before/after requests; transport timeouts bound in-flight work.
    Text models deliberately omit images. Calls without an image also report
    accessibility mode, even when the selected model is vision-capable.
    """
    import time
    result = {"status": "UNAVAILABLE", "provider": "ollama", "model": None,
              "supports_vision": False, "perception_mode": "accessibility", "response": None,
              "detail": "Local Ollama inventory or capabilities unavailable."}
    generating = False
    try:
        _desktop_cancel(cancel_check)
        budget = _desktop_timeout(timeout, 60.0)
        deadline = time.monotonic() + budget
        origin = _desktop_origin()
        with _desktop_http_client(budget) as client:
            selected, vision = _desktop_select(client, origin, deadline, model, cancel_check)
            result.update(model=selected, supports_vision=vision,
                          perception_mode="vision" if vision and image_b64 else "accessibility")
            _desktop_cancel(cancel_check)
            generating = True
            if (not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 65536
                    or not isinstance(system_prompt, str) or len(system_prompt) > 65536):
                raise ValueError("Invalid desktop prompt.")
            payload = {"model": selected, "prompt": prompt, "system": system_prompt,
                       "stream": False, "format": "json",
                       "options": {"temperature": 0, "seed": 0, "num_predict": 1024, "num_ctx": 4096}}
            if vision and image_b64:
                import base64
                if (not isinstance(image_b64, str) or len(image_b64) > 16 * 1024 * 1024
                        or image_mime not in {"image/png", "image/jpeg", "image/webp"}):
                    raise ValueError("Invalid desktop image.")
                if not base64.b64decode(image_b64, validate=True):
                    raise ValueError("Empty desktop image.")
                payload["images"] = [image_b64]

            from core.reliability.circuit_breaker import circuit_breaker_manager, CircuitBreakerOpenException
            import hashlib
            identity = hashlib.sha256((origin + "\0" + _desktop_model_key(selected)).encode()).hexdigest()
            breaker = circuit_breaker_manager.get_breaker("ollama_desktop:" + identity)

            def generate():
                # All transport/decoding/identity failures MUST escape this callable
                # so the shared breaker records failures rather than false success.
                data = _desktop_json(client.post(origin + "/api/generate", json=payload,
                                                 timeout=_desktop_remaining(deadline)))
                if _desktop_model_key(data.get("model")) != _desktop_model_key(selected):
                    raise ValueError("Ollama model identity mismatch.")
                if data.get("done") is not True:
                    raise ValueError("Incomplete Ollama generation.")
                raw = data.get("response")
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("Empty Ollama plan.")

                def invalid_constant(_):
                    raise ValueError("Invalid JSON constant.")

                parsed = json.loads(raw, parse_constant=invalid_constant)
                if not isinstance(parsed, dict) or not parsed:
                    raise ValueError("Ollama plan must be a nonempty JSON object.")
                _desktop_remaining(deadline)
                return parsed

            _desktop_cancel(cancel_check)
            try:
                parsed = breaker.call(generate)
            except CircuitBreakerOpenException:
                _desktop_cancel(cancel_check)
                result.update(status="UNAVAILABLE", detail="Local desktop model circuit is open.")
                return result
            except Exception:
                _desktop_cancel(cancel_check)
                raise
            _desktop_cancel(cancel_check)
            result.update(status="SUCCESS", response=parsed,
                          detail="Local JSON plan generated; no desktop actions were executed.")
    except _DesktopCancelled:
        result.update(status="CANCELLED", response=None, detail="Local desktop planning cancelled.")
    except _DesktopUnavailable as exc:
        result["detail"] = str(exc)
    except Exception:
        # Check again even if metadata failed while a cancellation was requested.
        try:
            _desktop_cancel(cancel_check)
        except _DesktopCancelled:
            result.update(status="CANCELLED", response=None, detail="Local desktop planning cancelled.")
            return result
        except Exception:
            pass
        result.update(status="FAILED" if generating else "UNAVAILABLE", response=None,
                      detail="Local desktop generation failed." if generating
                      else "Local Ollama inventory or capabilities unavailable.")
    return result


class LocalProvider(BaseLLMProvider):
    def __init__(self):
        self._http_client = None
        self._inference_lock = threading.Lock()
        self._active_requests = 0
        self._unload_timer = None
        # Phase N1: the REAL model id used by the most recent call.
        self.last_model_id: Optional[str] = None
        self.last_vision_model: Optional[str] = None

    # Both module-level and provider-instance entry points use this same runtime.
    probe_desktop = staticmethod(probe_desktop)
    plan_desktop = staticmethod(plan_desktop)

    @property
    def http_client(self):
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    @http_client.setter
    def http_client(self, client):
        self._http_client = client

    @property
    def is_active(self) -> bool:
        """Honestly probes Ollama before claiming availability."""
        try:
            import socket
            with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
                return True
        except Exception:
            return False

    async def chat(self, text: str, system_prompt: str, context: str = "", *, structured: bool = False) -> Optional[Dict[str, Any]]:
        return await self.chat_with_model(text, system_prompt, context,
                                          model=TARGET_CORE_MODEL, structured=structured)

    async def chat_with_model(self, text: str, system_prompt: str, context: str = "",
                              model: Optional[str] = None, *, structured: bool = False) -> Optional[Dict[str, Any]]:
        """Phase N1 — address a specific local worker by its real Ollama model id."""
        with self._inference_lock:
            self._active_requests += 1
            if self._unload_timer:
                self._unload_timer.cancel()

        try:
            model = model or TARGET_CORE_MODEL
            self.last_model_id = model
            suffix = "\nJSON:" if structured else "\nResponse:"
            full_prompt = f"{system_prompt}\nContext: {context}\nUser: {text}{suffix}"
            payload = {
                "model": model,
                "prompt": full_prompt,
                "stream": False,
            }
            if structured:
                payload["format"] = "json"
            res = await self.http_client.post(OLLAMA_URL, json=payload, timeout=15.0)
            if res.status_code == 200:
                raw = res.json()["response"]
                if structured:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        return {"type": "CHAT", "intent": "chat",
                                "response": "I could not parse the structured operation result.",
                                "mode": "hud", "structured_fallback": True}
                stripped = (raw or "").strip()
                if stripped:
                    return {"type": "CHAT", "intent": "chat", "response": stripped, "mode": "hud"}
        except Exception as e:
            print(f"[BRAIN_LOCAL_FAIL] {e}")
        finally:
            should_reset = False
            with self._inference_lock:
                self._active_requests -= 1
                if self._active_requests <= 0:
                    self._active_requests = 0
                    should_reset = True
            if should_reset:
                self._reset_idle_timer()
        return None

    async def vision(self, text: str, image_path: str, context: str = "") -> Optional[Dict[str, Any]]:
        # Phase N1: Moondream / LLaVA are real local vision workers behind the
        # same Ollama endpoint. Try them in preference order so local vision is
        # routable instead of forcing every vision task to the cloud.
        import base64 as _b64
        try:
            with open(image_path, "rb") as f:
                img_b64 = _b64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return None
        last_err: Optional[Exception] = None
        for model in LOCAL_VISION_MODELS:
            try:
                payload = {
                    "model": model,
                    "prompt": f"Context: {context}\nQuestion: {text}",
                    "images": [img_b64],
                    "stream": False,
                }
                res = await self.http_client.post(OLLAMA_URL, json=payload, timeout=30.0)
                if res.status_code == 200:
                    raw = (res.json().get("response") or "").strip()
                    self.last_vision_model = model
                    self.last_model_id = model
                    return {"type": "CHAT", "intent": "vision_analyze",
                            "response": raw or "I examined the image but could not describe it.",
                            "mode": "hud"}
                last_err = RuntimeError(f"ollama vision {model} status {res.status_code}")
            except Exception as e:  # pragma: no cover - depends on local service
                last_err = e
        if last_err is not None:
            print(f"[LOCAL_VISION_FAIL] {last_err}")
        return None

    def _reset_idle_timer(self):
        with self._inference_lock:
            if self._unload_timer:
                self._unload_timer.cancel()

            def unload():
                with self._inference_lock:
                    if self._active_requests > 0: return
                    subprocess.run(["ollama", "stop", TARGET_CORE_MODEL], capture_output=True)

            self._unload_timer = threading.Timer(300, unload)
            self._unload_timer.daemon = True
            self._unload_timer.start()

    def init_llamacpp_quantized_engine(self, model_path: Optional[str] = None, n_gpu_layers: int = 24, n_ctx: int = 2048) -> Dict[str, Any]:
        """
        Initializes llama.cpp native GGUF engine tuned for 4GB VRAM GPU.
        Wires local quantization into Jarvis's central brain and neurons.
        """
        try:
            from core.cognition.reasoning.neural_extensions_bridge import neural_bridge
            return neural_bridge.load_quantized_model(model_path=model_path, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx)
        except Exception as e:
            return {"status": "ERROR", "detail": str(e)}

"""Offline contract tests: every HTTP request terminates in MockTransport."""
import asyncio
import base64
import json

import httpx
import pytest

from core.model_gateway import local_provider as local
from core.reliability import circuit_breaker as circuits


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ("OLLAMA_HOST", "OLLAMA_BASE_URL", "AEGIS_GUI_LOCAL_MODEL",
                 "OLLAMA_VISION_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(local, "TARGET_CORE_MODEL", "qwen2.5:1.5b")
    monkeypatch.setattr(circuits, "circuit_breaker_manager", circuits.CircuitBreakerManager())
    # An unconfigured test must fail closed instead of contacting real services.
    def no_network(timeout):
        raise AssertionError("An offline test omitted its mock HTTP boundary")
    monkeypatch.setattr(local, "_desktop_http_client", no_network)


def server(monkeypatch, *, models=None, capabilities=None, generation=None, hook=None):
    models = ["moondream:latest", "llava:latest", "qwen2.5:1.5b"] if models is None else models
    capabilities = capabilities or {"moondream:latest": ["completion", "vision"],
                                    "llava:latest": ["completion", "vision"]}
    requests = []

    def handle(request):
        requests.append(request)
        if hook:
            override = hook(request)
            if override is not None:
                return override
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": model} for model in models]})
        body = json.loads(request.content)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": capabilities.get(body["model"], ["completion"])})
        if request.url.path == "/api/generate":
            data = generation if generation is not None else {
                "model": body["model"], "done": True,
                "response": '{"actions":[{"name":"wait","arguments":{}}]}'
            }
            return httpx.Response(200, json=data)
        raise AssertionError("Unexpected HTTP route")

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(local, "_desktop_http_client", lambda timeout: httpx.Client(
        transport=transport, timeout=timeout, trust_env=False, follow_redirects=False))
    return requests


def test_probe_prefers_installed_moondream_without_inference(monkeypatch):
    calls = server(monkeypatch, models=["aegis-sovereign:latest", "phi3:mini", "moondream:latest",
                                       "llama3.1:latest", "phi3:latest", "llava:latest", "qwen2.5:1.5b"])
    result = local.LocalProvider().probe_desktop()
    assert result["status"] == "AVAILABLE"
    assert result["provider"] == "ollama"
    assert result["model"] == "moondream:latest"
    assert result["supports_vision"] is True
    assert "inference has not been tested" in result["detail"]
    assert [(r.method, r.url.path) for r in calls] == [("GET", "/api/tags"), ("POST", "/api/show")]
    assert all(r.url.host == "127.0.0.1" for r in calls)


@pytest.mark.parametrize("models,expected", [(["llava:latest", "qwen2.5:1.5b"], "llava:latest"),
                                           (["qwen2.5:1.5b"], "qwen2.5:1.5b")])
def test_fallback_uses_installed_models_only(monkeypatch, models, expected):
    server(monkeypatch, models=models)
    result = local.probe_desktop()
    assert result["status"] == "AVAILABLE"
    assert result["model"] == expected
    assert result["supports_vision"] == expected.startswith("llava")


def test_model_names_are_not_capability_evidence(monkeypatch):
    calls = server(monkeypatch, capabilities={"moondream:latest": ["completion"],
                                              "llava:latest": ["completion"]})
    result = local.probe_desktop()
    assert result["model"] == "qwen2.5:1.5b"
    assert result["supports_vision"] is False
    assert len(calls) == 4


@pytest.mark.parametrize("source", ["argument", "AEGIS_GUI_LOCAL_MODEL", "OLLAMA_VISION_MODEL"])
def test_explicit_missing_model_is_not_replaced(monkeypatch, source):
    calls = server(monkeypatch)
    kwargs = {"model": "phi3:mini"} if source == "argument" else {}
    if source != "argument":
        monkeypatch.setenv(source, "phi3:mini")
    result = local.probe_desktop(**kwargs)
    assert result["status"] == "UNAVAILABLE"
    assert [r.url.path for r in calls] == ["/api/tags"]


def test_explicit_precedence_and_latest_only_normalization(monkeypatch):
    server(monkeypatch, models=["phi3:latest", "phi3:mini", "moondream:latest"])
    monkeypatch.setenv("AEGIS_GUI_LOCAL_MODEL", "phi3")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "missing")
    assert local.probe_desktop()["model"] == "phi3:latest"
    assert local.probe_desktop(model="moondream")["model"] == "moondream:latest"
    assert local.probe_desktop(model="phi3:mini")["model"] == "phi3:mini"
    assert local.probe_desktop(model="Phi3")["status"] == "UNAVAILABLE"
    assert local.probe_desktop(model="phi3:other")["status"] == "UNAVAILABLE"


def test_missing_default_and_empty_inventory_are_unavailable(monkeypatch):
    server(monkeypatch, models=["phi3:mini"])
    assert local.probe_desktop()["status"] == "UNAVAILABLE"
    monkeypatch.setenv("OLLAMA_MODEL", "phi3:mini")
    assert local.probe_desktop()["model"] == "phi3:mini"
    server(monkeypatch, models=[])
    assert local.probe_desktop()["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("origin", ["http://example.com:11434", "http://192.168.1.2:11434",
    "file:///tmp/ollama", "http://127.0.0.1:11434/api",
    "http://user:private@localhost:11434", "http://localhost:11434/?x=1", "http://localhost:11434/#x",
    "http://127.0.0.1:0", "http://localhost:bad", "http://localhost\\@evil.invalid", "http://[::1"])
def test_rejects_non_loopback_or_non_origin_without_http(monkeypatch, origin):
    calls = server(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", origin)
    result = local.probe_desktop()
    assert result["status"] == "UNAVAILABLE"
    assert not calls
    assert origin not in result["detail"]


@pytest.mark.parametrize("variable,origin,host", [("OLLAMA_HOST", "localhost:11434", "127.0.0.1"),
    ("OLLAMA_HOST", "0.0.0.0", "127.0.0.1"),
    ("OLLAMA_HOST", "http://[::]:11434", "::1"),
    ("OLLAMA_BASE_URL", "http://[::1]:11434/", "::1"),
    ("OLLAMA_BASE_URL", "http://127.0.0.2:11434", "127.0.0.2")])
def test_loopback_origins(monkeypatch, variable, origin, host):
    calls = server(monkeypatch)
    monkeypatch.setenv(variable, origin)
    assert local.probe_desktop()["status"] == "AVAILABLE"
    assert all(r.url.host == host for r in calls)
    assert all(r.url.port == 11434 for r in calls)


def test_host_precedence_cannot_bypass_invalid_host(monkeypatch):
    calls = server(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://remote.invalid")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert local.probe_desktop()["status"] == "UNAVAILABLE"
    assert not calls


def test_real_http_factory_disables_environment_and_redirects(monkeypatch):
    options = {}
    def capture(**kwargs):
        options.update(kwargs)
        return object()
    # Fixture replaced the factory; exercise its original function via saved ref.
    monkeypatch.setattr(httpx, "Client", capture)
    HTTP_FACTORY(1.25)
    assert options == {"timeout": 1.25, "trust_env": False, "follow_redirects": False}


HTTP_FACTORY = local._desktop_http_client


def test_probe_does_not_follow_redirects(monkeypatch):
    calls = server(monkeypatch, hook=lambda r: httpx.Response(
        302, headers={"Location": "http://remote.invalid/private"}))
    assert local.probe_desktop()["status"] == "UNAVAILABLE"
    assert len(calls) == 1


def test_vision_plan_is_bounded_deterministic_and_json(monkeypatch):
    calls = server(monkeypatch)
    image = base64.b64encode(b"mock pixels").decode()
    result = local.LocalProvider().plan_desktop("private prompt", "private system", image_b64=image)
    assert result["status"] == "SUCCESS"
    assert result["perception_mode"] == "vision"
    assert result["supports_vision"] is True
    assert result["response"]["actions"][0]["name"] == "wait"
    body = json.loads(calls[-1].content)
    assert body == {"model": "moondream:latest", "prompt": "private prompt", "system": "private system",
                    "stream": False, "format": "json", "images": [image],
                    "options": {"temperature": 0, "seed": 0, "num_predict": 1024, "num_ctx": 4096}}
    assert all(0 < r.extensions["timeout"]["read"] <= 30 for r in calls)
    assert "private" not in result["detail"]


def test_text_plan_is_accessibility_and_never_sends_or_opens_image(monkeypatch):
    calls = server(monkeypatch, models=["qwen2.5:1.5b"])
    result = local.plan_desktop("UIA controls", "plan", image_b64="/private/screenshot.png")
    assert result["status"] == "SUCCESS"
    assert result["perception_mode"] == "accessibility"
    assert result["supports_vision"] is False
    assert "images" not in json.loads(calls[-1].content)


def test_vision_capability_without_an_image_is_accessibility_mode(monkeypatch):
    calls = server(monkeypatch)
    result = local.plan_desktop("UIA controls", "plan")
    assert result["status"] == "SUCCESS"
    assert result["supports_vision"] is True
    assert result["perception_mode"] == "accessibility"
    assert "images" not in json.loads(calls[-1].content)


@pytest.mark.parametrize("metadata", [{}, {"capabilities": "vision"}, {"capabilities": [None]},
    {"capabilities": ["completion", "vision"], "model": "other:latest"}])
def test_invalid_or_mismatched_capabilities_fail_closed(monkeypatch, metadata):
    calls = server(monkeypatch, hook=lambda r: httpx.Response(200, json=metadata)
                   if r.url.path == "/api/show" else None)
    assert local.probe_desktop()["status"] == "UNAVAILABLE"
    assert local.plan_desktop("prompt", "system")["status"] == "UNAVAILABLE"
    assert all(r.url.path != "/api/generate" for r in calls)


def test_embedding_only_model_is_not_an_available_planner(monkeypatch):
    server(monkeypatch, models=["embed:latest"], capabilities={"embed:latest": ["embedding"]})
    assert local.probe_desktop(model="embed")["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("image,mime", [("not-base64!", "image/png"),
                                       ("YWJj", "application/octet-stream")])
def test_invalid_vision_input_never_generates(monkeypatch, image, mime):
    calls = server(monkeypatch)
    result = local.plan_desktop("prompt", "system", image_b64=image, image_mime=mime)
    assert result["status"] == "FAILED"
    assert all(r.url.path != "/api/generate" for r in calls)


def test_generated_latest_alias_is_the_same_identity(monkeypatch):
    server(monkeypatch, generation={"model": "moondream", "done": True, "response": '{"actions":[]}'})
    assert local.plan_desktop("prompt", "system")["status"] == "SUCCESS"


@pytest.mark.parametrize("response", ["", "   ", "not json", "[]", "null", "{}", '{"x":NaN}'])
def test_invalid_plans_are_breaker_failures(monkeypatch, response):
    server(monkeypatch, generation={"model": "moondream:latest", "done": True, "response": response})
    for _ in range(3):
        assert local.plan_desktop("prompt", "system")["status"] == "FAILED"
    status = next(s for n, s in circuits.circuit_breaker_manager.list_breakers().items()
                  if n.startswith("ollama_desktop:"))
    assert status["failure_count"] == 3
    assert status["state"] == circuits.CircuitState.OPEN
    assert local.plan_desktop("prompt", "system")["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("data", [
    {"model": "llava:latest", "done": True, "response": '{"actions":[]}'},
    {"model": "moondream:latest", "done": False, "response": '{"actions":[]}'},
    {"model": "moondream:latest", "done": True},
    {"done": True, "response": '{"actions":[]}'},
])
def test_mismatched_incomplete_and_missing_generation_fields_count(monkeypatch, data):
    server(monkeypatch, generation=data)
    result = local.plan_desktop("prompt", "system")
    assert result["status"] == "FAILED"
    assert result["response"] is None
    statuses = circuits.circuit_breaker_manager.list_breakers()
    assert any(s["failure_count"] == 1 for n, s in statuses.items() if n.startswith("ollama_desktop:"))


@pytest.mark.parametrize("kind", ["http", "redirect", "timeout", "bad_json", "empty"])
def test_generation_transport_errors_count_and_are_sanitized(monkeypatch, kind, caplog):
    private = "SECRET_PRIVATE_PROMPT_IMAGE"
    def hook(request):
        if request.url.path != "/api/generate":
            return None
        if kind == "timeout":
            raise httpx.ReadTimeout(private)
        if kind == "bad_json":
            return httpx.Response(200, text=private)
        if kind == "empty":
            return httpx.Response(200, json={})
        return httpx.Response(500 if kind == "http" else 307, text=private,
                              headers={"Location": "http://remote.invalid"})
    calls = server(monkeypatch, hook=hook)
    result = local.plan_desktop(private, private)
    assert result["status"] == "FAILED"
    assert private not in json.dumps(result)
    assert private not in caplog.text
    assert len(calls) == 3
    assert any(s["failure_count"] == 1 for n, s in circuits.circuit_breaker_manager.list_breakers().items()
               if n.startswith("ollama_desktop:"))


def test_breakers_are_partitioned_by_origin_and_model(monkeypatch):
    calls = server(monkeypatch, hook=lambda r: httpx.Response(500) if r.url.path == "/api/generate" else None)
    for _ in range(3):
        assert local.plan_desktop("prompt", "system")["status"] == "FAILED"
    generated = sum(r.url.path == "/api/generate" for r in calls)
    assert local.plan_desktop("prompt", "system")["status"] == "UNAVAILABLE"
    assert sum(r.url.path == "/api/generate" for r in calls) == generated
    assert local.plan_desktop("prompt", "system", model="llava")["status"] == "FAILED"
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11435")
    assert local.plan_desktop("prompt", "system")["status"] == "FAILED"
    assert len([n for n in circuits.circuit_breaker_manager.list_breakers() if n.startswith("ollama_desktop:")]) == 3


@pytest.mark.parametrize("phase", ["before", "tags", "show", "generate"])
def test_cancellation_before_and_after_each_network_phase(monkeypatch, phase):
    cancelled = [phase == "before"]
    def hook(request):
        if request.url.path == "/api/" + phase:
            cancelled[0] = True
    calls = server(monkeypatch, hook=hook)
    result = local.plan_desktop("prompt", "system", cancel_check=lambda: cancelled[0])
    assert result["status"] == "CANCELLED"
    assert result["response"] is None
    assert len(calls) == {"before": 0, "tags": 1, "show": 2, "generate": 3}[phase]
    assert not any(s["failure_count"] for n, s in circuits.circuit_breaker_manager.list_breakers().items()
                   if n.startswith("ollama_desktop:"))


def test_cancellation_also_wins_over_transport_failure(monkeypatch):
    cancelled = [False]
    def hook(request):
        cancelled[0] = True
        raise httpx.ConnectError("private")
    server(monkeypatch, hook=hook)
    assert local.plan_desktop("prompt", "system", cancel_check=lambda: cancelled[0])["status"] == "CANCELLED"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), "bad"])
def test_invalid_timeouts_never_make_requests(monkeypatch, timeout):
    calls = server(monkeypatch)
    assert local.probe_desktop(timeout=timeout)["status"] == "UNAVAILABLE"
    assert local.plan_desktop("prompt", "system", timeout=timeout)["status"] == "UNAVAILABLE"
    assert not calls


def test_timeouts_have_finite_caps(monkeypatch):
    calls = server(monkeypatch)
    assert local.probe_desktop(timeout=1000000)["status"] == "AVAILABLE"
    assert all(0 < r.extensions["timeout"]["read"] <= 5 for r in calls)
    calls.clear()
    assert local.plan_desktop("prompt", "system", timeout=1000000)["status"] == "SUCCESS"
    assert all(0 < r.extensions["timeout"]["read"] <= 60 for r in calls)


@pytest.mark.parametrize("phase", ["tags", "show", "generate"])
def test_elapsed_deadline_cannot_report_success(monkeypatch, phase):
    import time
    monotonic = time.monotonic
    elapsed = [0]
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + elapsed[0])
    def hook(request):
        if request.url.path == "/api/" + phase:
            elapsed[0] += 10
    calls = server(monkeypatch, hook=hook)
    result = local.plan_desktop("prompt", "system", timeout=1)
    assert result["status"] == ("FAILED" if phase == "generate" else "UNAVAILABLE")
    assert result["response"] is None
    assert len(calls) == {"tags": 1, "show": 2, "generate": 3}[phase]


def test_optional_http_dependency_is_lazy_and_safely_unavailable(monkeypatch):
    import sys
    monkeypatch.setattr(local, "_desktop_http_client", HTTP_FACTORY)
    monkeypatch.setitem(sys.modules, "httpx", None)
    provider = local.LocalProvider()
    assert provider._http_client is None
    assert provider.probe_desktop()["status"] == "UNAVAILABLE"
    assert provider.plan_desktop("prompt", "system")["status"] == "UNAVAILABLE"


def test_existing_async_chat_and_vision_interfaces(monkeypatch, tmp_path):
    provider = local.LocalProvider()
    assert provider._http_client is None
    calls = []
    class AsyncHTTP:
        async def post(self, url, **kwargs):
            calls.append(kwargs["json"])
            return httpx.Response(200, json={"response": '{"type":"CHAT","response":"ok"}'})
    provider.http_client = AsyncHTTP()
    monkeypatch.setattr(provider, "_reset_idle_timer", lambda: None)
    result = asyncio.run(provider.chat("hello", "system", structured=True))
    assert result["response"] == "ok"
    image = tmp_path / "existing-vision.png"
    image.write_bytes(b"existing async image API")
    result = asyncio.run(provider.vision("describe", str(image)))
    assert result["intent"] == "vision_analyze"
    assert calls[0]["format"] == "json"
    assert calls[1]["images"]

import os
import sys
import json
import time
import logging

# Ensure project root is on sys.path when launched from backend/ or via pythonw
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Prevent Windows asyncio WinError 10054 crashes on client disconnects
if sys.platform == "win32":
    import asyncio
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_conn_lost = _ProactorBasePipeTransport._call_connection_lost
        def _safe_call_connection_lost(self, exc):
            try:
                _orig_call_conn_lost(self, exc)
            except (ConnectionResetError, OSError):
                pass
        _ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT_DIR, ".env"))
    load_dotenv(os.path.join(ROOT_DIR, "backend", ".env"))
except ImportError:
    pass

# #region agent log
def _debug_log(location, message, data=None, hypothesis_id="A"):
    try:
        payload = {
            "sessionId": "d2db55",
            "runId": os.getenv("JARVIS_DEBUG_RUN", "startup"),
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with open(os.path.join(ROOT_DIR, "debug-d2db55.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception:
        pass
# #endregion

_debug_log("backend/api.py:path", "sys.path bootstrap", {"root_dir": ROOT_DIR, "cwd": os.getcwd(), "in_path": ROOT_DIR in sys.path})

import psutil
import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from backend.middleware.saas_billing import saas_rate_limiter_middleware
from backend.middleware.auth import create_access_token, verify_pin, require_auth
from starlette.middleware.base import BaseHTTPMiddleware
try:
    from backend.routes.stripe_webhooks import router as stripe_router
except ModuleNotFoundError:
    stripe_router = None
from backend.routes.orchestrator_routes import router as orchestrator_router
from backend.routes.stream_routes import router as stream_router
from backend.routes.agency_routes import router as agency_router
from backend.routes.system_routes import router as system_router
from backend.routes.ai_routes import router as ai_router
from backend.routes.apps_routes import router as apps_router
from backend.routes.capability_routes import router as capability_router
from backend.routes.autonomy_routes import router as autonomy_router
from backend.routes.voice_routes import router as voice_router
from backend.routes.install_routes import router as install_router
from backend.routes.settings_routes import router as settings_router
from core.orchestration.world_state_engine import world_state_engine
from core.orchestration.situational_agent_router import situational_agent_router
from core.cognition.memory.scored_memory import scored_memory_store
from core.cognition.reasoning.model_backed_reasoner import model_backed_reasoner
from core.orchestration.rollback_tool_executor import rollback_tool_executor
from core.perception.perception_session import perception_session

app = FastAPI(title="O.M.E.G.A. Neural Core API", version="20.0")

# CORS — explicit dev origins; NO wildcard-with-credentials (invalid per ASGI spec)
_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_allowed_origins = [o.strip() for o in os.getenv("AEGIS_CORS_ORIGINS", "").split(",") if o.strip()]
_allowed_origins = _allowed_origins or _default_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register SaaS Token Bucket Rate Limiter
app.add_middleware(BaseHTTPMiddleware, dispatch=saas_rate_limiter_middleware)

# Register Webhook Routes
if stripe_router is not None:
    app.include_router(stripe_router)
else:
    @app.get("/webhooks/stripe/status")
    async def stripe_webhook_status():
        return {"status": "DISABLED", "reason": "python_stripe_package_missing"}
        
app.include_router(orchestrator_router, prefix="/orchestrator")
app.include_router(stream_router)
app.include_router(agency_router)
app.include_router(agency_router, prefix="/api")
app.include_router(system_router)
app.include_router(system_router, prefix="/api")
app.include_router(ai_router)
app.include_router(ai_router, prefix="/api")
app.include_router(apps_router)
app.include_router(apps_router, prefix="/api")
app.include_router(capability_router)
app.include_router(capability_router, prefix="/api")
app.include_router(autonomy_router)
app.include_router(autonomy_router, prefix="/api")
app.include_router(voice_router)
app.include_router(voice_router, prefix="/api")
app.include_router(install_router)
app.include_router(install_router, prefix="/api")
app.include_router(settings_router)
app.include_router(settings_router, prefix="/api")

# ─── AUTH ───
class HandshakeRequest(BaseModel):
    pin: str


@app.post("/auth/handshake")
@app.post("/api/auth/handshake")
async def handshake(req: HandshakeRequest):
    if verify_pin(req.pin):
        token = create_access_token({"identity": "ZENITH_HUD", "access_level": "CORE"})
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=403, detail="INVALID_HANDSHAKE_PIN")


# ─── GHOST SYNC (keep-alive / health) ───
@app.get("/ghost_sync")
@app.get("/api/ghost_sync")
async def ghost_sync():
    return {"status": "OMEGA_ONLINE", "version": "20.0"}


@app.get("/metrics")
@app.get("/api/metrics")
async def get_metrics():
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
    except Exception:
        cpu, mem = 0, 0
    return {"status": "ok", "cpu_percent": cpu, "memory_percent": mem, "metrics": {"cpu": cpu, "ram": mem}}


logging.basicConfig(level=logging.INFO)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logging.info("[API] Frontend HUD Connected via WebSocket.")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logging.info("[API] Frontend HUD Disconnected.")

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# Proactive Autonomous Ambient Engine Integration
from core.cognition.reasoning.proactive_engine import proactive_engine

@app.on_event("startup")
async def start_proactive_engine_loop():
    import asyncio
    proactive_engine.register_broadcast(manager.broadcast)
    asyncio.create_task(proactive_engine.start_loop())
    logging.info("[API] Proactive Autonomous Engine loop initialized on background worker.")

@app.on_event("startup")
async def maybe_boot_autonomy_loop():
    """AEGIS_AUTONOMY_AUTOSTART=true ignites the bounded autonomy mission loop
    at API boot, so the system self-serves without a human issuing /start."""
    import asyncio
    from core.autonomy.supervisor import autonomy_supervisor
    if os.getenv("AEGIS_AUTONOMY_AUTOSTART", "false").strip().lower() == "true":
        try:
            result = await autonomy_supervisor.start()
            logging.info(f"[API] Autonomy loop AUTO-STARTED: {result.get('running')}")
        except Exception as e:
            logging.warning(f"[API] Autonomy autostart skipped: {e}")
    else:
        logging.info("[API] Autonomy loop autostart OFF (set AEGIS_AUTONOMY_AUTOSTART=true to enable).")

@app.get("/proactive/insights")
@app.get("/api/proactive/insights")
async def get_proactive_insights():
    return {"insights": proactive_engine.get_latest_insights()}

from fastapi.responses import FileResponse, Response
from core.orchestration.enterprise_event_bus import enterprise_event_bus
from core.perception.browser_driver import browser_driver

# Phase 4.2 Browser Execution Progress Cache
_latest_browser_progress = {
    "task_id": None,
    "mode": "visible",
    "website": "",
    "page_title": "",
    "stage": "IDLE",
    "action": "Awaiting browser interaction",
    "verified_outcome": "IDLE",
    "window_state": {
        "WINDOW_CREATED": False,
        "WINDOW_VISIBLE": False,
        "FOREGROUND_CONFIRMED": False,
        "PAGE_STATE_VERIFIED": False
    },
    "is_pending_confirmation": False,
    "screenshot_url": None,
    "timestamp": time.time()
}

def _on_browser_progress_event(event):
    global _latest_browser_progress
    try:
        data = event.payload if hasattr(event, "payload") else event
        _latest_browser_progress.update(data)
        msg = json.dumps({"type": "BROWSER_PROGRESS", "data": _latest_browser_progress})
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(manager.broadcast(msg))
        except RuntimeError:
            pass
    except Exception:
        pass

enterprise_event_bus.subscribe("browser.execution.progress", _on_browser_progress_event)

@app.websocket("/ws/hud")
async def websocket_endpoint(websocket: WebSocket):
    """
    TRUE O.M.E.G.A. STREAM
    Pushes real-time Orchestrator logs, DAG states, and Vision data to the React HUD.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Maintain connection and listen for any abort signals
            data = await websocket.receive_text()
            if data == "ABORT":
                logging.warning("[API] Received ABORT signal from HUD.")
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/browser/live_progress")
@app.get("/api/browser/live_progress")
async def get_browser_live_progress():
    status = await browser_driver.get_live_status()
    payload = dict(_latest_browser_progress)
    payload["driver_status"] = status
    return payload


@app.post("/browser/control")
@app.post("/api/browser/control")
async def control_browser(action_req: dict):
    cmd = (action_req.get("action") or "").lower()
    if cmd == "pause":
        await browser_driver.pause()
        return {"status": "SUCCESS", "message": "Browser execution paused."}
    elif cmd == "resume":
        await browser_driver.resume()
        return {"status": "SUCCESS", "message": "Browser execution resumed."}
    elif cmd == "cancel":
        await browser_driver.cancel()
        return {"status": "SUCCESS", "message": "Browser execution cancelled."}
    elif cmd == "focus":
        res = browser_driver.force_foreground()
        return {"status": "SUCCESS", "focus_result": res}
    raise HTTPException(status_code=400, detail=f"Unknown control action: {cmd}")


@app.get("/browser/preview")
@app.get("/api/browser/preview")
async def get_browser_preview():
    rel = _latest_browser_progress.get("screenshot_url")
    if rel:
        from core.config.runtime_config import runtime_config
        full_path = os.path.join(runtime_config.BASE_DIR, rel)
        if os.path.exists(full_path):
            return FileResponse(full_path, media_type="image/png")
    # Fresh fallback capture
    data = browser_driver.capture_desktop_preview()
    if data:
        return Response(content=data, media_type="image/png")
    return {"status": "NO_PREVIEW"}



# ─── 5D SYSTEM METRICS (CPU + RAM → Tesseract breathing) ───
@app.get("/system_metrics")
@app.get("/api/system_metrics")
async def system_metrics():
    """
    Returns real-time CPU and RAM metrics that drive the 5th dimension
    of the Tesseract4D component (geometry breathing + spin rate).
    """
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": round(cpu, 2),
        "ram_percent": round(mem.percent, 2),
        "ram_used_gb": round(mem.used / (1024 ** 3), 2),
        "ram_total_gb": round(mem.total / (1024 ** 3), 2),
        "ram_available_gb": round(mem.available / (1024 ** 3), 2),
    }

@app.get("/health")
@app.get("/api/health")
async def health():
    return world_state_engine.health()


@app.get("/world/state")
@app.get("/api/world/state")
async def world_state():
    return world_state_engine.snapshot()


@app.post("/world/event")
@app.post("/api/world/event")
async def world_event(payload: dict):
    event_type = payload.get("type", "CUSTOM_EVENT")
    source = payload.get("source", "hud")
    event_payload = payload.get("payload", {})
    event = world_state_engine.record_event(event_type, event_payload, source)
    return {"status": "RECORDED", "event": event, "world": world_state_engine.snapshot()}


@app.post("/action/execute")
@app.post("/api/action/execute")
async def action_execute(payload: dict):
    intent = payload.get("intent", "unknown")
    result = world_state_engine.verify_action(intent, payload)
    return result


@app.post("/agents/route")
@app.post("/api/agents/route")
async def agents_route(payload: dict):
    situation = payload.get("situation") or payload.get("error") or payload.get("intent") or "unknown situation"
    return situational_agent_router.route(situation, payload)


@app.post("/ide/diagnostics")
@app.post("/api/ide/diagnostics")
async def receive_ide_diagnostics(payload: dict):
    """
    AEGIS OMNIPRESENT EYE - VS Code Diagnostic Ingestion Gateway.
    Receives real-time LSP diagnostics (errors, warnings) from VS Code.
    Dispatches errors to the CodewrightAgent / Self-Healer squads for auto-healing.
    """
    diagnostics = payload.get("diagnostics", [])
    fixed_count = 0
    errors_detected = []
    
    for item in diagnostics:
        file_path = item.get("file")
        issues = item.get("issues", [])
        for issue in issues:
            severity = issue.get("severity")
            msg = issue.get("message", "")
            line = issue.get("line", 1)
            if severity in ("ERROR", "WARNING", "warning", "error"):
                errors_detected.append({"file": file_path, "message": msg, "line": line, "severity": severity})
                try:
                    asyncio.create_task(asyncio.to_thread(
                        situational_agent_router.route, f"VSCode Diagnostic in {file_path}: {msg}", {
                            "file": file_path, "message": msg, "line": line, "type": "IDE_LSP_DIAGNOSTIC", "severity": severity
                        }
                    ))
                except Exception:
                    pass

                # 1. Auto-heal missing Python imports (e.g. Could not find name `os`, `sys`, `json`)
                if "Could not find name" in msg or "is not defined" in msg:
                    import re
                    m = re.search(r"[`'\"](\w+)[`'\"]", msg)
                    if m and file_path and os.path.exists(file_path):
                        missing_name = m.group(1)
                        if missing_name in ("os", "sys", "json", "time", "asyncio", "re", "math", "logging"):
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    content = f.read()
                                if f"import {missing_name}" not in content:
                                    new_content = f"import {missing_name}\n" + content
                                    with open(file_path, "w", encoding="utf-8") as f:
                                        f.write(new_content)
                                    fixed_count += 1
                                    logging.info(f"[IDE Sentinel] Autonomously healed missing import '{missing_name}' in {file_path}")
                            except Exception as ex:
                                logging.error(f"[IDE Sentinel] Auto-heal failed: {ex}")

                # 2. Auto-heal Tailwind CSS canonical class suggestions
                # e.g.: "The class `z-[1]` can be written as `z-1`"
                # or "The class `hover:bg-white/[0.04]` can be written as `hover:bg-white/4`"
                # or "The class `bg-gradient-to-r` can be written as `bg-linear-to-r`"
                if "can be written as" in msg:
                    import re
                    m = re.search(r"[`'\"]([^`'\"]+)[`'\"]\s+can be written as\s+[`'\"]([^`'\"]+)[`'\"]", msg)
                    if m and file_path and os.path.exists(file_path):
                        old_cls, new_cls = m.group(1).strip(), m.group(2).strip()
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            if old_cls in content:
                                new_content = content.replace(old_cls, new_cls)
                                with open(file_path, "w", encoding="utf-8") as f:
                                    f.write(new_content)
                                fixed_count += 1
                                logging.info(f"[IDE Sentinel] Autonomously healed Tailwind class '{old_cls}' -> '{new_cls}' in {file_path}")
                        except Exception as ex:
                            logging.error(f"[IDE Sentinel] Tailwind auto-heal failed: {ex}")
                                
    return {
        "status": "PROCESSED",
        "errors_received": len(errors_detected),
        "healed_count": fixed_count,
        "details": errors_detected
    }


@app.get("/agents/status")
@app.get("/api/agents/status")
async def agents_status():
    return situational_agent_router.status()


@app.get("/system_diagnostic")
@app.get("/api/system_diagnostic")
async def system_diagnostic():
    health_state = world_state_engine.health()
    world = world_state_engine.snapshot()
    agent_state = situational_agent_router.status()
    return [
        {"module": "CORE_API", "details": health_state["status"]},
        {"module": "WORLD_4D_ENGINE", "details": f"events={world['dimensions']['d4_timeline_events']}"},
        {"module": "CONFIDENCE_5D_GATE", "details": f"confidence={world['confidence']} risk={world['risk']['level']}"},
        {"module": "AGENT_ROUTER", "details": agent_state["status"]},
        {"module": "ACTION_CONTRACT", "details": "verified_results_enabled"},
    ]


# ─── MEMORY ───
@app.post("/memory/sync")
async def memory_sync(payload: dict):
    scored_memory_store.remember("User preferences sync", "hud", "preferences", 5, 1.0, payload)
    return {"status": "SYNCED"}

@app.get("/memory/load")
async def memory_load():
    memories = scored_memory_store.recall("User preferences sync", limit=1)
    return {"preferences": memories[0]["metadata"] if memories else {}}

@app.get("/memory/integrity")
async def memory_integrity():
    stats = scored_memory_store.stats()
    return {"status": "STABLE" if stats["avg_score"] > 0 else "DEGRADED", "stats": stats}

# ─── REASONING ───
@app.post("/reason")
async def reason(payload: dict):
    prompt = payload.get("prompt", "")
    context = payload.get("context", {})
    return model_backed_reasoner.reason(prompt, context)

# ─── TOOLS (ROLLBACK LEDGER) ───
@app.post("/tools/prepare")
async def tools_prepare(payload: dict):
    return rollback_tool_executor.prepare(payload.get("tool"), payload.get("intent"), payload.get("payload"))

@app.post("/tools/execute")
async def tools_execute(payload: dict):
    return rollback_tool_executor.mark_executed(payload.get("id"), payload.get("result"))

@app.post("/tools/rollback")
async def tools_rollback(payload: dict):
    return rollback_tool_executor.rollback(payload.get("id"))

@app.get("/tools/ledger")
async def tools_ledger():
    return {"ledger": rollback_tool_executor.ledger()}

# ─── ENTERPRISE TASKS & PROJECTS GATEWAY ───
@app.get("/api/v1/tasks")
@app.get("/v1/tasks")
async def get_enterprise_tasks():
    from core.orchestration.live_kanban import live_kanban
    board_state = live_kanban.get_board_state()
    columns = board_state.get("columns", {})
    tasks = []
    for col, task_list in columns.items():
        if isinstance(task_list, list):
            for t in task_list:
                tasks.append({
                    "_id": t.get("task_id", ""),
                    "type": t.get("title", ""),
                    "githubRepo": "Local / AEGIS Swarm",
                    "status": t.get("column", "TODO"),
                    "progress": 100 if t.get("column") == "DONE" else (50 if t.get("column") == "IN_PROGRESS" else 10),
                    "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t.get("created_at", time.time())))
                })
    return tasks

@app.post("/api/v1/tasks")
@app.post("/v1/tasks")
async def post_enterprise_task(payload: dict):
    from core.orchestration.live_kanban import live_kanban, KanbanColumn
    title = payload.get("type") or payload.get("title") or "Enterprise Task"
    desc = payload.get("description", "")
    card = live_kanban.add_task(
        task_id=f"TASK-{int(time.time()*1000)}",
        title=title,
        description=desc,
        pod_id=payload.get("pod_id", "software_engineering"),
        assigned_agent=payload.get("assigned_agent", "AGENT-CORE"),
        column=KanbanColumn.SCHEDULED,
        priority=payload.get("priority", "MEDIUM")
    )
    return {
        "_id": card.task_id,
        "type": card.title,
        "status": card.column,
        "progress": 0,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(card.created_at))
    }

@app.get("/api/v1/projects")
@app.get("/v1/projects")
async def get_enterprise_projects():
    return [{
        "_id": "PROJ-AEGIS-CORE",
        "name": "A.E.G.I.S. Enterprise Swarm",
        "description": "Autonomous Enterprise Governance & Intelligence Swarm",
        "status": "ACTIVE"
    }]

@app.post("/api/v1/projects")
@app.post("/v1/projects")
async def post_enterprise_project(payload: dict):
    return {
        "_id": f"PROJ-{int(time.time())}",
        "name": payload.get("name", "New Enterprise Project"),
        "description": payload.get("description", ""),
        "status": "ACTIVE"
    }

# ─── O.M.E.G.A. IDE RUNTIME ───
@app.post("/ide/run")
async def ide_run(payload: dict):
    from core.orchestration.meta_architect_agent import meta_architect
    task = payload.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="Missing task description.")

    # Trigger the autonomous ReAct agent loop
    result = await meta_architect.react_engine.run(task)
    return {"status": "COMPLETED", "result": result}


# ─── PERCEPTION ───
@app.post("/perception/session")
async def perception_session_state(payload: dict):
    action = payload.get("action", "start")
    if action == "start":
        return perception_session.start()
    return perception_session.stop()

@app.get("/vision/analyze")
async def vision_analyze():
    if not perception_session.active:
        perception_session.start()
    return perception_session.analyze_frame()

# ─── COMPUTER USE AGENT (LOCAL AUTOMATION) ───
@app.post("/computer_use")
async def computer_use_endpoint(payload: dict):
    from core.orchestration.computer_use_agent import computer_use_agent
    task = payload.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="Missing task description.")
    result = await computer_use_agent.execute_task(task)
    return result


# ─── COMPUTER USE SESSION MANAGEMENT ───
@app.get("/computer_use/session")
@app.get("/api/computer_use/session")
async def get_active_session():
    from core.orchestration.computer_use_session import computer_use_session_manager
    session = computer_use_session_manager.active_session
    if session:
        return {"active": True, "session": session.to_dict()}
    return {"active": False, "session": None}

@app.get("/computer_use/session/history")
@app.get("/api/computer_use/session/history")
async def get_session_history():
    from core.orchestration.computer_use_session import computer_use_session_manager
    return {"history": computer_use_session_manager.get_history()}

@app.post("/computer_use/session/end")
@app.post("/api/computer_use/session/end")
async def end_active_session():
    from core.orchestration.computer_use_session import computer_use_session_manager
    result = computer_use_session_manager.complete_session("User requested session end.")
    return {"status": "ENDED", "session": result}

@app.post("/desktop/navigate")
@app.post("/api/desktop/navigate")
async def desktop_navigate(payload: dict):
    """Direct browser navigation endpoint using ChromeSessionManager."""
    from core.execution.chrome_session_manager import chrome_session_manager
    url = payload.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL.")
    success = chrome_session_manager.navigate_to(url, reuse_tab=payload.get("reuse_tab", True))
    return {"status": "SUCCESS" if success else "FAILED", "url": url}

@app.get("/desktop/chrome/status")
@app.get("/api/desktop/chrome/status")
async def chrome_status():
    """Check if Chrome is running and get window info."""
    from core.execution.chrome_session_manager import chrome_session_manager
    windows = chrome_session_manager.find_chrome_windows()
    return {
        "running": len(windows) > 0,
        "window_count": len(windows),
        "windows": [{"hwnd": hwnd, "title": title} for hwnd, title in windows[:5]],
    }


# ─── GO MICROSERVICES (TIER 2 HYBRID ENGINE) ───────────────────────────────
@app.on_event("startup")
async def _on_startup_go():
    try:
        from core.services.go_bridge import go_services
        go_services.start_all()
    except Exception as e:
        logging.warning(f"[GoBridge] Startup note: {e}")

@app.on_event("shutdown")
async def _on_shutdown_go():
    try:
        from core.services.go_bridge import go_services
        go_services.stop_all()
    except Exception:
        pass

@app.get("/go_services/status")
async def get_go_services_status():
    from core.services.go_bridge import go_services
    return go_services.status()

@app.post("/internal/file_changed")
async def internal_file_changed(payload: dict):
    path = payload.get("path", "")
    event = payload.get("event", "")
    category = payload.get("category", "")
    logging.info(f"[GoFileWatcher] {event} on {category}: {path}")
    if category == "config" and path.endswith(".env"):
        try:
            from dotenv import load_dotenv
            load_dotenv(path, override=True)
            logging.info("[GoFileWatcher] Hot-reloaded .env successfully")
        except Exception as e:
            logging.warning(f"[GoFileWatcher] .env reload error: {e}")
    return {"status": "ACK", "received": payload}


# ─── BUILT HUD (SELF-CONTAINED DESKTOP APP) ───────────────────────────────
# When `frontend/dist` exists (vite build), the backend serves the AEGIS HUD
# so the PyWebview desktop shell runs as one local app on 127.0.0.1:8000.
# All API routes registered above take priority; only unmatched paths fall
# through to the SPA. Resolves through sys._MEIPASS when packaged by
# PyInstaller so the dist ships inside the executable.
_DIST_ROOT = getattr(
    sys, "_MEIPASS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_DIST_ROOT = os.path.join(_DIST_ROOT, "frontend", "dist")
if os.path.isfile(os.path.join(_DIST_ROOT, "index.html")):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    _ASSETS = os.path.join(_DIST_ROOT, "assets")
    if os.path.isdir(_ASSETS):
        app.mount("/assets", StaticFiles(directory=_ASSETS), name="aegis_hud_assets")

    @app.get("/api/{path:path}", include_in_schema=False)
    async def _unknown_api(path: str):
        raise HTTPException(status_code=404, detail=f"API route not found: /{path}")

    @app.get("/{path:path}", include_in_schema=False)
    async def _aegis_hud_spa(path: str):
        target = os.path.join(_DIST_ROOT, *path.split("/"))
        if path and os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(_DIST_ROOT, "index.html"))


if __name__ == "__main__":
    import uvicorn
    _port = int(os.getenv("AEGIS_PORT", "8000"))
    _host = os.getenv("AEGIS_HOST", "0.0.0.0")
    uvicorn.run(app, host=_host, port=_port, log_level="info")

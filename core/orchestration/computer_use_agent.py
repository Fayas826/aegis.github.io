import os
import sys
import time
import asyncio
from typing import Dict, Any, List
from dotenv import load_dotenv
load_dotenv()

if sys.platform == "win32":
    try:
        reconf_out = getattr(sys.stdout, 'reconfigure', None)
        if callable(reconf_out):
            reconf_out(encoding='utf-8', errors='replace')
        reconf_err = getattr(sys.stderr, 'reconfigure', None)
        if callable(reconf_err):
            reconf_err(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Ensure project root is in sys.path when running script directly
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Core imports
from core.perception.screen_capture import screen_capturer
from core.perception.visual_state import ActionResult
from core.orchestration.agent_state_machine import state_machine
from core.orchestration.task_recovery import failure_classifier, recovery_strategy_router
from core.cognition.memory.context_memory import app_action_memory
from core.perception.gui_grounding import action_regrounder
from core.orchestration.action_verifier import action_verifier, ActionObservation, action_delta_classifier
from core.orchestration.computer_use.observer import observer_pipeline
from core.orchestration.computer_use.planner import planner_pipeline
from core.orchestration.computer_use.grounder import grounder_pipeline
from core.orchestration.computer_use.actor import actor_pipeline

class ComputerUseAgent:
    """🤖 O.M.E.G.A. COMPUTER_USE_AGENT: Upgraded Pipeline (Observer -> Planner -> Grounder -> Actor)."""
    
    def __init__(self):
        self.is_running = False
        self.current_task = None
        self.step_history: List[Dict[str, Any]] = []
        # FIX: Increased from 10 → 25. Complex web tasks (forms, booking flows) need 25-50 steps.
        self.max_steps = 25
        self.current_step = 0
        self.bounding_boxes = [] # Active bounding box highlights
        self.agent_state = "STANDBY"
        self.confidence_score = 100
        self.max_retries = 3

    async def execute_task(self, task_description: str) -> Dict[str, Any]:
        """Runs the pipeline execution loop with failure recovery."""
        # Pillar 1: Frontier Input & Credential Gating
        from core.security.frontier_input_gate import frontier_input_gate
        is_valid, reason, meta = frontier_input_gate.validate_command(task_description)
        if not is_valid:
            print(f"[INPUT_GATE] Rejected ungrounded command '{task_description}': {reason} - {meta.get('reason')}")
            return {
                "status": "REJECTED",
                "error": reason,
                "detail": meta.get("reason"),
                "steps_executed": 0
            }

        self.is_running = True
        self.current_task = task_description
        self.step_history = []
        self.current_step = 0
        self.bounding_boxes = []
        
        try:
            from core.ui.desktop_perimeter_overlay import desktop_perimeter
            desktop_perimeter.activate("A.E.G.I.S. VISION & DESKTOP ACTIVE")
        except Exception:
            pass

        try:
            return await self._execute_task_internal(task_description)
        finally:
            try:
                from core.ui.desktop_perimeter_overlay import desktop_perimeter
                desktop_perimeter.deactivate()
            except Exception:
                pass

    async def _execute_task_internal(self, task_description: str) -> Dict[str, Any]:
        active_recipe = None
        try:
            from core.cognition.memory.action_memory import action_memory
            active_recipe = action_memory.query_recipe(task_description, "Desktop")
            if active_recipe:
                print(f"[COMPUTER_USE] Found successful cached recipe: {task_description}")
        except Exception as e:
            print(f"[COMPUTER_USE] Failed querying action memory: {e}")

        # ── UNIVERSAL FRONTIER WEB EXECUTION (Claude Code / Operator Parity) ──
        # Handles ticket booking, web browsing, form filling, product research, and arbitrary site workflows
        lowered_task = task_description.lower()
        
        is_desktop_explicit = any(app in lowered_task for app in (
            "notepad", "paint", "calculator", "word", "excel", "desktop", "explorer", "cmd", "powershell", "terminal"
        ))
        
        is_web_task = not is_desktop_explicit and (any(w in lowered_task for w in (
            "go to", "navigate", "search", "browse", "visit", "look up",
            "book", "buy", "order", "fill", "sign in", "login", "download",
            "youtube", "google", "wikipedia", "amazon", "github", "reddit",
            "ticket", "tickets", "cinema", "flight", "hotel", "bookmyshow"
        )) or any(domain in lowered_task for domain in (".com", ".org", ".net", ".in", ".io", "http")))

        if is_web_task:
            from core.orchestration.swarm_orchestrator import SwarmHandoff
            
            # If it's explicitly a booking/ticket task, hand off to the Ticket Agent
            is_booking_task = any(w in lowered_task for w in ("book", "ticket", "tickets", "cinema", "flight", "hotel", "bookmyshow"))
            
            if is_booking_task:
                print(f"[COMPUTER_USE] Detected booking workflow. Initiating Swarm Handoff to Ticket Agent.")
                raise SwarmHandoff("ticket_booking", task_description, {"source": "computer_use"})
            else:
                print(f"[COMPUTER_USE] Detected general web workflow. Initiating Swarm Handoff to Vision Agent.")
                raise SwarmHandoff("vision", task_description, {"source": "computer_use"})


        while self.is_running and self.current_step < self.max_steps:
            self.current_step += 1
            print(f"[COMPUTER_USE] Cycle Step {self.current_step}/{self.max_steps}")
            
            # 1. OBSERVE
            self.agent_state = "OBSERVING"
            before_frame, temp_before_path = await observer_pipeline.observe(task_description)
            
            # --- LLaVA VISION INTEGRATION (FIXED) ---
            llava_semantic_context = ""
            try:
                from core.perception.llava_client import llava_client
                import base64, io
                from PIL import Image as PILImage

                # FIX 1: Convert image to base64 string — handle str (already base64), PIL, or numpy
                raw_image = before_frame.image
                if raw_image is not None:
                    if isinstance(raw_image, str):
                        img_b64 = raw_image
                    elif isinstance(raw_image, PILImage.Image):
                        buf = io.BytesIO()
                        raw_image.save(buf, format="JPEG", quality=85)
                        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                    else:
                        import numpy as _np
                        pil_img = PILImage.fromarray(_np.asarray(raw_image, dtype=_np.uint8))
                        buf = io.BytesIO()
                        pil_img.save(buf, format="JPEG", quality=85)
                        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                else:
                    img_b64 = None

                if img_b64:
                    print(f"[COMPUTER_USE] Engaging LLaVA Vision Cortex for: {task_description}")
                    llava_understanding = llava_client.analyze_image(
                        img_b64,
                        f"Task: {task_description}. Describe all relevant UI elements, buttons, text fields, and their positions on screen."
                    )
                    if llava_understanding and len(llava_understanding) > 10:
                        llava_semantic_context = llava_understanding
                        before_frame.semantic_context = llava_understanding
                        print(f"[COMPUTER_USE] LLaVA Vision: {llava_understanding[:150]}...")
                    else:
                        print("[COMPUTER_USE] LLaVA returned empty — falling back to OCR-only planning")
                else:
                    print("[COMPUTER_USE] No image available for LLaVA")
            except Exception as e:
                print(f"[COMPUTER_USE] LLaVA Vision offline or failed: {e}")
            # -----------------------------------------

            # 2. PLAN — now with LLaVA semantic context wired in
            self.agent_state = "UNDERSTANDING"
            plan_res = await planner_pipeline.plan(
                task_description,
                self.current_step,
                self.max_steps,
                temp_before_path,
                active_recipe or {},
                semantic_context=llava_semantic_context  # FIX 2: Vision context now reaches planner
            )
            
            action_type = plan_res.get("action_type")
            target = plan_res.get("target") or "Screen target"
            text_payload = plan_res.get("text_payload")
            is_final_step = plan_res.get("finished", False)
            
            if not action_type or action_type == "WAIT" and is_final_step:
                print("[COMPUTER_USE] Loop terminated: Completed flag resolved or empty action.")
                break
            
            # 3. GROUND
            self.agent_state = "GROUNDING"
            ground_res = await grounder_pipeline.ground(task_description, target, before_frame)
            self.confidence_score = ground_res["confidence_score"]
            self.bounding_boxes = ground_res["bounding_boxes"]
            mapped_coords = ground_res["mapped_coords"]
            element_source = ground_res["element_source"]
            element = ground_res["element"]

            # Visual Grounding Fallback: OmniParser v2 Set-of-Marks + VLM
            if (self.confidence_score < 70 or not mapped_coords) and action_type in ["CLICK", "TYPE", "MOVE"]:
                print(f"[COMPUTER_USE] Traditional grounding confidence ({self.confidence_score}%). Engaging OmniParser v2 Set-of-Marks Visual Grounding...")
                try:
                    from core.perception.omni_parser import omni_parser
                    from core.perception.llava_client import llava_client
                    import re
                    
                    # 1. Parse screen with OmniParser to get numbered badges
                    temp_annotated = os.path.join("data", "temp", f"omni_{int(time.time()*1000)}.png")
                    os.makedirs(os.path.dirname(temp_annotated), exist_ok=True)
                    elements, annotated_path = omni_parser.parse_screen(before_frame.image, save_annotated_path=temp_annotated)
                    
                    if elements:
                        # 2. Check direct semantic / label match across parsed elements
                        target_clean = str(target).lower().strip()
                        best_match_badge = None
                        for el in elements:
                            if target_clean in el.label.lower() or el.label.lower() in target_clean:
                                best_match_badge = el.index
                                break
                        
                        # 3. If no label match, invoke VLM (LLaVA / Moondream) with Set-of-Marks prompt
                        if best_match_badge is None and annotated_path and os.path.exists(annotated_path):
                            elements_table = omni_parser.format_elements_for_vlm(elements[:25])
                            vlm_prompt = (
                                f"Task: Find element to {action_type} for '{target}'.\n"
                                f"{elements_table}\n"
                                f"Respond ONLY with the badge number like [N]."
                            )
                            vlm_ans = llava_client.analyze_image(annotated_path, vlm_prompt)
                            print(f"[COMPUTER_USE] VLM Set-of-Marks Grounding Answer: {vlm_ans}")
                            m = re.search(r"\[(\d+)\]", vlm_ans)
                            if m:
                                best_match_badge = int(m.group(1))

                        if best_match_badge is not None:
                            coords = omni_parser.resolve_badge(best_match_badge, elements)
                            if coords:
                                mapped_coords = coords
                                self.confidence_score = 92
                                element_source = "omni_parser_vlm"
                                print(f"[COMPUTER_USE] OmniParser successfully visually grounded '{target}' -> Badge [{best_match_badge}] at {mapped_coords} (Confidence: 92%)")

                except Exception as e_omni:
                    print(f"[COMPUTER_USE] OmniParser visual grounding fallback note: {e_omni}")

            if (self.confidence_score < 70 or not mapped_coords) and action_type in ["CLICK", "TYPE", "MOVE"]:
                print(f"[COMPUTER_USE] Grounding match confidence too low ({self.confidence_score}%). Aborting step to prevent blind click.")
                self.agent_state = "REPLANNING"
                state_machine.transition_to("REPLANNING", task_description, "Grounding match score below threshold")
                self.is_running = False
                return {"status": "FAILED", "reason": f"LOW_CONFIDENCE: Grounding match score below threshold ({self.confidence_score}%) for '{target}'."}

            # 4. SAFETY & ACT
            coords_list = list(mapped_coords) if mapped_coords is not None else []
            payload_str = str(text_payload) if text_payload is not None else ""
            safety_res = actor_pipeline.check_safety(
                action_type=action_type,
                target=target,
                mapped_coords=coords_list,
                text_payload=payload_str,
                confidence_score=int(self.confidence_score),
                task_description=task_description
            )
            if safety_res["safety_status"] == "PENDING_CONFIRMATION":
                print("[COMPUTER_USE] Perimeter gate intercepted. Confirmation required.")
                self.is_running = False
                return {
                    "status": "BLOCKED",
                    "action_id": safety_res["action_id"],
                    "message": safety_res["safety_message"],
                    "action": safety_res["normalized_action"].to_dict()
                }

            self.agent_state = "ACTING"
            exec_result_35 = await actor_pipeline.act(
                action_type=action_type,
                target=target,
                mapped_coords=coords_list,
                text_payload=payload_str,
                before_frame=before_frame,
                confidence_score=int(self.confidence_score),
                element_source=element_source,
                controller_action=safety_res["controller_action"],
                controller_payload=safety_res["controller_payload"],
                task_description=task_description
            )

            # --- POST FLIGHT (Legacy Phase 35) ---
            self.agent_state = "VERIFYING"
            state_machine.transition_to("VERIFYING", task_description, "Verify action results state transition")
            verification_success = exec_result_35.success

            after_frame_35 = None
            try:
                after_frame_35 = await screen_capturer.capture_frame_async()
            except Exception as e:
                from core.reliability.system_logger import system_logger
                system_logger.log('ERROR', 'computer_use_agent', f'Unhandled exception: {e}')
                pass

            pre_obs = ActionObservation(
                active_window_title=getattr(before_frame, 'active_window', 'Desktop') or 'Desktop',
                active_process_name=getattr(before_frame, 'active_process', 'none') or 'none',
                active_pid=0,
                dom_context=getattr(before_frame, 'browser_context', None),
                ocr_text=""
            )
            post_window = (getattr(after_frame_35, 'active_window', None) or 'Desktop') if after_frame_35 else 'Desktop'
            post_process = (getattr(after_frame_35, 'active_process', None) or 'none') if after_frame_35 else 'none'
            post_context = getattr(after_frame_35, 'browser_context', None) if after_frame_35 else None
            post_obs = ActionObservation(
                active_window_title=post_window,
                active_process_name=post_process,
                active_pid=0,
                dom_context=post_context,
                ocr_text=""
            )

            expected_state = {}
            if not expected_state and target:
                expected_state = {"ocr_text_contains": [target]}

            exec_res_dict = {"status": "SUCCESS"}
            if not verification_success:
                exec_res_dict = {"status": "ERROR", "error": exec_result_35.error or ""}

            verification_report = action_verifier.verify_post_flight(expected_state, pre_obs, post_obs, exec_res_dict)
            delta_report = action_delta_classifier.classify_delta(pre_obs, post_obs, expected_state, exec_res_dict)

            if verification_report["status"] == "SAFETY_BLOCK" or delta_report["category"] == "SAFETY_BLOCKED":
                self.is_running = False
                self.agent_state = "STANDBY"
                return {"status": "BLOCKED", "reason": "SAFETY_BLOCKED", "verification": verification_report}

            if verification_report["status"] in ["PARTIAL", "FAILED"]:
                reground_res = await action_regrounder.attempt_reground(
                    instruction=target, screen=after_frame_35 or before_frame, pre_element=element,
                    delta_metadata=delta_report.get("changed_metadata"), action_execution_result=exec_res_dict
                )
                if reground_res["status"] == "SAFETY_BLOCK":
                    self.is_running = False
                    self.agent_state = "STANDBY"
                    return {"status": "BLOCKED", "reason": "SAFETY_BLOCKED: Re-ground safety block."}

            action_result = ActionResult(
                action=safety_res["normalized_action"],
                success=verification_success,
                before_state=before_frame,
                after_state=after_frame_35,
                error=exec_result_35.error if not verification_success else None
            )
            self.step_history.append({
                **action_result.to_dict(),
                "confidence_report": exec_result_35.confidence.to_dict(),
                "verification_method": exec_result_35.verification_method,
                "backend_used": exec_result_35.backend_used,
                "latency_ms": exec_result_35.latency_ms,
            })

            try:
                active_app = getattr(before_frame, 'active_window', 'Desktop') or 'Desktop'
                app_action_memory.record(
                    app_name=active_app, action_type=action_type, tool=exec_result_35.backend_used,
                    success=verification_success, latency_ms=exec_result_35.latency_ms,
                )
            except Exception:
                pass

            if is_final_step and verification_success:
                print(f"[COMPUTER_USE] Single-step intention successfully completed: {action_type} on '{target}'.")
                break

            if not verification_success:
                self.agent_state = "REPLANNING"
                state_machine.transition_to("REPLANNING", task_description, f"Recovery: {action_type} failed.")
                failure_type = failure_classifier.classify(
                    action_type=action_type, target=target, exec_result=exec_res_dict,
                    target_confidence=self.confidence_score / 100.0,
                    before_active_window=pre_obs.active_window_title,
                    after_active_window=post_obs.active_window_title,
                    grounding_from_cache=False,
                )
                strategy = recovery_strategy_router.select_strategy(failure_type)
                
                if recovery_strategy_router.needs_user(strategy) or recovery_strategy_router.is_halt(strategy):
                    self.is_running = False
                    self.agent_state = "STANDBY"
                    return {"status": "FAILED" if recovery_strategy_router.is_halt(strategy) else "BLOCKED", "failure_type": failure_type}

        self.agent_state = "STANDBY"
        self.is_running = False
        # Running out of steps is not the same as succeeding. Report honestly:
        # SUCCESS only when at least one step actually verified.
        succeeded = any(
            isinstance(s, dict) and s.get("success") for s in self.step_history
        )
        if succeeded:
            return {"status": "SUCCESS", "history": self.step_history}
        return {
            "status": "FAILED",
            "reason": "No step completed successfully within the step budget.",
            "steps_attempted": len(self.step_history),
            "history": self.step_history,
        }

    def stop(self):
        self.is_running = False
        self.agent_state = "STANDBY"

computer_use_agent = ComputerUseAgent()

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    if not task:
        print("\n[COMPUTER_USE_AGENT] Status: OPERATIONAL / READY")
        print("[COMPUTER_USE_AGENT] Computer Use Agent initialized cleanly without memory errors.")
        print("[USAGE] Provide a task description to execute, for example:")
        print("        python core/orchestration/computer_use_agent.py \"Open Notepad\"")
    else:
        print(f"\n[COMPUTER_USE_AGENT] Executing task: '{task}'")
        res = asyncio.run(computer_use_agent.execute_task(task))
        print("\n=== EXECUTION RESULT ===")
        print(res)

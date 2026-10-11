from core.cognition.reasoning.brain import brain
from core.orchestration.agent_state_machine import state_machine
import os
from typing import Optional

try:
    from pydantic import BaseModel, field_validator, ValidationError

    class PlannerAction(BaseModel):
        """Pydantic schema for LLM planner output — prevents crashes from malformed JSON."""
        reasoning: str = "No reasoning provided"
        action_type: str = "WAIT"
        target: str = "System"
        text_payload: Optional[str] = None
        finished: bool = False

        @field_validator('action_type')
        @classmethod
        def normalize_action(cls, v):
            allowed = {"CLICK", "TYPE", "SCROLL", "MOVE", "OPEN_APP", "CLOSE_APP",
                       "HOTKEY", "PRESS", "WAIT", "DOUBLE_CLICK", "RIGHT_CLICK"}
            upper = str(v).upper().strip()
            return upper if upper in allowed else "WAIT"

    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False

# Operator-configurable city for booking tasks (no more hardcoded Kochi)
_USER_CITY = os.environ.get("AEGIS_USER_CITY", "Mumbai")

class PlannerPipeline:
    @staticmethod
    async def plan(
        task_description: str,
        current_step: int,
        max_steps: int,
        image_path: str,
        active_recipe: dict = None,
        semantic_context: str = ""   # FIX: LLaVA vision analysis now wired in here
    ) -> dict:
        state_machine.transition_to("UNDERSTANDING", task_description, "Decomposing task steps")
        
        # Check if we can reuse a step from the cached recipe
        if active_recipe and (current_step - 1) < len(active_recipe["action_sequence"]):
            recipe_step = active_recipe["action_sequence"][current_step - 1]
            return {
                "action_type": recipe_step.get("action_type"),
                "target": recipe_step.get("target"),
                "text_payload": recipe_step.get("text_payload"),
                "reasoning": "Reusing action recipe procedure.",
                "finished": False
            }

        # Deterministic instant dispatch for direct user intentions
        lower = task_description.lower().strip()
        
        # Pillar 1: Semantic Intent & Credential Gating (Frontier Big Tech Standard)
        from core.security.frontier_input_gate import frontier_input_gate
        is_valid, status, meta = frontier_input_gate.validate_command(task_description)
        if not is_valid:
            reason = meta.get("reason", f"Ambiguous or unsafe instruction '{task_description}' blocked by Frontier Input Gate.")
            return {
                "action_type": "WAIT",
                "target": "System",
                "text_payload": None,
                "reasoning": reason,
                "finished": True
            }

        for pfx in ("type ", "write ", "type in ", "type text "):
            if lower.startswith(pfx):
                payload = task_description[len(pfx):].strip()
                return {
                    "action_type": "TYPE",
                    "target": "Active Window",
                    "text_payload": payload,
                    "reasoning": f"Typing text '{payload}' directly into active focus.",
                    "finished": True
                }
        for pfx in ("press ", "hit ", "key "):
            if lower.startswith(pfx):
                key = lower[len(pfx):].strip()
                return {
                    "action_type": "PRESS",
                    "target": "Active Window",
                    "text_payload": key,
                    "reasoning": f"Pressing key '{key}'.",
                    "finished": True
                }
        for pfx in ("click ", "double click ", "double-click ", "right click ", "right-click ", "move mouse to ", "move to "):
            if lower.startswith(pfx):
                target_name = task_description[len(pfx):].strip(" .!?,:;")
                act = "DOUBLE_CLICK" if "double" in pfx else ("RIGHT_CLICK" if "right" in pfx else ("MOVE" if "move" in pfx else "CLICK"))
                return {
                    "action_type": act,
                    "target": target_name,
                    "text_payload": None,
                    "reasoning": f"Interacting with target '{target_name}'.",
                    "finished": True
                }
        import re
        import urllib.parse
        # 1. Compound "open X and play/search Y"
        m_compound = re.search(r"open\s+(?:the\s+)?(youtube|chrome|spotify|browser|google)\s+and\s+(?:play|search|find|listen to)\s+(.+)", lower)
        if m_compound:
            target_service = m_compound.group(1).lower()
            query = m_compound.group(2).strip(" .!?,:;")
            if "youtube" in target_service:
                clean_q = re.sub(r"^(the\s+song|song|the\s+music|music|the\s+video|video)\s+", "", query, flags=re.I).strip()
                dest_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}" if clean_q else "https://www.youtube.com"
                return {
                    "action_type": "OPEN_APP",
                    "target": dest_url,
                    "text_payload": None,
                    "reasoning": f"Navigating directly to YouTube for: {clean_q or 'music'}.",
                    "finished": True
                }
            elif "spotify" in target_service:
                return {
                    "action_type": "OPEN_APP",
                    "target": f"https://open.spotify.com/search/{urllib.parse.quote(query)}",
                    "text_payload": None,
                    "reasoning": f"Opening Spotify to play: {query}.",
                    "finished": True
                }
            elif "chrome" in target_service or "browser" in target_service or "google" in target_service:
                return {
                    "action_type": "OPEN_APP",
                    "target": f"https://www.google.com/search?q={urllib.parse.quote(query)}",
                    "text_payload": None,
                    "reasoning": f"Searching in Chrome for: {query}.",
                    "finished": True
                }

        # 2. "play ... on youtube / spotify"
        if (lower.startswith("play ") or lower.startswith("watch ") or lower.startswith("listen to ")) and ("youtube" in lower or "spotify" in lower):
            is_spotify = "spotify" in lower
            clean_q = re.sub(r"\b(on\s+youtube|from\s+youtube|in\s+youtube|on\s+spotify|from\s+spotify|in\s+spotify|youtube|spotify)\b", "", task_description, flags=re.I)
            clean_q = re.sub(r"^(play the song|play song|play the music|play music|watch the video|watch video|watch|listen to|play)\s+", "", clean_q, flags=re.I).strip(" .!?,:;")
            if is_spotify:
                dest_url = f"https://open.spotify.com/search/{urllib.parse.quote(clean_q)}" if clean_q else "https://open.spotify.com"
            else:
                dest_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}" if clean_q else "https://www.youtube.com"
            return {
                "action_type": "OPEN_APP",
                "target": dest_url,
                "text_payload": None,
                "reasoning": f"Opening {'Spotify' if is_spotify else 'YouTube'} for: {clean_q}.",
                "finished": True
            }

        for pfx in ("open ", "launch ", "start "):
            if lower.startswith(pfx):
                full_rest = task_description[len(pfx):].strip()
                # Split clauses by comma, semicolon, "and then", "then", or "and"
                clauses = [c.strip() for c in re.split(r'[,;]|\band\s+then\b|\bthen\b', full_rest) if c.strip()]
                is_compound = len(clauses) > 1

                app = clauses[0].strip()
                if app.lower().startswith("the "):
                    app = app[4:].strip()

                if not is_compound:
                    return {
                        "action_type": "OPEN_APP",
                        "target": app,
                        "text_payload": None,
                        "reasoning": f"Opening application '{app}'.",
                        "finished": True
                    }

                # Multi-clause compound task sequence handling:
                if current_step == 1:
                    return {
                        "action_type": "OPEN_APP",
                        "target": app,
                        "text_payload": None,
                        "reasoning": f"Step 1: Opening '{app}' for multi-step workflow.",
                        "finished": False
                    }
                elif current_step == 2:
                    for cl in clauses[1:]:
                        m_txt = re.search(r"(?:write|type|input|enter)\s+['\"]?([^'\"]+?)['\"]?(?:\s+and|$)", cl, re.I)
                        if m_txt:
                            text_to_type = m_txt.group(1).strip()
                            has_save = any("save" in c.lower() for c in clauses[1:])
                            return {
                                "action_type": "TYPE",
                                "target": "Active Window",
                                "text_payload": text_to_type,
                                "reasoning": f"Step 2: Typing text '{text_to_type}' into active {app}.",
                                "finished": not has_save
                            }
                elif current_step == 3:
                    for cl in clauses[1:]:
                        if "save" in cl.lower():
                            m_file = re.search(r"(?:as|to)\s+['\"]?([^'\" ]+\.[a-zA-Z0-9]+)['\"]?", cl, re.I)
                            filename = m_file.group(1).strip() if m_file else ""
                            return {
                                "action_type": "HOTKEY",
                                "target": "ctrl+s",
                                "text_payload": "ctrl+s",
                                "reasoning": "Step 3: Triggering save dialog via Ctrl+S shortcut.",
                                "finished": not bool(filename)
                            }
                elif current_step == 4:
                    for cl in clauses[1:]:
                        if "save" in cl.lower():
                            m_file = re.search(r"(?:as|to)\s+['\"]?([^'\" ]+\.[a-zA-Z0-9]+)['\"]?", cl, re.I)
                            if m_file:
                                filename = m_file.group(1).strip()
                                return {
                                    "action_type": "TYPE",
                                    "target": "Active Window",
                                    "text_payload": f"{filename}\n",
                                    "reasoning": f"Step 4: Submitting save filename '{filename}'.",
                                    "finished": True
                                }
        if "maximize" in lower:
            return {"action_type": "HOTKEY", "target": "win+up", "text_payload": "win+up", "reasoning": "Maximize window", "finished": True}
        if "minimize" in lower:
            return {"action_type": "HOTKEY", "target": "win+down", "text_payload": "win+down", "reasoning": "Minimize window", "finished": True}
        if "close window" in lower or "close it" in lower:
            return {"action_type": "HOTKEY", "target": "alt+f4", "text_payload": "alt+f4", "reasoning": "Close window", "finished": True}

        # Multi-Step Autonomous Search & Visual Booking Pipeline
        import re
        url_match = re.search(r"(?:navigate to|go to|open)\s+([a-zA-Z0-9.-]+\.(?:com|org|net|in|io|co|ai)[^\s,]*)", task_description, re.IGNORECASE)
        search_query = None
        search_match = re.search(r"search for ['\"]([^'\"]+)['\"]", task_description, re.IGNORECASE)
        if search_match:
            search_query = search_match.group(1).strip()
        else:
            search_match = re.search(r"search for\s+([^,]+?)(?:\s+and\s+|,|$)", task_description, re.IGNORECASE)
            if search_match:
                search_query = search_match.group(1).strip().strip("'\"")

        is_booking_task = any(w in task_description.lower() for w in ("book ticket", "book tickets", "bookmyshow", "booking", "tickets"))

        if url_match and current_step == 1:
            dest_url = url_match.group(1).strip()
            if not dest_url.startswith("http"):
                dest_url = f"https://{dest_url}"
            return {
                "action_type": "OPEN_APP",
                "target": dest_url,
                "text_payload": None,
                "reasoning": f"Navigating browser to target destination: {dest_url}.",
                "finished": False
            }

        # Dynamic Visual State Observation (Frontier Closed-Loop ReAct)
        if (url_match or is_booking_task or search_query) and current_step >= 2:
            import os
            screen_text_corpus = ""
            if image_path and os.path.exists(image_path):
                try:
                    from core.perception.ocr import ocr_engine
                    ocr_engine._init_reader()
                    if ocr_engine.easyocr_reader:
                        raw = ocr_engine.easyocr_reader.readtext(image_path)
                        screen_text_corpus = " ".join([r[1] for r in raw]).lower()
                except Exception:
                    pass

            # 1. Location / Region Modal Interruption (e.g. BookMyShow Popular Cities)
            if any(k in screen_text_corpus for k in ["select your region", "popular cities", "detect my location", "search for your city"]):
                # FIX: Use configured city from env, not hardcoded Kochi
                return {
                    "action_type": "CLICK",
                    "target": _USER_CITY,
                    "text_payload": None,
                    "reasoning": f"Detected location selection modal. Selecting configured city '{_USER_CITY}' to dismiss overlay.",
                    "finished": False
                }

            # 2. Terms & Conditions Modal
            if "accept" in screen_text_corpus and any(k in screen_text_corpus for k in ["terms", "condition", "guidelines", "disclaimer"]):
                return {
                    "action_type": "CLICK",
                    "target": "Accept",
                    "text_payload": None,
                    "reasoning": "Accepting terms and conditions modal.",
                    "finished": False
                }

            # 3. Format / Language Selection (e.g. 2D)
            if "2d" in screen_text_corpus and any(k in screen_text_corpus for k in ["format", "select format", "malayalam", "tamil", "hindi", "screen"]):
                return {
                    "action_type": "CLICK",
                    "target": "2D",
                    "text_payload": None,
                    "reasoning": "Selecting 2D movie format.",
                    "finished": False
                }

            # 4. Seat Count / Selection Button
            if "select seats" in screen_text_corpus:
                return {
                    "action_type": "CLICK",
                    "target": "Select Seats",
                    "text_payload": None,
                    "reasoning": "Clicking 'Select Seats' to enter cinema seat layout.",
                    "finished": True
                }

            # 5. Book Tickets Button (on movie details page)
            if "book tickets" in screen_text_corpus:
                return {
                    "action_type": "CLICK",
                    "target": "Book tickets",
                    "text_payload": None,
                    "reasoning": "Locating and clicking 'Book tickets' action button.",
                    "finished": False
                }

            # 6. Movie Card in Search Results
            if search_query and search_query.lower() in screen_text_corpus:
                return {
                    "action_type": "CLICK",
                    "target": search_query,
                    "text_payload": None,
                    "reasoning": f"Clicking target movie '{search_query}' from search results.",
                    "finished": False
                }

            # 7. Search Bar input (when search hasn't been executed yet)
            if search_query and ("search" in screen_text_corpus or "movies" in screen_text_corpus):
                return {
                    "action_type": "TYPE",
                    "target": "Search for Movies",
                    "text_payload": search_query,
                    "reasoning": f"Locating BookMyShow search bar and inputting '{search_query}'.",
                    "finished": False
                }

        # Build a rich chain-of-thought prompt (mirrors Claude Computer Use reasoning structure)
        vision_section = ""
        if semantic_context and len(semantic_context.strip()) > 10:
            vision_section = f"""\n=== LLaVA VISION ANALYSIS (LIVE SCREEN) ===\n{semantic_context}\n==========================================\n"""

        prompt = (
            f"You are A.E.G.I.S., an elite autonomous AI computer use agent.\n"
            f"CURRENT GOAL: {task_description}\n"
            f"STEP: {current_step} of {max_steps}\n"
            f"{vision_section}"
            "\nBefore deciding the action, reason through:\n"
            "1. What does the current screen show?\n"
            "2. What is the most logical SINGLE next action?\n"
            "3. Is the goal complete?\n"
            "\nReturn ONLY valid JSON in this exact format:\n"
            "{\n"
            "  \"reasoning\": \"Step-by-step reasoning about what you see and what to do\",\n"
            "  \"action_type\": \"CLICK\" | \"TYPE\" | \"SCROLL\" | \"MOVE\" | \"OPEN_APP\" | \"CLOSE_APP\" | \"HOTKEY\" | \"PRESS\" | \"WAIT\",\n"
            "  \"target\": \"Exact name or text of the target GUI element\",\n"
            "  \"text_payload\": \"Text to type if action is TYPE, otherwise null\",\n"
            "  \"finished\": true | false\n"
            "}"
        )
        
        import json
        import os
        from core.cognition.reasoning.local_llm import local_llm_provider
        from core.cognition.reasoning.cloud_llm import cloud_llm_provider
        
        try:
            is_local = os.environ.get("AEGIS_LOCAL_FIRST", "true").lower() == "true"
            vision_text = ""
            resp_text = ""
            
            # 1. Try Cloud Vision/Chat if allowed
            if not is_local and cloud_llm_provider.gemini_active:
                vision_res = await cloud_llm_provider.call_gemini_vision("Describe the relevant UI elements for the task: " + task_description, image_path, "")
                vision_text = vision_res.get("response", "") if vision_res else ""
                
                full_prompt = f"Screen Vision Description: {vision_text}\n\n{prompt}"
                ai_res = await cloud_llm_provider.call_gemini_chat(full_prompt, "You are a JSON-only Computer Use Planner.")
                resp_text = ai_res.get("response", "") if ai_res else ""
                
            # 2. Fallback to local if cloud failed or is disabled
            if not resp_text:
                vision_res = await local_llm_provider.call_local_vision("Describe the relevant UI elements for the task: " + task_description, image_path, "")
                vision_text = vision_res.get("response", "") if vision_res else ""
                
                full_prompt = f"Screen Vision Description: {vision_text}\n\n{prompt}"
                ai_res = await local_llm_provider.call_local_ollama(full_prompt, "You are a JSON-only Computer Use Planner.")
                resp_text = ai_res.get("response", "") if ai_res else ""
            
            import re
            json_match = re.search(r'\{.*\}', resp_text, re.DOTALL)
            if json_match:
                raw_dict = json.loads(json_match.group(0))
                # FIX: Pydantic validation catches malformed LLM output cleanly
                if _PYDANTIC_AVAILABLE:
                    try:
                        validated = PlannerAction(**raw_dict)
                        parsed_res = validated.model_dump()
                    except ValidationError as ve:
                        print(f"[COMPUTER_USE_PLANNER] Pydantic validation warning: {ve}. Using safe defaults.")
                        parsed_res = {"action_type": "WAIT", "target": "System", "finished": False,
                                      "reasoning": "Validation failed on LLM output"}
                else:
                    parsed_res = raw_dict
            else:
                raise ValueError("No JSON block found in LLM response")
        except Exception as e:
            print(f"[COMPUTER_USE_PLANNER] Failed to plan: {e}. Raw output: {resp_text[:100] if 'resp_text' in locals() else 'None'}")
            parsed_res = {"action_type": "WAIT", "target": "System", "finished": False}
        
        # Safety Gate: Reject hallucinated placeholder typing (e.g. 'your_password_here', 'dummy', 'example')
        text_payload = parsed_res.get("text_payload")
        if text_payload and re.search(r"your[_\s]password|your[_\s]username|dummy|example|<.*?>|placeholder", str(text_payload), re.I):
            print(f"[COMPUTER_USE_PLANNER] Rejected hallucinated placeholder text: '{text_payload}'")
            parsed_res["action_type"] = "WAIT"
            parsed_res["text_payload"] = None
            parsed_res["finished"] = True
            parsed_res["reasoning"] = f"Safety gate rejected hallucinated placeholder text '{text_payload}'."

        # Log the reasoning trace every step (like Claude Computer Use)
        reasoning = parsed_res.get("reasoning", "")
        if reasoning:
            print(f"[PLANNER REASONING] Step {current_step}: {reasoning[:200]}")

        # BACKTRACK/REPLAN: If action is WAIT (failure mode), escalate with
        # a different strategy prompt on retry. Mirrors Claude's re-plan loop.
        action_type = parsed_res.get("action_type")
        if not action_type or action_type in ["fallback", "vision_fail", "WAIT"]:
            if action_type == "WAIT":
                print(f"[PLANNER] Step {current_step}: WAIT detected — triggering backtrack replan")
                # Replan attempt: tell the LLM the current approach isn't working
                try:
                    backtrack_prompt = (
                        f"Previous step returned WAIT because the planner couldn't identify a target.\n"
                        f"GOAL: {task_description}\n"
                        f"STEP: {current_step} of {max_steps}\n"
                        f"{vision_section if 'vision_section' in dir() else ''}"
                        "Try a DIFFERENT approach than before. Consider:\n"
                        "- Using SCROLL to reveal hidden elements\n"
                        "- Using HOTKEY (e.g. Tab, Enter, Escape) to navigate\n"
                        "- Using OPEN_APP if an app needs to launch first\n"
                        "Return ONLY valid JSON with action_type, target, text_payload, reasoning, finished."
                    )
                    is_local = os.environ.get("AEGIS_LOCAL_FIRST", "true").lower() == "true"
                    if is_local:
                        retry_res = await local_llm_provider.call_local_ollama(
                            backtrack_prompt, "You are a JSON-only Computer Use Planner. Output only JSON."
                        )
                    else:
                        retry_res = await cloud_llm_provider.call_gemini_chat(
                            backtrack_prompt, "You are a JSON-only Computer Use Planner."
                        )
                    retry_text = retry_res.get("response", "") if retry_res else ""
                    retry_match = re.search(r'\{.*\}', retry_text, re.DOTALL)
                    if retry_match:
                        retry_dict = json.loads(retry_match.group(0))
                        if retry_dict.get("action_type", "WAIT") != "WAIT":
                            print(f"[PLANNER] Backtrack succeeded: {retry_dict.get('action_type')} -> {retry_dict.get('target')}")
                            parsed_res = retry_dict
                        else:
                            print("[PLANNER] Backtrack also returned WAIT — accepting as terminal step")
                except Exception as be:
                    print(f"[PLANNER] Backtrack attempt error: {be}")
            else:
                parsed_res["action_type"] = "WAIT"
                parsed_res["target"] = "System"
                parsed_res["finished"] = False
                
        return parsed_res

planner_pipeline = PlannerPipeline()

import os
import base64
from typing import List, Optional
from dotenv import load_dotenv
load_dotenv()
from core.perception.visual_state import ScreenFrame, GUIElement

async def query_vlm_direct_coordinates(screen: ScreenFrame, instruction: str) -> Optional[GUIElement]:
    """
    Direct Vision LLM Grounding:
    Uses Gemini 2.0 Flash or Groq Vision to directly predict (x, y) coordinates
    of any visual target on the screen without requiring pre-computed DOM/OCR boxes.
    """
    import json
    import re
    import base64
    from PIL import Image as PILImage
    import io

    prompt = (
        f"You are a computer vision UI grounding assistant. Look at this desktop screenshot of dimensions "
        f"{screen.width}x{screen.height}. Find the exact visual center of: '{instruction}'.\n"
        f"If the target '{instruction}' is NOT clearly visible on this screen, output: {{\"found\": false}}.\n"
        f"If the target IS clearly visible, output: {{\"found\": true, \"x\": <integer_x>, \"y\": <integer_y>}}.\n"
        f"Output ONLY a raw JSON object with no markdown fences."
    )

    # 0. Circuit Breakers
    from core.reliability.circuit_breaker import circuit_breaker_manager
    gemini_breaker = circuit_breaker_manager.get_breaker("cloud_gemini", failure_threshold=2, recovery_timeout=300.0)
    groq_breaker = circuit_breaker_manager.get_breaker("cloud_groq", failure_threshold=2, recovery_timeout=120.0)
    local_first = os.getenv("AEGIS_LOCAL_FIRST", "true").lower() == "true"

    # ── Tier 0 (Sovereign Local GPU First): LLaVA / Moondream on RTX 3050 ──
    if local_first or (not gemini_breaker.can_execute() and not groq_breaker.can_execute()):
        try:
            import requests
            preferred_model = os.getenv("OLLAMA_VISION_MODEL", "llava:latest")
            vision_models = [preferred_model] + [m for m in ("llava:latest", "moondream:latest") if m != preferred_model]
            for v_model in vision_models:
                try:
                    print(f"[GROUNDER] Direct VLM query via Local {v_model} for '{instruction}'...")
                    payload = {
                        "model": v_model,
                        "prompt": prompt,
                        "images": [screen.image],
                        "stream": False,
                        "format": "json"
                    }
                    resp = requests.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=45.0)
                    if resp.status_code == 200:
                        raw = resp.json().get("response", "").strip()
                        if not raw:
                            continue
                        match = re.search(r'\{.*\}', raw, re.DOTALL)
                        if match:
                            coords = json.loads(match.group(0))
                            if coords.get("found") is False:
                                print(f"[GROUNDER] Local {v_model} confirmed '{instruction}' is NOT on screen.")
                                return None
                            x, y = int(coords.get("x", 0)), int(coords.get("y", 0))
                            if x <= 15 and y <= 15:
                                continue
                            if 0 <= x <= screen.width and 0 <= y <= screen.height:
                                print(f"[GROUNDER] Local {v_model} pinpointed target at ({x}, {y})")
                                return GUIElement(
                                    element_id=f"vlm_direct_{v_model.replace(':', '_')}",
                                    element_type="visual_target",
                                    text=instruction,
                                    role="target",
                                    bbox=[max(0, x-25), max(0, y-25), min(screen.width, x+25), min(screen.height, y+25)],
                                    center=[x, y],
                                    confidence=0.95,
                                    clickable=True,
                                    source=f"local_{v_model}"
                                )
                except Exception as model_err:
                    print(f"[GROUNDER] Model {v_model} pass: {model_err}")
                    continue
        except Exception as el:
            print(f"[GROUNDER] Local direct grounding bypass exception: {el}")
            
    # 1. Tier 1: Gemini Flash (Guarded by Circuit Breaker)
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_breaker.can_execute():
        try:
            print(f"[GROUNDER] Direct VLM query via Gemini for '{instruction}'...")
            from google import genai
            client = genai.Client(api_key=gemini_key)
            img_data = base64.b64decode(screen.image)
            pil_img = PILImage.open(io.BytesIO(img_data))
            response = None
            for g_model in ("gemini-flash-lite-latest", "gemini-flash-latest"):
                try:
                    response = client.models.generate_content(
                        model=g_model,
                        contents=[prompt, pil_img]
                    )
                    if response and response.text:
                        break
                except Exception as e_gen:
                    if any(k in str(e_gen) for k in ("429", "ResourceExhausted", "QuotaExceeded", "Too Many Requests")):
                        print(f"[GROUNDER] Gemini quota limit (429) hit on {g_model}. Tripping breaker to fallback.")
                        gemini_breaker.record_failure()
                        break
                    continue
            raw_text = response.text.strip() if (response and response.text) else ""
            if not raw_text and gemini_breaker.can_execute():
                # Direct REST fallback
                import requests
                payload = {'contents': [{'parts': [{'text': prompt}, {'inline_data': {'mime_type': 'image/jpeg', 'data': screen.image}}]}]}
                for m_id in ("gemini-flash-lite-latest", "gemini-flash-latest"):
                    try:
                        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m_id}:generateContent?key={gemini_key}", json=payload, timeout=8)
                        if r.status_code == 200:
                            raw_text = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                            if raw_text:
                                break
                        elif r.status_code == 429:
                            gemini_breaker.record_failure()
                            break
                    except Exception:
                        continue
            if response and response.text:
                raw = response.text.strip()
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    coords = json.loads(match.group(0))
                    if coords.get("found") is False:
                        print(f"[GROUNDER] Gemini confirmed '{instruction}' is NOT on screen.")
                        return None
                    x, y = int(coords.get("x", 0)), int(coords.get("y", 0))
                    if x <= 15 and y <= 15:
                        print(f"[GROUNDER] Gemini returned origin coordinates ({x}, {y}) for '{instruction}'; rejected.")
                        return None
                    if 0 <= x <= screen.width and 0 <= y <= screen.height:
                        gemini_breaker.record_success()
                        print(f"[GROUNDER] Gemini pinpointed target at ({x}, {y})")
                        return GUIElement(
                            element_id="vlm_direct_gemini",
                            element_type="visual_target",
                            text=instruction,
                            role="target",
                            bbox=[max(0, x-25), max(0, y-25), min(screen.width, x+25), min(screen.height, y+25)],
                            center=[x, y],
                            confidence=0.96,
                            clickable=True,
                            source="gemini_vlm"
                        )
            gemini_breaker.record_failure("No Gemini coordinate response")
        except Exception as eg:
            gemini_breaker.record_failure(eg)
            print(f"[GROUNDER] Direct Gemini vision attempt error: {eg}")

    # 2. Tier 2: Groq Vision (Guarded by Circuit Breaker)
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and groq_breaker.can_execute():
        try:
            print(f"[GROUNDER] Direct VLM query via Groq Vision for '{instruction}'...")
            import httpx
            headers = {"Authorization": f"Bearer {groq_key}"}
            payload = {
                "model": "llama-3.2-11b-vision-preview",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screen.image}"}}
                        ]
                    }
                ],
                "max_tokens": 100
            }
            with httpx.Client(timeout=10.0) as client:
                res = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                if res.status_code == 200:
                    raw = res.json()["choices"][0]["message"]["content"].strip()
                    match = re.search(r'\{.*\}', raw, re.DOTALL)
                    if match:
                        coords = json.loads(match.group(0))
                        if coords.get("found") is False:
                            print(f"[GROUNDER] Groq Vision confirmed '{instruction}' is NOT on screen.")
                            return None
                        x, y = int(coords.get("x", 0)), int(coords.get("y", 0))
                        if x <= 15 and y <= 15:
                            print(f"[GROUNDER] Groq Vision returned origin coordinates ({x}, {y}) for '{instruction}'; rejected.")
                            return None
                        if 0 <= x <= screen.width and 0 <= y <= screen.height:
                            groq_breaker.record_success()
                            print(f"[GROUNDER] Groq Vision pinpointed target at ({x}, {y})")
                            return GUIElement(
                                element_id="vlm_direct_groq",
                                element_type="visual_target",
                                text=instruction,
                                role="target",
                                bbox=[max(0, x-25), max(0, y-25), min(screen.width, x+25), min(screen.height, y+25)],
                                center=[x, y],
                                confidence=0.92,
                                clickable=True,
                                source="groq_vlm"
                            )
                else:
                    groq_breaker.record_failure(f"HTTP {res.status_code}")
        except Exception as egq:
            groq_breaker.record_failure(egq)
            print(f"[GROUNDER] Direct Groq vision attempt error: {egq}")

    return None

async def query_vlm_fallback(screen: ScreenFrame, instruction: str, elements: List[GUIElement]) -> Optional[GUIElement]:
    if not elements:
        return await query_vlm_direct_coordinates(screen, instruction)

    # Draw numbered bounding boxes (Set-of-Mark)
    import cv2
    import numpy as np
    
    # Decode image
    img_data = base64.b64decode(screen.image)
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    element_map = {}
    for idx, el in enumerate(elements):
        x1, y1, x2, y2 = el.bbox
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(img, str(idx), (x1, max(y1-5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        element_map[str(idx)] = el

    # Encode back to base64 in memory
    success, buffer = cv2.imencode('.png', img)
    if not success:
        print("[GROUNDER] Failed to encode SOM image in memory")
        return None
    som_b64 = base64.b64encode(buffer).decode('utf-8')

    # Query VLM (Check local sovereign model first or guarded cloud circuit breakers)
    from core.reliability.circuit_breaker import circuit_breaker_manager
    gemini_breaker = circuit_breaker_manager.get_breaker("cloud_gemini", failure_threshold=2, recovery_timeout=300.0)
    groq_breaker = circuit_breaker_manager.get_breaker("cloud_groq", failure_threshold=2, recovery_timeout=120.0)
    mistral_breaker = circuit_breaker_manager.get_breaker("cloud_mistral", failure_threshold=2, recovery_timeout=120.0)
    openai_breaker = circuit_breaker_manager.get_breaker("cloud_openai", failure_threshold=1, recovery_timeout=600.0)
    
    prompt = f"I have overlaid numbered bounding boxes on this UI. Which number corresponds to the target: '{instruction}'? Reply with ONLY the number."
    answer = None

    LOCAL_FIRST = os.getenv("AEGIS_LOCAL_FIRST", "true").lower() == "true"

    # ── Tier 0 (Sovereign Local GPU First): Moondream on RTX 3050 ──
    if LOCAL_FIRST and not answer:
        try:
            import requests
            resp = requests.post('http://127.0.0.1:11434/api/generate', json={
                "model": "moondream:latest",
                "prompt": prompt,
                "images": [som_b64],
                "stream": False
            }, timeout=60.0)
            if resp.status_code == 200:
                candidate = resp.json().get('response', '').strip()
                if candidate:
                    answer = candidate
                    print(f"[GROUNDER] Local moondream:latest resolved target immediately: {answer}")
                else:
                    print(f"[GROUNDER] Local moondream returned empty response.")
            else:
                print(f"[GROUNDER] Local moondream failed with status code: {resp.status_code}")
        except Exception as e_local:
            print(f"[GROUNDER] Local moondream bypass exception: {e_local}")
            
    # ── Tier 1: Gemini Flash (Fastest multimodal vision, Active Key) ──
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_breaker.can_execute() and not answer:
        try:
            print("[GROUNDER] Routing vision task to Gemini...")
            from google import genai
            from PIL import Image as PILImage
            import io

            client = genai.Client(api_key=gemini_key)
            pil_img = PILImage.open(temp_path)
            response = None
            for g_model in ("gemini-flash-lite-latest", "gemini-flash-latest"):
                try:
                    response = client.models.generate_content(
                        model=g_model,
                        contents=[prompt, pil_img]
                    )
                    if response and response.text:
                        break
                except Exception:
                    continue
            if response and response.text:
                answer = response.text.strip()
                gemini_breaker.record_success()
                print(f"[GROUNDER] Gemini resolved answer: {answer}")
            else:
                gemini_breaker.record_failure("No Gemini response")
        except Exception as eg:
            gemini_breaker.record_failure(eg)
            print(f"[GROUNDER] Gemini vision attempt failed (breaker tripped): {eg}")

    # ── Tier 2: Groq Vision (Active Key) ──
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and groq_breaker.can_execute() and not answer:
        try:
            print("[GROUNDER] Routing vision task to Groq Vision...")
            import httpx
            headers = {"Authorization": f"Bearer {groq_key}"}
            payload = {
                "model": "llama-3.2-11b-vision-preview",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{som_b64}"}}
                        ]
                    }
                ],
                "max_tokens": 50
            }
            with httpx.Client(timeout=10.0) as client:
                res = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                if res.status_code == 200:
                    answer = res.json()["choices"][0]["message"]["content"].strip()
                    groq_breaker.record_success()
                    print(f"[GROUNDER] Groq resolved answer: {answer}")
                else:
                    groq_breaker.record_failure(f"HTTP {res.status_code}")
        except Exception as egq:
            groq_breaker.record_failure(egq)
            print(f"[GROUNDER] Groq vision attempt failed (breaker tripped): {egq}")

    # ── Tier 3: Mistral Pixtral (Active Key) ──
    mistral_key = os.getenv("MISTRAL_API_KEY")
    if mistral_key and mistral_breaker.can_execute() and not answer:
        try:
            print("[GROUNDER] Routing vision task to Mistral Pixtral...")
            import httpx
            headers = {"Authorization": f"Bearer {mistral_key}"}
            payload = {
                "model": "pixtral-large-latest",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{som_b64}"}}
                        ]
                    }
                ],
                "max_tokens": 50
            }
            with httpx.Client(timeout=10.0) as client:
                res = client.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload)
                if res.status_code == 200:
                    answer = res.json()["choices"][0]["message"]["content"].strip()
        except Exception as em:
            print(f"[GROUNDER] Mistral vision attempt failed: {em}")

    # ── Tier 4: OpenAI (Guarded with immediate error catch, 0 retries) ──
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and openai_breaker.can_execute() and not openai_key.startswith("your_") and not answer:
        try:
            print("[GROUNDER] Attempting OpenAI GPT-4o fallback...")
            import litellm
            litellm.num_retries = 0
            response = litellm.completion(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{som_b64}"}}
                        ]
                    }
                ],
                timeout=3.0,
                num_retries=0
            )
            answer = response.choices[0].message.content.strip()
            openai_breaker.record_success()
        except Exception as e_oai:
            openai_breaker.record_failure(e_oai)
            print(f"[GROUNDER] OpenAI vision bypassed (breaker tripped): {e_oai}")

    # ── Tier 5: Local Vision (Moondream prioritized for 4GB RTX 3050, then LLaVA) ──
    if not answer:
        for local_v_model in ("moondream:latest", "llava:latest"):
            try:
                import requests
                resp = requests.post('http://127.0.0.1:11434/api/generate', json={
                    "model": local_v_model,
                    "prompt": prompt,
                    "images": [som_b64],
                    "stream": False
                }, timeout=60.0)
                if resp.status_code == 200:
                    answer = resp.json().get('response', '').strip()
                    if answer:
                        print(f"[GROUNDER] Local {local_v_model} resolved target")
                        break
            except Exception:
                pass

    # ── Resolve Box Number from Answer ──
    import re
    if answer:
        # Match explicit box/badge designations: "[3]", "Box 3", "Badge #3", "#3" or pure digit output
        m_box = re.search(r'(?:box|badge|element|#|\[)\s*(\d+)\]?', answer, re.IGNORECASE)
        box_num = m_box.group(1) if m_box else None
        if not box_num and answer.strip().isdigit():
            box_num = answer.strip()

        if box_num and box_num in element_map:
            target_el = element_map[box_num]
            print(f"[GROUNDER] VLM successfully resolved target to Box #{box_num} at {target_el.center}")
            target_el.confidence = 0.90
            target_el.evidence = {"som_vlm_fallback": True, "vlm_raw_answer": answer}
            return target_el

    # ── Tier 6: Heuristic Best-Match Fallback (Only if element text has genuine semantic overlap) ──
    instr_lower = instruction.lower()
    stop_words = {"the", "and", "for", "click", "open", "button", "screen", "icon", "target"}
    meaningful_words = [w for w in instr_lower.split() if len(w) > 2 and w not in stop_words]
    if meaningful_words:
        for idx_str, el in element_map.items():
            if el.text and any(word in el.text.lower() for word in meaningful_words):
                print(f"[GROUNDER] Fallback heuristic matched Box #{idx_str} ('{el.text}') for '{instruction}'")
                el.confidence = 0.80
                return el

    # CRITICAL: NEVER return a blind random element like element_map[0]!
    # If the visual grounder cannot find the real target, return None so the system
    # does NOT click random areas of the user's screen.
    print(f"[GROUNDER] No confident target match for '{instruction}'. Rejecting blind click.")
    return None

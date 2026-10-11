import logging
import os
from PIL import ImageGrab

import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO)

@dataclass
class GUIElement:
    element_id: str
    element_type: str
    text: str = ""
    role: str = "element"
    bbox: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    center: List[int] = field(default_factory=lambda: [0, 0])
    confidence: float = 1.0
    clickable: bool = True
    visible: bool = True
    source: str = "generic"
    evidence: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ScreenFrame:
    image_b64: str = ""
    width: int = 1920
    height: int = 1080
    active_window: str = "Desktop"
    browser_context: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)
    semantic_context: str = ""

    @property
    def image(self) -> str:
        return self.image_b64

@dataclass
class Action:
    action_type: str = "CLICK"
    target: str = ""
    coords: Optional[List[int]] = None
    text_payload: Optional[str] = None
    confidence: float = 1.0
    payload: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ActionResult:
    status: str = "SUCCESS"
    action_type: str = ""
    target: Optional[Any] = None
    detail: str = ""
    timestamp: float = field(default_factory=time.time)
    error: Optional[str] = None
    success: bool = True
    pre_validation_passed: bool = True
    post_verification_passed: bool = True
    verification_method: str = "n/a"
    backend_used: str = "native"
    latency_ms: float = 0.0
    action: Optional[Any] = None
    before_state: Optional[Any] = None
    after_state: Optional[Any] = None

    def __post_init__(self):
        if self.action and not self.action_type:
            self.action_type = getattr(self.action, "action_type", str(self.action))
        if self.action and not self.target:
            self.target = getattr(self.action, "target", None)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "action_type": self.action_type,
            "target": str(self.target or ""),
            "detail": self.detail,
            "timestamp": self.timestamp,
            "error": self.error,
            "success": self.success,
            "pre_validation_passed": self.pre_validation_passed,
            "post_verification_passed": self.post_verification_passed,
            "verification_method": self.verification_method,
            "backend_used": self.backend_used,
            "latency_ms": self.latency_ms,
        }




class VisualStateParser:
    """
    JARVIS O.M.E.G.A. - The "Eyes"
    Captures the current state of the screen so the ComputerUseAgent or a Vision Model
    can determine the (X,Y) coordinates of target elements.
    """
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def capture_screen(self) -> str:
        """Takes a full screenshot of the primary monitor."""
        logging.info("[VisualState] Capturing desktop state...")
        try:
            screenshot = ImageGrab.grab()
            save_path = os.path.join(self.output_dir, "current_desktop_state.png")
            screenshot.save(save_path)
            logging.info(f"[VisualState] Screenshot saved to {save_path}")
            return save_path
        except Exception as e:
            logging.error(f"[VisualState] Failed to capture screen: {e}")
            return ""

    def find_coordinates(self, target_description: str) -> tuple[int, int]:
        """
        Sends the current desktop screenshot to a LOCAL Vision LLM (e.g., Ollama running LLaVA).
        100% Offline. Zero API Keys.
        """
        import base64
        import json
        import urllib.request
        import urllib.error
        import re
        
        logging.info(f"[VisualState] Capturing image and routing to LOCAL Vision API for '{target_description}'...")
        image_path = self.capture_screen()
        
        if not image_path:
            return (-1, -1)
            
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        # OLLAMA LOCAL API PAYLOAD
        payload = {
            "model": "moondream:latest",
            "prompt": f"You are a computer control agent. Locate the exact center of '{target_description}' in this image. Return ONLY a JSON object with 'x' and 'y' integer coordinates. Example: {{\"x\": 500, \"y\": 500}}",
            "images": [base64_image],
            "stream": False,
            "format": "json"
        }
        
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    response_text = result.get("response", "{}")
                    
                    # Extract JSON from response
                    match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if match:
                        coords = json.loads(match.group(0))
                        logging.info(f"[VisualState] Local Model located target: {coords}")
                        return (coords.get("x", -1), coords.get("y", -1))
            except urllib.error.URLError:
                logging.error("[VisualState] Ollama server not detected at localhost:11434. Ensure Ollama is running.")
                return (-1, -1)
                
            return (-1, -1)
        except Exception as e:
            logging.error(f"[VisualState] Local Vision parsing failed: {e}")
            return (-1, -1)

visual_state = VisualStateParser(r"c:\jarvis AI\jarvis\.jarvis_vision")

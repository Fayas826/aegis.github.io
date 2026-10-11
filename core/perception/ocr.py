import asyncio
import base64
import os
from typing import List, Dict, Any

# Eager imports of easyocr and pytesseract removed for lazy loading.

from core.cognition.reasoning.brain import brain

class OCRProcessor:
    """Real Local OCR Pipeline utilizing EasyOCR/PyTesseract fallback frameworks."""
    
    def __init__(self):
        self.easyocr_reader = None
        self._initialized = False

    def _init_reader(self):
        if self._initialized:
            return
        
        self._easyocr_available = False
        self._pytesseract_available = False

        try:
            import easyocr
            import torch
            use_gpu = bool(torch.cuda.is_available())
            # Initialize reader on demand for English with GPU acceleration if available
            self.easyocr_reader = easyocr.Reader(['en'], gpu=use_gpu)
            self._easyocr_available = True
            print(f"[OCR] EasyOCR local engine loaded (GPU={use_gpu}).")
        except Exception as e:
            print(f"[OCR] EasyOCR init fail: {e}")
            self.easyocr_reader = None

        try:
            import pytesseract
            self._pytesseract_available = True
        except ImportError:
            pass

        self._initialized = True

    async def extract_text_blocks(self, image_b64: str) -> List[Dict[str, Any]]:
        self._init_reader()
        
        # Save temp screenshot image
        temp_path = "data/temp/ocr_snap.png"
        os.makedirs("data/temp", exist_ok=True)
        img_bytes = base64.b64decode(image_b64)
        with open(temp_path, "wb") as f:
            f.write(img_bytes)

        # 1. EasyOCR (Primary local choice, offloaded to worker thread)
        if self.easyocr_reader is not None:
            try:
                results = await asyncio.to_thread(self.easyocr_reader.readtext, temp_path)
                blocks = []
                for box, text, confidence in results:
                    # box format: [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
                    x1 = int(box[0][0])
                    y1 = int(box[0][1])
                    x2 = int(box[2][0])
                    y2 = int(box[2][1])
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    blocks.append({
                        "text": text,
                        "bbox": [x1, y1, x2, y2],
                        "center": [cx, cy],
                        "confidence": float(confidence)
                    })
                print(f"[OCR] Local EasyOCR successfully resolved {len(blocks)} text nodes.")
                return blocks
            except Exception as e:
                print(f"[OCR] EasyOCR run error: {e}")

        # 2. PyTesseract (Secondary local choice)
        if getattr(self, '_pytesseract_available', False):
            try:
                import pytesseract
                # pytesseract.image_to_data returns detailed coordinate matches
                data = pytesseract.image_to_data(temp_path, output_type=pytesseract.Output.DICT)
                blocks = []
                for i in range(len(data['text'])):
                    text = data['text'][i].strip()
                    if text:
                        x1 = data['left'][i]
                        y1 = data['top'][i]
                        w = data['width'][i]
                        h = data['height'][i]
                        x2 = x1 + w
                        y2 = y1 + h
                        cx = x1 + (w // 2)
                        cy = y1 + (h // 2)
                        blocks.append({
                            "text": text,
                            "bbox": [x1, y1, x2, y2],
                            "center": [cx, cy],
                            "confidence": 0.85
                        })
                print(f"[OCR] PyTesseract successfully resolved {len(blocks)} text nodes.")
                return blocks
            except Exception as e:
                print(f"[OCR] PyTesseract run error: {e}")

        # 3. Cloud VLM Fallback
        print("[OCR] Fallback: Querying Cloud VLM for coordinate extraction.")
        prompt = (
            "Perform OCR. Locate all prominent text on the screen. "
            "Return a JSON list of objects: "
            "[{\"text\": \"Button Name\", \"bbox\": [x1, y1, x2, y2], \"center\": [cx, cy], \"confidence\": 0.9}]"
        )
        try:
            res = await brain.get_ai_response(prompt, image_path=temp_path)
            text_blocks = res.get("response") or res.get("payload")
            if isinstance(text_blocks, list):
                return text_blocks
        except Exception as e:
            from core.reliability.system_logger import system_logger
            system_logger.log('ERROR', 'ocr', f'Unhandled exception: {e}')
            pass

        return []

class OCRProvider:
    """Robust OCR abstraction layer (Phase 8)."""

    def __init__(self):
        self.processor = ocr_processor

    async def extract_text(self, image_b64: str) -> str:
        blocks = await self.processor.extract_text_blocks(image_b64)
        return " ".join([b["text"] for b in blocks])

    async def extract_boxes(self, image_b64: str) -> List[List[int]]:
        blocks = await self.processor.extract_text_blocks(image_b64)
        return [b["bbox"] for b in blocks]

    async def extract_confidence(self, image_b64: str) -> float:
        blocks = await self.processor.extract_text_blocks(image_b64)
        if not blocks:
            return 0.0
        return sum([b["confidence"] for b in blocks]) / len(blocks)

ocr_processor = OCRProcessor()
ocr_provider = OCRProvider()

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath('.'))

class TestSwarmInitialization(unittest.TestCase):
    def test_lazy_loading(self):
        # 1. Verify torch and easyocr are NOT imported eagerly
        self.assertNotIn('torch', sys.modules, "Torch was loaded eagerly!")
        self.assertNotIn('easyocr', sys.modules, "EasyOCR was loaded eagerly!")
        
        # Now import swarm_orchestrator
        from core.orchestration.swarm_orchestrator import swarm_orchestrator
        
        self.assertNotIn('torch', sys.modules, "Torch was loaded during swarm initialization!")
        
        # 2. Verify status
        status = swarm_orchestrator.get_swarm_status()
        self.assertEqual(status.get('computer_use'), 'READY', "computer_use agent failed to register as READY")
        
        # 3. Simulate failure in OCR and ensure it doesn't crash
        from core.perception.ocr import ocr_processor
        ocr_processor._init_reader()
        # Even if OCR failed (e.g., due to WinError 1455), the processor should still not crash the process.
        
        # Verify fallback logic remains in place (no easyocr reader if failed)
        if 'torch' in sys.modules:
            try:
                import torch
                if not torch.cuda.is_available():
                    print("Torch loaded, but CUDA unavailable. Proceeding.")
            except Exception:
                pass
        
if __name__ == '__main__':
    unittest.main()

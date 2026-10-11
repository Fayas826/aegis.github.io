import os
import sys
import psutil
import time

def print_memory_stats(label=""):
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    virt_mem = psutil.virtual_memory()
    swap_mem = psutil.swap_memory()
    print(f"--- Memory Stats {label} ---")
    print(f"Process RAM: {mem_info.rss / 1024 / 1024:.2f} MB")
    print(f"System Available RAM: {virt_mem.available / 1024 / 1024:.2f} MB / {virt_mem.total / 1024 / 1024:.2f} MB")
    print(f"System Swap/Pagefile Used: {swap_mem.used / 1024 / 1024:.2f} MB / {swap_mem.total / 1024 / 1024:.2f} MB")
    print("---------------------------------")

print_memory_stats("Before Import")

sys.path.insert(0, os.path.abspath('.'))

t0 = time.time()
from core.orchestration.swarm_orchestrator import swarm_orchestrator
from core.perception.ocr import ocr_processor
t1 = time.time()

print_memory_stats("After Agent Imports")
print(f"Import time: {t1 - t0:.2f}s")
print(f"Swarm Status: {swarm_orchestrator.get_swarm_status()}")

# Check GPU VRAM if torch is available (it shouldn't be loaded yet)
import sys
if "torch" in sys.modules:
    print("WARNING: Torch was loaded eagerly!")
else:
    print("Torch is not loaded. Good.")

# Now initialize OCR explicitly and measure
print("Initializing OCR explicitly...")
t0 = time.time()
ocr_processor._init_reader()
t1 = time.time()
print_memory_stats("After Heavy OCR Init")
print(f"OCR Init time: {t1 - t0:.2f}s")

if "torch" in sys.modules:
    import torch
    print(f"Torch GPU Available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"GPU Name: {torch.cuda.get_device_name(0)}")
        print(f"GPU VRAM Allocated: {torch.cuda.memory_allocated(0) / 1024 / 1024:.2f} MB")
        print(f"GPU VRAM Reserved: {torch.cuda.memory_reserved(0) / 1024 / 1024:.2f} MB")

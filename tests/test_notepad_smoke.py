import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath('.'))
from core.orchestration.swarm_orchestrator import swarm_orchestrator

async def run_smoke_test():
    goal = "Open Notepad, write 'AEGIS OS Control Verified', and save it to the Desktop as 'aegis_test.txt'."
    print(f"Running Desktop Smoke Test: {goal}")
    
    # Execute through swarm starting with computer_use
    result = await swarm_orchestrator.execute_swarm_task("computer_use", goal)
    
    print("\n=== SMOKE TEST RESULT ===")
    print(result)

if __name__ == "__main__":
    asyncio.run(run_smoke_test())

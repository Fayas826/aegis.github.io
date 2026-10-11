from typing import Dict, Any

class SwarmHandoff(Exception):
    """
    Exception raised when an agent decides to hand off the task to another agent.
    This safely interrupts the current agent and triggers the swarm router to continue with the new agent.
    """
    def __init__(self, target_agent: str, goal: str, context: Dict[str, Any] = None):
        self.target_agent = target_agent
        self.goal = goal
        self.context = context or {}
        super().__init__(f"Swarm Handoff to {target_agent}: {goal}")


class SwarmOrchestrator:
    """
    Coordinates the AEGIS Swarm Agents.
    Enables Agent-to-Agent Handoff dynamically during execution.
    """
    def __init__(self):
        self.registry = {}
        self.agent_status = {}

    def register_agent(self, name: str, agent_instance):
        self.registry[name.lower()] = agent_instance
        self.agent_status[name.lower()] = "READY"
        print(f"[SWARM] Registered Agent: {name}")

    def mark_agent_failed(self, name: str, reason: str):
        self.agent_status[name.lower()] = f"FAILED: {reason}"
        print(f"[SWARM] Agent '{name}' failed to initialize: {reason}")

    def get_swarm_status(self) -> Dict[str, str]:
        return self.agent_status

    async def execute_swarm_task(self, initial_agent_name: str, goal: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Executes a task starting with an initial agent. 
        If the agent raises a SwarmHandoff, control is smoothly passed to the next agent.
        """
        current_agent_name = initial_agent_name.lower()
        current_goal = goal
        current_context = context or {}
        
        execution_trace = []
        
        while True:
            agent = self.registry.get(current_agent_name)
            if not agent:
                status_msg = self.agent_status.get(current_agent_name, "Not Registered")
                return {"status": "FAILED", "reason": f"Agent '{current_agent_name}' not found in Swarm. Status: {status_msg}", "trace": execution_trace}
                
            print(f"[SWARM] Dispatching goal to {current_agent_name.upper()}: {current_goal}")
            execution_trace.append({"agent": current_agent_name, "goal": current_goal})
            
            try:
                # We expect agents to have an `execute_task` or similar method
                if hasattr(agent, "execute_task"):
                    result = await agent.execute_task(current_goal)
                elif hasattr(agent, "execute_visual_task"):
                    result = await agent.execute_visual_task(goal=current_goal)
                elif hasattr(agent, "run"):
                    result = await agent.run(current_goal)
                else:
                    return {"status": "FAILED", "reason": f"Agent '{current_agent_name}' lacks execution interface."}
                
                execution_trace.append({"agent": current_agent_name, "result": result})
                return {"status": "SUCCESS", "final_result": result, "trace": execution_trace}
                
            except SwarmHandoff as handoff:
                print(f"[SWARM] HANDOFF INITIATED: {current_agent_name} -> {handoff.target_agent}")
                current_agent_name = handoff.target_agent.lower()
                current_goal = handoff.goal
                current_context.update(handoff.context)
                # Loop continues with the new agent
                
            except Exception as e:
                import traceback
                print(f"[SWARM] Agent '{current_agent_name}' crashed: {e}")
                traceback.print_exc()
                return {"status": "FAILED", "reason": str(e), "trace": execution_trace}

swarm_orchestrator = SwarmOrchestrator()

def _initialize_swarm():
    import traceback

    try:
        from core.orchestration.computer_use_agent import computer_use_agent
        swarm_orchestrator.register_agent("computer_use", computer_use_agent)
    except Exception as e:
        print(f"[SWARM_INIT_ERROR] computer_use failed:")
        traceback.print_exc()
        swarm_orchestrator.mark_agent_failed("computer_use", str(e))

    try:
        from core.perception.multimodal_vision_agent import multimodal_vision_agent
        swarm_orchestrator.register_agent("vision", multimodal_vision_agent)
    except Exception as e:
        print(f"[SWARM_INIT_ERROR] vision failed:")
        traceback.print_exc()
        swarm_orchestrator.mark_agent_failed("vision", str(e))

    try:
        from core.ticket_booking_agent import jarvis_ticket_booker
        swarm_orchestrator.register_agent("ticket_booking", jarvis_ticket_booker)
    except Exception as e:
        print(f"[SWARM_INIT_ERROR] ticket_booking failed:")
        traceback.print_exc()
        swarm_orchestrator.mark_agent_failed("ticket_booking", str(e))

_initialize_swarm()

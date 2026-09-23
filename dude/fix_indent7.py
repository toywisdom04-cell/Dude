with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the factory function indentation - it should be at module level
old = '''            return interpretation


            def create_cognitive_front_door(
        capability_bus: CapabilityBus,
        agent_state: AgentState,
        intelligence_router: Optional[IntelligenceRouter] = None,
        memory: Optional[Any] = None,
            ) -> CognitiveFrontDoor:
        """Factory for CognitiveFrontDoor with production dependencies.\"\"\"
            return CognitiveFrontDoor(
            capability_bus=capability_bus,
            agent_state=agent_state,
            intelligence_router=intelligence_router,
            memory=memory,
        )'''

new = '''            return interpretation


def create_cognitive_front_door(
    capability_bus: CapabilityBus,
    agent_state: AgentState,
    intelligence_router: Optional[IntelligenceRouter] = None,
    memory: Optional[Any] = None,
) -> CognitiveFrontDoor:
    """Factory for CognitiveFrontDoor with production dependencies."""
    return CognitiveFrontDoor(
        capability_bus=capability_bus,
        agent_state=agent_state,
        intelligence_router=intelligence_router,
        memory=memory,
    )'''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed')
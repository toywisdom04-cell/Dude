import asyncio
import sys
sys.path.insert(0, r'E:\Dude\dude')

async def test():
    from core.orchestrator.task_engine import TaskEngine
    from core.brain import Brain
    from core.memory import Memory
    from core.orchestrator.intelligence_router import IntelligenceRouter
    from core.orchestrator.perception import PerceptionEngine
    from core.orchestrator.state import TaskState
    from core.orchestrator.action_executor import ActionExecutor
    from core.orchestrator.verification import VerificationEngine
    from core.orchestrator.recovery import RecoveryEngine
    from core.screentree import ScreenMap
    from core.tools import init_screentree
    from core.memory import Memory
    
    # Create shared perception components
    screentree = ScreenMap()
    init_screentree(screentree)
    
    # Give screentree time to capture initial UI
    import time
    time.sleep(1.0)
    
    perception = PerceptionEngine(get_screentree=lambda: screentree)
    
    te = TaskEngine(
        intelligence=None,
        perception=perception,
        action_executor=ActionExecutor(perception=perception, memory=Memory()),
        verification=VerificationEngine(perception=perception),
        recovery=RecoveryEngine(),
        permission_gate=None,
        voice=None,
        memory=Memory(),
        max_retries=3,
        perception_freshness_seconds=5.0,
        intelligence_router=IntelligenceRouter(),
        procedure_learner=None,
        recovery_action_executor=None,
        use_real_execution=False,
    )
    
    # Test: Open Notepad, write a paragraph, close without saving
    goal = 'Open Notepad and write a short paragraph about productivity, then close it without saving'
    ts = TaskState(goal='test')
    ts.user_interrupt = 'Open Notepad and write a short paragraph about productivity, then close it without saving'
    
    try:
        result = await te.run(ts.user_interrupt)
        print('Result:', result)
        print('State:', te._state if te._state else 'None')
        print('Current step:', getattr(te._task_state, 'current_step', None) if hasattr(te, '_task_state') else 'None')
        print('Completed steps:', len(te._task_state.completed_steps) if hasattr(te, '_task_state') and te._task_state.completed_steps else 0)
        for s in te._task_state.completed_steps:
            print(f'  - {s.subgoal.description} | {s.action.action_type} | success: {s.success} | result: {s.actual_result[:80] if s.actual_result else "None"}')
    except Exception as e:
        import traceback
        traceback.print_exc()
        print('ERROR:', e)

if __name__ == '__main__':
    import asyncio
    asyncio.run(test())
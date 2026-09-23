import sys
sys.path.insert(0, r'E:\Dude\dude')

try:
    from core.orchestrator.goal_executor import create_cognitive_front_door, CognitiveFrontDoor
    print('Import successful')
    print('Methods:', [m for m in dir(CognitiveFrontDoor) if not m.startswith('_')])
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
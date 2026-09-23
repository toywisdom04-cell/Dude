import re

with open(r'E:\Dude\dude\core\orchestrator\realtime_voice.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check for actual phrase-based routing patterns (not just comments or variable names)
patterns_to_check = [
    (r'"what are you doing"', 'literal phrase "what are you doing"'),
    (r'"what happened"', 'literal phrase "what happened"'),
    (r'"what is the status"', 'literal phrase "what is the status"'),
    (r'if.*what.*in low', 'phrase-based if check'),
    (r'if.*calculate.*in low', 'math phrase if check'),
    (r'if.*compute.*in low', 'compute phrase if check'),
]

for pattern, desc in patterns_to_check:
    if re.search(pattern, content, re.IGNORECASE):
        print(f'FAIL: {desc} still present')
    else:
        print(f'PASS: {desc} removed')

# Check _fast_reply only does time/date/math
if 'return None  # All other routing goes through CognitiveFrontDoor' in content:
    print('PASS: _fast_reply returns None for non-deterministic routes')
else:
    print('FAIL: _fast_reply not properly returning None')

# Check no heuristic fallback in CognitiveFrontDoor
with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'r', encoding='utf-8') as f:
    content3 = f.read()
if 'def _heuristic_classify' in content3:
    print('FAIL: heuristic fallback still in CognitiveFrontDoor')
else:
    print('PASS: no heuristic fallback in CognitiveFrontDoor')

# Check calculator-specific logic removed from intelligence_router
with open(r'E:\Dude\dude\core\orchestrator\intelligence_router.py', 'r', encoding='utf-8') as f:
    content4 = f.read()
if 'calc_match' in content4:
    print('FAIL: calculator-specific logic still in intelligence_router')
else:
    print('PASS: calculator-specific logic removed')

# Check _cognitive_deep uses CapabilityBus
with open(r'E:\Dude\dude\core\orchestrator\realtime_voice.py', 'r', encoding='utf-8') as f:
    content5 = f.read()
if 'capability_bus.perception.observe' in content5:
    print('PASS: _cognitive_deep uses CapabilityBus for perception')
else:
    print('FAIL: _cognitive_deep does not use CapabilityBus')

# Check no heuristic fallback in CognitiveFrontDoor
if 'def _heuristic_classify' in content3:
    print('FAIL: heuristic fallback still in CognitiveFrontDoor')
else:
    print('PASS: no heuristic fallback in CognitiveFrontDoor')

# Check no calculator-specific logic in intelligence_router
if '_calc_built' in content4 or 'calc_match' in content4 or 'DIGIT_WORDS' in content4:
    print('FAIL: Calculator-specific remnants still in intelligence_router')
else:
    print('PASS: Calculator-specific remnants removed')

print('\nAll checks complete.')
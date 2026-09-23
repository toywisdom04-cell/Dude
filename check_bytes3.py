with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find the docstring
idx = content.find(b'Factory to create an ObservationLearner instance')
if idx >= 0:
    snippet = content[idx:idx+200]
    for i, b in enumerate(snippet):
        if b in (10, 13, 34):
            if b == 10:
                c = 'LF'
            elif b == 13:
                c = 'CR'
            elif b == 34:
                c = 'QUOTE'
            else:
                c = chr(b)
            print(f'{i}: 0x{b:02x} ({c})')
        elif 32 <= b < 127:
            print(f'{i}: {chr(b)}')
        else:
            print(f'{i}: 0x{b:02x}')
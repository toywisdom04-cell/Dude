with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')
for i, line in enumerate(lines):
    if 'Factory to create an ObservationLearner instance' in line:
        print(f'Line {i}: {repr(line)}')
        for j in range(max(0, i-2), min(len(lines), i+5)):
            print(f'  {j}: {repr(lines[j])}')
        break
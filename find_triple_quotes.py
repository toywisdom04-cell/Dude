import re

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    source = f.read()

# Find all triple-quoted strings
pattern = re.compile(r'"""[\s\S]*?"""', re.DOTALL)
matches = list(pattern.finditer(source))

print(f'Found {len(matches)} triple-quoted strings')
for i, m in enumerate(matches):
    print(f'Match {i}: start={m.start()}, end={m.end()}')
    snippet = source[m.start():m.end()]
    if len(snippet) > 100:
        print(f'  {snippet[:100]}...')
    else:
        print(f'  {snippet}')
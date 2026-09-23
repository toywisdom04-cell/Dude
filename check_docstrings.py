import re

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    source = f.read()

pattern = re.compile(r'"""[\s\S]*?"""', re.DOTALL)
matches = list(re.finditer(r'"""[\s\S]*?"""', source, re.DOTALL))
print(f'Found {len(matches)} triple-quoted strings')

for i, m in enumerate(matches):
    end = m.end()
    if m.end() < len(source):
        next_chars = source[end:end+3]
        has_ws = any(c in ' \t\n\r' for c in next_chars)
        if not has_ws:
            print(f'Match {i}: pos={m.start()}-{m.end()}, next={repr(source[m.end():m.end()+3])}')
            snippet = source[m.start():m.end()]
            print(f'  {snippet[:100]}...')
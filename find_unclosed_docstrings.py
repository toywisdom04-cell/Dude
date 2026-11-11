import re

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find all triple-quoted strings
matches = list(re.finditer(rb'"""[\s\S]*?"""', content, re.DOTALL))
print(f'Found {len(matches)} triple-quoted strings')

# Check which ones don't have newline after closing quotes
for i, m in enumerate(matches):
    start = m.start()
    end = m.end()
    # Check what comes after the closing """
    if m.end() < len(content):
        next_chars = content[end:end+3]
        # Check if there's a whitespace character (space, tab, newline, carriage return)
        has_whitespace = any(c in (9, 10, 13, 32) for c in next_chars)
        if not has_whitespace:
            print(f'Match {i}: start={m.start()}, end={m.end()}, next_chars={next_chars}')
            print(f'  Snippet: {content[start:end+3]}')

print()
print('Total triple-quoted strings:', len(matches))
print('Triple quotes count:', content.count(b'"""'))
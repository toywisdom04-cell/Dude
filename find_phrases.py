import re

with open(r'E:\Dude\dude\core\orchestrator\realtime_voice.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all double-quoted strings containing status/what/happened/doing phrases
matches = re.findall(r'"[^"]*(what|are|you|doing|happened|status)[^"]*"', content, re.IGNORECASE)
for m in matches:
    print(repr(m))
import re

with open(r'E:\Dude\dude\core\orchestrator\realtime_voice.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all double-quoted strings containing "what are you doing" or "what happened" or "status"
phrases = ['what are you doing', 'what happened', 'what is the status', 'what is your status']
for phrase in phrases:
    if phrase.lower() in content.lower():
        print(f'FOUND: {phrase}')
    else:
        print(f'NOT FOUND: {phrase}')

# Also check for "what is the status" pattern
if 'what is the status' in content.lower():
    print('FOUND: what is the status')
else:
    print('NOT FOUND: what is the status')
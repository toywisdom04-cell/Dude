import chardet

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Check for BOM
if content.startswith(b'\xef\xbb\xbf'):
    print('Has UTF-8 BOM')
else:
    print('No BOM')

# Count triple quotes
count = content.count(b'"""')
print(f'Triple quotes: {count}')

# Check encoding
result = chardet.detect(content)
print(f'Detected encoding: {result}')
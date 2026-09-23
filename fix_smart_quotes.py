with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Replace smart quotes with regular ASCII quotes
# 0xe2 0x86 0x93 = right single quotation mark
content = content.replace(bytes([0xe2, 0x86, 0x93]), b"'")
content = content.replace(bytes([0xe2, 0x80, 0x93]), b'-')  # en dash
content = content.replace(bytes([0xe2, 0x80, 0x94]), b'--')  # em dash
content = content.replace(bytes([0xe2, 0x80, 0x9c]), b'"')  # left double quote
content = content.replace(bytes([0xe2, 0x80, 0x9d]), b'"')  # right double quote
content = content.replace(bytes([0xe2, 0x80, 0x98]), b"'")  # left single quote
content = content.replace(bytes([0xe2, 0x80, 0x99]), b"'")  # right single quote

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
    f.write(content)
print('Fixed smart quotes')
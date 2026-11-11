import re
with open(r'E:\Dude\dude\core\ear.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the transcript accepted logging
old = '''if text and _is_garbage_transcript(text):
                print(f"[ear] discarded hallucinated phrase: {text!r}")
                log.info("ear: discarded hallucinated phrase: %r", text)
                continue
            if text:
                self.text_q.put(("text", text))'''

new = '''if text and _is_garbage_transcript(text):
                print(f"[ear] discarded hallucinated phrase: {text!r}")
                log.info("ear: discarded hallucinated phrase: %r", text)
                continue
            if text:
                log.info("TRANSCRIPT_ACCEPTED text=%r chars=%d", text, len(text))
                self.text_q.put(("text", text))'''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\ear.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
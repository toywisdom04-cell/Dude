import re

with open(r'E:\Dude\dude\core\voice.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the _worker method logging
old = '''    def _worker(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            if self._stop_evt.is_set():
                # A stop/interrupt won the race with pickup (e.g. barge-in
                # fired between say() and dequeue): drop the stale item
                # instead of clearing someone else's stop and speaking
                # over the user. New speech clears the flag in say().
                log.info("STALE_TTS_DROPPED %r", str(item)[:80])
                continue
            self.interrupted = False
            self._speak_text(item)
            self._speaking.clear()
            try:
                if self.q.empty() and not self._stop_evt.is_set():
                    cb = getattr(self, "on_playback_end", None)
                    if cb is not None:
                        cb()
                    except Exception:
                        pass'''

new = '''    def _worker(self):
        log.info("voice: worker thread started")
        while True:
            item = self.q.get()
            if item is None:
                log.info("voice: worker thread received None, exiting")
                break
            if self._stop_evt.is_set():
                log.info("STALE_TTS_DROPPED %r", str(item)[:80])
                continue
            self.interrupted = False
            self._speak_text(item)
            self._speaking.clear()
            try:
                if self.q.empty() and not self._stop_evt.is_set():
                    cb = getattr(self, "on_playback_end", None)
                    if cb is not None:
                        cb()
                    except Exception:
                        pass'''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\voice.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed')
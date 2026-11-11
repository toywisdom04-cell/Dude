import sys
sys.path.insert(0, r'E:\Dude\dude')

from core.voice import Voice
from core.config import get_config

cfg = get_config()
print('Voice config:')
print('  tts_voice:', cfg.get("voice", "tts_voice", default="en-US-ChristopherNeural"))
print('  rate:', cfg.get("voice", "rate", default="+0%"))
print('  volume:', cfg.get("voice", "volume", default="+0%"))
print('  output_device:', cfg.get("voice", "output_device", default=None))

voice = Voice()
print('Voice initialized successfully')
print('Voice speaking:', voice.speaking)

print('Testing TTS...')
result = voice.say('Hello, this is a test.')
print('Say returned:', result)

import time
time.sleep(3)
print('Voice speaking after wait:', voice.speaking)
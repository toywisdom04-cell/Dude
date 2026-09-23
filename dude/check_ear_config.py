import sys
sys.path.insert(0, r'E:\Dude\dude')

from core.ear import Ear
from core.config import get_config

cfg = get_config()
print('Ear config:')
print(f'  sample_rate: {cfg.get("ear", "sample_rate", default=16000)}')
print(f'  frame_ms: {cfg.get("ear", "frame_ms", default=32)}')
print(f'  whisper_model: {cfg.get("ear", "whisper_model", default="base.en")}')
print(f'  vad: {cfg.get("ear", "vad", default="webrtc")}')
print(f'  mic_index: {cfg.get("ear", "mic_index", default=None)}')
print(f'  vad_min_rms: {cfg.get("ear", "vad_min_rms", default=0.012)}')
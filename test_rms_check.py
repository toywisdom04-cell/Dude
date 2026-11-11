import sys
sys.path.insert(0, r'E:\Dude\dude')

from core.config import get_config

cfg = get_config()
print('Ear config:')
print(f'  min_utt_rms: {cfg.get("ear", "min_utt_rms", default=0.008)}')
print(f'  mic_gain: {cfg.get("ear", "mic_gain", default=6.0)}')
print(f'  vad_min_rms: {cfg.get("ear", "vad_min_rms", default=0.012)}')

# Test the RMS threshold logic
import numpy as np

test_rms_values = [0.0188, 0.0106, 0.008, 0.005, 0.020]
min_utt_rms = 0.008

print('\nRMS threshold test (threshold=0.008):')
for rms in test_rms_values:
    result = 'PASS' if rms >= 0.008 else 'REJECT'
    print(f'  RMS {rms:.4f} -> {result} (threshold=0.008)')
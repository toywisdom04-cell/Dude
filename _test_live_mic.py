import sounddevice as sd
import numpy as np
print('Testing microphone...')
dev = sd.query_devices(1)
print('Using device 1:', dev['name'])
rec = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype='int16', device=1)
sd.wait()
audio = rec[:, 0].astype(np.float32) / 32768.0
peak = float(np.max(np.abs(audio)))
rms = float(np.sqrt(np.mean(np.square(audio))))
print('Peak:', peak, 'RMS:', rms)
if peak > 0.01:
    print('MIC OK - should work with DUDE')
else:
    print('MIC STILL TOO QUIET')
import sounddevice as sd

for i in [1, 5, 9, 10, 16, 24]:
    try:
        dev = sd.query_devices(i)
        if dev['max_input_channels'] > 0:
            print('Testing device {}: {}...'.format(i, dev['name']))
            rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype='int16', device=i)
            sd.wait()
            audio = rec[:, 0].astype(float) / 32768.0
            peak = float(abs(audio).max())
            print('  Device {} ({}): peak={:.6f}'.format(i, dev['name'][:40], peak))
    except Exception as e:
        print('  Device {} error: {}'.format(i, e))
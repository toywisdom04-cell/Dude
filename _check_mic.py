import sounddevice as sd
print('Default input device:', sd.default.device)
print('All input devices:')
for i, d in enumerate(sd.query_devices()):
    if d['max_input_channels'] > 0:
        print(f'  {i}: {d["name"]} ({d["max_input_channels"]} ch)')
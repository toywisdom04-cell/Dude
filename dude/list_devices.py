import sounddevice as sd
print('Available audio output devices:')
for i, dev in enumerate(sd.query_devices()):
    if dev['max_output_channels'] > 0:
        print(f'  {i}: {dev["name"]} (max_out={dev["max_output_channels"]}, default_sr={dev["default_samplerate"]})')
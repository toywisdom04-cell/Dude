"""Voice activity detection using webrtcvad over a stream.

The detector marks when speech starts/ends so the conversation manager can
decide turn boundaries and detect barge-in while DUDE is speaking.
"""
import collections

try:
    import webrtcvad
    HAVE_VAD = True
except ImportError:
    HAVE_VAD = False


class VoiceActivityDetector:
    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2,
                 frame_ms: int = 30, speech_pad_frames: int = 5):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_size = int(sample_rate * frame_ms / 1000) * 2  # 16-bit mono bytes
        self.speech_pad_frames = speech_pad_frames
        self._vad = None
        if HAVE_VAD:
            self._vad = webrtcvad.Vad(aggressiveness)
        self._ring = collections.deque(maxlen=speech_pad_frames)
        self._triggered = False
        self._num_voiced = 0

    def _is_speech(self, frame: bytes) -> bool:
        if self._vad is None:
            # no webrtcvad: fall back to RMS energy heuristic
            import struct
            samples = struct.unpack(f"<{len(frame)//2}h", frame)
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
            return rms > 400
        return self._vad.is_speech(frame, self.sample_rate)

    def feed(self, frame: bytes) -> str:
        """Feed a PCM frame. Returns 'start', 'end', 'speech' or 'silence'."""
        if not self._is_speech(frame):
            self._num_voiced = 0
            self._ring.append(frame)
            num_unvoiced = len(self._ring)
            if self._num_voiced > 0 and num_unvoiced >= self.speech_pad_frames:
                self._triggered = False
                self._ring.clear()
                return "end"
            if self._triggered:
                return "speech"
            return "silence"
        else:
            self._num_voiced += 1
            self._ring.clear()
            if not self._triggered:
                self._triggered = True
                return "start"
            return "speech"


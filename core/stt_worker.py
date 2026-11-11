"""Standalone Whisper STT worker (run as `python core/stt_worker.py <model>`).

Runs Whisper in its own subprocess so ctranslate2/MKL never shares address
space with the rest of DUDE (numpy, PyQt5, SDL), which previously crashed the
whole app. Communication is length-prefixed pickle over stdio:

  main -> child : [4-byte big-endian length][pickle (model_name, )]  signal load
  child -> main : [4-byte][pickle ("ready", None)] or ("load_failed", err)
  main -> child : [4-byte][pickle (audio_ndarray, lang)]
  child -> main : [4-byte][pickle ("text", text)] or ("error", err)
  main -> child : [4-byte][pickle ("bye",)]            -> child exits
"""
import os
import pickle
import struct
import sys

# Run from the site-packages/dev environment, NOT from core/ (which shadows
# stdlib modules like `platform` via core/platform.py).
_here = os.path.dirname(os.path.abspath(__file__))
if sys.path and sys.path[0] == _here:
    sys.path.pop(0)

os.environ.setdefault("KMP_ABORT_ON_MALLOC_FAILURE", "FALSE")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OMP_DYNAMIC", "FALSE")


def _read_msg():
    hdr = sys.stdin.buffer.read(4)
    if not hdr or len(hdr) < 4:
        return None
    (n,) = struct.unpack(">I", hdr)
    data = sys.stdin.buffer.read(n)
    if len(data) < n:
        return None
    return pickle.loads(data)


_CLAUDE_HINTS = ("open", "run", "use", "learn", "install", "start", "launch",
                 "type", "code", "ask", "talk", "speak", "command", "execute",
                 "about", "with", "to", "using")


def _fix_claude_cloud(text):
    words = text.split()
    out = []
    for i, w in enumerate(words):
        low = w.lower().rstrip(".,!?")
        prev = out[i - 1].lower() if out else ""
        if low == "cloud" and prev in _CLAUDE_HINTS:
            w = "claude"
        out.append(w)
    return " ".join(out)


def _fix_homophones(text):
    """Command-domain corrections for common Whisper mishears."""
    _POWERSHELL_MIS = {"portion", "portions", "parcel", "parcels"}
    _POWERSHELL_WORDS = {"powershell", "port shell", "porcelain"}
    words = text.split()
    n = len(words)
    out = list(words)
    for i, w in enumerate(out):
        low = w.lower().rstrip(".,!?")
        prev = out[i - 1].lower().rstrip(".,!?") if i > 0 else ""
        nxt = words[i + 1].lower().rstrip(".,!?") if i + 1 < n else ""
        if low in ("comment", "comments") and nxt in ("browser", "browsers"):
            out[i] = "comet"
        elif low in _POWERSHELL_MIS and (
                prev in _CLAUDE_HINTS or prev in _POWERSHELL_WORDS
                or nxt in _POWERSHELL_MIS or nxt in _POWERSHELL_WORDS):
            out[i] = "powershell"
        elif low in ("nude", "nudes"):
            out[i] = "dude"
    return " ".join(out)


def _write_msg(obj):
    """Length-prefixed pickle over stdout; returns False when the pipe is dead."""
    try:
        data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        payload = struct.pack(">I", len(data)) + data
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return True
    except (OSError, BrokenPipeError, IOError):
        # Pipe broken - parent gone
        return False
    except Exception:
        return False


def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "base.en"
    os.environ.setdefault("HF_HOME", r"D:\whisper\hf")
    os.environ.setdefault("HF_HUB_CACHE", r"D:\whisper\hf\hub")
    if getattr(sys, "frozen", False):
        bundled = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)),
                               "models", "faster-whisper-base.en")
        if os.path.isdir(bundled):
            model_name = bundled
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    except Exception as e:  # noqa: BLE001
        _write_msg(("load_failed", str(e)))
        return
    if not _write_msg(("ready", None)):
        # Parent went away during model load; exit quietly (no OSError:22 spit).
        return

    while True:
        try:
            obj = _read_msg()
        except Exception:  # noqa: BLE001
            return
        if obj is None:
            return
        if isinstance(obj, tuple) and len(obj) == 1 and obj[0] == "bye":
            return
        audio, lang = obj
        try:
            # No initial_prompt: Whisper transcribes ONLY what it hears. Any prompt
            # text makes it echo prompt words over unclear audio ("open powershell…").
            # vad_filter drops pure silence/noise segments BEFORE Whisper sees them,
            # and a lower no_speech_threshold makes it emit "" instead of inventing
            # speech out of mic hiss (a known whisper failure mode). no_speech_threshold
            # is the no-speech-probability cutoff; a HIGHER value accepts quiet and
            # distant speech instead of dropping it. Fine-tuned 0.65 to capture voice
            # from 1-2 feet while still skipping pure hiss.
            segments, _info = model.transcribe(
                audio,
                language=lang or "en",
                task="transcribe",
                beam_size=1,
                best_of=1,
                temperature=0,
                no_speech_threshold=0.65,
                vad_filter=True,
                vad_parameters={
                    "threshold": 0.4,
                    "min_silence_duration_ms": 400,
                    "speech_pad_ms": 400,
                },
                condition_on_previous_text=False,
            )
            text = " ".join(s.text for s in segments).strip()
            text = _fix_claude_cloud(text)
            text = _fix_homophones(text)
            if not _write_msg(("text", text)):
                return  # parent gone; exit quietly
        except SystemExit:
            raise
        except BaseException as e:  # noqa: BLE001
            if not _write_msg(("error", str(e))):
                return


if __name__ == "__main__":
    main()

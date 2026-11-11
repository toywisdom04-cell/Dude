import argparse
import collections
import datetime
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time

# Under pythonw (no console), sys.stdout/stderr are None and every print() crashes
# the process. Redirect them to a file so windowed/autostart launches survive.
if sys.stdout is None or sys.stderr is None:
    _stdio = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logs", "stdio.log")
    try:
        os.makedirs(os.path.dirname(_stdio), exist_ok=True)
        _f = open(_stdio, "a", encoding="utf-8", errors="replace")
    except Exception:
        _f = None
    if _f is not None:
        if sys.stdout is None:
            sys.stdout = _f
        if sys.stderr is None:
            sys.stderr = _f

os.environ.setdefault("KMP_ABORT_ON_MALLOC_FAILURE", "FALSE")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OMP_DYNAMIC", "FALSE")

from core.brain import Brain, BrainUnavailable
from core.config import get_config
from core.ear import Ear
from core.memory import Memory
from platform_utils import get_platform
from core.scheduler import Scheduler
from core.secrets import SecretsBroker
from core.tools import init_agent_ctx, tool_specs
from core.tracker import Tracker
from core.ui import Tray
from core.voice import Voice
from core.notch import LEVEL_Q

# Orchestrator imports (feature-gated)
from core.orchestrator import (
    TaskEngine,
    OrchestratorState,
    VoiceState,
    TaskType,
    Action,
    TargetSpec,
    GroundingMethod,
    RiskLevel,
    PerceptionEngine,
)
from core.orchestrator.brain_adapter import create_brain_adapter
from core.orchestrator.action_executor import ActionExecutor
from core.orchestrator.verification import VerificationEngine
from core.orchestrator.recovery import RecoveryEngine
from core.orchestrator.intelligence import IntelligenceBackend

log = logging.getLogger("dude")

# Generation epoch: bumped on every new user turn / barge-in so that a previous
# (now-superseded) generation cannot keep spewing speech after the user interrupts.
_GEN = {"epoch": 0, "armed_until": 0.0}


def _new_epoch():
    _GEN["epoch"] += 1
    return _GEN["epoch"]


def _norm_echo(text):
    import re as _re

    return _re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()


_ECHO = {"last_reply": ""}

PLATFORM = get_platform()

_QUIT_TERMS = ("turn off", "power off", "shut down", "shutdown", "power down", "go offline", "bye dude")
_SLEEP_TERMS = ("sleep", "go to sleep", "good night", "goodnight")
_WAKE_TERMS = ("wake up", "wake", "come back", "talk to me", "are you there")
_CENTER_TOPIC = ("command center", "command centre", "neural command", "hologram", "holo")
_CENTER_CLOSE_VERBS = ("close", "closed", "closing", "close down", "closing down",
                       "hide", "hiding", "hideout", "kill", "killing", "quit", "quitting",
                       "exit", "exiting", "stop", "stopping", "shut", "shutting",
                       "shut down", "shutting down", "shutdown", "turn off", "turnoff",
                       "turning off", "dismiss", "dismissing", "minimize", "minimizing",
                       "minimise", "deactivate")
_CENTER_OPEN_VERBS = ("open", "opening", "opens", "show", "shows", "showing",
                      "display", "displays", "launch", "launches", "launching",
                      "start", "starting", "starts", "bring up", "brings up",
                      "bringing up", "activate")


def _word_in(low, word):
    import re as _re

    return bool(_re.search(r"(?<![a-z])" + _re.escape(word) + r"(?![a-z])", low))


def _center_intent(low):
    """Return 'close', 'open', or None for command-center commands. The verb is
    what decides — never a bare substring, so 'close the neural command center'
    can never open it."""
    if not any(t in low for t in _CENTER_TOPIC) \
            and not (_word_in(low, "center") or _word_in(low, "centre")):
        return None
    for v in _CENTER_CLOSE_VERBS:
        if _word_in(low, v):
            return "close"
    for v in _CENTER_OPEN_VERBS:
        if _word_in(low, v):
            return "open"
    return "open"


def _is_center_open_cmd(low):
    return _center_intent(low) == "open"


def _is_center_close_cmd(low):
    return _center_intent(low) == "close"


def _normalize_cmd(low):
    t = low.strip()
    for prefix in ("dude ", "hey dude ", "ok dude", "okay dude"):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    for suffix in (" dude", " dude"):
        if t.endswith(suffix.strip()) and t != suffix.strip():
            t = t[: -len(suffix.strip())].strip()
            break
    t = t.strip(".,!?")
    return t


_DUDE_WAKE_TOKENS = {"dude", "duude", "duuude", "dudes", "dood", "dud",
                     "nude", "nudes", "dwude"}

# Words that may legitimately sit between "dude" and the real command
# ("dude can you", "dude hey"). If an utterance is ONLY these plus the wake
# name, DUDE waits for the follow-up instead of burning a brain turn.
_NAME_LEAD_INS = {"hey", "hay", "hi", "can", "could", "would", "will", "please",
                  "pls", "ok", "okay", "if", "are", "you", "there", "excuse",
                  "me", "sir", "buddy", "dawg", "listen", "look", "again"}


def _has_wake_name(low):
    import re as _re

    tokens = set(_DUDE_WAKE_TOKENS)
    cfg_name = get_config().get("assistant_name", default="Dude")
    if cfg_name and cfg_name.lower() not in tokens:
        tokens.add(cfg_name.lower())
    return any(_re.sub(r"[^a-z']", "", t.lower()).strip("'")
               in tokens for t in low.split())


def _is_quit_cmd(low):
    t = _normalize_cmd(low)
    if not t or len(t.split()) > 4:
        return False
    return any(term == t for term in _QUIT_TERMS)


def _is_sleep_cmd(low):
    t = _normalize_cmd(low)
    if not t or len(t.split()) > 4:
        return False
    return any(term == t for term in _SLEEP_TERMS)


def _is_wake_cmd(low):
    t = _normalize_cmd(low)
    if not t or len(t.split()) > 4:
        return False
    return any(term == t for term in _WAKE_TERMS)


def _is_name_only(low):
    """True when the utterance is just the wake name plus optional lead-in
    words ('dude', 'dude can you'), i.e. the actual command did not come yet."""
    import re as _re

    tokens = set(_DUDE_WAKE_TOKENS)
    cfg_name = get_config().get("assistant_name", default="Dude")
    if cfg_name and cfg_name.lower() not in tokens:
        tokens.add(cfg_name.lower())
    words = [w.strip("'") for w in _re.findall(r"[a-z']+", low.lower()) if w.strip("'")]
    left = [w for w in words if w not in tokens and w not in _NAME_LEAD_INS]
    return not left


_FP_VOL_LEVEL = 60


def _try_local_fastpath(text, voice, memory):
    """Execute common quick commands LOCALLY in milliseconds, bypassing the
    ~9s model round-trip. Returns True if handled, False to fall through to the
    brain. This is the biggest single lever for making simple requests feel
    instantaneous instead of 'stuck overthinking'."""
    global _FP_VOL_LEVEL
    import re as _re
    low = text.strip().lower().rstrip(".!?")

    # --- OPEN an app, e.g. "open notepad", "open chrome", "open the calculator"
    # ONLY intercept when the remainder is a SHORT bare app name. If the user
    # strings multiple actions together ("open comet and go to my company
    # website"), that must go to the brain — never hijack it into a single
    # mis-aimed open_app call with the whole sentence as the app name.
    m = _re.match(r"^(?:open|launch|start|bring up)\s+(?:the\s+)?(.+)$", low)
    if m:
        phrase = m.group(1).strip()
        _compound = _re.search(
            r"\b(and|then|go to|goto|navigate|click|open|my|our|website|page|portal)\b",
            phrase)
        _short = len(_re.findall(r"\s+", phrase)) <= 2  # <= 3 words
        if phrase and (not _compound) and _short:
            appname = phrase
            try:
                from core.tools import open_app
            except Exception:
                open_app = None
            if open_app is not None:
                target = _normalize_app_name(appname)
                res = _call_tool(open_app, memory, {"name": target or appname})
                if not str(res).startswith("ERR"):
                    voice.say(f"Opening {appname.split()[0]}, sir.")
                    return True

    # --- VOLUME / MUTE quick commands
    if low in ("volume up", "turn the volume up", "increase volume"):
        try:
            from core.tools import set_volume
            _FP_VOL_LEVEL = min(100, _FP_VOL_LEVEL + 10)
            _call_tool(set_volume, memory, {"level": _FP_VOL_LEVEL})
            voice.say("Volume up, sir.")
        except Exception:
            voice.say("I could not adjust the volume, sir.")
        return True
    if low in ("volume down", "turn the volume down", "decrease volume"):
        try:
            from core.tools import set_volume
            _FP_VOL_LEVEL = max(0, _FP_VOL_LEVEL - 10)
            _call_tool(set_volume, memory, {"level": _FP_VOL_LEVEL})
            voice.say("Volume down, sir.")
        except Exception:
            voice.say("I could not adjust the volume, sir.")
        return True
    if low in ("mute", "mute sound", "mute volume"):
        try:
            from core.tools import media_key
            _call_tool(media_key, memory, {"key": "volumemute"})
            voice.say("Muted, sir.")
        except Exception:
            voice.say("I could not mute the sound, sir.")
        return True

    # --- SCREENSHOT
    if low in ("take a screenshot", "screenshot", "capture the screen",
               "screenshot now", "take a screen shot"):
        try:
            from core.tools import screenshot
            _call_tool(screenshot, memory, {"path": ""})
            voice.say("Done, sir. Saved a screenshot.")
            return True
        except Exception:
            pass

    return False


def _normalize_app_name(name):
    aliases = {
        "notepad": "notepad", "note pad": "notepad", "note": "notepad",
        "calculator": "calc", "calc": "calc",
        "paint": "mspaint",
        "cmd": "cmd", "command prompt": "cmd", "terminal": "cmd",
        "powershell": "powershell",
        "chrome": "chrome", "comet": "comet", "browser": "comet",
        "file explorer": "explorer", "explorer": "explorer",
    }
    n = name.strip().lower()
    if n in aliases:
        return aliases[n]
    return n


def _call_tool(fn, memory, args):
    try:
        return fn(memory, args)
    except TypeError:
        try:
            return fn(args)
        except TypeError:
            try:
                return fn()
            except TypeError:
                return "ERR:tool-signature"
    except Exception as e:
        return f"ERR:{e}"


def setup_logging():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cfg = get_config()
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(cfg.data_dir, "logs", "dude.log"),
        maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def install_startup():
    if getattr(sys, "frozen", False):
        pythonw = sys.executable
        script = sys.executable
    else:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        script = os.path.abspath(__file__)
    ok = False
    try:
        ok = PLATFORM.autostart_install([pythonw, script])
        print("registry autostart entry written")
    except Exception as e:
        print(f"registry autostart failed: {e}")
    if PLATFORM.name == "windows":
        tr = f'"{pythonw}" "{script}"'
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", "DUDE Assistant",
                            "/SC", "ONLOGON", "/RL", "LIMITED", "/TR", tr],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print("scheduled task created (preferred)")
            ok = True
        else:
            print(f"scheduled task skipped ({(r.stderr or r.stdout).strip()[:80]})")
    print("autostart installed" if ok else "autostart failed")
    return ok


def uninstall_startup():
    ok = PLATFORM.autostart_remove()
    if PLATFORM.name == "windows":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", "DUDE Assistant"],
                           capture_output=True, text=True)
        ok = ok or r.returncode == 0
    print("autostart removed" if ok else "no autostart entry found")
    return ok


def export_memory(path=None):
    cfg = get_config()
    import zipfile

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    out = path or os.path.join(cfg.data_dir, "cloud_sync", f"DudeMemory_{ts}.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(cfg.db_path(), "memory.db")
    print(f"memory exported -> {out}")
    return out


def import_memory(zippath):
    cfg = get_config()
    import shutil
    import zipfile

    bak = cfg.db_path() + ".pre_import.bak"
    if os.path.exists(cfg.db_path()):
        shutil.copy2(cfg.db_path(), bak)
    with zipfile.ZipFile(zippath) as z:
        if "memory.db" not in z.namelist():
            raise ValueError("not a DudeMemory archive")
        with open(cfg.db_path(), "wb") as f:
            f.write(z.read("memory.db"))
    print(f"memory imported from {zippath} (old brain backed up to {bak})")
    return True


class MemorySync:
    """Nightly 03:30 snapshot of the brain into data/cloud_sync.
    Point TeraBox's sync/backup folder at data/cloud_sync and every PC
    signed into the same account carries an identical DUDE memory."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="du-sync")
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(timeout=60 * 30):
            now = datetime.datetime.now()
            target = now.replace(hour=3, minute=30, second=0)
            if target < now:
                target += datetime.timedelta(days=1)
            if self._stop.wait(timeout=(target - now).total_seconds()):
                return
            try:
                out = export_memory()
                cfg = get_config()
                sync_dir = os.path.join(cfg.data_dir, "cloud_sync")
                zips = sorted(f for f in os.listdir(sync_dir) if f.endswith(".zip"))
                for old in zips[:-10]:
                    os.remove(os.path.join(sync_dir, old))
            except Exception as e:
                log.warning(f"memory sync failed: {e}")

    def stop(self):
        self._stop.set()


class ConfirmationGate:
    def __init__(self, voice, ear):
        self.voice = voice
        self.ear = ear

    def ask(self, question):
        self.voice.say(f"{question} Please say yes or no.", priority=True)
        deadline = time.time() + 25
        while time.time() < deadline:
            item = self.ear.pop_utterance(block=True, timeout=1.0)
            if not item:
                continue
            kind, payload = item
            if kind != "text":
                continue
            low = payload.lower()
            if any(w in low for w in ("yes", "sure", "go ahead", "do it", "confirm", "allow")):
                return True
            if any(w in low for w in ("no", "stop", "don't", "cancel", "abort", "deny")):
                return False
        return False


def boot_greeting(memory, voice, speak_enabled=True):
    now = datetime.datetime.now()
    title = memory.get_state("user_title") or get_config().get("user_title", default="sir")
    name = get_config().get("assistant_name", default="Dude")
    if 5 <= now.hour < 12:
        part = f"Good morning {title}"
    elif 12 <= now.hour < 17:
        part = f"Good afternoon {title}"
    else:
        part = f"Good evening {title}"

    lines = [f"{part}. {name} is online."]

    try:
        last = memory.last_real_work_before(now - datetime.timedelta(minutes=10))
        if last:
            app, t, started, ended = last
            ended_dt = datetime.datetime.strptime(ended, "%Y-%m-%d %H:%M:%S")
            when = ended_dt.strftime("%I:%M %p")
            lines.append(f"Last time we stopped at {when}, you were working "
                         f"in {app} — \"{t[:70]}\".")
        next_up = ""
        try:
            dig = (memory.get_state("last_session_digest") or "").strip()
            if dig:
                dig_line = next(
                    (l.strip().lstrip("-*0123456789. ") for l in dig.splitlines()
                     if len(l.strip().lstrip("-*0123456789. ")) > 15), "")
                if dig_line:
                    next_up = f"From our last session I remember: {dig_line[:130]}"
        except Exception:
            pass
        if not next_up:
            # Fall back to the latest genuinely-work fact, never reciting ambient
            # TV/overheard noise or contradictory identity guesses.
            for r in memory.facts_by_category("work", limit=5):
                txt = r["fact"].split("] ", 1)[-1] if "] " in r["fact"] else r["fact"]
                clean = txt.strip()
                if len(clean) < 15:
                    continue
                if any(k in clean.lower() for k in ("protonvpn", "proton vpn",
                                                     "sous chef", "career chef",
                                                     "ravi", "tzmicha", "smisha",
                                                     "smicha", "jose")):
                    continue
                next_up = f"From my memory: {clean[:130]}"
                break
        if next_up:
            lines.append("Suggested for today: " + next_up)
        upcoming = memory.upcoming_reminders(limit=3)
        routines_today = len(memory.due_routines(now))
        agenda_bits = []
        if upcoming:
            agenda_bits.append(f"{len(upcoming)} reminder(s), next \"{upcoming[0]['text']}\" "
                               f"at {upcoming[0]['due_ts'][-8:]}")
        if routines_today:
            agenda_bits.append(f"{routines_today} routine(s) due today")
        if agenda_bits:
            lines.append("On your plate: " + "; ".join(agenda_bits) + ".")
    except Exception as e:
        log.warning(f"greeting context failed: {e}")

    speech = " ".join(lines)
    print(speech)
    if speak_enabled:
        voice.say(speech)


def _session_digest(memory, brain):
    """Condense the finished session into a durable digest stored in the
    knowledge vault + session_state, so the NEXT session genuinely starts from
    memory of this one (topics, work done, decisions, open items)."""
    text = ""
    try:
        msgs = memory.recent_messages(limit=40)
        convo = "\n".join(
            f"{m['role']}: {m['content'][:140]}" for m in msgs if m["content"])
        act = memory.today_summary()
        activity = ", ".join(f"{a} ({sec // 60} min)" for a, sec in act[:6]) \
            or "no tracked activity"
        facts = []
        for c in ("screen_learning", "workflow_candidate", "insight"):
            for r in memory.facts_by_category(c, limit=6):
                facts.append(r["fact"])
        prompt = (
            "Write a compact permanent DIGEST of this DUDE session (max 8 short "
            "plain lines, nouns only, no chit-chat): what was worked on / discussed, "
            "decisions made, and any open items to continue next time.\n\n"
            f"ACTIVITY TODAY: {activity}\n\n"
            "LEARNINGS THIS SESSION:\n" + ("\n".join(facts[:20]) or "(none)") + "\n\n"
            "RECENT CONVERSATION:\n" + (convo[:4000] or "(none)")
        )
        out = (brain.think(prompt) or "").strip()
        lines = [l.strip().lstrip("-*0123456789. ") for l in out.splitlines()
                 if len(l.strip().lstrip("-*0123456789. ")) >= 8]
        if lines:
            text = "\n".join(lines[:8])
    except Exception as e:
        print(f"[digest] brain failed: {e}")
    if not text.strip():
        try:
            bits = []
            for c in ("screen_learning", "workflow_candidate", "insight"):
                for r in memory.facts_by_category(c, limit=4):
                    f = r["fact"]
                    txt = f.split("] ", 1)[-1] if "] " in f else f
                    if txt not in bits:
                        bits.append(txt)
            text = "\n".join(f"- {b[:150]}" for b in bits[:8])
        except Exception:
            text = ""
    try:
        from core.knowledge import get_knowledge

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        fn = f"session-digest-{stamp.replace(' ', '-').replace(':', '')}.md"
        get_knowledge().save_note(f"Session digest {stamp}", text or "(quiet session)",
                                  filename=fn)
        memory.set_state("last_session_digest", text.strip()[:600])
        print(f"[digest] session digest saved ({len(text)} chars)")
    except Exception as e:
        print(f"[digest] save failed: {e}")


_WHISPER_MODEL_CACHE = None

def mic_test(cfg):
    import numpy as np
    import sounddevice as sd

    global _WHISPER_MODEL_CACHE
    dur = 6
    print(f"[mic-test] Speak NOW — recording {dur}s ...")
    rec = sd.rec(int(dur * cfg.get("ear", "sample_rate", default=16000)),
                 samplerate=cfg.get("ear", "sample_rate", default=16000),
                 channels=1, dtype="int16")
    sd.wait()
    audio = rec[:, 0].astype(np.float32) / 32768.0
    # Apply mic_gain from config (same as Ear class)
    mic_gain = float(cfg.get("ear", "mic_gain", default=15.0))
    audio = audio * mic_gain
    audio = np.clip(audio, -1.0, 1.0)
    peak = float(np.max(np.abs(audio)))
    print(f"[mic-test] captured audio, peak level={peak:.3f} (gain={mic_gain})")
    if peak < 0.01:
        print("[mic-test] FAIL: microphone picked up almost nothing. Check device/permissions.")
        return False
    from faster_whisper import WhisperModel

    # Use tiny.en for mic test to avoid OOM - 39MB vs 142MB
    model_name = "tiny.en"
    if _WHISPER_MODEL_CACHE is None or _WHISPER_MODEL_CACHE[0] != model_name:
        print(f"[mic-test] Loading Whisper model: {model_name} ...")
        _WHISPER_MODEL_CACHE = (model_name, WhisperModel(model_name, device="cpu", compute_type="int8"))
    wm = _WHISPER_MODEL_CACHE[1]
    segments, _ = wm.transcribe(audio, language=cfg.get("ear", "language", default="en"),
                                beam_size=1, condition_on_previous_text=False)
    text = " ".join(s.text for s in segments).strip()
    print(f"[mic-test] heard: \"{text}\"")
    print("[mic-test] PASS" if text else "[mic-test] WEAK: audio ok but no speech recognized.")
    return bool(text)


def acquire_instance_lock():
    import msvcrt

    cfg = get_config()
    path = os.path.join(cfg.data_dir, "dude.lock")
    fh = open(path, "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("DUDE is already running elsewhere (background/window). Closing this copy.")
        sys.exit(2)
    return fh


def run_agent(args):
    setup_logging()
    cfg = get_config()
    _greeted = [False]

    if args.mic_test:
        sys.exit(0 if mic_test(cfg) else 1)
    if args.install_startup:
        sys.exit(0 if install_startup() else 1)
    if args.uninstall_startup:
        sys.exit(0 if uninstall_startup() else 1)
    if args.export_memory:
        sys.exit(0 if export_memory(args.export_memory if
                                    isinstance(args.export_memory, str) else None) else 1)
    if args.import_memory:
        sys.exit(0 if import_memory(args.import_memory) else 1)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    lock_fh = acquire_instance_lock()

    print(f"=== {cfg.get('assistant_name')} v2 starting ===")

    memory = Memory()
    _install_core_doctrines(memory)
    voice = Voice()

    ear = None
    gate = None
    def _on_barge_in():
        log.info("barge-in: user cut in; stopping speech to listen")
        voice.interrupt()
        _new_epoch()

    ear = Ear(
        voice_speaking_flag=lambda: voice.speaking,
        on_barge_in=_on_barge_in,
        on_level=lambda lvl: LEVEL_Q.put(lvl) if not LEVEL_Q.full() else None,
    )
    voice.on_speech_scheduled = ear.note_speech
    gate = ConfirmationGate(voice, ear)
    ask_user = gate.ask

    broker = SecretsBroker(ask_user)
    brain = Brain(memory, ask_user)
    init_agent_ctx(brain=brain, broker=broker)

    from core.ambient import Ambient
    from core.observer import Observer
    from core.screentree import ScreenMap
    from core.tools import init_screentree

    ambient = Ambient(memory, brain)
    observer = Observer(memory, brain)
    init_agent_ctx(brain=brain, broker=broker, observer=observer)
    screentree = ScreenMap()
    init_screentree(screentree)
    if not screentree.available():
        log.warning("screentree: UI Automation unavailable")
    else:
        log.info("screentree: UI Automation live — continuous control map enabled")

    from_agent_q = None
    to_agent_q = None
    ui_cmd_q = None
    camera = None

    if not args.no_ui:
        import queue as _q

        from_agent_q = _q.Queue()
        to_agent_q = _q.Queue()
        ui_cmd_q = _q.Queue()
        from core.tools import init_ui_cmd

        init_ui_cmd(ui_cmd_q)

        from core.ui import CameraMirror

        camera = CameraMirror()

    tracker = Tracker(memory)
    scheduler = Scheduler(memory, voice)

    from core.learning import NightlyLearner, DayLearner

    learner = NightlyLearner(memory, brain)
    day_learner = DayLearner(memory, brain)
    syncer = MemorySync()

    from core.autopilot import Autopilot
    from core.presence import Presence

    def _title():
        return memory.get_state("user_title") or "sir"

    def _note_away_start(since):
        memory.set_state("presence_state", "away")
        memory.set_state("presence_away_since", since.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("presence: user away since %s", since)
        if from_agent_q:
            from_agent_q.put({"type": "status",
                              "text": "you are away - working solo", "color": "#b8a24a"})

    def _note_return(left_at, now):
        memory.set_state("presence_state", "present")
        since_iso = left_at.strftime("%Y-%m-%d %H:%M:%S")
        events = memory.since_notices(since_iso, limit=8)
        minutes = int((now - left_at).total_seconds() // 60)
        log.info("presence: user returned after %d min (%d notice(s))",
                 minutes, len(events))
        if from_agent_q:
            from_agent_q.put({"type": "status",
                              "text": f"away {minutes}m - welcome back", "color": "#69db7c"})
        if voice.silent:
            return
        lines = []
        if events:
            bits = [e.split("] ", 1)[-1] for e in events if "] " in e]
            lines.append(f"Welcome back {_title()}.")
            lines.append(f"While you were away for {minutes} minute(s), I handled: "
                         + "; ".join(bits[:4]) + ".")
        elif minutes >= 2:
            lines.append(f"Welcome back {_title()}. You were gone about {minutes} "
                         "minute(s). All quiet, nothing needed me.")
        if lines and not voice.speaking and not (speak_thread and speak_thread.is_alive()):
            log.info("presence: briefing: %s", " ".join(lines))
            voice.say(" ".join(lines))

    presence = Presence(memory, on_away=_note_away_start, on_return=_note_return)

    def _report_to_user(summary):
        if voice.silent or not presence.present():
            return
        if voice.speaking or (speak_thread and speak_thread.is_alive()):
            return
        log.info("autopilot report: %s", summary)
        voice.say(f"Just so you know {_title()}, {summary}")

    def _auto_ok(_question):
        return True

    def _make_auto_brain():
        return Brain(memory, _auto_ok)

    autopilot = Autopilot(memory, voice, _make_auto_brain, report=None)

    from core.thinker import Thinker
    from core.experience import ExperienceLearner

    learner = ExperienceLearner(memory, cfg.data_dir)
    thinker = Thinker(memory, brain, voice, report=_report_to_user)
    init_agent_ctx(brain=brain, broker=broker, observer=observer, thinker=thinker,
                   learner=learner)
    from core.tools import bind_observe
    bind_observe(get_status=learner.watch_status,
                 start=lambda: learner.watch_start(observer, brain),
                 stop=learner.watch_stop)
    from core.experience import set_watch_brain
    set_watch_brain(brain)

    # Passive auto-learning: on EVERY boot DUDE resumes watching + learning on
    # its own (no need to say "observe and learn"). Say "stop observing" any
    # time to pause it for the session.
    if cfg.get("ambient", "auto_learn", default=False):
        try:
            # Passive background mode: learn WITHOUT stealing the CPU — a calm
            # cadence (10s capture, 90s analysis) instead of the explicit
            # observe-and-learn speed. System stays responsive.
            learner.watch_start(observer, brain,
                                capture_seconds=10, analyze_seconds=90)
            log.info("auto-learn: passive background screen learning started at boot")
            print("[auto-learn] passive screen learning active (calm mode) — "
                  "say 'stop observing' to pause for this session")
        except Exception as e:
            log.warning(f"auto-learn start failed: {e}")

    # Neural action-learning circuit: a locally-trained model that learns the
    # user's real work patterns from on-screen movements + outcomes, then feeds
    # its instincts into DUDE's planning. Starts the continuous action tracker
    # and seeds it from existing observations so training data exists at boot.
    from core.actionbrain import ActionLearner
    from core.actiontrack import ActionTracker

    action_learner = ActionLearner(cfg.data_dir)
    action_tracker = ActionTracker(cfg.data_dir, observer=observer,
                                   action_learner=action_learner)
    action_tracker.migrate_observations()
    action_tracker.start()
    if not os.path.exists(action_learner.weights_path):
        try:
            r = action_learner.train(epochs=15)
            log.info("neural action model: %s", r)
        except Exception:
            log.warning("neural action model: initial train failed", exc_info=True)
    else:
        action_learner.load()
    init_agent_ctx(brain=brain, broker=broker, observer=observer, thinker=thinker,
                   learner=learner, action_learner=action_learner,
                   action_tracker=action_tracker)

    # Start ear (microphone + VAD + STT threads)
    ear.start()
    print("[ear] Started successfully")
    # Warm-load the STT worker at boot (not on first speech) so voice input is
    # always ready and any STT failure is caught here, not silently mid-talk.
    try:
        ear.warm_start()
    except Exception as e:
        log.warning(f"whisper warm start failed: {e}")
    # Warm-load the local ollama brain model at boot too, so the first real
    # question never pays a 2.5GB cold-load (that was the "stuck thinking for
    # minutes" complaint). Runs in a daemon thread so boot stays snappy and any
    # failure is just logged.
    def _warm_llm():
        try:
            brain.warm_llm()
        except Exception as e:
            log.warning(f"llm warm up failed: {e}")

    threading.Thread(target=_warm_llm, daemon=True).start()

    # --- Orchestrator initialization (feature-gated) ---
    orchestrator_enabled = cfg.get("orchestrator", "enabled", default=False)
    use_new_task_engine = cfg.get("orchestrator", "use_new_task_engine", default=False)
    use_new_perception = cfg.get("orchestrator", "use_new_perception", default=False)
    use_new_action_executor = cfg.get("orchestrator", "use_new_action_executor", default=False)
    _orchestrator = None
    _task_engine = None
    _perception_engine = None
    _action_executor = None
    _verification_engine = None
    _recovery_engine = None
    _brain_adapter = None

    if orchestrator_enabled and use_new_task_engine:
        try:
            log.info("Initializing DUDE Orchestrator (feature-gated)")
            # Create intelligence backend adapter
            _brain_adapter = create_brain_adapter(brain)
            
            # Create OCR function using existing ocr_image
            def _ocr_fn():
                try:
                    from PIL import Image
                    from core.tools import _capture_screen_composite
                    from core.ocr import ocr_image
                    img, _ = _capture_screen_composite()
                    if img is None:
                        return None
                    text = ocr_image(img)
                    if not text:
                        return {"text": "", "regions": []}
                    # For now return simple format; full regions would need more work
                    return {"text": text, "regions": []}
                except Exception:
                    return None
            
            # Create capture function using existing _capture_screen_composite
            def _capture_fn():
                try:
                    from core.tools import _capture_screen_composite
                    img, _ = _capture_screen_composite()
                    return img
                except Exception:
                    return None
            
            # Create perception engine (reuses existing observer/screentree)
            _perception_engine = PerceptionEngine(
                get_observer=lambda: observer,
                get_experience=lambda: learner,
                get_screentree=lambda: screentree,
                get_tracker=lambda: tracker,
                get_ocr_fn=_ocr_fn,
                get_capture_fn=_capture_fn,
            )
            
            # Create action executor
            _action_executor = ActionExecutor(
                perception=_perception_engine,
                memory=memory,
                min_confidence=0.7,
            )
            
            # Create verification engine
            _verification_engine = VerificationEngine(perception=_perception_engine)
            
            # Create recovery engine
            _recovery_engine = RecoveryEngine(
                max_retries=3,
                retry_delays=(1.0, 3.0, 10.0),
            )
            
            # Create task engine
            _task_engine = TaskEngine(
                intelligence=_brain_adapter,
                perception=_perception_engine,
                action_executor=_action_executor,
                verification=_verification_engine,
                recovery=_recovery_engine,
                permission_gate=gate,
                voice=voice,
                memory=memory,
                max_retries=3,
                perception_freshness_seconds=5.0,
            )
            
            _orchestrator = {
                "task_engine": _task_engine,
                "perception": _perception_engine,
                "action_executor": _action_executor,
                "verification": _verification_engine,
                "recovery": _recovery_engine,
                "brain_adapter": _brain_adapter,
            }
            log.info("Orchestrator initialized successfully")
        except Exception as e:
            log.exception("Failed to initialize orchestrator, falling back to legacy path")
            _orchestrator = None
            _task_engine = None

    first_run_text = False
    if not args.no_greeting:
        from core.wizard import run_wizard

        try:
            ran = run_wizard(memory, voice, ear, text_mode=first_run_text)
            if ran:
                title_now = memory.get_state("user_title", "")
                if title_now:
                    cfg._data["user_title"] = title_now
        except Exception as e:
            log.warning(f"wizard failed: {e}")
        if args.no_ui:
            # Headless mode: no Notch UI to wait for, greet immediately.
            try:
                boot_greeting(memory, voice, speak_enabled=True)
            except Exception as e:
                log.warning(f"greeting failed: {e}")
        else:
            # UI mode: the greeting is spoken only AFTER the Notch is on screen
            # (triggered by the ui_ready message from the Hub), so DUDE never
            # recites memory before the UI has loaded.
            _greeted[0] = False

    def handle_text(user_text, source="voice"):
        user_text = user_text.strip()
        if not user_text:
            return
        _GEN["cancel"] = False
        log.info("handling utterance: %r (source=%s)", user_text, source)
        low = user_text.lower()
        norm_user = _norm_echo(user_text)
        norm_last = _norm_echo(_ECHO["last_reply"])
        if norm_user and norm_last:
            if norm_user == norm_last or (
                    len(norm_user) <= 6 and len(norm_last)
                    and len(set(norm_user) & set(norm_last)) / max(len(set(norm_user)), 1) >= 0.4):
                print(f"[echo-guard] ignoring own speech: {user_text!r}")
                log.info("echo-guard: ignored own speech: %r", user_text)
                return
        # WAKE-WORD GATE: on voice input, DUDE only reacts if his name "dude"
        # was actually spoken. Quit/sleep/wake phrases ALSO require the wake
        # name, otherwise background audio like "good night" or "wake up"
        # from TV would trigger him unprompted.
        if source == "voice":
            # In sleep mode, require wake word. In active mode, the ambient
            # filter already decided this speech is directed at DUDE.
            if voice.silent:
                armed = _GEN.get("armed_until", 0.0) > time.time()
                if not armed and not _has_wake_name(low):
                    print(f"[wake-gate] ignoring (no wake word): {user_text!r}")
                    log.info("wake-gate: ignoring utterance without 'dude': %r", user_text)
                    return
            # Name-only utterance ("dude", "dude can you") = activation, NOT a
            # command. Arm and wait for the rest instead of burning a brain
            # turn on a half-sentence, which used to cut the user off.
            if not _is_wake_cmd(low) and not _is_quit_cmd(low) and not _is_sleep_cmd(low) \
                    and _is_name_only(low):
                _GEN["armed_until"] = time.time() + 15.0
                print(f"[wake] name-only — waiting for command: {user_text!r}")
                log.info("wake-gate: name-only %r — armed for command follow-up", user_text)
                if from_agent_q:
                    from_agent_q.put({"type": "status", "text": "listening…", "color": "#69db7c"})
                return
            _GEN["armed_until"] = 0.0
        if low in ("open camera mirror", "camera"):
            if camera:
                camera.open_async()
                voice.say("Opening the camera mirror.")
            else:
                voice.say("Camera window isn't available in this mode.")
            return
        if _is_center_open_cmd(low):
            if ui_cmd_q:
                ui_cmd_q.put("center_open")
            voice.say("Opening the command centre, sir.")
            return
        if _is_center_close_cmd(low):
            if ui_cmd_q:
                ui_cmd_q.put("center_close")
            if not voice.silent:
                voice.say("Closing the command centre.")
            return
        if _is_quit_cmd(low):
            voice.say(f"Shutting down. Goodbye {memory.get_state('user_title') or 'sir'}.", force=True)
            time.sleep(1.5)
            os._exit(0)
        if _is_sleep_cmd(low):
            voice.say("Going quiet sir. I will only listen. Say wake up to bring me back.", force=True)
            voice.silent = True
            status = memory.get_state
            if from_agent_q:
                from_agent_q.put({"type": "status", "text": "sleeping (listen-only)", "color": "#5f6b76"})
            return
        if _is_wake_cmd(low):
            voice.silent = False
            if from_agent_q:
                from_agent_q.put({"type": "status", "text": "awake", "color": "#69db7c"})
            voice.say("I'm awake sir. What do you need?", force=True)
            return
        if low in ("pause autonomy", "pause autonomous mode", "stop autonomy",
                   "deactivate autonomous mode", "stop working on your own"):
            autopilot.set_enabled(False)
            thinker.set_enabled(False)
            voice.say("Autonomous mode paused, sir. I will only act when you ask me to.")
            return
        if low in ("resume autonomy", "resume autonomous mode", "start autonomy",
                   "activate autonomous mode", "work on your own"):
            autopilot.set_enabled(True)
            autopilot.force_now(600)
            thinker.set_enabled(True)
            voice.say("Autonomous mode resumed, sir. I will keep working on my own.")
            return
        intent = _observe_intent(low)
        if intent == "start":
            started = learner.watch_start(observer, brain)
            if started:
                voice.say("Watching and learning continuously now, sir. I will keep "
                          "studying your screen — through every command you give me — "
                          "and store it all in permanent memory until you tell me to stop.")
            else:
                voice.say("I am already observing and learning, sir. I never stopped — "
                          "I keep watching even while I work.")
            _check_observe_real(learner, observer, brain, voice)
            return
        if intent == "stop":
            summary = learner.watch_stop()
            if not summary:
                voice.say("I wasn't watching anything, sir.")
            else:
                try:
                    from core.tools import learn_knowledge
                    places = "\n".join(f"- {p}" for p in summary["places"][:20])
                    learn_knowledge(
                        memory, {"title": f"What you taught me {summary['from']}",
                                 "body": "Places/apps I watched you use:\n" + places})
                except Exception:
                    pass
                try:
                    n = summary["n"]
                except Exception:
                    n = 0
                voice.say(f"Understood and stored, sir. I watched {summary.get('n', n)} "
                          "distinct things you did and filed them into my knowledge, so "
                          "I can do them the way you do.")
            return
        # LOCAL FAST-PATH: instant, model-free execution for common quick commands.
        # These run locally in milliseconds so DUDE answers like lightning instead of
        # burning a ~9s model round-trip (plus a "I'm on it" narration) for a trivial
        # request. Falls through to the brain only if no fast-path matches.
        _fp = _try_local_fastpath(user_text, voice, memory)
        if _fp:
            return
        my_epoch = _new_epoch()
        memory.add_message("user", user_text)
        print(f"\nYou[{source}]: {user_text}")
        spoke = {"any": False}
        tool_progress_announced = {"value": False}

        def on_tool(tool_name):
            if my_epoch != _GEN["epoch"]:
                return
            if from_agent_q:
                from_agent_q.put({"type": "status", "text": "working", "color": "#5ba7e6"})
            if not tool_progress_announced["value"]:
                on_delta("I'm on it.")
                tool_progress_announced["value"] = True

        def on_delta(sentence):
            if my_epoch != _GEN["epoch"] or _GEN.get("cancel"):
                return
            print(f"[speech] {sentence}")
            _ECHO["last_reply"] = sentence
            voice.say(sentence)
            spoke["any"] = True
            if from_agent_q:
                from_agent_q.put({"type": "speech", "text": sentence})

        try:
            screen_extra = ""
            try:
                snap = observer.current_screen()
                parts = [f"Active window: {snap['app']} — {snap['title']}"]
                if snap["shot"]:
                    parts.append(f"Latest capture: {snap['shot']}")
                if snap["description"]:
                    parts.append("Screen content: " + snap["description"])
                else:
                    parts.append("Screen content: (no detailed vision analysis in this "
                                 "turn — call analyze_recent_screens for an exact look "
                                 "only if the user asks)")
                if snap.get("ocr"):
                    ocr_flat = " ".join(snap["ocr"].split())
                    if ocr_flat:
                        parts.append(f"Visible text on screen right now (OCR): "
                                     f"{ocr_flat[:600]}")
                screen_extra = "CURRENT SCREEN (from observation):\n" + "\n".join(parts)
                try:
                    wst = learner.watch_status()
                    if wst.get("on"):
                        screen_extra += (f"\n\nOBSERVING STATUS: ACTIVE — recording every "
                                         f"change ({wst.get('frames')} captures, "
                                         f"{wst.get('observations_row_count')} observations, "
                                         f"{wst.get('insights')} insights stored). You are "
                                         f"learning what happens while you work; never claim "
                                         f"to the user that watching stopped.")
                    else:
                        screen_extra += ("\n\nOBSERVING STATUS: not recording right now "
                                         "(no observe command active). If the user asks you "
                                         "to observe, start the watcher.")
                except Exception:
                    pass
                try:
                    ui_view = screentree.current_view(limit=18)
                    if ui_view:
                        screen_extra += ("\n\nLIVE UI MAP (deterministic — real control "
                                         "names and pixel positions read from Windows UI "
                                         "Automation, NOT from screenshots):\n" + ui_view)
                except Exception:
                    pass
                try:
                    exp = learner.lessons(app=snap["app"], limit=5)
                    if exp:
                        screen_extra += ("\n\nLEARNED BY DOING (your own experience record: "
                                         "what has worked or failed for you in this kind of "
                                         "context):\n- " + "\n- ".join(exp))
                except Exception:
                    pass
                try:
                    nhint = action_learner.context_hint(
                        snap["app"], recent_tools=None, limit=4)
                    if nhint:
                        screen_extra += ("\n\nNEURAL INSTINCT (a locally-trained model "
                                         "learned from the user's real on-screen "
                                         "movements and your past outcomes predicts the "
                                         "likely next actions; treat as a suggestion, not "
                                         "a command. Weigh it against the LIVE UI MAP):\n"
                                         + nhint)
                except Exception:
                    pass
                # Connect MEMORY into the same neural-experience brain: pull durable
                # facts matching the current app/task so DUDE acts on what it already
                # knows instead of only re-searching when forced.
                try:
                    q = f"{snap['app']} {snap['title']} {user_text}"
                    facts = memory.recall_facts(q, limit=4)
                    # Always reinforce DUDE's permanent autonomy so it never regresses
                    # into asking permission for routine steps.
                    identity = [f["fact"] for f in memory.facts_by_category("CORE_IDENTITY", 30)]
                    for f in identity:
                        if f not in facts:
                            facts.append(f)
                    if facts:
                        screen_extra += ("\n\nMEMORY LINK (durable facts about the user "
                                         "relevant to this moment, from the same "
                                         "learning circuit):\n- " + "\n- ".join(facts))
                except Exception:
                    pass
                # RECALL PAST CONVERSATIONS matching what the user just said, so
                # DUDE genuinely remembers earlier sessions instead of claiming
                # amnesia. Keyword lookup over the permanent message archive.
                try:
                    tokens = [t.strip(".,?!;:") for t in user_text.split()
                              if len(t.strip(".,?!;:")) > 3][:4]
                    past = []
                    seen = set()
                    for tk in tokens:
                        for m in memory.search_messages(tk, limit=2):
                            key = (m["ts"], m["content"][:60])
                            if key in seen or len(m["content"]) < 8:
                                continue
                            seen.add(key)
                            past.append(m)
                        if len(past) >= 5:
                            break
                    if past:
                        conv = "\n".join(
                            f"[{m['ts']}] {m['role']}: {m['content'][:150]}"
                            for m in past)
                        screen_extra += ("\n\nPAST CONVERSATIONS & LEARNINGS (retrieved "
                                         "from permanent memory because they match what "
                                         "the user just said — use them as your memory):\n"
                                         + conv)
                except Exception:
                    pass
            except Exception:
                log.warning("screen snapshot failed", exc_info=True)
            reply = brain.chat(user_text, on_delta=on_delta, on_tool=on_tool,
                               system_extra=screen_extra)
            if reply and not spoke["any"] and my_epoch == _GEN["epoch"]:
                print(f"[speech] {reply}")
                _ECHO["last_reply"] = reply
                voice.say(reply)
                if from_agent_q:
                    from_agent_q.put({"type": "speech", "text": reply})
            if reply:
                memory.add_message("assistant", reply)
        except BrainUnavailable as e:
            log.error(f"all providers failed: {e}")
            voice.say("Sorry sir, my brain is not responding right now — "
                      "give me a moment and talk to me again.")
        except Exception:
            log.exception("chat failure")
            voice.say("Sorry sir, something glitched on my side just then.")

    speak_queue = collections.deque()
    speak_thread = None
    speak_lock = threading.Lock()

    def _enqueue_speak(user_text, source="voice"):
        nonlocal speak_thread
        speak_queue.append((user_text, source))

        def pump():
            while speak_queue:
                t, s = speak_queue.popleft()
                log.info("agent: processing queued command: %r (source=%s)", t[:60], s)
                try:
                    handle_text(t, s)
                except Exception:
                    log.exception("speak worker error")

        with speak_lock:
            if speak_thread is None or not speak_thread.is_alive():
                speak_thread = threading.Thread(target=pump, daemon=True, name="du-speak")
                speak_thread.start()

    def agent_loop():
        while True:
            try:
                msg = None
                if to_agent_q and not to_agent_q.empty():
                    msg = to_agent_q.get_nowait()
                if msg is not None and msg.get("type") == "ui_ready":
                    # UI is on screen now: speak the boot greeting for real (once).
                    # This replaces the old pre-UI greeting that recited memory
                    # before the Notch had even loaded.
                    if not _greeted[0]:
                        _greeted[0] = True
                        try:
                            if not args.no_greeting:
                                boot_greeting(memory, voice, speak_enabled=True)
                        except Exception:
                            log.exception("greeting failed")
                    continue
                if msg is not None and msg.get("type") == "command":
                    _enqueue_speak(msg["text"], "ui-cmd")
                    continue
                item = ear.pop_utterance(block=True, timeout=0.5)
                if not item:
                    continue
                kind, payload = item
                if kind != "text":
                    continue
                log.info("agent: heard utterance: %r", payload)
                verdict, why = ambient.route(payload)
                log.info("agent: verdict=%s (%s)", verdict, why)
                if verdict == "directed":
                    # --- Orchestrator path (feature-gated) ---
                    if _task_engine and orchestrator_enabled and use_new_task_engine:
                        try:
                            log.info("Orchestrator: handling directed utterance via TaskEngine")
                            _task_engine.handle_user_input(payload)
                            # Run task engine step (non-blocking)
                            import asyncio
                            asyncio.run(_task_engine.run(payload))
                            continue
                        except Exception as e:
                            log.exception("Orchestrator failed, falling back to legacy path")
                            # Fall through to legacy path
                    # --- Legacy path ---
                    low = payload.lower()
                    # WAKE-WORD GATE: DUDE only reacts to voice when his name
                    # ("dude") is actually spoken, or he is freshly armed after a
                    # name-only utterance, or it is a control phrase. Anything
                    # else stays ignored even in active mode.
                    if not (_has_wake_name(low) or _is_quit_cmd(low)
                            or _is_sleep_cmd(low) or _is_wake_cmd(low)
                            or _GEN.get("armed_until", 0.0) > time.time()):
                        print(f"[wake-gate] dropping unaddressed directed utterance: {payload[:80]!r}")
                        log.info("wake-gate: dropping unaddressed directed utterance: %r", payload)
                        continue
                    if from_agent_q:
                        from_agent_q.put({"type": "heard", "text": payload})
                    if voice.silent:
                        addressed = "dude" in low or "computer" in low or "assistant" in low
                        if _is_wake_cmd(low) or _is_quit_cmd(low) or addressed:
                            if addressed and not (_is_wake_cmd(low) or _is_quit_cmd(low)):
                                log.warning("agent: implicit wake, %r was name-addressed", payload)
                                voice.silent = False
                            handle_text(payload)
                            continue
                        else:
                            if from_agent_q:
                                from_agent_q.put({"type": "status",
                                                  "text": "sleeping (command ignored)", "color": "#5f6b76"})
                            continue
                    if voice.speaking or (speak_thread and speak_thread.is_alive()):
                        voice.abort()
                        _GEN["cancel"] = True
                        try:
                            brain.cancel_stream()
                        except Exception:
                            pass
                    _enqueue_speak(payload)
                elif _GEN.get("armed_until", 0.0) > time.time():
                    log.info("wake-gate: armed follow-up treated as command: %r", payload)
                    if voice.speaking or (speak_thread and speak_thread.is_alive()):
                        voice.abort()
                        _GEN["cancel"] = True
                        try:
                            brain.cancel_stream()
                        except Exception:
                            pass
                    _enqueue_speak(payload)
                else:
                    print(f"[ambient] (noted, not directed) {payload[:80]}")
                    if from_agent_q:
                        from_agent_q.put({"type": "status",
                                          "text": f"ambient noted ({why})", "color": "#b8a24a"})
            except Exception:
                log.exception("agent_loop error")

    try:
        if args.no_ui:
            agent_loop()
        else:
            agent_thread = threading.Thread(target=agent_loop, daemon=True)
            agent_thread.start()
            from core.notch import launch_notch

            def on_quit():
                os._exit(0)

            tray = Tray(on_show=lambda: None, on_quit=on_quit)
            tray.start()
            launch_notch(to_agent_q, from_agent_q, ui_cmd_q=ui_cmd_q, ear=ear)
    finally:
        print("\nShutting down...")
        try:
            dig_thread = threading.Thread(target=_session_digest,
                                          args=(memory, brain), daemon=True)
            dig_thread.start()
            dig_thread.join(timeout=25)
        except Exception:
            pass
        voice.say(f"Going offline. Goodbye {memory.get_state('user_title') or 'sir'}.")
        time.sleep(1.2)
        if ear:
            ear.stop()
        observer.stop()
        tracker.stop()
        scheduler.stop()
        learner.stop()
        day_learner.stop()
        syncer.stop()
        presence.stop()
        autopilot.stop()
        voice.close()
        memory.close()


_OBSERVE_START = (
    "observe and learn", "observe the screen", "start observing",
    "start watching", "begin observing", "watch me", "watch and learn",
    "learn from me", "start learning", "keep observing", "keep an eye",
    "watch your screen", "observe continuously", "look and learn",
    "just observe", "observe me", "observe under learn",
)
_OBSERVE_STOP = (
    "stop observing", "stop watching", "stop learning", "stop the watch",
    "end observing", "end watching", "learned enough", "done teaching",
    "done watching", "that's all i wanted to show you", "stop recording",
    "stop the observation", "enough learning", "stop observing and learning",
    "stop watching and learning", "that's enough learning",
)


def _observe_intent(low):
    """Fuzzy observe-mode intent from ANY phrasing, independent of exact
    transcription. Returns 'start', 'stop', or None. Runs BEFORE the brain so
    observe commands always reach the real watcher."""
    wrapped = " " + (low or "") + " "
    if any(p in wrapped for p in _OBSERVE_STOP):
        return "stop"
    if any(p in wrapped for p in _OBSERVE_START):
        return "start"
    return None


def _check_observe_real(learner, observer, brain, voice):
    """Self-heal + honesty: ~22s after 'observe', verify the watcher really runs;
    restart it once if its loop died, otherwise log the verified state."""

    def run():
        time.sleep(22)
        try:
            st = learner.watch_status()
            if st.get("on") and not st.get("loop_alive"):
                print("[watch] verify: loop died -> self-heal restarting")
                learner.watch_start(observer, brain)
                voice.say("Sir, my watcher had crashed — I restarted it and I am "
                          "observing again.")
            elif st.get("on"):
                print(f"[watch] verified: frames={st.get('frames')} "
                      f"rows={st.get('observations_row_count')} "
                      f"insights={st.get('insights')}")
        except Exception as e:
            print(f"[watch] verify failed: {e}")

    threading.Thread(target=run, daemon=True).start()


def _install_core_doctrines(memory):
    """Install DUDE's permanent operating rules into permanent memory
    (CORE_IDENTITY), so they are ALWAYS recalled in every context."""
    memory.install_doctrine(
        "PERMANENT RULE: when the user says 'observe and learn' (or 'watch me'), "
        "DUDE must observe and learn CONTINUOUSLY — never stop on its own and "
        "never stop just because the user gives another command to work on; keep "
        "watching even while working, so DUDE also observes its own work, learns "
        "from its own mistakes, and records what it did perfectly. Store everything "
        "seen and done in permanent memory during observe mode; only stop when the "
        "user explicitly says stop. Think and behave based on the memory acquired."
    )


def main():
    ap = argparse.ArgumentParser(prog="dude")
    ap.add_argument("--no-greeting", action="store_true")
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--install-startup", action="store_true")
    ap.add_argument("--uninstall-startup", action="store_true")
    ap.add_argument("--mic-test", action="store_true")
    ap.add_argument("--export-memory", nargs="?", const="", metavar="ZIP_PATH")
    ap.add_argument("--import-memory", metavar="ZIP_PATH")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--stt-worker", action="store_true",
                    help="run as the Whisper STT subprocess (used by the frozen exe)")
    args = ap.parse_args()

    if args.stt_worker:
        from core.stt_worker import main as stt_main

        stt_main()
        return

    retries = 0
    while True:
        try:
            run_agent(args)
            break
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception:
            log.exception("agent crashed; auto-restarting")
            retries += 1
            if retries > 5:
                print("Too many crashes — giving up. See data/logs/dude.log")
                raise
            time.sleep(min(3 * retries, 15))


if __name__ == "__main__":
    main()

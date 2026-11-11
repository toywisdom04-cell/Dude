"""Opt-in, bounded component checks; never starts DUDE's autonomous runtime.

Desktop uses only a window created by this process. Voice sends one fixed public
phrase to the configured TTS backend. Microphone samples stay in RAM and are
never transcribed, saved or uploaded. Each check has an outer process deadline.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid


PHRASE = "DUDE voice validation complete."


def desktop_check():
    import win32api
    import win32con
    import win32gui
    import win32process
    import uiautomation as auto
    from core.screentree import ScreenMap
    from core.orchestrator.perception import PerceptionEngine
    from core.orchestrator.action_executor import ActionExecutor
    from core.orchestrator.verification import VerificationEngine
    from core.orchestrator.state import (
        Action, ExpectedResult, TargetSpec, VerificationMethod,
    )

    title = "DUDE bounded validation " + uuid.uuid4().hex[:10]
    state = {}
    ready = threading.Event()

    def window_loop():
        def handler(hwnd, message, wparam, lparam):
            if message == win32con.WM_CLOSE:
                win32gui.DestroyWindow(hwnd)
                return 0
            if message == win32con.WM_DESTROY:
                win32gui.PostQuitMessage(0)
                return 0
            return win32gui.DefWindowProc(hwnd, message, wparam, lparam)
        try:
            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = title
            wc.lpfnWndProc = handler
            atom = win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindowEx(
                win32con.WS_EX_NOACTIVATE, atom, title,
                win32con.WS_OVERLAPPEDWINDOW, 80, 80, 500, 180,
                0, 0, wc.hInstance, None)
            edit = win32gui.CreateWindowEx(
                win32con.WS_EX_CLIENTEDGE, "EDIT", "",
                win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.ES_AUTOHSCROLL,
                20, 25, 440, 35, hwnd, 1001, wc.hInstance, None)
            state.update(hwnd=hwnd, edit=edit)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            ready.set()
            win32gui.PumpMessages()
            win32gui.UnregisterClass(atom, wc.hInstance)
        except Exception as exc:
            state["error"] = type(exc).__name__
            ready.set()

    thread = threading.Thread(target=window_loop, daemon=True)
    thread.start()
    try:
        if not ready.wait(10) or "error" in state:
            raise RuntimeError("owned window did not become ready")
        hwnd, edit = state["hwnd"], state["edit"]
        if win32process.GetWindowThreadProcessId(hwnd)[1] != os.getpid():
            raise RuntimeError("window ownership check failed")
        # Invoke the real stateless pinned-window methods without starting
        # ScreenMap's unrelated continuous foreground-observation thread.
        screen = object.__new__(ScreenMap)
        perception = PerceptionEngine(get_screentree=lambda: screen)
        before = perception.observe_window(hwnd)
        verifier = VerificationEngine(perception)
        appeared = Action(
            task_id="bounded-live", step_id=0, intent="verify owned window",
            action_type="open_app", target=TargetSpec(hwnd=hwnd),
            target_description=title,
            expected_result=ExpectedResult(window_title=title),
            verification_method=VerificationMethod.WINDOW_APPEARED)
        window_result = asyncio.run(verifier.verify(
            appeared, appeared.expected_result, before, before))
        if not window_result.success:
            raise RuntimeError("owned window verification failed")
        with auto.UIAutomationInitializerInThread():
            control = auto.ControlFromHandle(edit)
            control.GetValuePattern().SetValue(PHRASE)
        after = perception.observe_window(hwnd)
        action = Action(
            task_id="bounded-live", step_id=1, intent="read owned validation field",
            action_type="read_text",
            target=TargetSpec(control_id="1001", control_role="EditControl"),
            target_description="owned validation field",
            expected_result=ExpectedResult(text_expected=PHRASE),
            verification_method=VerificationMethod.TEXT_READ)
        result = asyncio.run(ActionExecutor(perception).execute(action, after))
        if not result.success:
            raise RuntimeError("DUDE grounded read failed: " + result.status.value)
        verified = asyncio.run(verifier.verify(
            action, action.expected_result, before, after, result.actual_result))
        if not verified.success or result.actual_result.text_found != PHRASE:
            raise RuntimeError("DUDE read-back verification failed")
        report = {"status": "pass", "owned_window_verified": True,
                  "uia_value_write_verified": True, "dude_grounded_read_verified": True,
                  "controls_observed": len(after.controls), "global_input_sent": False}
    finally:
        hwnd = state.get("hwnd")
        if hwnd and win32gui.IsWindow(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        thread.join(timeout=5)
    report["owned_window_closed"] = not win32gui.IsWindow(hwnd)
    if thread.is_alive() or not report["owned_window_closed"]:
        raise RuntimeError("owned window cleanup failed")
    return report


def audio_check(directory, microphone=False):
    import numpy as np
    import sounddevice as sd
    from core import config
    from core.voice import Voice

    # Preserve selected voice/devices, but keep synthesized files temporary.
    # No Brain/provider instance or vault lookup is involved.
    config._config = config.Config(data_dir=directory)
    voice = Voice()
    path = None
    try:
        path = voice._synth(PHRASE)
        if not path or os.path.getsize(path) == 0:
            raise RuntimeError("TTS did not produce audio")
        played = voice._play(path)
        if not played:
            raise RuntimeError("configured TTS audio playback failed")
        report = {"status": "pass", "synthesized": True,
                  "playback_api_completed": True, "human_audibility_confirmed": False}
        if microphone:
            mic = config._config.get("ear", "mic_index", default=None)
            info = sd.query_devices(mic, "input")
            sample_rate = int(info["default_samplerate"])
            # Capture only aggregate signal evidence; never return raw samples.
            samples = sd.rec(5 * sample_rate, samplerate=sample_rate,
                             channels=1, dtype="float32", device=mic, blocking=True)
            report["microphone"] = {
                "seconds": 5, "frames": len(samples),
                "rms": round(float(np.sqrt(np.mean(samples * samples))), 6),
                "peak": round(float(np.max(np.abs(samples))), 6),
                "saved": False, "uploaded": False, "transcribed": False}
            del samples
        return report
    finally:
        voice.abort()
        voice.close()
        voice._thread.join(timeout=3)
        sd.stop()
        if path:
            voice._rm(path)
        if voice._mixer_ready:
            import pygame
            pygame.mixer.quit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--microphone", action="store_true",
                        help="opt into five seconds of in-memory mic capture after playback")
    parser.add_argument("--_worker", choices=("desktop", "voice"), help=argparse.SUPPRESS)
    parser.add_argument("--_directory", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.microphone and not (args.voice or args._worker == "voice"):
        parser.error("--microphone requires --voice")
    if args._worker:
        try:
            result = (desktop_check() if args._worker == "desktop" else
                      audio_check(args._directory, args.microphone))
        except Exception as exc:
            result = {"status": "fail", "error_type": type(exc).__name__,
                      "detail": str(exc)[:240]}
        print("DUDE_VALIDATION_RESULT=" + json.dumps(result, ensure_ascii=True))
        return int(result["status"] != "pass")
    if not (args.desktop or args.voice):
        parser.error("choose --desktop and/or --voice explicitly")
    results = {}
    for name, enabled in (("desktop", args.desktop), ("voice", args.voice)):
        if not enabled:
            continue
        with tempfile.TemporaryDirectory(prefix="dude-live-") as directory:
            command = [sys.executable, "-B", str(Path(__file__).resolve()),
                       "--_worker", name, "--_directory", directory]
            if name == "voice" and args.microphone:
                command.append("--microphone")
            try:
                process = subprocess.run(command, timeout=60, capture_output=True,
                                         text=True, encoding="utf-8", errors="replace")
                lines = [line for line in process.stdout.splitlines()
                         if line.startswith("DUDE_VALIDATION_RESULT=")]
                results[name] = (json.loads(lines[-1].split("=", 1)[1]) if lines else
                                 {"status": "fail", "detail": "worker returned no receipt"})
                if process.returncode and results[name].get("status") == "pass":
                    results[name] = {"status": "fail", "detail": "worker exited unsuccessfully"}
            except subprocess.TimeoutExpired:
                results[name] = {"status": "fail", "detail": "60-second deadline; worker terminated"}
            except (OSError, ValueError):
                results[name] = {"status": "fail", "detail": "worker launch or receipt failed"}
    print(json.dumps({"scope": "bounded components, not full autonomous runtime", "results": results},
                     indent=2, ensure_ascii=True))
    return int(any(value["status"] != "pass" for value in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline import-readiness check for DUDE's Windows runtime.

No runtime services are constructed. Audio dependencies are discovered without
importing them: sounddevice's import alone can initialize PortAudio. Success is
not evidence that the desktop, devices, credentials, models or providers work.
"""

import argparse
import importlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time


APP_ROOT = Path(__file__).resolve().parent
SCOPE = (
    "Import readiness only, not live validation. Desktop access, application "
    "launching, audio devices, model availability and provider connectivity "
    "were not tested. Configurations and vaults were not loaded."
)

# module, exported symbols, required, method, Windows-only
# Keep optional audio probes discovery-only; do not import core.ear here.
CHECKS = (
    ("core.brain", ("Brain",), True, "import", False),
    ("core.tools", ("ui_scan", "open_app", "execute_tool"), True, "import", False),
    ("core.orchestrator.recovery", ("RecoveryEngine",), True, "import", False),
    ("core.orchestrator.task_engine", ("TaskEngine",), True, "import", False),
    ("core.orchestrator.perception_service", ("LatencyMetrics",), True, "import", False),
    ("core.screentree", ("ScreenMap",), True, "import", False),
    ("uiautomation", (), True, "import", True),
    ("comtypes", (), True, "import", True),
    ("win32api", (), True, "import", True),
    ("win32gui", (), True, "import", True),
    ("win32ui", (), True, "import", True),
    ("core.voice", ("Voice",), False, "import", False),
    ("sounddevice", (), False, "discovery", False),
    ("numpy", (), False, "discovery", False),
    ("faster_whisper", (), False, "discovery", False),
    ("onnxruntime", (), False, "discovery", False),
    ("edge_tts", (), False, "discovery", False),
    ("pygame", (), False, "discovery", False),
    ("pyttsx3", (), False, "discovery", False),
)


def _probe_in_process(check):
    """Worker entry: inspect exports, never call them or construct services."""
    module_name, symbols, _, method, _ = check
    started = time.perf_counter()
    try:
        if method == "discovery":
            # All discovery names are top-level, so find_spec executes no parent.
            if importlib.util.find_spec(module_name) is None:
                raise ModuleNotFoundError("Optional package not installed: " + module_name)
            detail = "Package found; import, native libraries and devices not tested."
        else:
            module = importlib.import_module(module_name)
            for symbol in symbols:
                if not callable(getattr(module, symbol, None)):
                    raise ImportError("Missing callable export: " + module_name + "." + symbol)
            detail = "Import succeeded"
            if symbols:
                detail += "; exports: " + ", ".join(symbols)
            detail += "; no services constructed."
        return {"ok": True, "detail": detail,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3)}
    except Exception as exc:
        return {"ok": False, "detail": type(exc).__name__ + ": " + str(exc),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3)}


def probe(check, timeout=10.0):
    """Bound each check in a fresh interpreter, using the app's import root.

    -I ignores ambient PYTHONPATH/current-directory imports; the worker adds the
    explicit app root for the runtime's existing absolute core.* imports.
    -B avoids generating bytecode as part of this diagnostic.
    """
    started = time.perf_counter()
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
               "--_probe", check[0]]
    try:
        completed = subprocess.run(
            command, cwd=str(APP_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if completed.returncode:
            result = {"ok": False,
                      "detail": "Import worker exited with code " + str(completed.returncode)}
        else:
            # Worker stdout contains only our JSON; library chatter is discarded.
            result = json.loads(completed.stdout)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("invalid worker result")
            if not isinstance(result.get("detail"), str):
                raise ValueError("missing worker detail")
    except subprocess.TimeoutExpired:
        result = {"ok": False, "detail": "Probe timed out after %.3g seconds" % timeout}
    except (OSError, ValueError) as exc:
        result = {"ok": False, "detail": "Probe failed: " + str(exc)}
    # End-to-end probe measurement includes interpreter startup; not app latency.
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def collect_checks(timeout=10.0, platform_name=None, probe_fn=None):
    """Return deterministic check ordering and measured diagnostic durations."""
    started = time.perf_counter()
    platform_name = sys.platform if platform_name is None else platform_name
    probe_fn = probe if probe_fn is None else probe_fn
    windows = platform_name == "win32"
    results = [{
        "name": "windows_runtime", "required": True, "method": "platform",
        "status": "pass" if windows else "fail", "duration_ms": 0.0,
        "detail": ("Windows platform detected; desktop access not tested." if windows else
                   "Windows runtime requires win32; detected " + platform_name + "."),
    }]
    for check in CHECKS:
        name, _, required, method, windows_only = check
        if windows_only and not windows:
            status, detail, duration = "skip", "Windows-only dependency; not probed on this OS.", 0.0
        else:
            outcome = probe_fn(check, timeout)
            status = "pass" if outcome["ok"] else ("fail" if required else "warn")
            detail, duration = outcome["detail"], outcome["duration_ms"]
        results.append({"name": name, "required": required, "method": method,
                        "status": status, "detail": detail, "duration_ms": duration})
    summary = {status: sum(item["status"] == status for item in results)
               for status in ("pass", "fail", "warn", "skip")}
    return {
        "schema_version": 1, "scope": SCOPE, "platform": platform_name,
        "python": sys.version.split()[0], "executable": sys.executable,
        "app_root": str(APP_ROOT), "checks": results, "summary": summary,
        "ready": summary["fail"] == 0, "exit_code": int(summary["fail"] != 0),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def render_text(report):
    lines = ["DUDE Windows Runtime Import Readiness", report["scope"],
             "Python: " + report["python"] + " (" + report["executable"] + ")",
             "App root: " + report["app_root"], ""]
    for item in report["checks"]:
        lines.append("[{status}] {name} ({requirement}, {method}, {duration_ms:.1f} ms): {detail}".format(
            requirement="required" if item["required"] else "optional",
            **dict(item, status=item["status"].upper())))
    summary = report["summary"]
    lines.extend(["", "Summary: {pass} passed, {fail} required failures, "
                  "{warn} optional warnings, {skip} skipped.".format(**summary),
                  "Diagnostic elapsed: %.1f ms (includes import worker startup)." % report["duration_ms"],
                  "Required import checks: " + ("PASS" if report["ready"] else "FAIL"),
                  "Optional warnings do not fail this baseline; voice/STT may need these packages."])
    # Escape dynamic paths and errors too, not just our status labels (cp1252).
    return "\n".join(lines).encode("ascii", "backslashreplace").decode("ascii")


def _positive_timeout(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and greater than zero")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit one structured JSON report")
    parser.add_argument("--timeout", type=_positive_timeout, default=10.0,
                        help="maximum seconds per import/discovery worker (default: 10)")
    parser.add_argument("--_probe", choices=[check[0] for check in CHECKS], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._probe:
        import contextlib
        import os

        sys.path.insert(0, str(APP_ROOT))
        check = next(check for check in CHECKS if check[0] == args._probe)
        # Do not mix third-party banners/warnings with the JSON protocol.
        with open(os.devnull, "w") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                result = _probe_in_process(check)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    report = collect_checks(timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=True, indent=2) if args.json else render_text(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

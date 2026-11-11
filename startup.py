"""Shared, import-safe startup contract for package and direct-file launches."""

import argparse
from contextlib import contextmanager
import importlib
import logging
from pathlib import Path
import sys


def prepare_imports():
    """Keep the existing absolute imports on one canonical module namespace."""
    root = str(Path(__file__).resolve().parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def _positive_int(value):
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("must be between 1 and 1000")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="dude", description="DUDE personal assistant")
    parser.add_argument("--no-greeting", action="store_true")
    parser.add_argument("--no-ui", "--headless", dest="no_ui", action="store_true",
                        help="run the voice agent without its UI (microphone remains enabled)")
    parser.add_argument("--debug", "--verbose", dest="debug", action="store_true")
    parser.add_argument("--config", metavar="JSON_PATH",
                        help="canonical config JSON; explicit files bypass config.local.json")
    parser.add_argument("--data-dir", metavar="DIRECTORY", help="override canonical runtime storage")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--text", action="store_true",
                       help="bounded conversational Brain/Memory REPL; no audio, tools or background tasks")
    modes.add_argument("--startup-check", action="store_true",
                       help="check entry-point and lightweight imports only; no config, secrets or runtime")
    modes.add_argument("--wizard", action="store_true",
                       help="legacy setup wizard (currently unavailable; exits without writes)")
    modes.add_argument("--install-startup", action="store_true")
    modes.add_argument("--uninstall-startup", action="store_true")
    modes.add_argument("--mic-test", action="store_true")
    modes.add_argument("--export-memory", nargs="?", const="", metavar="ZIP_PATH")
    modes.add_argument("--import-memory", metavar="ZIP_PATH")
    modes.add_argument("--stt-worker", action="store_true",
                       help="run the Whisper subprocess (used by the frozen executable)")
    modes.add_argument("--capability-audit", action="store_true",
                       help="initialize the full runtime, print capability truth, and exit")
    parser.add_argument("--text-max-turns", type=_positive_int, default=None,
                        help="text input bound, 1-1000 (default: 100; blank lines count)")
    args = parser.parse_args(argv)
    if args.text_max_turns is not None and not args.text:
        parser.error("--text-max-turns requires --text")
    if args.startup_check or args.stt_worker:
        if args.config is not None or args.data_dir is not None or args.no_ui or args.no_greeting or args.debug:
            parser.error("--startup-check and --stt-worker do not accept runtime configuration flags")
    if (args.install_startup or args.uninstall_startup or args.mic_test
            or args.export_memory is not None or args.import_memory is not None):
        if args.no_ui or args.no_greeting:
            parser.error("--no-ui/--headless and --no-greeting apply only to agent modes")
    args.text_max_turns = args.text_max_turns or 100
    return args


def startup_check(runtime):
    """Import only modules that neither construct components nor read config."""
    config = importlib.import_module("core.config")
    memory = importlib.import_module("core.memory")
    platform = importlib.import_module("platform_utils")
    if runtime.get_config is not config.get_config or memory.get_config is not config.get_config:
        raise RuntimeError("startup configuration authority mismatch")
    if not callable(runtime.run_agent) or not callable(platform.get_platform):
        raise RuntimeError("startup entry mapping is incomplete")
    print("Startup check OK: dude/dude.py:run_agent; core.config:get_config; core.memory:Memory")
    print("Import/entry mapping only; audio, UI, provider readiness and configuration are not checked.")
    return 0


@contextmanager
def _windowed_stdio(config):
    """Redirect pythonw only on an actual launch, honoring --data-dir."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    stream = None
    try:
        if old_stdout is None or old_stderr is None:
            stream = open(Path(config.data_dir) / "logs" / "stdio.log", "a",
                          encoding="utf-8", errors="replace")
            if old_stdout is None:
                sys.stdout = stream
            if old_stderr is None:
                sys.stderr = stream
        yield
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        if stream is not None:
            stream.close()


def main(argv=None, *, runtime):
    args = parse_args(argv)
    if args.wizard:
        print("DUDE: --wizard is unavailable for the canonical runtime; no settings were written.",
              file=sys.stderr)
        return 2
    handlers = set(logging.getLogger().handlers)
    log_level = logging.getLogger().level
    try:
        if args.startup_check:
            return startup_check(runtime)
        if args.stt_worker:
            from core.stt_worker import main as stt_main
            return stt_main() or 0

        from core.config import configure
        try:
            cfg = configure(path=args.config, data_dir=args.data_dir)
        except (OSError, ValueError, TypeError) as exc:
            # Do not print raw JSON/provider values or attempt startup again.
            print(f"DUDE: invalid configuration ({type(exc).__name__}); check --config and --data-dir.",
                  file=sys.stderr)
            return 2
        with _windowed_stdio(cfg):
            if args.debug:
                logging.getLogger().setLevel(logging.DEBUG)
            return runtime.run_agent(args) or 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"DUDE: startup/runtime failed ({type(exc).__name__}); not restarting.", file=sys.stderr)
        return 1
    finally:
        root = logging.getLogger()
        root.setLevel(log_level)
        for handler in set(root.handlers) - handlers:
            root.removeHandler(handler)
            handler.close()


def _close_brain_clients(brain):
    """Brain owns provider clients but currently exposes no close() method."""
    providers = list(getattr(brain, "providers", ()))
    providers.extend(getattr(brain, "_fallback_cache", {}).values())
    closed = set()
    first_error = None
    for provider in providers:
        client = getattr(provider, "_client", None)
        if client is not None and id(client) not in closed:
            closed.add(id(client))
            try:
                client.close()
            except Exception as exc:
                first_error = first_error or exc
    if first_error is not None:
        raise first_error


def run_text_mode(args):
    """Use canonical conversation storage and tool-free Brain.think only.

    No warm-up, wizard, task engine, watcher, microphone or UI is started.
    Brain/provider construction is delayed until the first nonempty query.
    Provider attempts use Brain's existing finite chain/timeouts; each REPL
    invocation additionally has a finite number of input lines.
    """
    from core.memory import Memory

    memory = Memory()
    brain = None
    try:
        print("DUDE text mode (conversation only; tools disabled). Type 'quit' to exit.")
        for _ in range(args.text_max_turns):
            try:
                query = input("you> ").strip()
            except EOFError:
                return 0
            if query.lower() in ("quit", "exit"):
                return 0
            if not query:
                continue
            if len(query) > 8000:
                raise ValueError("text input exceeds 8000 characters")
            if brain is None:
                from core.brain import Brain
                brain = Brain(memory, lambda _question: False)
            history = memory.recent_messages(limit=8)
            prompt = "Recent conversation:\n" + "\n".join(
                f"{message['role']}: {message['content'][:2000]}" for message in history)
            prompt += "\n\nCurrent user message:\n" + query
            memory.add_message("user", query)
            response = brain.think(prompt, system_extra=(
                "This is conversation-only text mode. No tools, desktop actions, "
                "audio or background tasks are available. Never claim to have performed actions."))
            memory.add_message("assistant", response)
            print(f"dude> {response}")
        print("DUDE text mode input limit reached.")
        return 0
    finally:
        # Attempt both cleanups, preserving the original turn failure if any.
        unwinding = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if brain is not None:
                _close_brain_clients(brain)
        except Exception as exc:
            cleanup_error = exc
        try:
            memory.close()
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None and not unwinding:
            raise cleanup_error

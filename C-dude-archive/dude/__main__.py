"""DUDE entry point.

Usage:
    python -m dude                # run agent with tray UI
    python -m dude --headless     # run agent without UI
    python -m dude --wizard       # run first-run setup wizard
"""
import argparse
import logging
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dude", description="DUDE personal assistant")
    parser.add_argument("--headless", action="store_true",
                        help="run without a UI (tray icon)")
    parser.add_argument("--wizard", action="store_true",
                        help="run the first-run setup wizard")
    parser.add_argument("--config", type=str, default=None,
                        help="path to config.json")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="override data directory for memory")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--text", action="store_true",
                        help="interactive text REPL (for testing without a mic)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from pathlib import Path

    from .config import Config
    config = Config.load(Path(args.config) if args.config else None)

    if args.wizard:
        from .autostart.wizard import FirstRunWizard
        FirstRunWizard(config).run()
        return 0

    from .core.agent import DUDEAgent
    agent = DUDEAgent(config=config, data_dir=args.data_dir)

    if args.text:
        return _text_repl(agent)

    if args.headless:
        agent.start(greet=True)
        return 0

    from .ui.tray import TrayApp
    return TrayApp(agent).run()


def _text_repl(agent) -> int:
    """Minimal text loop so DUDE can be driven without a microphone.

    Type a command; 'quit' exits. Useful for testing on machines without
    audio hardware or for the headless Linux sandbox.
    """
    print("DUDE text mode. Type 'quit' to exit.")
    while True:
        try:
            query = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query:
            continue
        if query.lower() in ("quit", "exit"):
            break
        agent.memory.add_message("user", query, directed=True)
        response = agent.brain.respond(query, directed=True)
        print(f"dude> {response}")
        agent.memory.add_message("assistant", response, directed=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# DUDE v2 — JARVIS-class Personal AI Assistant

A self-learning voice assistant for Windows. Listens continuously (no wake word),
understands anything (LLM brain with tools), speaks naturally (neural TTS),
never interrupts you, lets YOU interrupt IT, remembers everything, tracks your work,
and controls your entire PC.

## Run

```powershell
cd E:\Dude\dude
python dude.py              # full voice mode (mic + speaker)
python dude.py --text       # conversation only; no audio, tools or background workers
python dude.py --no-greeting
python dude.py --install-startup    # auto-start at Windows logon (background)
python dude.py --uninstall-startup
```

From the repository root, `python -m dude` uses the same active runtime as
`python dude/dude.py`; it does not depend on the removed `core/agent.py`.
`--headless` aliases `--no-ui` (microphone remains enabled), and `--verbose`
aliases `--debug`. `--config PATH` selects a self-contained configuration without
merging `config.local.json`; `--data-dir PATH` overrides runtime storage.
The old `--wizard` is explicitly unavailable and exits without writing settings.

### Readiness and bounded validation

Run these from the repository root:

```powershell
python -m dude --startup-check
python -m dude.environment_check
python -m dude.environment_check --json
```

The startup check validates lightweight entry/import wiring without starting
services. The environment checker probes required imports and optional package
availability with per-check timeouts; it does not test hardware or credentials.

For an explicitly requested live component test:

```powershell
python dude/live_validation.py --desktop
python dude/live_validation.py --voice
# Only with permission to activate the microphone:
python dude/live_validation.py --voice --microphone
```

Desktop validation creates its own temporary native window, writes fixed text
via UI Automation, reads/verifies it through DUDE, and closes only that window.
Voice validation sends the fixed phrase "DUDE voice validation complete" to the
configured TTS service and plays it. The optional five-second microphone sample
stays in RAM: it is not saved, uploaded, or transcribed. Each component worker
has a 60-second outer deadline. These checks do not start autonomous workers or
prove full speech recognition, model reasoning, or end-to-end task completion.

First voice run downloads: Silero VAD model (~2 MB) + Whisper `base.en` (~150 MB).

## Architecture

```
core/ear.py        microphone -> Silero VAD -> faster-whisper STT (barge-in aware)
core/voice.py      edge-tts neural voices, streamed per sentence, instantly interruptible
core/brain.py      provider fallback chain (OmniRoute -> OpenRouter -> Codestral -> Ollama)
                   streaming + tool calling loop
core/tools.py      33 system tools: files, apps, shell, input control, screenshots,
                   volume/media, reminders, memory, work reports
core/memory.py     SQLite: conversations, facts about you, work sessions, reminders
core/tracker.py    logs which app/window you use every 10s ("where you stopped")
core/scheduler.py  fires due reminders by voice
dude.py            orchestrator: boot greeting (recaps last work), main loop, shutdown
```

## Provider chain

Reads keys automatically from your vault file (`vault_path` in config.json).
Order: local OmniRoute router (if running) -> OpenRouter -> Codestral ->
OpenAI (disabled, quota) -> local Ollama (`ollama pull qwen2.5:3b` to enable offline mode).

## Notes

- Barge-in works best with headphones; on open speakers DUDE may hear itself.
- Destructive actions (delete, shell) are confirmed by voice first.
- All data stays local in `data/` (memory.db, logs, screenshots).

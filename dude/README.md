# DUDE v2 — JARVIS-class Personal AI Assistant

A self-learning voice assistant for Windows. Listens continuously (no wake word),
understands anything (LLM brain with tools), speaks naturally (neural TTS),
never interrupts you, lets YOU interrupt IT, remembers everything, tracks your work,
and controls your entire PC.

## Run

```powershell
cd E:\Dude\dude
python dude.py              # full voice mode (mic + speaker)
python dude.py --text       # type instead of talk
python dude.py --no-greeting
python dude.py --install-startup    # auto-start at Windows logon (background)
python dude.py --uninstall-startup
```

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

import json


def _listen(ear):
    import time as t

    deadline = t.time() + 25
    while t.time() < deadline:
        item = ear.pop_utterance(block=True, timeout=1.0)
        if not item:
            continue
        kind, payload = item
        if kind == "text" and payload.strip():
            return payload.strip()
    return ""


def _ask(voice, ear, question, fallback_input=None):
    voice.say(question)
    answer = ""
    if ear is not None:
        answer = _listen(ear)
    if not answer and fallback_input is not None:
        try:
            answer = input(f"(type) {question}\n> ").strip()
        except EOFError:
            answer = ""
    return answer


def run_wizard(memory, voice, ear, text_mode=False):
    """Natural first-run conversation. Returns True if it ran."""
    if memory.get_state("wizard_done"):
        return False

    ear_in = None if text_mode else ear
    fallback = "" if ear_in else None
    if text_mode:
        fallback = ""

    def ask(q):
        return _ask(voice, ear_in, q, fallback_input=True)

    name = get_title = ""
    voice.say("Hey! I'm Dude — nice to finally meet you in person.")
    title = ask("What should I call you? You can say a name, or just say sir.")
    title = title.strip().strip(".").capitalize()
    get_title = title or "sir"
    memory.remember_fact(f"The user prefers to be called '{get_title}'.", "personal")

    ans = ask(f"Got it, {get_title}. Should I start automatically whenever this computer "
              f"turns on? Yes or no.")
    autostart = any(w in ans.lower() for w in ("yes", "sure", "yeah", "do it", "ok"))
    from platform_utils import get_platform

    plat = get_platform()
    if autostart:
        import sys as _sys

        if getattr(_sys, "frozen", False):
            exe = _sys.executable
            script = _sys.executable
        else:
            exe = _sys.executable.replace("python.exe", "pythonw.exe")
            script = __file__.replace("core\\wizard.py", "dude.py").replace("core/wizard.py", "dude.py")
        ok = plat.autostart_install([exe, script])
        memory.set_state("autostart", json.dumps(ok))
        voice.say("Done. I'll be here before you even finish your coffee.")

    apps = ask("Last thing for now — which apps do you use every day? Just list them casually.")
    for chunk in apps.replace(",", " ").split():
        pass
    if apps:
        memory.remember_fact(f"Daily apps mentioned during setup: {apps}", "preferences")

    voice.say(f"That's me set up, {get_title}. From now on, just talk to me like you'd "
              f"talk to a person. I'm always listening.")
    memory.set_state("user_title", get_title)
    memory.set_state("wizard_done", "1")
    return True

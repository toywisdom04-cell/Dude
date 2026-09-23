import json
import os
import re
import threading
import time
import datetime

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, OpenAI

from core.config import get_config
from core.personality import build_context, system_prompt
from core.tools import (AGENT_CTX, execute_tool, tool_specs, app_open_block,
                        app_close_then_reopen_block, _reset_app_turn)


class BrainUnavailable(Exception):
    pass


# Live-turn priority (Phase 11 latency): set while a LIVE user turn owns
# the Brain (realtime voice or active user task). Background circuits
# (Thinker/Autopilot/DayLearner/watchers) all funnel through think(),
# which defers a cycle instead of contending with the live user on the
# same provider/key. Functionality preserved — just never starving live.
_LIVE_TURN = threading.Event()


def live_turn_begin():
    _LIVE_TURN.set()


def live_turn_end():
    _LIVE_TURN.clear()


def live_turn_active():
    return _LIVE_TURN.is_set()


CORE_PROBE_TOOLS = {"scroll_screen", "analyze_recent_screens", "ui_scan", "screenshot",
                    "active_window_info", "list_windows"}

# FIX 3: tools that count as "looking at the screen" vs tools that ACT on it.
_SEE_TOOLS = {"ui_scan", "analyze_recent_screens", "screenshot", "find_on_screen",
              "active_window_info", "list_windows"}
_ACT_TOOLS = {"ui_click", "click_at", "click_fraction", "close_app", "close_window",
              "type_text", "window_action", "drag", "hover_at", "press_hotkey"}

_PROMISE_RE = re.compile(
    r"\b(i'?m on it|let me|i will|i'll|i am (about|going)|opening|let's|i'?m looking|"
    r"give me a (moment|second|sec)|right away|on my way|coming up|let me (check|see|try|"
    r"do|fix|open|go)|i'?ll (check|see|try|do|fix|open|go|look|handle|take care)|"
    r"let's (take|get|go|start)|i will (check|see|try|do|fix|open|go|look|handle)|"
    r"i'?m going to|i am going to|doing that now|working on (it|that)|almost done|"
    r"one moment|hang on|patience|just (a|one) (moment|sec|second)|i will get|"
    r"i will figure|i will make|i will take|let me take|let me handle|on it now)\b", re.I)


def _is_promise(content):
    """True if the reply is just a spoken promise/plan with no real action taken."""
    c = (content or "").strip()
    if not c or len(c) < 4:
        return False
    # Longer "i'm on it, let me open the browser" stalling also counts — raise the
    # ceiling from 200 to 320 so DUDE doesn't stall with longer promissory text.
    if len(c) < 320 and _PROMISE_RE.search(c):
        return True
    return False


class _StreamCancelled(Exception):
    pass


class _FirstOutputTimeout(Exception):
    """No token/tool bytes within the live first-output budget."""


# Tool-broker surfaces (section 7): chat gets NO tools (measured: attached
# tools stall/empty weak lanes; chat answers locally or from injected
# memory). Fast/action commands get application/OS capabilities only.
# Deep work keeps the full surface.
_ACTION_TOOL_SUBSET = {
    "open_app", "close_app", "list_running_apps", "active_window_info",
    "list_windows", "window_action", "move_window", "ui_scan", "ui_click",
    "find_on_screen", "click_fraction", "click_at", "type_text",
    "press_hotkey", "scroll_screen", "drag", "hover_at", "screenshot",
    "set_volume", "media_key",
}

# Retained for reference/tests; the chat lane currently sends no tools.
_CHAT_TOOL_SUBSET = {"remember_about_user", "recall_about_user",
                     "get_datetime"}


class Provider:
    def __init__(self, cfg, config):
        self.name = cfg["name"]
        self.base_url = cfg["base_url"]
        self.model = cfg.get("model", "gpt-4o-mini")
        self.timeout = cfg.get("timeout") or int(
            config.get("brain", "provider_timeout", default=35)
        )
        self.vision = bool(cfg.get("vision", False))
        self.chat_enabled = bool(cfg.get("chat", True))
        self.autostart_command = cfg.get("autostart_command") or None
        self.extra_body = dict(cfg.get("extra_body") or {})
        self.first_output_timeout = cfg.get("first_output_timeout")
        self.tool_subset = cfg.get("tool_subset") or None
        connect_timeout = cfg.get("connect_timeout", self.timeout)
        key = ""
        env_name = cfg.get("api_key_env")
        if env_name and os.environ.get(env_name):
            key = os.environ[env_name]
        if not key:
            key = cfg.get("api_key") or ""
        if not key:
            key = config.api_key_for(self.name)
        if not key:
            key = "EMPTY"
        import httpx

        http_client = None
        if cfg.get("browser_ua", False):
            http_client = httpx.Client(
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36"},
                timeout=httpx.Timeout(self.timeout, connect=min(connect_timeout, 10.0)),
            )

        self._client = OpenAI(
            base_url=self.base_url,
            api_key=key,
            timeout=httpx.Timeout(self.timeout, connect=min(connect_timeout, 10.0)),
            max_retries=0,
            http_client=http_client,
        )

    def ok(self):
        return True


class SentenceSplitter:
    _ENDINGS = {".", "!", "?", "…"}

    def __init__(self, min_len=16):
        self.buf = ""
        self.min_len = min_len

    def feed(self, delta):
        self.buf += delta
        out = []
        while True:
            end = None
            for i, ch in enumerate(self.buf):
                if ch in self._ENDINGS:
                    if i + 1 >= len(self.buf):
                        break
                    nxt = self.buf[i + 1]
                    if nxt in (" ", "\n", "\"", "'", ")"):
                        if len(self.buf[:i + 1]) >= self.min_len:
                            end = i
                            break
            if end is None:
                break
            s = self.buf[:end + 1].strip()
            self.buf = self.buf[end + 1:]
            if s:
                out.append(s)
        return out

    def flush(self):
        rest = self.buf.strip()
        self.buf = ""
        return [rest] if rest else []


_VISION_PAUSE_UNTIL = 0.0  # global: no vision calls while a 429 quota lockout is live
_VISION_BUDGET_DAY = ""    # daily vision budget: keeps the Gemini key alive all day
_VISION_BUDGET_LEFT = -1   # instead of burning the whole daily quota in the first hour
_VISION_BUDGET_NOTE = ""


class Brain:
    def __init__(self, memory, ask_user):
        self.cfg = get_config()
        self.memory = memory
        self.ask_user = ask_user
        self.providers = []
        self._fallback_skip = set(self.cfg.get(
            "brain", "fallback_skip",
            default=["nararouter", "freemodel", "9router"]))
        for p in self.cfg.get("brain", "providers", default=[]):
            if not p.get("enabled", True):
                continue
            if p.get("name") in self._fallback_skip:
                continue
            if p.get("api_key"):
                keys = [p["api_key"]]
            else:
                keys = self.cfg.api_keys_for(p["name"]) or ["EMPTY"]
            seen = set()
            variants = 0
            for k in keys:
                if not k or k in seen:
                    continue
                seen.add(k)
                pc = dict(p)
                pc["api_key"] = k
                prov = Provider(pc, self.cfg)
                prov.name = p["name"] + (f"#{variants + 1}" if len(keys) > 1 else "")
                self.providers.append(prov)
                variants += 1
        # Deterministic attempt order: gemini -> aion (experientiallabs) ->
        # ollama-local -> whatever else is enabled. Stable sort keeps config
        # order for ties, so a dead/rate-limited brain never makes DUDE
        # silently degrade to a junk provider.
        prio = self.cfg.get("brain", "fallback_priority",
                            default=["gemini", "experientiallabs", "ollama-local"])
        pidx = {n: i for i, n in enumerate(prio)}
        self.providers.sort(
            key=lambda p: (pidx.get(p.name.rstrip("#0123456789"), 999)))
        self.active_provider = None
        self._lock = threading.Lock()
        self._fail_until = {}
        self._url_cooldown = {}
        self._cancel_requested = False
        self._primary_failed = False
        self._fallback_cache = {}
        self._fallback_order = []
        self._vision_order = []
        self._defs = {p["name"]: p for p in self.cfg.get("brain", "providers", default=[])}
        for p in self._defs.values():
            if p["name"] in self._fallback_skip:
                continue
            local = "11434" in (p.get("base_url", "") or "")
            if p.get("fallback") and not p.get("enabled", True) \
                    and p.get("chat", True) is not False:
                self._fallback_order.append(p["name"])
            if p.get("vision") and not p.get("enabled", True):
                # Cloud vision is fast and reliable; local (Ollama) vision is a slow
                # last resort. Sort NON-local first so DUDE sees quickly and local
                # CPU-backed models are only tried if cloud vision is unavailable.
                self._vision_order.append((1 if local else 0, p["name"]))
        self._vision_order.sort()
        self._vision_names = [n for _, n in self._vision_order]

    def _build_fallback(self, name):
        if name in self._fallback_skip:
            return None
        for p in self.cfg.get("brain", "providers", default=[]):
            if p.get("name") != name:
                continue
            if p.get("api_key"):
                keys = [p["api_key"]]
            elif p.get("api_key_env") and os.environ.get(p["api_key_env"]):
                keys = [os.environ[p["api_key_env"]]]
            else:
                keys = self.cfg.api_keys_for(name) or ["EMPTY"]
            pc = dict(p)
            pc["api_key"] = keys[0]
            prov = Provider(pc, self.cfg)
            prov.name = name
            return prov
        return None

    def _url_cooled(self, url):
        return bool(url and self._url_cooldown.get(url, 0) > time.time())

    def _ollama_warm(self):
        """True if the local live model is already resident (no cold load).
        A cold local model must never own a live lane."""
        try:
            import urllib.request as _u
            import json as _j
            req = _u.Request("http://localhost:11434/api/ps")
            d = _j.load(_u.urlopen(req, timeout=2))
            loaded = {(m.get("name") or "") for m in d.get("models", [])}
            return any("llama3.2:3b" in n or "qwen3:4b" in n for n in loaded)
        except Exception:
            return False

    def _clients(self):
        now = time.time()
        live = bool(getattr(self, "_live_lane", False))
        # Provider order is explicit user policy: NaraRouter, 9Router,
        # OpenRouter pool, then local Ollama last. No local preemption:
        # clouds answer first while healthy; health/cooldown still rule.
        yielded = False
        for p in self.providers:
            deadline = self._fail_until.get(p.name, 0)
            if deadline and now < deadline:
                continue
            if self._url_cooled(p.base_url):
                continue
            yielded = True
            yield p
        if not self._primary_failed and yielded:
            return
        now = time.time()
        for name in self._fallback_order:
            deadline = self._fail_until.get(name, 0)
            if deadline and now < deadline:
                continue
            durl = (self._defs.get(name) or {}).get("base_url")
            if self._url_cooled(durl):
                continue
            if name == "ollama-local" and live and not self._ollama_warm():
                import logging as _lg
                _lg.getLogger("dude").info(
                    "LIVE_PROVIDER_SKIP provider=ollama-local reason=cold")
                continue
            prov = self._fallback_cache.get(name)
            if prov is None:
                prov = self._build_fallback(name)
                if prov is None:
                    continue
                self._fallback_cache[name] = prov
            yield prov

    def _autostart_attempted(self, provider):
        if not provider.autostart_command or getattr(provider, "_autostarted", False):
            return False
        provider._autostarted = True
        try:
            import subprocess

            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", provider.autostart_command],
                creationflags=0x08000000,
            )
            print(f"[brain] launched '{provider.autostart_command}' for {provider.name}; "
                  f"waiting for it to come up...")
            time.sleep(8)
            return True
        except Exception as e:
            print(f"[brain] autostart of {provider.name} failed: {e}")
            return False

    def _cooldown(self, name, secs, kind, url=None):
        """Provider health: mark degraded/rate_limited/unavailable with a
        cooldown. Dynamic routing (_clients) skips cooled-down providers.
        A 429 cools the whole account (same base_url): sibling lanes on
        the same quota are never probed one-by-one."""
        import logging as _lg
        log = _lg.getLogger("dude")
        deadline = time.time() + secs
        prev = self._fail_until.get(name, 0)
        self._fail_until[name] = deadline
        if url:
            self._url_cooldown[url] = deadline
            for p in self.providers:
                if (p.base_url or "") == url and p.name != name:
                    self._fail_until[p.name] = deadline
        if not prev or prev <= time.time():
            log.info("PROVIDER_STATE %s=%s cooldown=%ds", name, kind, secs)
            if url:
                log.info("LIVE_PROVIDER_COOLDOWN provider=%s seconds=%d "
                         "reason=%s", name, secs, kind)

    def _clear_cooldown(self, name):
        if self._fail_until.pop(name, None) is not None:
            import logging as _lg
            _lg.getLogger("dude").info("PROVIDER_STATE %s=healthy", name)
        for p in list(self.providers):
            if p.name == name and (p.base_url or ""):
                self._url_cooldown.pop(p.base_url, None)
        try:
            u = (self._defs.get(name) or {}).get("base_url")
            if u:
                self._url_cooldown.pop(u, None)
        except Exception:
            pass

    def _stream_once(self, provider, messages, on_delta):
        client = provider._client
        specs = tool_specs()
        if getattr(provider, "tool_subset", None):
            allowed = set(provider.tool_subset)
            specs = [s for s in specs if s["function"]["name"] in allowed]
        if getattr(self, "_tool_mode", "") == "minimal":
            # Measured: agnes stalls/empties when tools are attached.
            # Chat answers locally or from memory context; actions run
            # on TaskEngine, deep work keeps the full surface.
            specs = []
        elif getattr(self, "_tool_mode", "") == "action":
            specs = [s for s in specs
                     if s["function"]["name"] in _ACTION_TOOL_SUBSET]
        create_kwargs = dict(
            model=provider.model,
            messages=messages,
            tools=specs,
            temperature=self.cfg.get("brain", "temperature", default=0.6),
            stream=True,
        )
        extra_body = dict(getattr(provider, "extra_body", {}) or {})
        if "qwen3" in (provider.model or "").lower():
            extra_body.setdefault("enable_thinking", False)
        if extra_body:
            create_kwargs["extra_body"] = extra_body
        stream = client.chat.completions.create(**create_kwargs)
        splitter = SentenceSplitter()
        final_text = ""
        tool_calls = {}
        finish_reason = None
        t_start = time.perf_counter()
        per_prov = getattr(provider, "first_output_timeout", None)
        budget = float(per_prov or getattr(self, "_first_output_timeout", 8) or 8)
        got_bytes = False
        for event in stream:
            if self._cancel_requested:
                raise _StreamCancelled()
            if not got_bytes and (time.perf_counter() - t_start) > budget:
                # Fast failure: headers accepted but nothing flows (stalled
                # free tier). Abandon in seconds, fail over — never sit 60s+.
                raise _FirstOutputTimeout(
                    f"no first output in {budget:.0f}s")
            if not event.choices:
                continue
            choice = event.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            d = choice.delta
            if d is None:
                continue
            if d.content:
                final_text += d.content
                got_bytes = True
                for s in splitter.feed(d.content):
                    on_delta(s)
            if d.tool_calls:
                got_bytes = True
                for tc in d.tool_calls:
                    slot = tool_calls.setdefault(tc.index, {
                        "id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        for s in splitter.flush():
            on_delta(s)

        final_text = re.sub(r"<think>.*?</think>", "", final_text, flags=re.DOTALL).strip()
        
        # Strip qwen thinking remnants before they reach TTS ('??', ' ** ', lone punct).
        final_text = re.sub(r"[?.,!;]{2,}", " ?", final_text)
        final_text = re.sub(r"\s*\*\*+\s*", " ", final_text).strip()
        final_text = re.sub(r"[?.,!;]{2,}", "", final_text).strip()
        if tool_calls:
            msg = {"role": "assistant", "content": final_text or None, "tool_calls": []}
            for idx in sorted(tool_calls):
                tc = tool_calls[idx]
                msg["tool_calls"].append({
                    "id": tc["id"] or f"call_{idx}",
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })
            return msg, True

        return {"role": "assistant", "content": final_text}, False

    def cancel_stream(self):
        self._cancel_requested = True

    def _record_experience(self, tool, args, result):
        try:
            learner = AGENT_CTX.get("learner")
            if learner is None:
                return
            observer = AGENT_CTX.get("observer")
            app, window = "", ""
            if observer is not None:
                app, window = learner.screen_signal(observer)
            outcome = learner.classify(result)
            learner.record(tool, args, outcome=outcome,
                           detail=(result or "")[:300], app=app, window=window)
        except Exception:
            pass

    def chat(self, user_text, on_delta=None, on_tool=None, system_extra="",
             fast=False, mode="deep", hop_cap=None, prompt_mode="full",
             voice_label="", on_provider=None):
        on_delta = on_delta or (lambda s: None)
        on_tool = on_tool or (lambda _name: None)
        on_provider = on_provider or (lambda _name, _n: None)
        self._cancel_requested = False
        if fast:
            mode = "fast"
        # Live lanes (fast/chat) route before generation: at most 2
        # provider attempts, then one clean unavailable reply. Deep work
        # may walk the whole chain. Never accumulate a minute of probing.
        self._live_lane = mode in ("fast", "chat")
        self._tool_mode = ("minimal" if mode == "chat"
                           else ("action" if mode == "fast" else "default"))
        live_cap = 2 if self._live_lane else 999
        live_attempts = 0
        # First-output budget per lane: live turns abandon a stalled
        # provider in seconds (then fail over); deep work gets longer.
        self._first_output_timeout = {"fast": 6, "chat": 8}.get(mode, 20)
        # Hop budget matches task complexity: fast 3, conversational 6,
        # deep configurable (default 10). Ordinary live turns can never
        # wander a long tool loop; hard stop preserved in-loop.
        history = self.memory.recent_messages(
            limit=self.cfg.get("brain", "history_turns", default=24))
        ctx = build_context(self.memory)
        if prompt_mode == "compact":
            history = history[-4:]
            base = system_prompt(compact=True, voice_label=voice_label)
        else:
            base = system_prompt()
        if history:
            base = base + ("\n\nCONVERSATION: read the recent history above carefully. "
                           "A new short request nearly always continues the same ongoing "
                           "task or topic — connect it to your previous commands and answers, "
                           "and use recall_about_user for older context when it would help.")
        if system_extra:
            base = base + "\n\n" + system_extra
        if fast:
            base = base + ("\n\nFAST ACTION MODE: this is a simple command or "
                           "quick question. Act first with tools if needed, "
                           "then reply in ONE short spoken sentence. No "
                           "preamble, no explanation, no essay. To act, you "
                           "may instead reply with exactly one line like: "
                           "ACTION: OPEN_APP Calculator")
        messages = [
            {"role": "system", "content": base + "\n\n" + ctx},
        ] + history + [{"role": "user", "content": user_text}]

        last_err = None
        self._primary_failed = False
        _reset_app_turn()   # FIX 1: fresh app-open/close state per user turn
        for provider in self._clients():
            live_attempts += 1
            if live_attempts > live_cap:
                last_err = BrainUnavailable(
                    "live failover budget spent (2 attempts)")
                break
            try:
                on_provider(provider.name, live_attempts)
            except Exception:
                pass
            spoke_count = {"n": 0}

            def counting_delta(sentence):
                spoke_count["n"] += 1
                on_delta(sentence)

            while True:
                try:
                    hops = 0
                    tools_run = 0
                    budget_warned = False
                    if hop_cap is not None:
                        max_hops = hop_cap
                    elif mode == "fast":
                        max_hops = 3
                    elif mode == "chat":
                        max_hops = 10
                    else:
                        max_hops = self.cfg.get(
                            "brain", "max_tool_hops", default=10)
                    local_messages = list(messages)
                    empty_streak = 0
                    wrap_tried = False
                    executed_tools = set()
                    probe_streak = 0
                    probe_intervened = False
                    promise_streak = 0
                    seen_screen = False
                    last_seen_ts = 0.0
                    repeat_last = None
                    repeat_count = 0
                    repeat_intervened = False
                    act_since_check = 0
                    checks_in_row = 0
                    while True:
                        with self._lock:
                            assistant_msg, has_tools = self._stream_once(provider, local_messages, counting_delta)
                        local_messages.append(assistant_msg)
                        if not has_tools:
                            content = (assistant_msg.get("content") or "").strip()
                            ran_tools = any(m.get("role") == "tool" for m in local_messages)
                            if not content and ran_tools and not wrap_tried:
                                # Model finished the tool work but never spoke a wrap-up,
                                # so we force one - otherwise DUDE sits silent and shows
                                # "(tool actions only)".
                                wrap_tried = True
                                local_messages.append({
                                    "role": "user",
                                    "content": ("You just completed the task. Wrap up like a "
                                                "human in ONE short spoken sentence (no bullets, "
                                                "no lists): tell him exactly what you did."),
                                })
                                continue
                            if not content and spoke_count["n"] == 0:
                                print(f"[brain] {provider.name} returned empty response; skipping provider")
                                last_err = BrainUnavailable("empty")
                                self._primary_failed = True
                                self._cooldown(provider.name, 45, "empty")
                                break
                            if content or spoke_count["n"]:
                                # PROMISE-GUARD: if the model only SPOKE a plan/promise
                                # ("I'm on it", "let me open...") but did not make progress,
                                # don't accept that as done — push it to carry the action
                                # out, and after repeated stalls force execution or honesty.
                                if (not ran_tools or (ran_tools and _is_promise(content))) \
                                        and not wrap_tried and _is_promise(content):
                                    promise_streak += 1
                                    wrap_tried = True
                                    if promise_streak >= 3:
                                        local_messages.append({
                                            "role": "user",
                                            "content": ("STOP! You have now said you would do it "
                                                        "THREE times and taken NO meaningful "
                                                        "action. Do NOT speak again. Take ONE "
                                                        "concrete tool action right now "
                                                        "(open_app / open_url / ui_scan / "
                                                        "find_on_screen / click_fraction / "
                                                        "run_powershell / type_text). If you "
                                                        "genuinely cannot perform this task "
                                                        "autonomously, reply with EXACTLY: "
                                                        "'I cannot do this autonomously.'"),
                                        })
                                    else:
                                        local_messages.append({
                                            "role": "user",
                                            "content": ("You said you would do it but have not "
                                                        "made progress. Do not just speak "
                                                        "again — actually perform the task now "
                                                        "(open_url / open_app / ui_scan / click / "
                                                        "scroll / type) using the tools, then give "
                                                        "a one-line summary. No more promises."),
                                        })
                                    continue
                                self.active_provider = provider
                                self._clear_cooldown(provider.name)
                                return content
                        hops += 1
                        if hops > max_hops:
                            # Safety bound stays (runaway/cost protection),
                            # but the user is told a one-line status only —
                            # never a problem dump, never begging. Details
                            # stay in logs/state for the next turn.
                            on_delta("Still working on it, sir.")
                            return ""
                        for tc in assistant_msg["tool_calls"]:
                            on_tool(tc["function"]["name"])
                            try:
                                key = (tc["function"]["name"],
                                       json.dumps(json.loads(tc["function"]["arguments"] or "{}"),
                                                  sort_keys=True))
                            except Exception:
                                key = (tc["function"]["name"], tc["function"]["arguments"])
                            if key in executed_tools:
                                result = ("SKIPPED (duplicate): you already ran this exact tool "
                                          "call earlier this turn. Don't call it again — use the "
                                          "result already returned, or act differently.")
                            else:
                                executed_tools.add(key)
                                # FIX 1: anti open/reopen and close->reopen guard.
                                # A weak model loops opening and closing the same app
                                # (e.g. Comet) and spawns duplicates. Intercept the
                                # harmful call BEFORE it runs and steer it to focus.
                                _fn = tc["function"]["name"]
                                if _fn == "open_app":
                                    g = app_close_then_reopen_block(
                                        tc["function"]["arguments"]) or \
                                        app_open_block(tc["function"]["arguments"])
                                    if g:
                                        result = ("SKIPPED (guard): " + g)
                                        local_messages.append({
                                            "role": "tool",
                                            "tool_call_id": tc["id"],
                                            "content": result,
                                        })
                                        continue
                                # FIX 3: SEE before ACT. The CURRENT SCREEN block is only
                                # useful if the model actually looks. Block blind acting
                                # on screen (closing, clicking, typing, dragging) until it
                                # has taken a look this turn. This stops the "blindly close
                                # and reopen Comet" cycle.
                                if _fn in _SEE_TOOLS:
                                    seen_screen = True
                                    last_seen_ts = time.time()
                                elif _fn in _ACT_TOOLS:
                                    # FIX 3 + STALENESS: never act on a screen description
                                    # that could be minutes out of date. If the last real
                                    # look was too long ago (user may have switched apps /
                                    # tabs / windows), force a fresh look before acting so
                                    # DUDE never clicks a window that is no longer in front.
                                    stale = (time.time() - last_seen_ts) > 25.0 if last_seen_ts else True
                                    if (not seen_screen) or stale:
                                        result = ("SKIPPED (guard): this screen info may be "
                                                  "from earlier / out of date. The user may "
                                                  "have switched apps, tabs or windows since "
                                                  "you looked. Re-check what is on screen "
                                                  "RIGHT NOW with analyze_recent_screens or "
                                                  "find_on_screen (and check active_window_info "
                                                  "for the real foreground app) before you "
                                                  "click/close/type again.")
                                        local_messages.append({
                                            "role": "tool",
                                            "tool_call_id": tc["id"],
                                            "content": result,
                                        })
                                        continue
                                result = execute_tool(
                                    _fn,
                                    tc["function"]["arguments"],
                                    self.memory,
                                    self.ask_user,
                                )
                                tools_run += 1
                            # LEARNING CIRCUIT: record this action + its outcome and the
                            # screen it happened on, so next turns are shaped by what
                            # actually worked (or kept failing).
                            self._record_experience(
                                tc["function"]["name"],
                                tc["function"]["arguments"],
                                result,
                            )
                            local_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result,
                            })
                        # Live tool budget: weak models wander through tools
                        # instead of answering. Cap executed tools per live
                        # turn, then force a spoken finish (once).
                        if mode in ("fast", "chat") and not budget_warned:
                            _tblimit = 3 if mode == "fast" else 8
                            if tools_run >= _tblimit:
                                budget_warned = True
                                local_messages.append({
                                    "role": "user",
                                    "content": ("You have used enough tools. "
                                                "FINISH now with ONE short "
                                                "spoken summary. Do not call "
                                                "any more tools."),
                                })
                        # Self-monitoring: catch a mindless loop and STOP it from burning
                        # hops — whether it is blind probing (scroll/scan only) or the
                        # SAME tool repeated over and over (any args). Interrupt once.
                        action_names = [tc["function"]["name"]
                                        for tc in assistant_msg["tool_calls"]]
                        if action_names and all(n in CORE_PROBE_TOOLS for n in action_names):
                            probe_streak += len(action_names)
                        else:
                            probe_streak = 0
                        if action_names:
                            last = action_names[-1]
                            if last == repeat_last:
                                repeat_count += 1
                            else:
                                repeat_last = last
                                repeat_count = 1
                        # OVER-VERIFICATION GUARD: after a real action (open/click/type)
                        # already changed the screen, the model should not burn 4+ hops in a
                        # row purely checking (active_window_info / list_windows / ui_scan /
                        # analyze_recent_screens / run_powershell status checks) without a
                        # NEW action. That is the "redundant PowerShell checks" waste loop
                        # DUDE admitted to. Track checks-in-a-row since the last real action.
                        if action_names and all(n not in _ACT_TOOLS and n != "open_app"
                                                and n != "open_url" and n != "run_powershell"
                                                for n in action_names):
                            checks_in_row += len(action_names)
                        else:
                            checks_in_row = 0
                        reason = ""
                        if checks_in_row >= 4:
                            reason = ("you have done 4+ checks in a row (verifying/listing/"
                                      "re-reading) with NO new action. If the task's goal is "
                                      "already met, FINISH now with a short summary. Do not "
                                      "keep checking the same thing.")
                        elif probe_streak >= 3:
                            reason = ("you have only been probing (scroll/screenshot/scan) "
                                      "without doing anything else for a while")
                        elif repeat_count >= 3:
                            reason = (f"you keep calling the SAME tool ({repeat_last}) over "
                                      "and over without progress")
                        if reason and not probe_intervened:
                            probe_intervened = True
                            local_messages.append({
                                "role": "user",
                                "content": ("DUDE SELF-CHECK: STOP what you are doing. "
                                            + reason.capitalize() + ". That is exactly how "
                                            "you waste time and hit the tool-use limit. "
                                            "THINK before your next move: look at the CURRENT "
                                            "SCREEN map and your last screenshot description — "
                                            "check the scrollbar position, the nav menu and "
                                            "pagination links. Then take ONE new, deliberate "
                                            "action — a precise ui_click on a real control or "
                                            "a typed term — or, if you already have what he "
                                            "asked for (or the page has not changed), summarize "
                                            "what you learned and FINISH now. Do NOT call that "
                                            "same tool again."),
                            })
                    break
                except APIConnectionError as e:
                    last_err = e
                    self._primary_failed = True
                    self._cooldown(provider.name, 60, "unavailable")
                    break  # fail OVER to the next provider — never spin on the same one
                except AuthenticationError as e:
                    last_err = e
                    self._primary_failed = True
                    self._cooldown(provider.name, 300, "auth")
                    break  # fail over to the next provider
                except (APITimeoutError, APIStatusError) as e:
                    last_err = e
                    self._primary_failed = True
                    code = getattr(e, "status_code", 0) or 0
                    if code in (401, 403):
                        self._cooldown(provider.name, 300, "auth")
                    elif code == 429:
                        self._cooldown(provider.name, 180, "rate_limited",
                                       url=provider.base_url)
                    else:
                        self._cooldown(provider.name, 60, "timeout")
                    break  # try the NEXT provider so a dead/rate-limited
                           # provider never takes DUDE down (a 'continue' here
                           # used to retry the SAME provider forever -> 429 spam
                           # storm and a turn that never answers)
                except _FirstOutputTimeout as e:
                    last_err = e
                    self._primary_failed = True
                    self._cooldown(provider.name, 45, "stalled")
                    break  # stalled stream: fail over in seconds
                except _StreamCancelled:
                    return ""
                except Exception as e:
                    last_err = e
                    self._primary_failed = True
                    self._cooldown(provider.name, 30, "error")
                    break  # fail over to the next provider
        raise BrainUnavailable(str(last_err))

    def think(self, prompt, system_extra=""):
        """Non-streaming helper for internal summaries.

        Same per-provider backoff as chat(): a provider that fails here goes
        into _fail_until so the background circuits (Thinker, Autopilot,
        DayLearner, Experience watcher) stop hammering a dead or rate-limited
        brain every cycle. Without this, one quota death became 300+ 429s/day
        and each retry added 1-2s of dead time in front of every answer.
        Live-turn priority: a background think never contends with an active
        live user turn on the same provider — it defers this cycle.
        """
        if live_turn_active():
            raise BrainUnavailable("deferred: live turn active")
        messages = [
            {"role": "system", "content": system_prompt() + ("\n\n" + system_extra if system_extra else "")},
            {"role": "user", "content": prompt},
        ]
        last_err = None
        self._primary_failed = False
        for provider in self._clients():
            try:
                r = provider._client.chat.completions.create(
                    model=provider.model,
                    messages=messages,
                    temperature=0.4,
                    extra_body=dict(getattr(provider, "extra_body", {}) or {}) or None,
                )
                self.active_provider = provider
                self._clear_cooldown(provider.name)
                return r.choices[0].message.content or ""
            except AuthenticationError as e:
                last_err = e
                self._primary_failed = True
                self._cooldown(provider.name, 30, "error")
            except (APITimeoutError, APIStatusError) as e:
                last_err = e
                self._primary_failed = True
                code = getattr(e, "status_code", 0) or 0
                if code in (401, 403):
                    self._cooldown(provider.name, 300, "auth")
                elif code == 429:
                    self._cooldown(provider.name, 180, "rate_limited")
                else:
                    self._cooldown(provider.name, 60, "timeout")
            except Exception as e:
                last_err = e
                self._primary_failed = True
                self._cooldown(provider.name, 30, "error")
        raise BrainUnavailable(str(last_err))

    def has_vision(self):
        if any(p.vision for p in self.providers):
            return True
        return bool(self._vision_names)

    def warm_llm(self):
        """Preload the local ollama model at boot so the first real question does
        not pay the cold-load cost (minutes of silence while llama-server loads
        a 2.5GB model into RAM). Mirrors ear.warm_start: a tiny prompt is enough
        to pull the model resident; with OLLAMA_KEEP_ALIVE it stays loaded.
        Any failure here is logged immediately, not silently at first query.
        """
        target = None
        for pname in ("ollama-local",):
            try:
                cand = self._build_fallback(pname)
            except Exception:
                cand = None
            if cand is not None:
                target = cand
                break
        if target is None:
            for p in self.providers:
                if "11434" in (p.base_url or "") and "ollama" in p.name.lower():
                    target = p
                    break
        if target is None:
            # Ollama lives in the fallback chain: build it on demand so
            # the local model still preloads at boot.
            try:
                target = self._build_fallback("ollama-local")
            except Exception:
                target = None
        if target is None:
            print("[brain] warm_llm: no local ollama provider in the active chain; skipping")
            return False
        try:
            print(f"[brain] warm_llm: preloading {target.model} ...")
            r = target._client.chat.completions.create(
                model=target.model,
                messages=[{"role": "user", "content": "reply with one word: ready"}],
                temperature=0,
                stream=False,
                max_tokens=5,
                timeout=60,
                extra_body=dict(target.extra_body or {}),
            )
            self._fail_until.pop(target.name, None)
            print(f"[brain] warm_llm: {target.model} loaded OK")
            return True
        except Exception as e:
            self._fail_until[target.name] = time.time() + 30
            print(f"[brain] warm_llm preload failed (transient ok): {e}")
            return False

    def _vision_clients(self):
        for p in self.providers:
            if p.vision:
                yield p
        for name in self._vision_names:
            prov = self._fallback_cache.get(name)
            if prov is None:
                prov = self._build_fallback(name)
                if prov is None:
                    continue
                self._fallback_cache[name] = prov
            yield prov

    def vision_analyze(self, image_b64, prompt):
        global _VISION_PAUSE_UNTIL, _VISION_BUDGET_DAY, _VISION_BUDGET_LEFT, _VISION_BUDGET_NOTE
        now0 = time.time()
        if _VISION_PAUSE_UNTIL and now0 < _VISION_PAUSE_UNTIL:
            # Quota lockout active: do not even try — keeps DUDE responsive and
            # frees the API key for the text brain. Raw OCR observation continues.
            return ""
        day = datetime.date.today().isoformat()
        if _VISION_BUDGET_DAY != day:
            _VISION_BUDGET_DAY = day
            _VISION_BUDGET_LEFT = int(get_config().get(
                "ambient", "daily_vision_budget", default=150))
        if _VISION_BUDGET_LEFT <= 0:
            if _VISION_BUDGET_NOTE != _VISION_BUDGET_DAY:
                _VISION_BUDGET_NOTE = _VISION_BUDGET_DAY
                print("[vision] daily vision budget reached — using local OCR "
                      "understanding for the rest of the day (quota keep-alive).")
            return ""
        _VISION_BUDGET_LEFT -= 1
        data_uri = f"data:image/jpeg;base64,{image_b64}"
        for provider in self._vision_clients():
            # Retry once per provider so a transient router/network blip does NOT
            # silently turn into "DUDE cannot see" (empty result). Log the failure.
            for attempt in (1, 2):
                try:
                    r = provider._client.chat.completions.create(
                        model=provider.model,
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url",
                                 "image_url": {"url": data_uri}},
                            ],
                        }],
                        max_tokens=500,
                        extra_body=dict(getattr(provider, "extra_body", {}) or {}) or None,
                    )
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        return text
                except Exception as e:
                    code = getattr(e, "status_code", 0) or 0
                    estr = str(e)
                    if code == 429 or "RateLimitError" in type(e).__name__ or "429" in estr[:120]:
                        # QUOTA EXHAUSTED: stop hammering. Pause ALL vision for 5 min,
                        # log exactly once, return fast so DUDE stays responsive.
                        self._fail_until[provider.name] = now0 + 300
                        if now0 >= _VISION_PAUSE_UNTIL:
                            _VISION_PAUSE_UNTIL = now0 + 300
                            print("[vision] gemini quota exceeded (429) — pausing ALL "
                                  "vision analysis for 5 min to stop the flood and keep "
                                  "the text brain alive.")
                        return ""
                    print(f"[vision] {provider.name} attempt {attempt} failed: "
                          f"{type(e).__name__}: {estr[:160]}")
                    if attempt == 1:
                        time.sleep(1.5)
        return ""

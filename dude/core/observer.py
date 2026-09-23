import base64
import datetime
import io
import os
import re
import threading
import time

from core.config import get_config
from platform_utils import get_platform


class Observer:
    """Watches the screen while the user works and learns what they do.
    Captures downscaled screenshots on a timer; every analysis interval it
    sends recent shots to the vision brain and stores durable insights."""

    def __init__(self, memory, brain):
        self.memory = memory
        self.brain = brain
        self.platform = get_platform()
        cfg = get_config()
        self.enabled = bool(cfg.get("ambient", "observer", default=True))
        self.capture_seconds = int(cfg.get("ambient", "capture_seconds", default=240))
        self.analysis_minutes = int(cfg.get("ambient", "analysis_minutes", default=30))
        self.max_screens = int(cfg.get("ambient", "max_screens", default=60))
        self.vision_gap = int(cfg.get("ambient", "vision_gap_seconds", default=20))
        self.shots_dir = os.path.join(cfg.data_dir, "observation")
        os.makedirs(self.shots_dir, exist_ok=True)
        self._stop = threading.Event()
        self._conserving = False
        self._last_capture = 0
        self._last_analysis = time.time()
        self._last_title = ""
        self._thread = None
        self._lock = threading.Lock()
        self._latest_shot = ""
        self._desc = ""
        self._desc_src = ""
        self._desc_at = 0
        self._ocr = ""
        self._ocr_at = 0
        self._ocr_src = ""
        if self.enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="du-observer")
            self._thread.start()
            print("[observer] screen learning active "
                  f"(capture {self.capture_seconds}s, analyze {self.analysis_minutes}m)")

    def _battery_ok(self):
        try:
            import psutil

            b = psutil.sensors_battery()
            if b is None or b.power_plugged:
                return True
            return b.percent > 25
        except Exception:
            return True

    def _loop(self):
        while not self._stop.wait(timeout=2):
            try:
                now = time.time()
                if not self._battery_ok():
                    continue
                if now - self._last_capture >= self.capture_seconds:
                    self._last_capture = now
                    title_changed = self._capture()
                    ocr_changed = self._ocr_refresh()
                    # Re-describe the screen when the user switched app, OR the
                    # visible text actually changed (typing / new content / new
                    # page). The BOOLEAN `stale` only forces re-analysis while
                    # vision is healthy; in conservation mode (quota/budget out)
                    # we do the LOCAL understanding instead and stay quiet on
                    # unchanged screens so the API key lasts the day.
                    stale = (now - self._desc_at) > 90 and not self._conserving
                    if (title_changed or ocr_changed or stale) and \
                            (now - self._desc_at) >= self.vision_gap:
                        self._analyze_fresh()
                if (now - self._last_analysis) >= self.analysis_minutes * 60:
                    self._last_analysis = now
                    learned = self.analyze_recent()
                    if learned:
                        print(f"[observer] learned {learned} new insight(s)")
            except Exception as e:
                print(f"[observer] {e}")

    def _capture(self):
        try:
            from PIL import Image
            from core.tools import _capture_screen_composite

            w = self.platform.foreground_window()
            title_changed = w["title"] != self._last_title
            self._last_title = w["title"]
            img, _ = _capture_screen_composite()
            img.thumbnail((1280, 1280))
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"obs_{ts}{'_c' if title_changed else ''}.jpg"
            path = os.path.join(self.shots_dir, name)
            img.convert("RGB").save(path, "JPEG", quality=55)
            with self._lock:
                self._latest_shot = name
            self._prune()
            return title_changed
        except Exception as e:
            print(f"[observer] capture failed: {e}")
            return False

    def _ocr_refresh(self):
        """Read the current screen's visible text with tesseract. Returns True
        when the text changed significantly (that is when vision re-analysis is
        worth it). Kept cheap: OCR only, no model round-trip."""
        try:
            from PIL import Image
            from core.ocr import ocr_image

            with self._lock:
                shot = self._latest_shot
            if not shot:
                return False
            path = os.path.join(self.shots_dir, shot)
            if not os.path.exists(path):
                return False
            im = Image.open(path).convert("RGB")
            im.thumbnail((1280, 1280))
            text = ocr_image(im)
            cleaned = " ".join(text.split())[:1200] if text else ""
            with self._lock:
                prev = self._ocr
                self._ocr = cleaned
                self._ocr_at = time.time()
                self._ocr_src = shot
            if not cleaned or not prev:
                return bool(cleaned)
            a = set(cleaned.split())
            b = set(prev.split())
            if not a or not b:
                return True
            return len(a.symmetric_difference(b)) / max(len(a), len(b)) > 0.4
        except Exception as e:
            print(f"[observer] ocr failed: {e}")
            return False

    def _record_semantics(self, viewer_out, ocr_text):
        """Understand the CONTENT of what is on screen (topic/tags/goal) and
        store it in the associative-memory link graph. Runs with vision when
        available and with a local fallback when vision is quota-paused."""
        try:
            from core.associations import get_associations
            ab = get_associations()
        except Exception:
            return
        try:
            w = self.platform.foreground_window()
            app = w.get("app", "unknown")
            window = w.get("title", "")
        except Exception:
            app, window = "unknown", ""
        ab.record_analytics(app=app, window=window, ocr_text=ocr_text or "",
                            viewer_out=viewer_out)

    def _local_screen_lines(self, ocr_text):
        """Understand the screen fully LOCALLY (no API) and return plain
        'TOPIC:/GOAL:/SUMMARY'-style lines, so seeing + note-taking survive a
        quota-exhausted day."""
        try:
            from core.associations import get_associations
            w = self.platform.foreground_window()
            app = w.get("app", "unknown")
            window = w.get("title", "")
            u = get_associations().understand_local(app, window, ocr_text or "")
        except Exception as e:
            print(f"[observer] local understand failed: {e}")
            return ""
        lines = []
        for k in ("app", "window", "topic", "goal"):
            v = (u.get(k) or "").strip()
            if v:
                lines.append(f"{k.upper()}: {v}")
        summ = (u.get("summary") or "").strip()
        if summ:
            lines.append(summ)
        return "\n".join(lines)

    def _persist_content(self, out_text, ocr_text):
        """Store the screen's CONTENT (not just app names) into permanent
        memory: deduped screen_learning facts plus an append-only daily OCR
        note in the searchable knowledge vault."""
        try:
            from core.knowledge import get_knowledge
            kb = get_knowledge()
        except Exception:
            kb = None
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.date.today().strftime("%Y-%m-%d")
        added = 0
        kb_lines = []
        for line in (out_text or "").splitlines():
            line = line.strip().lstrip("-*0123456789. ")
            if len(line) < 12 or len(line) > 300:
                continue
            if re.match(r"^(APP|WINDOW|TOPIC|GOAL|TAGS):", line, re.I):
                continue
            dupe = False
            try:
                for e in self.memory.recall_facts(line[:50], limit=4):
                    if line[:70].lower() in e.lower():
                        dupe = True
                        break
            except Exception:
                pass
            if not dupe:
                try:
                    self.memory.remember_fact(f"[screen {stamp}] {line}",
                                              category="screen_learning")
                    added += 1
                except Exception:
                    pass
            kb_lines.append(f"({stamp}) {line}")
        if ocr_text and len(ocr_text.split()) >= 4:
            kb_lines.append(f"({stamp}) [text-on-screen] "
                            f"{' '.join(ocr_text.split())[:300]}")
        if kb and kb_lines:
            try:
                kb.append_daily_note(f"screen-ocr-{today}", kb_lines)
            except Exception:
                pass
        return added

    def _analyze_fresh(self, ocr_text=None):
        try:
            if not self.brain.has_vision():
                # No vision backend: still understand locally from OCR +
                # associations so description is never permanently empty.
                out = self._local_screen_lines(ocr_text or self._ocr)
                if out:
                    with self._lock:
                        self._desc = out.strip()
                        self._desc_src = self._latest_shot
                        self._desc_at = time.time()
                return
            with self._lock:
                shot = self._latest_shot
                if ocr_text is None:
                    ocr_text = self._ocr
            if not shot:
                return
            from PIL import Image

            im = Image.open(os.path.join(self.shots_dir, shot)).convert("RGB")
            im.thumbnail((1280, 1280))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=60)
            b64 = base64.b64encode(buf.getvalue()).decode()
            ocr_part = (f"EXACT VISIBLE TEXT ON SCREEN (OCR):\n"
                        f"{' '.join((ocr_text or '').split())[:1200]}\n\n"
                        if ocr_text else "")
            prompt = ("This is the user's LIVE screen right now. The WORK CONTENT "
                      "matters most: read and understand what is actually on the "
                      "screen. In 2-4 short plain lines: which app/window, WHAT THE "
                      "USER IS DOING, and the real content visible — documents, "
                      "values, fields, dialogs, buttons, the text that matters. "
                      "Do not just name the app; describe the substance.\n\n" +
                      ocr_part)
            out = self.brain.vision_analyze(b64, prompt)
            if not out:
                # vision paused / quota-locked / budget used up: STILL understand
                # the screen with the local extractive reader so real content,
                # digests and notes keep coming all day. Enter conservation mode
                # so unchanged screens stop bumping the API.
                self._conserving = True
                out = self._local_screen_lines(ocr_text or self._ocr)
                if self.vision_gap < 120:
                    self.vision_gap = 120
                    print("[observer] conservation mode: vision gap widened to "
                          f"{self.vision_gap}s, local OCR understanding active")
            if out:
                with self._lock:
                    self._desc = out.strip()
                    self._desc_src = shot
                    self._desc_at = time.time()
                local_only = self._conserving
                try:
                    self._persist_content(out, ocr_text)
                except Exception as e:
                    print(f"[observer] persist content failed: {e}")
                self._record_semantics(None if local_only else out, ocr_text)
        except Exception as e:
            print(f"[observer] live analyze failed: {e}")

    def _prune(self):
        try:
            files = sorted(f for f in os.listdir(self.shots_dir) if f.endswith(".jpg"))
            excess = len(files) - self.max_screens
            for fn in files[:max(0, excess)]:
                os.remove(os.path.join(self.shots_dir, fn))
        except OSError:
            pass

    def analyze_recent(self, count=2):
        files = sorted(f for f in os.listdir(self.shots_dir) if f.endswith(".jpg"))
        if not files:
            return 0
        recent = files[-count:]
        from PIL import Image

        imgs = []
        for fn in recent:
            im = Image.open(os.path.join(self.shots_dir, fn))
            im.thumbnail((640, 640))
            imgs.append(im)
        w = max(im.width for im in imgs)
        h = max(im.height for im in imgs)
        cols = 2 if len(imgs) > 1 else 1
        rows = (len(imgs) + 1) // 2
        grid = Image.new("RGB", (w * cols, h * rows), "black")
        for i, im in enumerate(imgs):
            grid.paste(im, ((i % cols) * w, (i // cols) * h))
        buf = io.BytesIO()
        grid.save(buf, "JPEG", quality=60)
        b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = (
            "These are sequential screenshots from one user's desktop work. Infer durable facts " + 
            "and possible repeatable workflows. Focus on app order, repeated task sequences, " + 
            "documents used, and safe automation opportunities. For a repeatable sequence, write " + 
            "one line beginning WORKFLOW: followed by a short name and the observed steps. " + 
            "Never guess missing steps and never recommend sending, publishing, deleting, paying, " + 
            "or submitting anything automatically. Maximum 3 short plain lines; no preamble."
        )
        out = self.brain.vision_analyze(b64, prompt)
        if not out:
            return 0
        with self._lock:
            self._desc = out.strip()
            self._desc_src = ",".join(recent)
            self._desc_at = time.time()
        added = 0
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for line in out.splitlines():
            line = line.strip().lstrip("-*0123456789. ")
            if len(line) < 12 or len(line) > 250:
                continue
            fact = f"[screen {stamp}] {line}"
            dupe = False
            for e in self.memory.recall_facts(line[:50], limit=4):
                if line[:70].lower() in e.lower():
                    dupe = True
                    break
            if not dupe:
                category = "workflow_candidate" if line.upper().startswith("WORKFLOW:") else "screen_learning"
                self.memory.remember_fact(fact, category=category)
                added += 1
        if added:
            try:
                from core.knowledge import get_knowledge
                today = datetime.date.today().strftime("%Y-%m-%d")
                kb = get_knowledge()
                kb.append_daily_note(f"screen-learning-{today}",
                                     [f"({stamp}) {l.strip().lstrip('-*0123456789. ')}"
                                      for l in out.splitlines() if len(l.strip()) >= 12])
            except Exception:
                pass
        return added

    def refresh_snapshot(self, timeout_s=12.0):
        """Targeted observation on demand: capture + OCR synchronously and
        return a fresh current_screen(). Used for screen questions so the
        answer comes from evidence taken for that question, not a stale
        cache. Bounded by timeout; never raises."""
        t0 = time.time()
        try:
            self._capture()
            if time.time() - t0 < timeout_s:
                self._ocr_refresh()
        except Exception as e:
            print(f"[observer] refresh failed: {e}")
        try:
            return self.current_screen()
        except Exception:
            return {}

    def current_screen(self):
        """Cheap, always-available snapshot of what is on screen right now:
        the active window plus the freshest vision description (if any) and the
        visible text read by OCR."""
        try:
            w = self.platform.foreground_window()
        except Exception:
            w = {"app": "unknown", "title": "unknown"}
        with self._lock:
            shot = self._latest_shot or ""
            desc = self._desc
            age = int((time.time() - self._desc_at) / 60) if self._desc_at else None
            ocr = self._ocr
            ocr_age = int((time.time() - self._ocr_at) / 60) if self._ocr_at else None
        return {
            "app": w.get("app", "unknown"),
            "title": w.get("title", "unknown"),
            "shot": shot,
            "description": desc,
            "description_age_min": age,
            "ocr": ocr,
            "ocr_age_min": ocr_age,
        }

    def stop(self):
        self._stop.set()

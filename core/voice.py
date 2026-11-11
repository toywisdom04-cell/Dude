import asyncio
import logging
import os
import queue
import tempfile
import threading
import time

from core.config import get_config

log = logging.getLogger("dude")


def _ensure_sir(text):
    """Return spoken text with the user's required form of address."""
    import re as _re

    text = (text or "").strip()
    if text and not _re.search(r"\bsir\b", text, _re.IGNORECASE):
        return text.rstrip(" .!?;:") + ", sir."
    return text


class Voice:
    def __init__(self, on_speech_scheduled=None):
        self.cfg = get_config()
        self.q = queue.Queue()
        self._stop_evt = threading.Event()
        self._speaking = threading.Event()
        self.interrupted = False
        self.silent = False
        self.on_speech_scheduled = on_speech_scheduled
        self.on_playback_start = None
        self.on_playback_end = None
        self._thread = threading.Thread(target=self._worker, daemon=True, name="du-voice")
        self._thread.start()
        self._mixer_ready = False
        # Prefetch cache: combined-text -> synthesized mp3 path. Filled by
        # prewarm() while the current chunk plays; consumed by _speak_text.
        self._synth_cache = {}
        self._cache_lock = threading.Lock()
        log.info("VOICE_SELECTED backend=edge-tts voice_id=%s rate=%s volume=%s "
                 "fallback=edge-tts-%s",
                 self.cfg.get("voice", "tts_voice",
                               default="en-US-ChristopherNeural"),
                 self.cfg.get("voice", "rate", default="+0%"),
                 self.cfg.get("voice", "volume", default="+0%"),
                 ("plus-sapi5-plus-gtts" if self.cfg.get(
                     "voice", "allow_sapi_fallback", default=False) and self.cfg.get(
                     "voice", "allow_gtts_fallback", default=False)
                  else "plus-sapi5" if self.cfg.get(
                     "voice", "allow_sapi_fallback", default=False)
                  else "plus-gtts" if self.cfg.get(
                     "voice", "allow_gtts_fallback", default=False)
                  else "natural-only"))
        log.info("VOICE_OWNER id=%d worker=du-voice", id(self))

    # ---------- public ----------
    def say(self, text, priority=False, force=False):
        text = (text or "").strip()
        if not text:
            return
        # The user's chosen form of address is part of DUDE's spoken identity:
        # every audible reply ends with "sir" unless it already addresses him.
        # Keep this at the single Voice owner so Brain, TaskEngine, greetings,
        # health notices, and local fast paths all behave consistently.
        text = _ensure_sir(text)
        if self.silent and not force:
            log.info("voice: suppressed by quiet mode: %r", text[:80])
            return
        log.info("voice: say: %r", text[:80])
        if self.on_speech_scheduled:
            try:
                self.on_speech_scheduled(text)
            except Exception:
                pass
        if priority:
            self.interrupt()
            self._stop_evt.clear()
            self.q.put(text)
            return
        self._stop_evt.clear()
        self.q.put(text)

    def interrupt(self):
        self.interrupted = True
        self._stop_evt.set()

    def abort(self):
        self.interrupt()
        with self._cache_lock:
            for _, p in list(self._synth_cache.items()):
                self._rm(p)
            self._synth_cache.clear()
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    @property
    def speaking(self):
        return self._speaking.is_set()

    def wait_done(self, timeout=30):
        t0 = time.time()
        while (not self.q.empty() or self.speaking) and time.time() - t0 < timeout:
            time.sleep(0.05)

    def close(self):
        self.interrupt()
        self.q.put(None)

    # ---------- internals ----------
    def _ensure_mixer(self, force_reset=False):
        if self._mixer_ready and not force_reset:
            return True
        try:
            import pygame
            import sounddevice as sd
            if force_reset and self._mixer_ready:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
                self._mixer_ready = False
            dev = self.cfg.get("voice", "output_device", default=None)
            sr = 44100
            try:
                if dev is not None:
                    info = sd.query_devices(int(dev), "output")
                    sr = int(info["default_samplerate"])
            except Exception:
                pass
            pygame.mixer.pre_init(sr, -16, 2, 4096)
            pygame.mixer.init()
            self._mixer_ready = True
            return True
        except Exception as e:
            print(f"[voice] mixer init failed: {e}")
            log.warning("voice: mixer init failed: %s", e)
            self._mixer_ready = False
            return False

    @staticmethod
    def _ensure_windows_audio_session():
        """Keep DUDE's per-app Windows mixer session audible.

        Windows stores a separate volume/mute value for ``python.exe``. The
        master speaker slider can be at 100% while that session is effectively
        muted or set to 1%. Best effort only: the voice path must still work
        on machines without pycaw or outside Windows.
        """
        try:
            from pycaw.pycaw import AudioUtilities
            import os
            pid = os.getpid()
            for session in AudioUtilities.GetAllSessions():
                proc = session.Process
                if proc is not None and proc.pid == pid:
                    session.SimpleAudioVolume.SetMute(0, None)
                    session.SimpleAudioVolume.SetMasterVolume(1.0, None)
                    log.info("voice: Windows Python audio session forced to 100%%")
                    return True
        except Exception as e:
            log.debug("voice: could not normalize Windows audio session: %s", e)
        return False

    def _worker(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            if self._stop_evt.is_set():
                # A stop/interrupt won the race with pickup (e.g. barge-in
                # fired between say() and dequeue): drop the stale item
                # instead of clearing someone else's stop and speaking
                # over the user. New speech clears the flag in say().
                log.info("STALE_TTS_DROPPED %r", str(item)[:80])
                continue
            # Prefetch: while this item plays, synthesize the NEXT queued
            # item in the background so there is no network gap between
            # consecutive sentences.
            try:
                nxt = self.q.queue[0]
            except Exception:
                nxt = None
            if isinstance(nxt, str) and nxt.strip():
                try:
                    self.prewarm(self._combined(nxt))
                except Exception:
                    pass
            self.interrupted = False
            self._speak_text(item)
            self._speaking.clear()
            try:
                if self.q.empty() and not self._stop_evt.is_set():
                    cb = getattr(self, "on_playback_end", None)
                    if cb is not None:
                        cb()
            except Exception:
                pass

    def _combined(self, text):
        """Exact speakable text for a queue item (sanitize → sentences → cap)."""
        cap = max(1, int(self.cfg.get("voice", "max_spoken_sentences", default=2)))
        sentences = self._sentences(self._sanitize_for_tts(text))[:cap]
        return " ".join(sentences)

    @staticmethod
    def _rm(path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def prewarm(self, key):
        """Background-synthesize `key` (already-combined text) into the
        prefetch cache. Best effort: failures just mean normal synthesis
        later. Bounded so barges can't pile up wasted work."""
        key = (key or "").strip()
        if not key:
            return
        with self._cache_lock:
            if key in self._synth_cache:
                return
            if len(self._synth_cache) >= 3:
                return

        def _run():
            try:
                path = self._synth(key)
            except Exception:
                return
            with self._cache_lock:
                if key in self._synth_cache:
                    self._rm(path)  # raced with a fresher synth; drop dup
                    return
                while len(self._synth_cache) >= 3:
                    _, old = self._synth_cache.popitem(last=False)
                    self._rm(old)
                self._synth_cache[key] = path

        threading.Thread(target=_run, daemon=True,
                         name="du-voice-prewarm").start()

    def _speak_text(self, text):
        log.info("voice: speaking item: %r", text[:100])
        combined = self._combined(text)
        if not combined:
            return
        if self._stop_evt.is_set():
            return
        with self._cache_lock:
            path = self._synth_cache.pop(combined, None)
            if path and not os.path.exists(path):
                path = None
        if path is not None:
            log.info("voice: prefetch hit, skipping synthesis")
        else:
            try:
                path = self._synth(combined)
            except Exception as e:
                print(f"[voice] natural TTS failed: {e}")
                log.warning("voice: natural Edge-TTS synthesis failed: %s", e)
                if self.cfg.get("voice", "allow_sapi_fallback", default=False):
                    self._fallback_speak(combined)
                else:
                    log.error("VOICE_NATURAL_ONLY no robotic SAPI fallback")
                return
            if self._stop_evt.is_set():
                # Interrupted while synthesizing (network call is blocking and
                # cannot be cancelled): drop the utterance instead of playing
                # a full stale chunk over the user after a barge-in.
                self._rm(path)
                return
        if self.on_speech_scheduled:
            try:
                self.on_speech_scheduled(combined)
            except Exception:
                pass
        try:
            played = self._play(path)
            if not played and not self._stop_evt.is_set():
                # A stale/failed mixer must not downgrade a natural voice to
                # robotic SAPI. Reset the mixer and retry the same Edge-TTS
                # audio once before declaring the natural path unavailable.
                log.warning("voice: natural playback failed; resetting mixer and retrying")
                self._ensure_mixer(force_reset=True)
                played = self._play(path)
        finally:
            self._rm(path)
        if not played and not self._stop_evt.is_set():
            if self.cfg.get("voice", "allow_sapi_fallback", default=False):
                log.warning("VOICE_FALLBACK robotic-sapi explicitly enabled for %r",
                            combined[:60])
                self._fallback_speak(combined)
            else:
                log.error("VOICE_NATURAL_ONLY Edge-TTS playback unavailable; "
                          "robotic fallback suppressed")

    @staticmethod
    def _sanitize_for_tts(text):
        """Make text spoken-friendly so the TTS doesn't spell out paths/underscores."""
        import re
        t = text or ""
        t = re.sub(r'[A-Za-z]:\\(?:[^ \t\r\n]+\\)*[^ \t\r\n]*', 'a file on your system', t)
        t = re.sub(r'\\\\[^ \t\r\n]+', 'a network location', t)
        t = t.replace("\\", " ").replace("_", " ")
        t = re.sub(r"\s{2,}", " ", t)
        return t.strip()

    @staticmethod
    def _sentences(text):
        parts, cur = [], ""
        for ch in text:
            cur += ch
            if ch in ".!?\n" and len(cur.strip()) > 2:
                parts.append(cur.strip())
                cur = ""
        if cur.strip():
            parts.append(cur.strip())
        merged = []
        for p in parts:
            if merged and len(merged[-1]) < 25:
                merged[-1] = merged[-1] + " " + p
            else:
                merged.append(p)
        return [p for p in merged if p]

    def _synth(self, sentence):
        voice = self.cfg.get("voice", "tts_voice", default="en-US-ChristopherNeural")
        rate = self.cfg.get("voice", "rate", default="+0%")
        volume = self.cfg.get("voice", "volume", default="+0%")
        cache_dir = self.cfg.data_dir + "/tts_cache"

        async def run_edge(path):
            import edge_tts
            com = edge_tts.Communicate(sentence, voice=voice, rate=rate,
                                        volume=volume)
            await asyncio.wait_for(com.save(path), timeout=25)

        fd, path = tempfile.mkstemp(suffix=".mp3", dir=cache_dir)
        os.close(fd)
        try:
            asyncio.run(run_edge(path))
            return path
        except Exception as edge_error:
            self._rm(path)
            fd, path = tempfile.mkstemp(suffix=".mp3", dir=cache_dir)
            os.close(fd)
            if not self.cfg.get("voice", "allow_gtts_fallback", default=False):
                self._rm(path)
                raise
            try:
                from gtts import gTTS
                tld = self.cfg.get("voice", "gtts_tld", default="com")
                gTTS(sentence, lang="en", tld=tld).save(path)
                log.warning("VOICE_SYNTH_FALLBACK backend=gtts edge_error=%s",
                            type(edge_error).__name__)
                return path
            except Exception:
                self._rm(path)
                raise

    def _play(self, path):
        try:
            import sounddevice as sd
            import numpy as np
            import pygame

            self._ensure_windows_audio_session()

            if not self._ensure_mixer():
                log.warning("voice: no mixer for decode, playback failed")
                return False

            output_device = self.cfg.get("voice", "output_device", default=None)
            if output_device is not None:
                output_device = int(output_device)

            sound = pygame.mixer.Sound(path)
            raw = sound.get_raw()
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            sr = pygame.mixer.get_init()[0]
            channels = pygame.mixer.get_init()[2] or 2
            if channels == 1:
                arr = np.repeat(arr, 2)
            arr = arr[: (len(arr) // 2) * 2]

            # Pick a real stereo output. The Windows default (MME mapper ->
            # 8-channel Senary device) stalls PortAudio callbacks when asked
            # for 2 channels, which produced silence + a hung voice worker.
            # Prefer an explicit 2-channel WASAPI Speakers device unless the
            # user configured a 2-channel device themselves.
            try:
                dev_info = sd.query_devices(output_device)
                max_ch = int(dev_info.get("max_output_channels") or 2)
                log.info("voice: output device info: name=%s max_out_ch=%d default_sr=%s",
                         dev_info.get('name'), dev_info.get('max_output_channels'), dev_info.get('default_samplerate'))
            except Exception as e:
                log.warning("voice: could not query device info: %s", e)
                dev_info, max_ch = {}, 2
            if output_device is None or max_ch != 2:
                try:
                    best, best_score = None, -1
                    for i, d in enumerate(sd.query_devices()):
                        if int(d.get("max_output_channels") or 0) != 2:
                            continue
                        name = str(d.get("name") or "")
                        host = str(d.get("hostapi") or "")
                        score = 0
                        if "WASAPI" in name:
                            score += 3
                        if "Speakers" in name:
                            score += 2
                        if "Senary" in name:
                            score += 1
                        if score > best_score:
                            best, best_score = i, score
                    if best is not None:
                        log.info("voice: auto-select output device %d (was=%s)",
                                 best, output_device)
                        output_device = best
                        dev_info = sd.query_devices(output_device)
                except Exception as e:
                    log.warning("voice: device auto-select failed: %s", e)
            want_ch = 2

            data = arr.reshape(-1, want_ch)
            try:
                dev_sr = int(float((dev_info or {}).get("default_samplerate") or sr))
            except Exception:
                dev_sr = sr
            if dev_sr != sr:
                # Linear resample to the device rate so shared-mode WASAPI
                # accepts the stream without stalling.
                try:
                    n_out = int(round(len(data) * dev_sr / float(sr)))
                    old_idx = np.linspace(0, len(data) - 1, len(data))
                    new_idx = np.linspace(0, len(data) - 1, n_out)
                    data = np.stack(
                        [np.interp(new_idx, old_idx, data[:, c]).astype(np.float32)
                         for c in range(want_ch)], axis=1)
                    log.info("voice: resampled %d->%dHz (%d->%d frames)",
                             sr, dev_sr, len(old_idx), n_out)
                    sr = dev_sr
                except Exception as e:
                    log.warning("voice: resample failed, keeping %dHz: %s", sr, e)

            # Edge-TTS volume changes synthesis dynamics, but a quiet Windows
            # output route can still make a valid stream hard to hear across a
            # room. Apply a bounded playback gain and protect the samples from
            # clipping. This is intentionally digital-only; it does not alter
            # the system mixer or other applications.
            try:
                playback_gain = float(self.cfg.get(
                    "voice", "playback_gain", default=1.0))
            except (TypeError, ValueError):
                playback_gain = 1.0
            if playback_gain > 0.0 and abs(playback_gain - 1.0) > 1e-3:
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                if peak > 0.0:
                    playback_gain = min(playback_gain, 0.98 / peak)
                data = np.clip(data * playback_gain, -0.98, 0.98)
            log.info("voice: playback gain=%.2f", playback_gain)

            self._speaking.set()
            log.info("voice: playing reply audio (device=%s sr=%d frames=%d)",
                     output_device, sr, len(data))
            log.info("TTS_PLAYBACK_START")
            try:
                cb = getattr(self, "on_playback_start", None)
                if cb is not None:
                    cb()
            except Exception:
                pass

            # Blocking-write playback (no callback): sd.play queues the whole
            # buffer, we poll for interrupt, sd.stop() cuts it short.
            duration = len(data) / float(sr)
            last_err = None
            for attempt in range(3):
                try:
                    sd.play(data, samplerate=sr, device=output_device)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    log.warning("voice: play on device %s attempt %d failed: %s",
                                output_device, attempt + 1, e)
                    time.sleep(0.4)
            if last_err is not None:
                raise last_err
            t_start = time.time()
            interrupted = False
            while time.time() - t_start < duration + 2.0:
                if self._stop_evt.is_set():
                    interrupted = True
                    try:
                        sd.stop(device=output_device)
                    except Exception:
                        try:
                            sd.stop()
                        except Exception:
                            pass
                    break
                time.sleep(0.02)
            try:
                sd.wait()
            except Exception:
                pass
            log.info("voice: playback loop ended, interrupted=%s", interrupted)
            self._speaking.clear()
            try:
                cb = getattr(self, "on_playback_end", None)
                if cb is not None:
                    cb()
            except Exception:
                pass
            log.info("TTS_PLAYBACK_END")
            if interrupted or self._stop_evt.is_set():
                return False
            return True
        except Exception as e:
            print(f"[voice] playback failed: {e}")
            log.warning("voice: playback failed: %s", e)
            self._speaking.clear()
            return False

    def _fallback_speak(self, sentence):
        if self.on_speech_scheduled:
            try:
                self.on_speech_scheduled(sentence)
            except Exception:
                pass

        try:
            import pyttsx3
            engine = pyttsx3.init("sapi5")
            self._speaking.set()
            log.info("voice: SAPI fallback speaking")
            engine.setProperty("rate", 200)
            engine.say(sentence)
            engine.runAndWait()
        except Exception as e:
            log.warning("voice: SAPI fallback failed: %s", e)
            return False
        finally:
            self._speaking.clear()
            log.info("voice: SAPI fallback completed")
            return True

    

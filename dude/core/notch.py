"""Liquid Notch UI — the small always-on-top companion.

A frameless, translucent, draggable pill that sits at the top of the screen
and expands/collapses with a smooth liquid animation. Fully movable to any
position. Pure QPainter, no layouts, no OpenGL.

It also hosts the Command Centre: the orb's CENTER button (or saying "open
command centre") toggles the separate holographic UI in ``core.holo``.
"""
import datetime
import math
import queue
import time

from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, QRect, QVariantAnimation, QEasingCurve
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QLinearGradient, QRadialGradient
from PyQt5.QtWidgets import QApplication, QWidget

from core.holo import HoloUI

LEVEL_Q = queue.Queue(maxsize=256)

CYAN = QColor(0, 255, 255)
MAGENTA = QColor(255, 0, 255)
GREEN = QColor(0, 255, 65)
VIOLET = QColor(177, 151, 252)
AMBER = QColor(255, 212, 59)
RED = QColor(255, 0, 60)

STATE_COLORS = {"listening": "#00FF41", "thinking": "#B197FC", "speaking": "#00FF41", "idle": "#5F6B76", "executing": "#5BA7E6", "verifying": "#B197FC", "recovering": "#E8A13D", "error": "#E05252"}


def _c(hexcolor):
    h = hexcolor.lstrip("#")
    return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _tri(t0, t1, x):
    x = max(0.0, min(1.0, (x - t0) / (t1 - t0)))
    return x * x * (3 - 2 * x)


COLLAPSED_W, COLLAPSED_H = 150, 46
EXPANDED_W, EXPANDED_H = 560, 430
ANIM_MS = 340

QUICK = [
    ("CENTER", "center"),
    ("CAM", "open camera mirror"),
    ("MUTE", "mute the volume"),
    ("DAY", "how is my work today summary"),
]


class LiquidNotch(QWidget):
    def __init__(self, to_agent, from_agent, on_center=None, capturing_event=None):
        super().__init__()
        self.to_agent = to_agent
        self.from_agent = from_agent
        self.capturing_event = capturing_event
        self.on_center = on_center or (lambda: None)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self.expanded = False
        self._t = 0.0
        self._anim_rect = None
        self._pressed = (0, 0)
        self._win_xy = (0, 0)
        self._moved = False
        self._drag = False
        self._hover = None
        self._ph = 0.0
        self._last_frame = time.time()

        self.state = "listening"
        self.status = "online"
        self._status_color = CYAN
        self._auth_state = "listening"
        self._auth_ts = 0.0
        self.ctx_text = ""
        self.chat_text = ""
        self.levels = [0.0] * 40
        self._last_msg_time = 0.0

        scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        self._scr = scr
        if scr:
            self.setGeometry(int(scr.center().x() - COLLAPSED_W / 2), 8, COLLAPSED_W, COLLAPSED_H)
        else:
            self.setGeometry(360, 8, COLLAPSED_W, COLLAPSED_H)
        self._collapsed_rect = QRect(self.geometry())

        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(ANIM_MS)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._on_anim)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(66)

        self.ctx_timer = QTimer(self)
        self.ctx_timer.timeout.connect(lambda: self.set_context(self._build_ctx()))
        self.ctx_timer.start(3000)

    # ---------- public API (used by the Hub) ----------
    def push_chat(self, who, text):
        self.chat_text = f"{('DUDE' if who == 'dude' else 'YOU')}: {text[:80]}"
        self._last_msg_time = time.time()
        self.update()

    def push_level(self, lvl):
        self.levels.append(float(lvl))
        del self.levels[:-40]

    def set_state(self, state, text=None, color=None):
        self.state = state
        self._auth_state = state
        self._auth_ts = time.time()
        if text is not None:
            self.status = text
        if color is not None:
            try:
                self._status_color = _c(color)
            except Exception:
                self._status_color = _c(STATE_COLORS.get(state, "#00FFFF"))
        elif state in STATE_COLORS:
            self._status_color = _c(STATE_COLORS[state])
        self.update()

    def set_context(self, text):
        self.ctx_text = text
        self.update()

    def run(self):
        self.show()

    # ---------- context ----------
    def _build_ctx(self):
        now = datetime.datetime.now().strftime("%a %d %b · %H:%M")
        batt = ""
        try:
            import psutil
            b = psutil.sensors_battery()
            if b:
                batt = f" · {int(b.percent)}%{'⚡' if b.power_plugged else ''}"
        except Exception:
            pass
        app = ""
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            t = win32gui.GetWindowText(hwnd)
            app = f" · {t[:40]}"
        except Exception:
            pass
        return now + batt + app

    # ---------- animation ----------
    def _expanded_target(self):
        c = self._collapsed_rect
        ex = c.x() + COLLAPSED_W / 2 - EXPANDED_W / 2
        ey = c.y()
        scr = self._scr
        if scr:
            if ey + EXPANDED_H > scr.bottom() - 8:
                ey = scr.bottom() - 8 - EXPANDED_H
            if ey < 0:
                ey = 0
            ex = max(scr.left() + 4, min(ex, scr.right() - EXPANDED_W - 4))
        return QRect(int(ex), ey, EXPANDED_W, EXPANDED_H)

    def _reset_collapsed(self):
        self._collapsed_rect = QRect(int(self.x() + self.width() / 2 - COLLAPSED_W / 2),
                                     self.y(), COLLAPSED_W, COLLAPSED_H)

    def _on_anim(self, v):
        t = max(0.0, min(1.0, float(v)))
        self._t = t
        c = self._collapsed_rect
        e = self._expanded_target()
        w = int(c.width() + (e.width() - c.width()) * t)
        h = int(c.height() + (e.height() - c.height()) * t)
        wx = int(c.x() + (e.x() - c.x()) * t)
        wy = int(c.y() + (e.y() - c.y()) * t)
        self.setGeometry(wx, wy, w, h)
        self.update()

    def toggle(self):
        self.expanded = not self.expanded
        self._reset_collapsed()
        self.anim.stop()
        self.anim.setStartValue(self._t)
        self.anim.setEndValue(1.0 if self.expanded else 0.0)
        self.anim.start()
        self.update()

    # ---------- painting ----------
    def paintEvent(self, _):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)
            w, h = self.width(), self.height()
            t = self._t

            path = QPainterPath()
            radius = 23 + max(0, (26 - 23) * t)
            rect = QRectF(2, 2, w - 4, h - 4)
            path.addRoundedRect(rect, radius, radius)

            # soft shadow
            p.setPen(Qt.NoPen)
            for i, dd in enumerate((5, 4, 3, 2, 1)):
                c = QColor(0, 0, 0, 70 - i * 10)
                p.setBrush(c)
                shr = QPainterPath()
                shr.addRoundedRect(rect.translated(0, dd), radius, radius)
                p.drawPath(shr)

            # glass body
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.0, QColor(16, 30, 58, 220))
            g.setColorAt(0.6, QColor(9, 14, 34, 215))
            g.setColorAt(1.0, QColor(7, 11, 28, 225))
            p.setBrush(g)
            p.drawPath(path)

            # animated border shimmer
            sc = self._status_color
            bord = QLinearGradient(0, 0, w, h)
            bord.setColorAt(0.0, QColor(0, 255, 255, 230))
            bord.setColorAt(0.5, QColor(sc.red(), sc.green(), sc.blue(), 150))
            bord.setColorAt(1.0, QColor(255, 0, 255, 120 if t > 0.5 else 60))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(bord, 2 if t < 0.5 else 1))
            p.drawPath(path)

            # top sheen
            top = QPainterPath()
            top.addRoundedRect(QRectF(6, 4, w - 12, h / 2), radius / 2, radius / 2)
            sheen = QLinearGradient(0, 0, 0, h / 2)
            sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(sheen)
            p.setPen(Qt.NoPen)
            p.drawPath(top)

            if t < 0.35:
                self._paint_collapsed(p, w, h, t)
            else:
                self._paint_expanded(p, w, h, t)
        except Exception:
            import traceback

            traceback.print_exc()
        finally:
            p.end()

    def _paint_collapsed(self, p, w, h, t):
        alpha = _tri(0.0, 0.3, t)
        ac = QColor(self._status_color)
        ac.setAlpha(int(220 * alpha))
        p.setBrush(ac)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(w / 2 - 48, h / 2), 4, 4)

        f = QFont("Segoe UI Semibold", 11)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        c = QColor(223, 246, 255, int(235 * alpha))
        p.setPen(c)
        p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "D U D E")

        fs = QFont("Segoe UI", 8)
        p.setFont(fs)
        c2 = QColor(self._status_color)
        c2.setAlpha(int(200 * alpha))
        p.setPen(c2)
        p.drawText(QRectF(w / 2 - 12, 0, w / 2 + 12, h), Qt.AlignVCenter | Qt.AlignLeft, self.status[:10])

    def _paint_expanded(self, p, w, h, t):
        a = _tri(0.45, 0.8, t)
        rnd = _tri(0.6, 0.95, t)

        f_t = QFont("Segoe UI Semibold", 11)
        f_t.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        f_s = QFont("Segoe UI", 9)
        f_ctx = QFont("Consolas", 8)

        # header
        sc = self._status_color
        c = QColor(sc)
        c.setAlpha(int(230 * a))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(30, 34), 5, 5)

        p.setFont(f_t)
        p.setPen(QColor(223, 246, 255, int(235 * a)))
        p.drawText(QRectF(44, 26, 150, 18), Qt.AlignVCenter | Qt.AlignLeft, "DUDE")
        p.setFont(f_s)
        c2 = QColor(self._status_color)
        c2.setAlpha(int(200 * a))
        p.setPen(c2)
        p.drawText(QRectF(170, 27, 260, 16), Qt.AlignVCenter | Qt.AlignLeft, self.status.upper())

        # chevron (collapse)
        chev = QRectF(w - 44, 22, 26, 24)
        ch = QColor(190, 220, 245, int(180 * a))
        p.setPen(QPen(ch, 2))
        p.drawLine(QPointF(chev.left() + 2, chev.top() + 6), QPointF(chev.center().x(), chev.bottom() - 4))
        p.drawLine(QPointF(chev.right() - 2, chev.top() + 6), QPointF(chev.center().x(), chev.bottom() - 4))

        # orb core
        cx, cy = w / 2, 150
        self._draw_orb(p, cx, cy, sc, a)

        p.setFont(f_ctx)
        c3 = QColor(120, 160, 200, int(180 * a))
        p.setPen(c3)
        p.drawText(QRectF(30, 216, w - 60, 18), Qt.AlignCenter, self.ctx_text[:80] or "—")

        c4 = QColor(223, 246, 255, int(200 * a))
        p.setPen(c4)
        p.setFont(f_s)
        p.drawText(QRectF(30, 234, w - 60, 18), Qt.AlignCenter, self.chat_text or "STANDBY")

        # quick buttons
        bw, bh, gap = 118, 30, 12
        total = bw * 4 + gap * 3
        x0 = (w - total) / 2
        for i, (label, _hint) in enumerate(QUICK):
            bx = x0 + i * (bw + gap)
            bry = 268
            hover = self._hover == i
            r = QRectF(bx, bry, bw, bh)
            path = QPainterPath()
            path.addRoundedRect(r, 9, 9)
            gv = QLinearGradient(0, r.top(), 0, r.bottom())
            gv.setColorAt(0.0, QColor(14, 30, 56, int(200 * a)))
            gv.setColorAt(1.0, QColor(8, 14, 32, int(200 * a)))
            p.setBrush(gv)
            p.setPen(Qt.NoPen)
            p.drawPath(path)
            bc = QColor(CYAN if i else MAGENTA)
            bc.setAlpha((255 if hover else 130 + int(40 * rnd)) if a > 0 else 0)
            p.setPen(QPen(bc, 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setFont(f_s)
            c5 = QColor(220, 240, 255, int(235 * a))
            p.setPen(c5)
            p.drawText(r, Qt.AlignCenter, label)

    def _draw_orb(self, p, cx, cy, sc, a):
        t = time.time()
        for i in range(3):
            rr = 26 + i * 16 + math.sin(t * 1.4 + i) * 3
            c = QColor(sc)
            c.setAlpha(int((80 - i * 14) * a))
            p.setPen(QPen(c, 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        lv = int(sum(self.levels) / max(1, len(self.levels)) * 50)
        for i in range(10):
            ang = i * 0.628 + t * 0.4
            x1 = cx + math.cos(ang) * 38
            y1 = cy + math.sin(ang) * 38
            x2 = cx + math.cos(ang) * (38 + lv * 0.6)
            y2 = cy + math.sin(ang) * (38 + lv * 0.6)
            c = QColor(sc)
            c.setAlpha(int(170 * a))
            p.setPen(QPen(c, 2))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        core = QRadialGradient(cx, cy, 26)
        c1 = QColor(sc)
        c1.setAlpha(int(235 * a))
        c2 = QColor(sc)
        c2.setAlpha(int(70 * a))
        core.setColorAt(0.0, c1)
        core.setColorAt(0.62, c2)
        core.setColorAt(1.0, QColor(5, 9, 22, 0))
        p.setBrush(core)
        pulse = 1 + 0.06 * math.sin(t * 2.4)
        p.setPen(QPen(QColor(sc.red(), sc.green(), sc.blue(), int(150 * a)), 2))
        p.drawEllipse(QPointF(cx, cy), 24 * pulse, 24 * pulse)

        pen = QPen(QColor(MAGENTA), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        phase = t % (2 * math.pi)
        p.drawArc(QRectF(cx - 34, cy - 34, 68, 68), int(phase * (90 * 57 / (2 * math.pi))), 180 * 16)

    # ---------- interaction ----------
    def _hit(self, pos):
        x, y = pos.x(), pos.y()
        if self._t < 0.5:
            return "toggle"
        bw, bh, gap = 118, 30, 12
        total = bw * 4 + gap * 3
        x0 = (self.width() - total) / 2
        for i, _ in enumerate(QUICK):
            r = QRectF(x0 + i * (bw + gap), 268, bw, bh)
            if r.adjusted(-4, -4, 4, 4).contains(x, y):
                return i
        if QRectF(self.width() - 44, 18, 30, 30).contains(x, y):
            return "chevron"
        return "body"

    def mouseMoveEvent(self, e):
        self._hover = self._hit(e.pos())
        if self._drag and (e.buttons() & Qt.LeftButton):
            gx, gy = e.globalPos().x(), e.globalPos().y()
            dx = gx - self._pressed[0]
            dy = gy - self._pressed[1]
            if abs(dx) > 3 or abs(dy) > 3:
                self._moved = True
            self.move(self._win_xy[0] + dx, self._win_xy[1] + dy)
            self.update()
        else:
            if self._drag and not (e.buttons() & Qt.LeftButton):
                self._drag = False
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressed = (e.globalPos().x(), e.globalPos().y())
            self._win_xy = (self.x(), self.y())
            self._drag = True
            self._moved = False

    def mouseReleaseEvent(self, e):
        was_drag = self._moved
        self._drag = False
        if was_drag:
            scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
            if scr and self.y() < 14 and self._t < 0.5:
                self.move(self.x(), 8)
            return
        hit = self._hit(e.pos())
        if hit == "toggle" or hit == "chevron" or hit == "body" or hit == "center":
            self.toggle()
        elif isinstance(hit, int):
            label, cmd = QUICK[hit]
            if label == "CENTER":
                self.on_center()
                return
            if self.to_agent is not None:
                self.to_agent.put({"type": "command", "text": cmd})
            self.set_state("thinking", cmd)

    def mouseDoubleClickEvent(self, e):
        self.toggle()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.expanded:
            self.toggle()

# ---------- loop ----------
    def _tick(self):
        try:
            while True:
                item = LEVEL_Q.get_nowait()
                self.push_level(item)
        except queue.Empty:
            pass
        if time.time() - self._last_msg_time > 8:
            # Authoritative states (speaking/thinking/executing/...) own the
            # display until they age out; only idle/listening fall back to
            # live capture/level guesses. No more cosmetic stomping.
            _auth_fresh = (time.time() - self._auth_ts < 12.0
                           and self._auth_state not in ("idle", "listening"))
            if self.capturing_event is not None and self.capturing_event.is_set():
                if not _auth_fresh:
                    self.set_state("listening", "listening")
            elif not _auth_fresh:
                avg = sum(self.levels) / max(1, len(self.levels))
                self.set_state("idle" if avg < 0.02 else "listening",
                               "online" if avg < 0.02 else "listening")
        self._ph += 0.016


class Hub:
    """Owns the Notch + Command Centre on the Qt thread and routes messages."""

    def __init__(self, to_agent, from_agent, ui_cmd_q=None, level_q=None, ear=None):
        self.to_agent = to_agent
        self.from_agent = from_agent
        self.ui_cmd_q = ui_cmd_q
        self.level_q = level_q or LEVEL_Q

        self.holo = HoloUI()
        self.notch = LiquidNotch(to_agent, from_agent, on_center=self.holo.toggle,
                                 capturing_event=getattr(ear, "capturing_event", None))
        self.holo.on_command = self._holo_command
        self._auth = ("idle", 0.0)

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    def _holo_command(self, text):
        if self.to_agent is not None:
            self.to_agent.put({"type": "command", "text": text})
            self.notch.push_chat("you", text)

    def _tick(self):
        try:
            while True:
                lvl = self.level_q.get_nowait()
                self.notch.push_level(lvl)
                self.holo.push_level(lvl)
        except queue.Empty:
            pass
        try:
            while True:
                msg = self.from_agent.get_nowait()
                kind = msg.get("type")
                if kind == "speech":
                    import time as _t2
                    txt = msg["text"]
                    self.notch.push_chat("dude", txt)
                    self.holo.push_chat("dude", txt)
                    self._auth = ("speaking", _t2.time())
                    self.notch.set_state("speaking", "speaking")
                    self.holo.set_state("speaking", "speaking")
                elif kind == "heard":
                    import time as _t3
                    txt = msg["text"]
                    self.notch.push_chat("you", txt)
                    self.holo.push_chat("you", txt)
                    self._auth = ("thinking", _t3.time())
                    self.notch.set_state("thinking", "thinking")
                    self.holo.set_state("thinking", "thinking")
                elif kind == "status":
                    import time as _t
                    _st = msg.get("state") or "idle"
                    # Idle-default puts (ambient notes etc.) must never stomp
                    # a fresh authoritative state like speaking/thinking.
                    _cur, _ts = self._auth
                    if _st == "idle" and _cur not in ("idle", "listening") \
                            and _t.time() - _ts < 12.0:
                        continue
                    self._auth = (_st, _t.time())
                    self.notch.set_state(_st, msg.get("text"), msg.get("color"))
                    self.holo.set_state(_st, msg.get("text"), msg.get("color"))
        except queue.Empty:
            pass
        if self.ui_cmd_q is not None:
            try:
                while True:
                    cmd = self.ui_cmd_q.get_nowait()
                    if cmd in ("center", "center_open"):
                        if not self.holo.isVisible():
                            self.holo.toggle()
                    elif cmd == "center_close":
                        if self.holo.isVisible():
                            self.holo.toggle()
                    elif cmd == "show":
                        if not self.notch.isVisible():
                            self.notch.show()
                        self.notch.raise_()
                        if not self.holo.isVisible():
                            self.holo.toggle()
            except queue.Empty:
                pass

    def start(self):
        self.notch.run()
        self.holo.toggle()
        if self.to_agent is not None:
            try:
                self.to_agent.put({"type": "ui_ready"})
            except Exception:
                pass


def launch_notch(to_agent, from_agent, ui_cmd_q=None, level_q=None, ear=None):
    import logging
    log = logging.getLogger("dude")
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    for attempt in range(15):
        scr = QApplication.primaryScreen()
        if scr is not None and scr.availableGeometry().isValid():
            break
        log.warning("notch: no usable screen yet (attempt %d/15) — waiting...", attempt + 1)
        time.sleep(2)
    else:
        log.error("notch: no usable screen after 30s — UI disabled for this session")
        return
    try:
        hub = Hub(to_agent, from_agent, ui_cmd_q=ui_cmd_q, level_q=level_q, ear=ear)
        hub.start()
        log.info("notch: UI up (Notch + Holo command centre on screen)")
    except Exception:
        log.exception("notch: UI failed to start")
        return
    app.exec_()
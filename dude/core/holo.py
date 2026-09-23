"""Holographic Command Centre — the sci-fi UI.

Independent of the Notch. Frameless, translucent, always-on-top panel with
holographic cards (cyan/magenta neon borders + hover glow), scanning line,
particle/starfield backdrop, matrix-style data streams, network node animation,
rotating 3D wireframe shapes, perspective-skewed dashboard cards, corner
brackets that draw themselves on load, glowing activity arcs, ripple effects
and a command input that lights up on focus. Pure QPainter — no OpenGL.
"""
import math
import random
import time

from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, QVariantAnimation, QEasingCurve
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QLinearGradient, QRadialGradient
from PyQt5.QtWidgets import QApplication, QWidget, QLineEdit

CYAN = QColor(0, 255, 255)          # Electric Cyan #00FFFF
MAGENTA = QColor(255, 0, 255)       # Neon Magenta #FF00FF
DEEP = QColor(10, 14, 39)           # Deep Space Blue #0A0E27
MATRIX = QColor(0, 255, 65)         # Matrix Green #00FF41
ORANGE = QColor(255, 107, 0)        # Warning Orange #FF6B00
RED = QColor(255, 0, 60)            # Laser Red #FF003C
BACK = QColor(13, 13, 26)           # Dark Galaxy #0D0D1A
BLUE = QColor(0, 128, 255)
WHITE = QColor(223, 246, 255)

STATE_COLORS = {"listening": "#00FFFF", "thinking": "#B197FC", "speaking": "#00FF41", "idle": "#5F6B76"}


def _c(hexcolor):
    h = hexcolor.lstrip("#")
    return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _tri(t0, t1, x):
    x = max(0.0, min(1.0, (x - t0) / (t1 - t0)))
    return x * x * (3 - 2 * x)


class HoloUI(QWidget):
    W, H = 900, 580

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        if scr:
            self._scr = scr
            self.setGeometry(scr.center().x() - self.W // 2, int(scr.height() * 0.09), self.W, self.H)
        else:
            self._scr = None
            self.setGeometry(100, 60, self.W, self.H)

        self.state = "listening"
        self.status = "online"
        self._status_color = CYAN
        self.ctx_text = "NEURAL CORE STANDBY"
        self.chat_text = ""
        self.levels = [0.0] * 48
        self.on_command = None

        self._t0 = time.time()
        self._last_frame = time.time()
        self._phase = 0.0
        self._load_t = 0.0

        self._stars = self._make_stars(80)
        self._matrix = self._make_matrix(28)
        self._nodes = self._make_nodes(14)
        self._pulses = []
        self._ripples = []
        self._cube_ang = 0.0
        self._tetra_ang = 0.0
        self._orb_ang = 0.0
        self._scan_y = 0.0
        self._scan_dir = 1.0
        self._hover = None
        self._drag = None
        self._pressed = (0, 0)
        self._moved = False
        self._tick = 0
        self._card_data = self._refresh_card_data()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.timer.start(33)

        self.entry = QLineEdit(self)
        self.entry.setPlaceholderText("TYPE COMMAND ▸")
        self.entry.setStyleSheet(
            "QLineEdit{background:rgba(10,14,30,200);color:#DFF6FF;"
            "border:1px solid rgba(0,255,255,80);border-radius:12px;"
            "padding:6px 12px;font-family:'Segoe UI';font-size:12px;}"
            "QLineEdit:hover{border:1px solid rgba(0,255,255,150);}"
            "QLineEdit:focus{border:1px solid #00FFFF;background:rgba(16,30,60,235);"
            "color:#FFFFFF;}")
        self.entry.returnPressed.connect(self._entry_send)
        self.entry.hide()

        self._load_anim = QVariantAnimation(self)
        self._load_anim.setStartValue(0.0)
        self._load_anim.setEndValue(1.0)
        self._load_anim.setDuration(1400)
        self._load_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._load_anim.valueChanged.connect(lambda v: (setattr(self, "_load_t", v), self.update()))

    # ---------- public API (fed by the Hub) ----------
    def set_state(self, state, text=None, color=None):
        self.state = state
        if text is not None:
            self.status = text
        if color is not None:
            try:
                self._status_color = _c(color)
            except Exception:
                self._status_color = STATE_COLORS.get(state, CYAN)
        elif state in STATE_COLORS:
            self._status_color = _c(STATE_COLORS[state])
        if self.isVisible():
            self.update()

    def push_chat(self, who, text):
        prefix = "DUDE" if who == "dude" else "YOU"
        self.chat_text = f"{prefix}: {text[:70]}"
        if self.isVisible():
            self.update()

    def push_level(self, lvl):
        self.levels.append(float(lvl))
        del self.levels[:-48]

    def set_context(self, text):
        self.ctx_text = text[:80]

    # ---------- helpers ----------
    @staticmethod
    def _rand(a, b):
        return a + random.random() * (b - a)

    def _make_stars(self, n):
        stars = []
        for _ in range(n):
            stars.append({
                "x": self._rand(0, self.W), "y": self._rand(0, self.H),
                "z": self._rand(0.2, 1.0), "vx": self._rand(-4, 4) * 0.3,
                "vy": self._rand(-2, 6) * 0.3, "ph": self._rand(0, 6.28),
                "col": random.choice([CYAN, BLUE, MATRIX, MAGENTA]),
            })
        return stars

    def _make_matrix(self, n):
        cols = []
        for _ in range(n):
            cols.append({
                "x": self._rand(0, self.W),
                "y": self._rand(0, self.H),
                "len": int(self._rand(8, 26)),
                "sp": self._rand(40, 140),
                "a": self._rand(0.08, 0.38),
                "col": random.choice([MATRIX, CYAN, BLUE]),
            })
        return cols

    def _make_nodes(self, n):
        nodes = []
        for _ in range(n):
            nodes.append({
                "x": self._rand(0, self.W), "y": self._rand(0, self.H),
                "vx": self._rand(-8, 8) * 0.25, "vy": self._rand(-8, 8) * 0.25,
                "ph": self._rand(0, 6.28), "s": self._rand(2, 5),
                "col": random.choice([CYAN, MAGENTA, BLUE, MATRIX]),
            })
        return nodes

    def _refresh_card_data(self):
        data = {"cpu": "—", "ram": "—", "learn": "ACTIVE", "screen": "30 SEC"}
        try:
            import psutil
            data["cpu"] = f"{psutil.cpu_percent(interval=None):.1f}%"
            vm = psutil.virtual_memory()
            data["ram"] = f"{vm.percent:.1f}%"
            boot = time.time() - psutil.boot_time()
            h, m = int(boot // 3600), int(boot % 3600 // 60)
            data["up"] = f"{h}h {m:02d}m"
            io = psutil.net_io_counters()
            data["net"] = f"{(io.bytes_sent + io.bytes_recv) // 1048576}MB"
        except Exception:
            pass
        return data

    def _entry_send(self):
        text = self.entry.text().strip()
        if not text:
            return
        self.entry.clear()
        self.push_chat("you", text)
        if self.on_command:
            self.on_command(text)

    # ---------- animation ----------
    def _advance(self):
        now = time.time()
        dt = min(0.1, now - self._last_frame)
        self._last_frame = now
        self._phase += dt
        self._tick += 1

        for s in self._stars:
            s["x"] += s["vx"] * dt * 60
            s["y"] += s["vy"] * dt * 60
            if s["x"] < -4:
                s["x"] = self.W + 3
            if s["x"] > self.W + 4:
                s["x"] = -3
            if s["y"] > self.H + 4:
                s["y"] = -3
            if s["y"] < -4:
                s["y"] = self.H + 3

        for m in self._matrix:
            m["y"] += m["sp"] * dt
            if m["y"] - m["len"] * 13 > self.H + 6:
                m["y"] = -self._rand(0, 40)
                m["len"] = int(self._rand(8, 26))

        for nd in self._nodes:
            nd["x"] += nd["vx"] * dt
            nd["y"] += nd["vy"] * dt
            if not (0 <= nd["x"] <= self.W):
                nd["vx"] *= -1
                nd["x"] = max(0, min(self.W, nd["x"]))
            if not (0 <= nd["y"] <= self.H):
                nd["vy"] *= -1
                nd["y"] = max(0, min(self.H, nd["y"]))

        self._cube_ang += dt * 0.6
        self._tetra_ang -= dt * 0.8
        self._orb_ang += dt * 1.6
        self._scan_y += self._scan_dir * 170 * dt
        if self._scan_y > self.H * 0.97:
            self._scan_dir = -1.0
        elif self._scan_y < self.H * 0.03:
            self._scan_dir = 1.0

        if self._tick % 15 == 0:
            self._card_data = self._refresh_card_data()
            if len(self._pulses) < 4:
                pair = (random.choice(self._nodes), random.choice(self._nodes))
                self._pulses.append({"a": pair[0], "b": pair[1], "t": 0.0})

        for p in [x for x in self._pulses if x["t"] >= 1.0]:
            self._pulses.remove(p)
        for p in self._pulses:
            p["t"] += dt * 0.7
        for r in [x for x in self._ripples if x["t"] >= 1.0]:
            self._ripples.remove(r)
        for r in self._ripples:
            r["t"] += dt * 2.6

        self.update()

    # ---------- painting ----------
    def paintEvent(self, _):
        qp = QPainter(self)
        try:
            p = qp
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)
            w, h = self.width(), self.height()

            self._paint_backdrop(p, w, h)
            self._paint_grid(p, w, h)
            self._paint_matrix(p, w, h)
            self._paint_nodes(p, w, h)
            self._paint_scan(p, w, h)
            self._paint_glass(p, w, h)
            self._paint_3d(p, w, h)
            self._paint_core(p, w, h)
            self._paint_cards(p, w, h)
            self._paint_ripples(p, w, h)
            self._paint_header(p, w, h)
            self._paint_scanline_footer(p, w, h)
            self._paint_footer(p, w, h)
            self._paint_corners(p, w, h)
        except Exception:
            import traceback

            traceback.print_exc()
        finally:
            p.end()

    def _panel_rect(self, w, h):
        return QRectF(2, 2, w - 4, h - 4)

    def _paint_backdrop(self, p, w, h):
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(20, 20, 40, 255))
        g.setColorAt(0.5, QColor(BACK))
        g.setColorAt(1.0, QColor(8, 10, 24, 255))
        p.fillRect(self.rect(), g)

        for s in self._stars:
            tw = 0.5 + 0.5 * math.sin(self._phase * 2 + s["ph"])
            a = int(50 * s["z"] * tw) + 20
            c = QColor(s["col"])
            c.setAlpha(int(min(140, a * 1.4)))
            p.setPen(QPen(c, max(0.6, s["z"] * 1.5)))
            p.setBrush(Qt.NoBrush)
            p.drawPoint(QPointF(s["x"], s["y"]))

        halo = QRadialGradient(w / 2, h * 0.33, w * 0.55)
        halo.setColorAt(0.0, QColor(0, 128, 255, 24))
        halo.setColorAt(0.6, QColor(0, 255, 255, 8))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), halo)

    def _paint_grid(self, p, w, h):
        g = QColor(CYAN)
        g.setAlpha(16)
        pen = QPen(g, 1)
        p.setPen(pen)
        step = 44
        yoff = self._phase * 26 % step
        for x in range(0, w + step, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + step, step):
            yy = int(y - yoff)
            p.drawLine(0, yy, w, yy)
            if yy >= 0:
                for x in range(0, w + step, step):
                    c = QColor(CYAN)
                    c.setAlpha(60)
                    p.setPen(QPen(c, 1))
                    p.drawPoint(x, yy)

    def _paint_matrix(self, p, w, h):
        seg = 13
        for m in self._matrix:
            c = QColor(m["col"])
            for i in range(m["len"]):
                yy = m["y"] - i * seg
                if yy < -seg or yy > h + seg:
                    continue
                a = m["a"] * (1 - i / m["len"])
                if i == 0:
                    a = min(0.9, a * 2.2)
                c.setAlpha(int(a * 255))
                p.setPen(QPen(c, 1))
                p.drawLine(int(m["x"]), int(yy), int(m["x"] + (1 - i / m["len"]) * 5), int(yy))

    def _paint_nodes(self, p, w, h):
        nodes = self._nodes
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                dx = a["x"] - b["x"]
                dy = a["y"] - b["y"]
                d = math.hypot(dx, dy)
                if d < 150:
                    c = QColor(CYAN)
                    c.setAlpha(int(70 * (1 - d / 150)))
                    p.setPen(QPen(c, 1))
                    p.drawLine(QPointF(a["x"], a["y"]), QPointF(b["x"], b["y"]))
        for nd in nodes:
            tw = 0.5 + 0.5 * math.sin(self._phase * 3 + nd["ph"])
            c = QColor(nd["col"])
            c.setAlpha(int(90 + 120 * tw))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            r = nd["s"] * (0.7 + 0.6 * tw)
            p.drawEllipse(QPointF(nd["x"], nd["y"]), r, r)
        for pl in self._pulses:
            t = pl["t"]
            x = pl["a"]["x"] + (pl["b"]["x"] - pl["a"]["x"]) * t
            y = pl["a"]["y"] + (pl["b"]["y"] - pl["a"]["y"]) * t
            c = QColor(MAGENTA)
            c.setAlpha(int(230 * (1 - t)))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, y), 3.2, 3.2)

    def _paint_scan(self, p, w, h):
        y = int(self._scan_y)
        band = QLinearGradient(0, y - 30, 0, y + 30)
        band.setColorAt(0.0, QColor(0, 255, 255, 0))
        band.setColorAt(0.5, QColor(0, 255, 255, 70))
        band.setColorAt(1.0, QColor(0, 255, 255, 0))
        p.fillRect(QRectF(0, y - 30, w, 60), band)
        c = QColor(0, 255, 255, 150)
        p.setPen(QPen(c, 1))
        p.drawLine(0, y, w, y)

    def _paint_glass(self, p, w, h):
        rect = self._panel_rect(w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 26, 26)

        p.setPen(Qt.NoPen)
        for dd in (4, 3, 2, 1):
            c = QColor(0, 0, 0, 80)
            p.setBrush(c)
            p.drawPath(path)
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(dd, dd, -dd, -dd), max(0, 26 - dd * 2), max(0, 26 - dd * 2))

        path = QPainterPath()
        path.addRoundedRect(rect, 26, 26)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(16, 30, 58, 195))
        g.setColorAt(0.45, QColor(9, 14, 34, 165))
        g.setColorAt(1.0, QColor(8, 12, 30, 180))
        p.setBrush(g)
        p.drawPath(path)

        bord = QLinearGradient(0, 0, w, h)
        bord.setColorAt(0.0, QColor(0, 255, 255, 230))
        bord.setColorAt(0.45, QColor(0, 128, 255, 80))
        bord.setColorAt(0.8, QColor(255, 0, 255, 60))
        bord.setColorAt(1.0, QColor(0, 255, 255, 210))
        p.setPen(QPen(bord, 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        top = QPainterPath()
        top.addRoundedRect(QRectF(6, 5, w - 12, h / 2), 22, 22)
        gg = QLinearGradient(0, 0, 0, h / 2)
        gg.setColorAt(0.0, QColor(255, 255, 255, 20))
        gg.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(gg)
        p.setPen(Qt.NoPen)
        p.drawPath(top)

    def _project(self, pt, cx, cy, f, scale):
        x, y, z = pt
        xp = cx + x * scale * f / (f + z)
        yp = cy + y * scale * f / (f + z)
        return xp, yp, z

    def _draw_cube(self, p, cx, cy, s, ang, ph):
        a = ang
        v = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    x = sx * math.cos(a) * s - sz * s * 0.6 * math.sin(a)
                    z = sx * math.sin(a) * s + sz * s * 0.6 * math.cos(a)
                    v.append((x, sy * s, z))
        pts = {}
        for idx, p3 in enumerate(v):
            pts[idx] = self._project(p3, cx, cy, 260, 1.15)
        c = QColor(0, 255, 255, 90)
        p.setPen(QPen(c, 1))
        edges = [(i, j) for i in range(8) for j in range(i + 1, 8) if (i ^ j) in (1, 2, 4)]
        for i, j in edges:
            p.drawLine(QPointF(pts[i][0], pts[i][1]), QPointF(pts[j][0], pts[j][1]))
        for idx, (px, py, pz) in pts.items():
            glow = 0.5 + 0.5 * math.sin(ph * 3 + idx)
            col = QColor(0, 255, 255, int(120 + 110 * glow))
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(px, py), 2.4, 2.4)

    def _draw_tetra(self, p, cx, cy, s, ang, ph):
        a = ang
        c3, s3 = 0.866, 0.5
        verts = [(0, -s, 0), (s * c3, s * s3, 0), (-s * c3, s * s3, 0), (0, 0, s * 1.2)]
        pts = {}
        for idx, p3 in enumerate(verts):
            x, y, z = p3
            xr = x * math.cos(a) - z * math.sin(a)
            zr = x * math.sin(a) + z * math.cos(a)
            pts[idx] = self._project((xr, y, zr), cx, cy, 260, 1.1)
        c = QColor(255, 0, 255, 70)
        p.setPen(QPen(c, 1))
        for i in range(4):
            for j in range(i + 1, 4):
                p.drawLine(QPointF(pts[i][0], pts[i][1]), QPointF(pts[j][0], pts[j][1]))
        for idx, (px, py, pz) in pts.items():
            glow = 0.5 + 0.5 * math.sin(ph * 3 + idx * 1.7)
            col = QColor(255, 0, 255, int(110 + 110 * glow))
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(px, py), 2.2, 2.2)

    def _paint_3d(self, p, w, h):
        self._draw_cube(p, w - 92, 96, 46, self._cube_ang, self._phase)
        self._draw_tetra(p, 92, 96, 42, self._tetra_ang, self._phase)

    def _paint_core(self, p, w, h):
        cx, cy = w / 2, 224
        sc = _c(STATE_COLORS.get(self.state, "#00FFFF"))
        t = time.time()

        for i in range(4):
            rr = 34 + i * 22 + math.sin(t * 1.2 + i) * 4
            c = QColor(sc)
            c.setAlpha(int(75 - i * 13))
            p.setPen(QPen(c, 1 if i else 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        # glowing loading arcs (circular activity progress)
        arc_r = 60
        progress = 0.5 + 0.5 * math.sin(self._phase * 0.8)
        span = int(72 + 300 * (0.5 + 0.5 * math.sin(self._phase * 0.5)))
        pen = QPen(QColor(MAGENTA), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2),
                  int(self._orb_ang * 57), span * 16)
        pen2 = QPen(QColor(CYAN), 2)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawArc(QRectF(cx - arc_r - 7, cy - arc_r - 7, (arc_r + 7) * 2, (arc_r + 7) * 2),
                  int(-self._orb_ang * 40), int(120 * (0.4 + 0.6 * progress)) * 16)

        for i in range(14):
            a = -math.pi / 2 + i * 0.12
            r = 74 + math.sin(t * 2 + i) * 6
            x = cx + math.cos(a) * r
            y = cy + math.sin(a) * r
            c = QColor(CYAN if i % 2 == 0 else MATRIX)
            c.setAlpha(150)
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, y), 2.0, 2.6)

        core = QRadialGradient(cx, cy, 46)
        c1 = QColor(sc)
        c1.setAlpha(235)
        c2 = QColor(sc)
        c2.setAlpha(70)
        core.setColorAt(0.0, c1)
        core.setColorAt(0.6, c2)
        core.setColorAt(1.0, QColor(6, 10, 22, 0))
        p.setBrush(core)
        pulse = 1 + 0.06 * math.sin(t * 2.4)
        p.setPen(QPen(QColor(sc.red(), sc.green(), sc.blue(), 150), 2))
        p.drawEllipse(QPointF(cx, cy), 40 * pulse, 40 * pulse)

        lv = int(sum(self.levels) / max(1, len(self.levels)) * 50)
        for i in range(8):
            a = i * 0.785
            x1 = cx + math.cos(a) * 40
            y1 = cy + math.sin(a) * 40
            x2 = cx + math.cos(a) * (40 + lv * 0.35)
            y2 = cy + math.sin(a) * (40 + lv * 0.35)
            c = QColor(sc)
            c.setAlpha(200)
            p.setPen(QPen(c, 2))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _card_rects(self, w, h):
        margin = 36
        gap = 14
        cw = (w - margin * 2 - gap * 3) / 4
        ch = 96
        y = h - 148
        return [QRectF(margin + i * (cw + gap), y, cw, ch) for i in range(4)]

    def _sheared_card_path(self, r, shear):
        p = QPainterPath()
        dx = r.height() * shear
        p.moveTo(r.left() + dx, r.top())
        p.lineTo(r.right() + dx, r.top())
        p.lineTo(r.right() - dx, r.bottom())
        p.lineTo(r.left() - dx, r.bottom())
        p.closeSubpath()
        return p

    def _paint_cards(self, p, w, h):
        rects = self._card_rects(w, h)
        labels = ["CPU", "RAM", "LEARNING", "SCREEN"]
        keys = ["cpu", "ram", "learn", "screen"]
        f_small = QFont("Segoe UI", 8)
        f_val = QFont("Segoe UI Semibold", 15)
        f_val.setWeight(63)
        hover = self._hover if isinstance(self._hover, int) and 0 <= self._hover < 4 else -1

        for i, r in enumerate(rects):
            shear = 0.05 if i % 2 == 0 else -0.05       # perspective / dashboard angle
            path = self._sheared_card_path(r, shear)
            glow = _tri(0.0, 0.4, math.sin(self._phase * 2 + i * 1.5) * 0.5 + 0.5)

            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0.0, QColor(12, 24, 46, 200))
            g.setColorAt(1.0, QColor(7, 12, 30, 155))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawPath(path)

            bord = QColor(MAGENTA if i % 2 else CYAN)
            bord.setAlpha(255 if hover == i else int(120 + 60 * glow))
            p.setPen(QPen(bord, 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

            p.setFont(f_small)
            p.setPen(QColor(160, 190, 220, 210))
            p.drawText(QRectF(r.left() + 12, r.top() + 8, r.width() - 16, 16), Qt.AlignLeft | Qt.AlignVCenter, labels[i])
            p.setFont(f_val)
            c = QColor(bord)
            c.setAlpha(220)
            p.setPen(c)
            p.drawText(QRectF(r.left() + 12, r.top() + 30, r.width() - 24, 34), Qt.AlignLeft | Qt.AlignVCenter,
                       self._card_data.get(keys[i], "—"))

        # hover glow halo
        if hover != -1:
            r = rects[hover]
            halo = QRadialGradient(r.center().x(), r.center().y(), r.width() * 0.7)
            halo.setColorAt(0.0, QColor(0, 255, 255, 45))
            halo.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(halo)
            p.setPen(Qt.NoPen)
            p.drawEllipse(r.center(), r.width() * 0.7, r.width() * 0.7)

    def _paint_ripples(self, p, w, h):
        for r in self._ripples:
            t = r["t"]
            rad = 8 + t * 46
            c = QColor(0, 255, 255, int(180 * (1 - t)))
            p.setPen(QPen(c, 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(r["x"], r["y"]), rad, rad)
            c2 = QColor(255, 0, 255, int(120 * (1 - t)))
            p.setPen(QPen(c2, 1))
            p.drawEllipse(QPointF(r["x"], r["y"]), rad * 0.6, rad * 0.6)

    def _paint_header(self, p, w, h):
        f = QFont("Segoe UI Semibold", 12)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(f)
        p.setPen(QColor(223, 240, 250, 235))
        p.drawText(QRectF(30, 18, w - 220, 24), Qt.AlignLeft | Qt.AlignVCenter, "D U D E")
        f2 = QFont("Segoe UI Semilight", 9)
        f2.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        p.setFont(f2)
        c = QColor(self._status_color)
        c.setAlpha(210)
        p.setPen(c)
        p.drawText(QRectF(30, 42, w - 220, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   f"NEURAL COMMAND CENTRE  ▲  {self.status.upper()}")

        close_r = QRectF(w - 46, 18, 28, 28)
        hover = self._hover == "close"
        c = QColor(255, 0, 60, 220 if hover else 150)
        if hover:
            p.setBrush(QColor(255, 0, 60, 40))
            p.setPen(Qt.NoPen)
            p.drawEllipse(close_r.center(), 15, 15)
        p.setPen(QPen(c, 2))
        p.drawLine(QPointF(close_r.left() + 9, close_r.top() + 9), QPointF(close_r.right() - 9, close_r.bottom() - 9))
        p.drawLine(QPointF(close_r.right() - 9, close_r.top() + 9), QPointF(close_r.left() + 9, close_r.bottom() - 9))

    def _paint_scanline_footer(self, p, w, h):
        y = h - 88
        c = QColor(0, 255, 255, int(40 * (0.5 + 0.5 * math.sin(self._phase * 3))))
        p.setPen(QPen(c, 1))
        p.drawLine(16, y, w - 16, y)

    def _paint_footer(self, p, w, h):
        f = QFont("Segoe UI", 10)
        p.setFont(f)
        p.setPen(QColor(170, 205, 235, 210))
        p.drawText(QRectF(34, h - 76, w - 68, 22), Qt.AlignCenter, self.chat_text or "STANDBY")
        f2 = QFont("Consolas", 8)
        p.setFont(f2)
        p.setPen(QColor(90, 130, 170, 150))
        p.drawText(QRectF(34, h - 26, w - 68, 16), Qt.AlignCenter,
                   f"D.U.D.E. ONLINE  ·  {self.ctx_text.upper()}")

    def _entry_rect(self, w, h):
        return QRectF(int(w / 2 - 170), h - 46, 340, 30)

    def _paint_corners(self, p, w, h):
        bl = 26 * self._load_t
        dash = 12 * self._load_t
        col = QColor(CYAN)
        col.setAlpha(210)
        p.setPen(QPen(col, 3))
        for (dx, dy) in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            x = (w / 2) + (w / 2 - 8) * dx
            y = (h / 2) + (h / 2 - 8) * dy
            p.drawLine(QPointF(x, y), QPointF(x + bl * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + bl * dy))
            c2 = QColor(MAGENTA)
            c2.setAlpha(int(150 * self._load_t))
            p.setPen(QPen(c2, 2))
            p.drawLine(QPointF(x + bl * dx, y), QPointF(x + (bl + dash) * dx, y))
            p.drawLine(QPointF(x, y + bl * dy), QPointF(x, y + (bl + dash) * dy))
            p.setPen(QPen(col, 3))

    # ---------- show / interaction ----------
    def _hit(self, pos):
        x, y = pos.x(), pos.y()
        close_r = QRectF(self.width() - 46, 18, 28, 28)
        if close_r.contains(x, y):
            return "close"
        for i, r in enumerate(self._card_rects(self.width(), self.height())):
            if r.adjusted(-6, -6, 6, 6).contains(x, y):
                return i
        return None

    def mouseMoveEvent(self, e):
        self._hover = self._hit(e.pos())
        if self._drag is None or not (e.buttons() & Qt.LeftButton):
            if self._drag and not (e.buttons() & Qt.LeftButton):
                self._drag = None
            return
        gx, gy = e.globalPos().x(), e.globalPos().y()
        dx = gx - self._pressed[0]
        dy = gy - self._pressed[1]
        if abs(dx) > 2 or abs(dy) > 2:
            self._moved = True
        self.move(self._win_x + dx, self._win_y + dy)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pressed = (e.globalPos().x(), e.globalPos().y())
            self._win_x, self._win_y = self.x(), self.y()
            self._drag = (e.globalPos().x(), e.globalPos().y())
            self._moved = False

    def mouseReleaseEvent(self, e):
        was_drag = self._moved
        self._drag = None
        if was_drag:
            return
        hit = self._hit(e.pos())
        if hit == "close":
            self.hide()
        elif isinstance(hit, int):
            self._ripples.append({"x": e.x(), "y": e.y(), "t": 0.0})
            labels = ["CPU", "RAM", "LEARNING", "SCREEN"]
            keys = ["cpu", "ram", "learn", "screen"]
            self.push_chat("dude", f"Telemetry {labels[hit]} = {self._card_data.get(keys[hit], '—')}")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            self.entry.clearFocus()
        elif self.entry.hasFocus():
            super().keyPressEvent(e)
        else:
            self.entry.setFocus()
            self.entry.setCursorPosition(100)

    def resizeEvent(self, e):
        if self.entry.isVisible():
            r = self._entry_rect(self.width(), self.height())
            self.entry.setGeometry(int(r.left()), int(r.top()), int(r.width()), int(r.height()))

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            if self._scr:
                self.move(self._scr.center().x() - self.width() // 2, int(self._scr.height() * 0.09))
            self.show()
            self.raise_()
            self.activateWindow()
            self.entry.show()
            self._load_anim.stop()
            self._load_anim.start()
            self._load_t = 0.0
            self.update()


def launch_holo():
    return HoloUI()
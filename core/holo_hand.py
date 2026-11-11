"""Hand-perception panel for the Neural Command Centre (PyQt5/QPainter).

Display-only observability over the EXISTING Phase 16 sensor state:
latest camera frame (mirrored), landmark skeletons, confidences,
screen mapping, pointed-target readout, and sensor status. No input
handling, no cognition, no actions — pixels only. Every read is guarded
so a dead tracker/camera can never break the Command Centre, and the
panel draws nothing at all unless the camera is actually running.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

FINGERS = ((1, 2), (2, 3), (3, 4),
           (5, 6), (6, 7), (7, 8),
           (9, 10), (10, 11), (11, 12),
           (13, 14), (14, 15), (15, 16),
           (17, 18), (18, 19), (19, 20))
PALM = ((0, 1), (0, 5), (0, 17), (5, 9), (9, 13), (13, 17), (5, 17))

try:
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QColor, QFont, QImage, QPen
    _QT = True
except Exception:
    _QT = False

CYAN = (0, 255, 255)
GREEN = (80, 255, 170)
AMBER = (255, 190, 80)
RED = (255, 110, 110)
DIM = (150, 200, 210)
BG = (8, 18, 28, 205)
LINE = (0, 255, 255, 90)


class HoloHandPanel:
    """Paints live hand-sensor state inside HoloUI (helper, not a widget)."""

    def __init__(self):
        self.tracker = None
        self.visual_bus = None
        self._img = None
        self._img_w = 0
        self._img_h = 0
        self._cache = {
            "hands": 0, "stable": ("unknown", 0), "status": {},
            "hands_detail": [], "tip_screen": None, "map_live": False,
            "map_calibrated": False, "target": None, "target_why": "",
            "hand_age": -1.0, "visual_age": -1.0, "cam_index": -1,
            "model_ok": False, "lens": "?", "spatial": {},
        }

    def attach(self, tracker, visual_bus) -> None:
        self.tracker = tracker
        self.visual_bus = visual_bus

    def visible(self) -> bool:
        try:
            return self.tracker is not None and bool(
                self.tracker.camera_on)
        except Exception:
            return False

    # ---------------- data (UI thread, non-blocking guarded reads) ----

    def refresh(self) -> None:
        """Pull latest sensor state (bounded: one image, small dicts)."""
        c = self._cache
        try:
            tr = self.tracker
            if tr is None or not bool(tr.camera_on):
                self._img = None
                c.update(hands=0, hands_detail=[], target=None,
                         tip_screen=None, spatial={})
                return
            try:
                c["cam_index"] = int(getattr(tr.camera, "index", -1))
            except Exception:
                pass
            try:
                c["model_ok"] = bool(tr.perception.available)
            except Exception:
                pass
            try:
                c["lens"] = "COVERED?" if bool(
                    tr.camera.lens_covered) else "CLEAR"
            except Exception:
                c["lens"] = "?"
            # Latest frame only (replaced, never accumulated).
            try:
                frame, _fid = tr.camera.latest()
                if frame is not None and getattr(
                        frame, "size", 0) and len(frame.shape) == 3:
                    import cv2
                    scale = 264.0 / max(1, int(frame.shape[1]))
                    nh = max(1, int(frame.shape[0] * scale))
                    small = cv2.resize(frame, (264, nh))
                    rgb = cv2.cvtColor(cv2.flip(small, 1),
                                       cv2.COLOR_BGR2RGB)
                    if not rgb.flags["C_CONTIGUOUS"]:
                        rgb = rgb.copy()
                    h, w, _ch = rgb.shape
                    if _QT and h > 0 and w > 0:
                        from PyQt5.QtGui import QImage as _QI
                        self._img = _QI(
                            rgb.data, w, h, 3 * w,
                            _QI.Format_RGB888).copy()
                        self._img_w, self._img_h = w, h
            except Exception as e:
                log.warning(f"hand preview convert failed: {e}")
            try:
                st = tr.bus.get()
            except Exception:
                st = None
            if st is None:
                c.update(hands=0, hands_detail=[], hand_age=-1.0)
            else:
                try:
                    c["hand_age"] = st.age_seconds()
                except Exception:
                    c["hand_age"] = -1.0
                det = []
                try:
                    stable_by_hand = tr.stable_by_hand()
                except Exception:
                    stable_by_hand = {}
                try:
                    for index, h in enumerate((st.hands or [])[:2]):
                        identity = str(getattr(h, "identity", "") or
                                       getattr(h, "label", "") or
                                       f"hand_{index}").lower()
                        stable_gesture, stable_votes = stable_by_hand.get(
                            identity, (None, 0))
                        det.append({
                            "label": getattr(h, "label", "?"),
                            "identity": identity,
                            "bbox": tuple(getattr(h, "bbox", ()) or ()),
                            "lm": [tuple(p) for p in
                                   (getattr(h, "landmarks", ()) or [])],
                            "hscore": float(getattr(
                                h, "handedness_score", 0.0) or 0.0),
                            "gesture": str(getattr(
                                getattr(h, "gesture", None), "value",
                                "unknown")),
                            "gconf": float(getattr(
                                h, "gesture_confidence", 0.0) or 0.0),
                            "stable_gesture": str(getattr(
                                getattr(stable_gesture, "value", stable_gesture),
                                "value", stable_gesture or "unknown")),
                            "stable_votes": int(stable_votes or 0),
                        })
                except Exception:
                    det = []
                c["hands"] = len(det)
                c["hands_detail"] = det
            try:
                g, votes = tr.stable()
                c["stable"] = (str(getattr(g, "value", g)), int(votes))
            except Exception:
                pass
            try:
                c["status"] = dict(tr.status() or {})
            except Exception:
                pass
            try:
                c["spatial"] = dict(tr.spatial_snapshot() or {})
            except Exception:
                c["spatial"] = {}
            # Fingertip -> screen is a legacy single-target observation.  A
            # two-hand state is never collapsed to an arbitrary primary hand;
            # both hands remain available in ``hands_detail``.
            c["tip_screen"] = None
            c["map_live"] = False
            try:
                c["map_calibrated"] = bool(
                    tr.mapper.calibrated
                    and tr.mapper.calibration_live())
            except Exception:
                c["map_calibrated"] = False
            try:
                pointers = list(c.get("spatial", {}).get("pointers", []) or [])
                if pointers and pointers[0].get("screen_xy") is not None:
                    c["tip_screen"] = tuple(pointers[0]["screen_xy"])
                    c["map_live"] = True
                elif (st is not None and not st.two_hands()
                      and st.hands and len(
                          getattr(st.hands[0], "landmarks", "") or []) >= 9):
                    # Compatibility fallback for a transient frame before
                    # the spatial layer publishes its contextual pointer.
                    tip = st.hands[0].landmarks[8]
                    c["tip_screen"] = tr.mapper.map(tip[0], tip[1])
                    c["map_live"] = bool(c["tip_screen"])
            except Exception:
                pass
            try:
                vb = self.visual_bus.get() if self.visual_bus else None
                c["visual_age"] = vb.age_seconds() if vb else -1.0
            except Exception:
                c["visual_age"] = -1.0
            # Pointed target or the honest reason there is none.
            c["target"], c["target_why"] = None, ""
            try:
                if self.visual_bus is not None:
                    c["target"] = tr.pointed_element(self.visual_bus)
            except Exception:
                pass
            if c["target"] is None:
                c["target_why"] = self._why_none(c)
        except Exception as e:
            log.warning(f"hand panel refresh failed: {e}")

    @staticmethod
    def _why_none(c) -> str:
        try:
            if c["hands"] == 0:
                return "NO HAND IN VIEW" if c["hand_age"] < 0 \
                    else "NO CURRENT TARGET"
            if c["stable"][0] not in ("pointing",):
                return "GESTURE NOT STABLE" if c["stable"][1] < 3 \
                    else "NOT POINTING"
            if c["hand_age"] < 0 or c["hand_age"] > 1.0:
                return "HAND STATE STALE"
            if c["visual_age"] < 0 or c["visual_age"] > 5.0:
                return "VISUAL STATE STALE"
            return "OUTSIDE RESOLVED UI REGION"
        except Exception:
            return "NO CURRENT TARGET"

    # ---------------- painting (QPainter, display only) ---------------

    def paint(self, qp, w) -> None:
        """Draw the panel. No-op unless the camera is running."""
        if not _QT or not self.visible():
            return
        try:
            c = self._cache
            x0, y0 = 30.0, 64.0
            pw = 300.0
            lines = self._status_lines(c)
            ph = 210.0 + (self._img_h or 0) + len(lines) * 13.0 + 66.0
            qp.save()
            try:
                qp.setPen(QPen(QColor(*LINE), 1))
                qp.setBrush(QColor(*BG))
                qp.drawRoundedRect(QRectF(x0, y0, pw, ph), 10, 10)
                qp.setFont(QFont("Segoe UI", 9, QFont.Bold))
                qp.setPen(QColor(*CYAN))
                qp.drawText(int(x0 + 12), int(y0 + 20), "HAND PERCEPTION")
                st = c["status"].get("state", "?")
                qp.setPen(QColor(*(
                    GREEN if st == "AVAILABLE"
                    else AMBER if st == "DEGRADED" else RED)))
                qp.drawText(int(x0 + 190), int(y0 + 20), st)
                iy = y0 + 30
                if self._img is not None:
                    qp.drawImage(QRectF(x0 + 18, iy, 264,
                                        self._img_h), self._img)
                    self._paint_hands(qp, x0 + 18, iy, 264,
                                      self._img_h, c)
                    iy += self._img_h + 8
                else:
                    qp.setFont(QFont("Segoe UI", 8))
                    qp.setPen(QColor(*DIM))
                    qp.drawText(int(x0 + 18), int(iy + 16),
                                "NO FRAME")
                    iy += 26
                qp.setFont(QFont("Segoe UI", 8))
                for ln in lines:
                    qp.setPen(QColor(*ln[1]))
                    qp.drawText(int(x0 + 12), int(iy + 11), ln[0][:52])
                    iy += 13.0
                iy += 4
                # Screen-map mini-widget.
                mw, mh = 120.0, 68.0
                qp.setPen(QPen(QColor(*LINE), 1))
                qp.drawRect(QRectF(x0 + 12, iy, mw, mh))
                if c["tip_screen"] is not None:
                    try:
                        vx, vy, vw, vh = self._display_rect()
                        sx, sy = c["tip_screen"]
                        dx = max(1.0, min(float(mw - 4),
                                          (sx - vx) / max(1, vw) * mw))
                        dy = max(1.0, min(float(mh - 4),
                                          (sy - vy) / max(1, vh) * mh))
                        qp.setPen(QPen(QColor(*GREEN), 5))
                        qp.drawPoint(int(x0 + 12 + dx),
                                     int(iy + dy))
                    except Exception:
                        pass
                qp.setFont(QFont("Segoe UI", 7))
                qp.setPen(QColor(*DIM))
                qp.drawText(int(x0 + 140), int(iy + 14),
                            "CALIBRATED" if c["map_calibrated"]
                            else "UNCALIBRATED MAP")
                if c["tip_screen"] is not None:
                    qp.drawText(
                        int(x0 + 140), int(iy + 28),
                        "tip->(%d,%d)" % (int(c["tip_screen"][0]),
                                          int(c["tip_screen"][1])))
                # "What am I pointing at?"
                iy += mh + 12
                tgt = c["target"]
                qp.setFont(QFont("Segoe UI", 8, QFont.Bold))
                if tgt is not None:
                    qp.setPen(QColor(*GREEN))
                    qp.drawText(int(x0 + 12), int(iy),
                                "AT: %s" % str(
                                    tgt.get("element", "?"))[:30])
                    qp.setFont(QFont("Segoe UI", 7))
                    qp.setPen(QColor(*DIM))
                    qp.drawText(int(x0 + 12), int(iy + 13),
                                "%s | %s" % (
                                    tgt.get("kind", "?"),
                                    str(tgt.get("provenance", ""))[:34]))
                else:
                    qp.setPen(QColor(*AMBER))
                    qp.drawText(int(x0 + 12), int(iy),
                                c["target_why"] or "NO CURRENT TARGET")
            finally:
                qp.restore()
        except Exception as e:
            log.warning(f"hand panel paint failed: {e}")

    def _paint_hands(self, qp, ox, oy, pw, ph, c) -> None:
        """Skeleton + bbox + labels over the mirrored preview."""
        try:
            for idx, h in enumerate(c["hands_detail"]):
                lm = h.get("lm") or []
                if len(lm) < 21:
                    continue
                col = QColor(*CYAN) if idx == 0 else QColor(*GREEN)

                def _visible(p):
                    try:
                        return (0.0 <= float(p[0]) <= 1.0
                                and 0.0 <= float(p[1]) <= 1.0)
                    except Exception:
                        return False

                def _pt(p):
                    # Preview is mirrored: flip x back for overlay.
                    return ((1.0 - p[0]) * pw + ox, p[1] * ph + oy)

                qp.setPen(QPen(col, 2))
                for a, b in list(FINGERS) + list(PALM):
                    try:
                        if not (_visible(lm[a]) and _visible(lm[b])):
                            continue
                        xa, ya = _pt(lm[a])
                        xb, yb = _pt(lm[b])
                        qp.drawLine(int(xa), int(ya), int(xb), int(yb))
                    except Exception:
                        continue
                try:
                    if _visible(lm[8]):
                        xt, yt = _pt(lm[8])
                        qp.setPen(QPen(QColor(*GREEN), 6 if
                                       h.get("gesture") == "pointing" else 3))
                        qp.drawPoint(int(xt), int(yt))
                except Exception:
                    pass
                try:
                    bx, by, bw, bh = h.get("bbox") or (0, 0, 0, 0)
                    qp.setPen(QPen(col, 1))
                    qp.drawRect(QRectF((1.0 - bx - bw) * pw + ox,
                                       by * ph + oy, bw * pw, bh * ph))
                except Exception:
                    pass
                qp.setFont(QFont("Segoe UI", 7, QFont.Bold))
                qp.setPen(col)
                try:
                    qp.drawText(
                        int(ox + 4), int(oy + 12 + idx * 13),
                        "%s %s %.2f %s" % (
                            h.get("label", "?").upper(),
                            h.get("gesture", "?"),
                            float(h.get("gconf", 0.0)),
                            "STABLE" if h.get("stable_votes", 0) >= 3
                            and h.get("stable_gesture") == h.get("gesture")
                            else ""))
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"hand skeleton paint failed: {e}")

    def _status_lines(self, c) -> list:
        try:
            det = []
            for h in c["hands_detail"]:
                det.append("%.2f" % float(h.get("hscore", 0.0)))
            return [
                ("cam %s | model %s | lens %s" % (
                    c["cam_index"],
                    "OK" if c["model_ok"] else "MISSING",
                    c.get("lens", "?")), DIM),
                ("hands %d [hand score %s]" % (c["hands"], ",".join(det)),
                 CYAN),
                ("stable %s (%d/5)" % (c["stable"][0],
                                       c["stable"][1]),
                 GREEN if c["stable"][1] >= 3 else DIM),
                ("world %s | pointers %d" % (
                    c.get("spatial", {}).get("world", "idle"),
                    len(c.get("spatial", {}).get("pointers", []))),
                 CYAN if c.get("spatial", {}).get("pointers") else DIM),
                ("hand age %.1fs | visual age %.1fs" % (
                    c["hand_age"], c["visual_age"]), DIM),
            ]
        except Exception:
            return [("hand status unavailable", DIM)]

    def _display_rect(self):
        try:
            tr = self.tracker
            if tr is not None:
                return tr.mapper.display_rect()
        except Exception:
            pass
        return (0, 0, 1920, 1080)

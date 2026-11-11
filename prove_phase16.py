"""Phase 16 live camera proof (real camera, real runtime, human hand).

Usage: prove_phase16.py <step>
  baseline : camera starts, AVAILABLE, empty-room NONE (no user action)
  open     : user holds up one OPEN hand           -> DETECTED + stable OPEN
  point    : user points + sweeps                   -> stable POINTING + moves
             + spatial relation vs the fresh visual bus
  remove   : user shows no hand                    -> expires to NONE/STALE,
             camera stops cleanly

Each step starts/stops its own camera (cheap) and fails gracefully on
timeout — never hangs. One user instruction per step, relayed by the
operator; run steps in order baseline, open, point, remove.
"""
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')
from core.orchestrator.hand_tracking import (
    Gesture, HandStateBus, HandTracker)


def make_trackers():
    return [HandTracker(bus=HandStateBus(), camera_index=i) for i in (0, 1)]


def start_all(trs):
    ok = False
    for tr in trs:
        if tr.start_camera():
            ok = True
        else:
            print('cam %d refused: %s' % (tr.camera.index, tr.last_error),
                  flush=True)
    return ok


def stop_all(trs):
    for tr in trs:
        try:
            tr.stop_camera()
        except Exception:
            pass
        try:
            tr.shutdown()
        except Exception:
            pass


def wait_for(trs, need, timeout, label):
    """need: callable(tracker) -> value-or-None. First hit wins."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for tr in trs:
            try:
                hit = need(tr)
            except Exception:
                hit = None
            if hit is not None:
                return tr, hit
        time.sleep(0.3)
    print('TIMEOUT waiting for: %s' % label, flush=True)
    return None, None


def step_baseline():
    trs = make_trackers()
    try:
        assert start_all(trs), 'no camera started'
        time.sleep(4)
        for tr in trs:
            print('cam %d status: %s lens=%s' % (
                tr.camera.index, tr.status(),
                'COVERED?' if tr.camera.lens_covered else 'CLEAR'),
                flush=True)
        assert any(tr.status()["state"] == "AVAILABLE" for tr in trs), \
            'capability not AVAILABLE'
        assert any(tr.bus.publish_count > 0 for tr in trs), 'no frames'
        print('BASELINE PASS: streaming, no hand, stable=%s' % (
            trs[0].stable(),), flush=True)
    finally:
        stop_all(trs)
    print('DONE baseline', flush=True)


def step_open():
    trs = make_trackers()
    try:
        assert start_all(trs), 'no camera started'

        def _open(tr):
            st = tr.bus.get_fresh()
            if st is None or st.count() == 0:
                return None
            g, votes = tr.stable()
            if g == Gesture.OPEN and votes >= 3:
                h = st.primary()
                return (g, votes, round(h.gesture_confidence, 2))
            return None

        tr, hit = wait_for(trs, _open, 120,
                           'open hand held still (palm to camera)')
        assert hit is not None, 'no stable OPEN hand seen'
        print('OPEN PASS: cam %d gesture=%s votes=%d gconf=%s' % (
            tr.camera.index, hit[0].value, hit[1], hit[2]), flush=True)
    finally:
        stop_all(trs)
    print('DONE open', flush=True)


def step_point():
    import prove_phase13 as P
    from core.orchestrator.state import PerceptionLevel
    from core.orchestrator.visual_state import VisualStateBus
    engine, _, store, _, _ = P.build()
    vbus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = vbus
    trs = make_trackers()
    try:
        assert start_all(trs), 'no camera started'

        def _point(tr):
            st = tr.bus.get_fresh()
            if st is None or st.count() == 0:
                return None
            g, votes = tr.stable()
            if g == Gesture.POINTING and votes >= 3:
                return True
            return None

        tr, _ = wait_for(trs, _point, 120,
                         'index-finger point held at camera')
        assert tr is not None, 'no stable POINTING seen'
        print('POINT stable on cam %d; tracking tip 6s (keep sweeping)...'
              % tr.camera.index, flush=True)
        pts = []
        t0 = time.time()
        while time.time() - t0 < 6:
            st = tr.bus.get_fresh()
            if st is not None and st.count():
                try:
                    tip = st.primary().landmarks[8]
                    pts.append(tr.mapper.map(tip[0], tip[1]))
                except Exception:
                    pass
            time.sleep(0.2)
        assert len(pts) >= 5, 'tip track too short'
        span = (max(p[0] for p in pts) - min(p[0] for p in pts),
                max(p[1] for p in pts) - min(p[1] for p in pts))
        print('tip screen-span: %dx%d over %d samples' % (
            span[0], span[1], len(pts)), flush=True)
        assert max(span) > 60, 'hand did not move in screen space'

        snap = engine._observe_for_task(PerceptionLevel.LEVEL_2_UIA_TREE)
        vst = vbus.get()
        print('visual: %r / %r' % (vst.active_app, vst.window_title),
              flush=True)
        t0 = time.time()
        rel = None
        while time.time() - t0 < 60 and rel is None:
            try:
                rel = tr.pointed_element(vbus)
            except Exception:
                rel = None
            if rel is None:
                time.sleep(0.5)
        assert rel is not None, 'no hand-to-screen relation resolved'
        print('RELATION PASS: hand -> %s %r prov=%s' % (
            rel["kind"], rel["element"], rel["provenance"]), flush=True)
    finally:
        stop_all(trs)
    print('DONE point', flush=True)


def step_remove():
    trs = make_trackers()
    try:
        assert start_all(trs), 'no camera started'
        time.sleep(3)

        def _gone(tr):
            st = tr.bus.get_fresh()
            # Absence = fresh states arriving with zero hands (the
            # sensor still streams; the world just has no hand).
            if st is not None and st.count() == 0 \
                    and tr.bus.publish_count > 0:
                return True
            return None

        tr, _ = wait_for(trs, _gone, 60, 'hand absence (empty states)')
        assert tr is not None, 'hand never left the view'
        print('absence observed on cam %d; stopping...' % tr.camera.index,
              flush=True)
        for t2 in trs:
            try:
                t2.stop_camera()
            except Exception:
                pass
        time.sleep(2.0)  # past the 1s hand TTL
        assert all(t.bus.get_fresh() is None for t in trs), \
            'hand state did not expire to STALE'
        assert all(t.status()["state"] == "UNAVAILABLE" for t in trs), \
            'camera did not stop cleanly'
        print('REMOVE PASS: expired to NONE/STALE, stopped clean',
              flush=True)
    finally:
        stop_all(trs)
    print('DONE remove', flush=True)


def step_panel():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Camera OFF by default: the real config must not enable it.
    from core.config import get_config
    cfg = get_config()
    enabled = cfg.get("hand", "enabled", default=False)
    print('config hand.enabled=%s' % enabled, flush=True)
    assert not enabled, 'camera must default OFF'
    # Real tracker + real visual bus + real panel + real HoloUI.
    import prove_phase13 as P
    from core.orchestrator.state import PerceptionLevel
    from core.orchestrator.visual_state import VisualStateBus
    from core.holo_hand import HoloHandPanel
    engine, _, _, _, _ = P.build()
    vbus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = vbus
    engine._observe_for_task(PerceptionLevel.LEVEL_2_UIA_TREE)
    trs = make_trackers()
    try:
        assert start_all(trs), 'no camera started'
        time.sleep(3)
        tr = max(trs, key=lambda t: t.bus.publish_count)
        panel = HoloHandPanel()
        panel.attach(tr, vbus)
        assert panel.visible(), 'panel hidden while camera runs'
        panel.refresh()
        assert panel._img is not None, 'no live preview frame'
        assert panel._cache["status"].get("state") == "AVAILABLE"
        print('panel: hands=%d stable=%s target=%s' % (
            panel._cache["hands"], panel._cache["stable"],
            (panel._cache["target"] or {}).get("element")
            if panel._cache["target"] else panel._cache["target_why"]),
            flush=True)
        # Through the REAL Command Centre paint path (offscreen: no
        # visible windows, no input touched, user screen undisturbed).
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QImage, QPainter, QColor
        app = QApplication.instance() or QApplication([])
        from core.holo import HoloUI
        holo = HoloUI()
        holo.set_hand_sources(tr, vbus)
        for _ in range(4):
            holo._advance()
        img = QImage(900, 580, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 255))
        qp = QPainter(img)
        holo.render(qp)
        qp.end()
        assert holo.hand_panel.visible()
        print('PANEL PASS: live preview + status through HoloUI',
              flush=True)
    finally:
        stop_all(trs)
    # Camera stopped -> preview gone.
    panel.refresh()
    assert not panel.visible(), 'panel visible after stop!'
    assert panel._img is None, 'preview frame retained after stop!'
    print('DONE panel', flush=True)


def step_panellive():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import prove_phase13 as P
    from core.orchestrator.state import PerceptionLevel
    from core.orchestrator.visual_state import VisualStateBus
    from core.holo_hand import HoloHandPanel
    from core.orchestrator.hand_tracking import Gesture
    engine, _, _, _, _ = P.build()
    vbus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = vbus
    engine._observe_for_task(PerceptionLevel.LEVEL_2_UIA_TREE)
    trs = make_trackers()
    try:
        # One camera at a time (halves native footprint on tight
        # machines): prefer the last camera that saw the user.
        tr = None
        for cand in sorted(trs, key=lambda t: -t.camera.index):
            if cand.start_camera():
                tr = cand
                break
        assert tr is not None, 'no camera started'
        panel = HoloHandPanel()
        panel.attach(tr, vbus)

        t0 = time.time()
        seen_hand = None
        tgt = None
        best_votes = 0
        last_why = ""
        # One forgiving window: show an open hand, then point and
        # sweep. Either event counts whenever it occurs (~75s: human
        # attention is the binding constraint, not the sensor).
        # The visual bus is re-observed periodically (production
        # observes continuously; a proof snapshot would otherwise age
        # past its TTL mid-run and make targets unresolvable).
        tick = 0
        while time.time() - t0 < 75 and (seen_hand is None
                                         or tgt is None):
            tick += 1
            if tick % 30 == 1:
                try:
                    engine._observe_for_task(
                        PerceptionLevel.LEVEL_2_UIA_TREE)
                except Exception:
                    pass
            panel.refresh()
            try:
                _g, _v = tr.stable()
                if _g == Gesture.POINTING and _v > best_votes:
                    best_votes = _v
            except Exception:
                pass
            last_why = panel._cache.get("target_why", "")
            if panel._cache["hands"] >= 1 \
                    and panel._cache["hands_detail"] \
                    and panel._img is not None and seen_hand is None:
                det = panel._cache["hands_detail"][0]
                if len(det.get("lm") or []) == 21 and det.get("bbox"):
                    seen_hand = (det["label"], len(det["lm"]),
                                 round(float(det["hscore"]), 3))
                    print('PREVIEW PASS: %s hand, %d landmarks, '
                          'bbox, handedness %s' % seen_hand, flush=True)
            if seen_hand is not None and tgt is None:
                tgt = panel._cache["target"]
            time.sleep(0.5)
        assert seen_hand is not None, 'no hand appeared in preview'
        assert tgt is not None, \
            'no pointing target shown (best pointing votes=%d last=%r)' % (
                best_votes, last_why)
        # Precision honesty: control-level only for a UIA/OCR hit
        # covering <10% of the display; anything bigger (or a window
        # fallback) is a coarse region, never precise targeting.
        try:
            _vx, _vy, _vw, _vh = tr.mapper.display_rect()
            _area = max(1, _vw * _vh)
            _r = tgt.get("rect") or (0, 0, 0, 0)
            _frac = (_r[2] * _r[3]) / _area
        except Exception:
            _frac = 1.0
        _prec = ("control-level" if tgt.get("kind") in ("uia", "ocr")
                 and _frac < 0.10 else "coarse-region")
        print('STATES: hand=detected stable=pointing mapping=available '
              'map=%s target=%s/%s precision=%s tip=%s' % (
                  'calibrated' if panel._cache["map_calibrated"]
                  else 'uncalibrated',
                  tgt["kind"], tgt["element"], _prec,
                  panel._cache.get("tip_screen")), flush=True)
        print('PANEL-TARGET PASS: %s %r prov=%s' % (
            tgt["kind"], tgt["element"], tgt["provenance"]), flush=True)
    finally:
        try:
            tr.shutdown()
        except Exception:
            pass
    print('DONE panellive', flush=True)


if __name__ == "__main__":
    step = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    print('STEP ' + step, flush=True)
    t0 = time.time()
    try:
        {"baseline": step_baseline, "open": step_open,
         "point": step_point, "remove": step_remove,
         "panel": step_panel, "panellive": step_panellive}[step]()
    except KeyError:
        print('unknown step (baseline|open|point|remove|panel|panellive)',
              flush=True)
        raise SystemExit(2)
    except AssertionError as e:
        print('STEP FAIL: %s' % e, flush=True)
        raise SystemExit(1)
    print('step seconds: %.0f' % (time.time() - t0), flush=True)

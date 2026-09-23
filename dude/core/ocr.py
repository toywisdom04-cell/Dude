"""Shared tesseract wiring for DUDE's screen-reading pipeline.

The user keeps the tesseract binary (and its tessdata pack) on the machine but
outside the default install path, so ``pytesseract`` alone finds nothing. This
module points pytesseract at the configured binary (config ``observe.ocr_path``),
sets ``TESSDATA_PREFIX`` so language files load, and exposes one cheap
``ocr_image()`` call used by the observer, the continuous watcher and the
on-demand "look at the screen" tool.
"""
import os

from core.config import get_config

_cmd = None
_tesseract_available = False


def ensure_tesseract():
    global _cmd, _tesseract_available
    if _cmd is not None:
        return _cmd
    cfg = get_config()
    exe = (cfg.get("observe", "ocr_path", default="") or "").strip()
    if not exe or not os.path.exists(exe):
        exe = "tesseract"
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = exe
    except Exception:
        pass
    d = os.path.dirname(exe)
    td = os.path.join(d, "tessdata")
    if os.path.isdir(td):
        os.environ.setdefault("TESSDATA_PREFIX", td)
    _cmd = exe
    
    # Check if tesseract is actually available
    try:
        import pytesseract
        import subprocess
        result = subprocess.run([exe, "--version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            _tesseract_available = True
        else:
            _tesseract_available = False
    except Exception:
        _tesseract_available = False
    _cmd = exe
    return exe


def is_tesseract_available():
    """Check if tesseract OCR is available."""
    global _tesseract_available
    if not _tesseract_available:
        ensure_tesseract()
    return _tesseract_available


def ocr_image(img, psm=None, timeout=10):
    """Read visible text from a PIL image. Returns '' on any failure so callers
    never crash; binarises the frame first for clean modern-screen OCR."""
    if not is_tesseract_available():
        return "[OCR unavailable: tesseract not installed or not in PATH]"
    
    ensure_tesseract()
    try:
        import pytesseract

        if img is None:
            return ""
        gray = img.convert("L")
        gray = gray.point(lambda v: 0 if v < 140 else 255)
        cfg = "-l eng" + (f" --psm {psm}" if psm else " --psm 6")
        text = pytesseract.image_to_string(gray, config=cfg, timeout=timeout)
        return (text or "").strip()
    except Exception:
        return "[OCR error: tesseract failed]"
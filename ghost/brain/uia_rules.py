"""Hard-stop rules shared by guard.py (client) and uia_host.py (UIA helper).

Pure Python: no uiautomation / COM import here, so guard.py can load it in the
main process without touching UI Automation.
"""
from __future__ import annotations

import ctypes
import re
import time

_DANGER = (r"send|pay|pay now|purchase|buy|checkout|check out|delete|remove|submit|transfer|"
           r"confirm order|place order|sign out|log ?out|unsubscribe|post")
_DANGER_RE = re.compile(rf"\b(?:{_DANGER})\b", re.I)
# caption text: an imperative that starts with the word, or "click/press ... <word>"
_CAPTION_RE = re.compile(
    rf"(?:^|[.;:!]\s*)(?:now\s+|then\s+|and\s+)?(?:{_DANGER})\b"
    rf"|\b(?:click|press|tap|hit|select|choose|push|activate)\s+(?:on\s+)?(?:the\s+)?"
    rf"[\"'“‘]?(?:{_DANGER})\b",
    re.I,
)
_MAX_NAME = 40
_ANCESTOR_TYPES = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "SplitButtonControl"}
_SKIP_SELF_TYPES = {"WindowControl", "PaneControl", "DocumentControl", "TitleBarControl",
                    "EditControl", "DataItemControl", "TableControl", "ListControl"}


def is_dangerous_text(s: str) -> bool:
    """True for per-step captions like 'Click Send', 'Press Submit', 'Delete the row'.
    Use on captions / target names only, never on the user's task text."""
    return bool(s) and bool(_CAPTION_RE.search(s.strip()))


def _blocked(name: str) -> bool:
    return bool(name) and len(name) <= _MAX_NAME and bool(_DANGER_RE.search(name))


def _clean(s) -> str:
    s = str(s or "")
    s = re.sub(r"[‎‏‪-‮⁦-⁩]", "", s)     # bidi marks (Gmail)
    return " ".join(s.split())


# ---- the following take the `uiautomation` module; only uia_host calls them ----

def _inspect(auto, x: int, y: int) -> tuple[bool, str]:
    c = auto.ControlFromPoint(int(x), int(y))
    if c and c.ControlTypeName == "PaneControl" and not c.Name:
        # Chromium/Electron switch on their accessibility tree on the first
        # UIA query and answer with a bare pane; ask again once.
        time.sleep(0.15)
        c = auto.ControlFromPoint(int(x), int(y))
    if not c:
        return True, ""
    ctype = c.ControlTypeName
    name = _clean(c.Name)
    cands: list[str] = []                      # names that may block
    if ctype not in _SKIP_SELF_TYPES:
        cands.append(name)
        try:
            la = c.GetLegacyIAccessiblePattern()
            cands.append(_clean(la.Name))
            if ctype in _ANCESTOR_TYPES:
                cands.append(_clean(la.Value))
        except Exception:
            pass
    best = name if 0 < len(name) <= _MAX_NAME else ""
    p = c
    for _ in range(2):
        try:
            p = p.GetParentControl()
        except Exception:
            p = None
        if not p:
            break
        if p.ControlTypeName in _ANCESTOR_TYPES:
            pn = _clean(p.Name)
            cands.append(pn)
            if not best and 0 < len(pn) <= _MAX_NAME:
                best = pn
    for n in cands:
        if _blocked(n):
            return False, n
    return True, best


def _focused_name(auto) -> str:
    c = auto.GetFocusedControl()
    return _clean(c.Name) if c else ""


def _focused_point(auto):
    c = auto.GetFocusedControl()
    if not c:
        return None
    r = c.BoundingRectangle
    w, h = r.right - r.left, r.bottom - r.top
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    if w <= 4 or h <= 4 or w > sw * 0.8 or h > sh * 0.5:
        return None
    return (r.left + min(48, w // 4), r.top + h // 2)

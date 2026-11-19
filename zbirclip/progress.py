"""Live progress for the dashboard 'Progres' panel.

Each thread reports into a *channel*: 'watcher' (the 24/7 worker), 'download' (DVR/VOD backfill),
'post' (TikTok upload), 'job' (Generează clipuri). The channel is bound per thread (threading.local), so the
shared code (transcribe/render) reports to whoever called it, and a manual job never overwrites the watcher.
"""
import threading, time
from collections import deque

_lock = threading.Lock()
_tl = threading.local()
CH = {}                                   # channel -> {stage, label, pct, detail, t, ...}
SESSION = {"started": time.time(), "made": 0, "posted": 0, "skipped": 0, "deleted": 0, "rejected": 0, "chunks": 0}
RATE = deque(maxlen=12)                   # (media seconds, wall seconds) per processed watcher chunk
EVENTS = deque(maxlen=30)                 # notable events (auto-post toggles...)

STAGES = {"descarcare": "⬇️ Descarc bucata", "transcriere": "📝 Transcriere", "detectare": "🔎 Detectez momentele",
          "ai": "🤖 Verific cu AI", "randare": "🎬 Randez clipul", "postare": "📤 Postez pe TikTok",
          "astept": "⏳ Aștept"}


def bind(channel):
    _tl.ch = channel


def channel():
    return getattr(_tl, "ch", None)


def stage(name, pct=None, detail="", ch=None, **extra):
    ch = ch or channel()
    if not ch:
        return
    with _lock:
        d = CH.setdefault(ch, {})
        if d.get("stage") != name:
            d["since"] = time.time()
        d.update(stage=name, label=STAGES.get(name, name), pct=None if pct is None else max(0.0, min(100.0, float(pct))),
                 detail=detail, t=time.time(), **extra)


def pct(p, ch=None, detail=None):
    ch = ch or channel()
    if not ch:
        return
    with _lock:
        d = CH.setdefault(ch, {})
        d["pct"] = max(0.0, min(100.0, float(p))); d["t"] = time.time()
        if detail is not None:
            d["detail"] = detail


def update(ch=None, **kw):
    ch = ch or channel()
    if not ch:
        return
    with _lock:
        CH.setdefault(ch, {}).update(kw)


def idle(ch=None, detail=""):
    stage("astept", None, detail, ch=ch, clip_n=0, clip_m=0)


def count(key, n=1):
    with _lock:
        SESSION[key] = SESSION.get(key, 0) + n


def add_rate(media_s, wall_s):
    if media_s > 0 and wall_s > 0:
        with _lock:
            RATE.append((media_s, wall_s))


def speed():
    """Processed media seconds per wall second (x realtime); None until the first chunk."""
    with _lock:
        m = sum(a for a, _ in RATE); w = sum(b for _, b in RATE)
    return m / w if w else None


def eta_catch_up(remaining_s, live=True):
    """Seconds until processing reaches the live edge. Live keeps growing at 1x. None = unknown / never."""
    if remaining_s <= 0:
        return 0
    sp = speed()
    if not sp:
        return None
    net = sp - (1.0 if live else 0.0)
    return remaining_s / net if net > 0.05 else None


def event(msg):
    with _lock:
        EVENTS.appendleft({"t": time.time(), "msg": msg})


def snapshot():
    with _lock:
        return {"channels": {k: dict(v) for k, v in CH.items()}, "session": dict(SESSION), "events": list(EVENTS)[:10],
                "speed": (lambda m, w: round(m / w, 2) if w else None)(sum(a for a, _ in RATE), sum(b for _, b in RATE))}


def reset():          # tests
    with _lock:
        CH.clear(); RATE.clear(); EVENTS.clear()
        for k in SESSION:
            SESSION[k] = 0 if k != "started" else time.time()

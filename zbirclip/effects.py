"""Viral-style effects planned per clip (all toggleable in Settings -> 'Efecte'):
emoji pop-ups next to captions, word bounce, keyword colours, punch-in zoom + volume boost + meme SFX on
screams/funny peaks, slight shake on rage, optional progress bar.  Emojis: Twemoji (CC-BY 4.0);
SFX: synthesized by assets/sfx/make_sfx.py (no copyright)."""
import subprocess
import numpy as np
from .detect import _kw_score
import os
from pathlib import Path
from .util import cfg, tool

ASSETS = Path(os.environ.get("ZBIR_ASSETS") or Path(__file__).resolve().parent.parent / "assets")
EMOJI = {"ras": ["1f602", "1f62d", "1f480"], "injuratura": ["1f92c"], "exclamatie": ["1f631", "1f633"],
         "giveaway": ["1f381"], "actiune": ["1f525"], "chat": ["1f4ac"]}
PRIO = ["ras", "injuratura", "giveaway", "exclamatie", "actiune", "chat"]
DEFAULTS = {"emoji": True, "pop": True, "keyword_color": True, "zoom": True, "shake": True, "progress_bar": False,
            "sfx": True, "boost": True, "sfx_volume": 0.45, "boost_db": 4.0, "bass": True, "wait_for_it": True,
            "ziua_badge": False}


def settings():
    c = cfg()
    fx = dict(DEFAULTS)
    fx.update(c.get("effects") or {})
    try:
        from . import styles
        fx.update(styles.fx_overrides())
    except Exception:
        pass
    return fx


def word_cats(words):
    out = []
    for w in words:
        _, cats = _kw_score(w["w"])
        out.append([p for p in PRIO if p in cats])
    return out


def loud_peaks(src, s0, dur, max_n=3, min_gap=5.0):
    """Seconds (clip-relative) where the voice clearly jumps (screams, laughter bursts)."""
    try:
        r = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{s0:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
                            "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768
    except Exception:
        return []
    hop = 800                                   # 0.1 s
    n = len(x) // hop
    if n < 20:
        return []
    db = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-6)
    base = np.median(db)
    cand = [(db[i], i / 10) for i in range(n) if db[i] - base > 9 and db[i] >= db[max(0, i - 3): i + 4].max()]
    out = []
    for v, t in sorted(cand, reverse=True):
        if 0.6 <= t <= dur - 1.0 and all(abs(t - o) >= min_gap for o in out):
            out.append(round(t, 2))
        if len(out) >= max_n:
            break
    return sorted(out)


def plan(src, s0, e0, words, fx=None):
    """What happens when, in clip-relative seconds."""
    fx = fx or settings()
    dur = e0 - s0
    cats = word_cats(words)
    peaks = loud_peaks(src, s0, dur) if (fx["zoom"] or fx["sfx"] or fx["boost"]) else []
    emojis, last = [], -9.0
    used = {}
    for w, cs in zip(words, cats):
        if cs and w["s"] - last >= 3.5 and w["s"] < dur - 0.8 and len(emojis) < 6:
            k = cs[0]
            lst = EMOJI[k]; i = used.get(k, 0); used[k] = i + 1
            emojis.append({"t": round(w["s"], 2), "code": lst[i % len(lst)], "cat": k})
            last = w["s"]
    if fx["emoji"]:                      # a scream with no keyword nearby still gets a reaction emoji
        for t in peaks:
            if len(emojis) < 6 and t < dur - 0.8 and all(abs(t - e["t"]) >= 2.5 for e in emojis):
                near = [cs for w, cs in zip(words, cats) if abs(w["s"] - t) < 2 and cs]
                k = near[0][0] if near else "exclamatie"
                lst = EMOJI[k]; i = used.get(k, 0); used[k] = i + 1
                emojis.append({"t": round(t, 2), "code": lst[i % len(lst)], "cat": k})
        emojis.sort(key=lambda e: e["t"])
    rage = []
    for w, cs in zip(words, cats):
        if "injuratura" in cs and w["s"] < dur - 0.5:
            a, b = max(0.0, w["s"] - 0.2), min(dur, w["s"] + 0.9)
            if rage and a <= rage[-1][1]:
                rage[-1] = (rage[-1][0], b)
            else:
                rage.append((a, b))
    funny = [w["s"] for w, cs in zip(words, cats) if "ras" in cs]
    # meme SFX: on the strongest peaks + first big laugh, never more than 2 per clip
    sfx = []
    for t in peaks[:2] + funny[:1]:
        if len(sfx) < 2 and all(abs(t - s) >= 4 for s in sfx):
            sfx.append(round(t, 2))
    return {"dur": dur, "cats": cats, "punch": peaks if fx["zoom"] else [], "rage": rage[:3] if fx["shake"] else [],
            "emojis": emojis if fx["emoji"] else [], "sfx": sorted(sfx) if fx["sfx"] else [],
            "boost": [(max(0.0, t - 0.15), min(dur, t + 1.3)) for t in peaks] if fx["boost"] else []}


def zoom_filter(p, W, H):
    """zoompan expression: punch-in (+12% then ease out over 0.6 s) on peaks, gentle shake on rage."""
    if not p["punch"] and not p["rage"]:
        return None
    z = "1"
    for t in p["punch"]:
        z += f"+0.12*between(it,{t},{t + 0.6})*(1-(it-{t})/0.6)"
    for a, b in p["rage"]:
        z += f"+0.05*between(it,{a},{b})"
    shake = "+".join(f"between(it,{a},{b})*14*sin(it*47)" for a, b in p["rage"]) or "0"
    shake_y = "+".join(f"between(it,{a},{b})*10*cos(it*41)" for a, b in p["rage"]) or "0"
    x = f"max(0,min(iw-iw/zoom,iw/2-iw/zoom/2+({shake})))"
    y = f"max(0,min(ih-ih/zoom,ih/2-ih/zoom/2+({shake_y})))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps=30"


def volume_expr(p, boost_db):
    if not p["boost"]:
        return None
    g = 10 ** (boost_db / 20) - 1
    terms = "+".join(f"between(t,{a},{b})" for a, b in p["boost"])
    return f"volume=eval=frame:volume='1+{g:.3f}*min(1,{terms})'"

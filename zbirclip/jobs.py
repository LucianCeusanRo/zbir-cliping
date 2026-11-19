"""'✂️ Generează clipuri' from any link or local file pasted in the app (YouTube live/VOD, Kick, Twitch,
TikTok live replay - anything yt-dlp can read - or a video file on the PC). Runs next to the Zbir watcher."""
import math, re, threading, time, uuid
from pathlib import Path
from . import db, pipeline, progress
from .util import DATA, cfg, ffprobe_duration, log, run, tool, ytdlp_opts

JOBS = {}                     # id -> dict (shown in the app)
_q, _cv = [], threading.Condition()
MAX_LEN = 4 * 3600            # without a time range we take at most 4 h
LIVE_REC = 15 * 60            # a live link without a range: record the next 15 minutes


def parse_time(v):
    """'1:02:03', '62:03', '3723', '' -> seconds or None."""
    v = str(v or "").strip()
    if not v:
        return None
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", v):
        raise ValueError(f"timp invalid: {v} (ex: 1:05:30)")
    t = 0.0
    for part in v.split(":"):
        t = t * 60 + float(part)
    return t


def is_local(src):
    return bool(re.match(r"^([a-zA-Z]:[\\/]|/|\\\\|file://)", src)) and Path(src.replace("file://", "")).exists()


def submit(source, start=None, end=None, clips=5):
    source = (source or "").strip().strip('"')
    if not source:
        raise ValueError("lipește un link sau calea unui fișier")
    if not is_local(source):
        if not source.lower().startswith("http"):
            raise ValueError("nu e un link (http...) și nici un fișier care există pe PC")
        from .util import check_url
        source = check_url(source)
    elif not re.search(r"\.(mp4|mkv|mov|webm|flv|ts|m4v|avi)$", source, re.I):
        raise ValueError("fișierul trebuie să fie un video (mp4, mkv, mov, webm, flv, ts)")
    s, e = parse_time(start), parse_time(end)
    if s is not None and e is not None and e - s < 30:
        raise ValueError("intervalul trebuie să aibă cel puțin 30 de secunde")
    n = max(1, min(50, int(clips or 5)))
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"id": jid, "source": source, "start": s, "end": e, "clips": n, "status": "in asteptare",
                 "progress": 0, "stage": "în așteptare", "made": 0, "error": None, "created": time.time()}
    with _cv:
        _q.append(jid); _cv.notify()
    _ensure_worker()
    return JOBS[jid]


def list_jobs():
    return sorted(JOBS.values(), key=lambda j: -j["created"])[:20]


_worker = None


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, daemon=True, name="jobs"); _worker.start()


def _loop():
    progress.bind("job")
    while True:
        with _cv:
            while not _q:
                _cv.wait()
            jid = _q.pop(0)
        j = JOBS[jid]
        try:
            j["status"] = "ruleaza"
            _run(j)
            extra = "" if j["made"] >= j["clips"] else f" (ai cerut {j['clips']}, dar doar atâtea momente bune am găsit)"
            j.update(status="gata", progress=100, stage=f"gata: {j['made']} clipuri în galerie{extra}")
        except Exception as e:
            log.exception("job %s esuat", jid)
            j.update(status="eroare", error=str(e)[:300], stage="eroare")


def _set(j, p, stage):
    j["progress"] = int(max(j["progress"], min(99, p))); j["stage"] = stage


def _fetch(j, folder):
    """-> (local media path, timeline offset in seconds, title)."""
    src, s, e = j["source"], j["start"], j["end"]
    if is_local(src):
        p = Path(src.replace("file://", ""))
        return p, 0.0, p.stem
    from yt_dlp import YoutubeDL
    _set(j, 1, "citesc linkul…")
    with YoutubeDL(dict(ytdlp_opts(), quiet=True, skip_download=True, noplaylist=True)) as y:
        info = y.extract_info(src, download=False)
    title = info.get("title") or src
    max_h = cfg().get("video_max_height", 1080)
    out = folder / "src.mp4"

    def hook(d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                _set(j, 2 + 28 * d.get("downloaded_bytes", 0) / tot, f"descarc… {100 * d.get('downloaded_bytes', 0) / tot:.0f}%")
            else:
                _set(j, j["progress"], f"descarc… {d.get('downloaded_bytes', 0) / 1e6:.0f} MB")

    if info.get("is_live") or info.get("live_status") == "is_live":
        secs = (e - s) if (s is not None and e is not None) else LIVE_REC
        _set(j, 2, f"e LIVE: înregistrez {secs / 60:.0f} minute…")
        url = info.get("url") or next((f["url"] for f in reversed(info.get("formats") or []) if f.get("url")), None)
        if not url:
            raise RuntimeError("nu găsesc fluxul video al live-ului")
        run([tool("ffmpeg"), "-y", "-v", "error", "-i", url, "-t", f"{secs}", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
             folder / "src.ts"], timeout=secs + 600)
        return folder / "src.ts", 0.0, title
    dur = info.get("duration") or 0
    s = s or 0.0
    e = e if e is not None else (min(dur, s + MAX_LEN) if dur else s + MAX_LEN)
    o = dict(ytdlp_opts(), quiet=True, noprogress=True, progress_hooks=[hook], noplaylist=True,
             format=f"bv*[height<={max_h}]+ba/b[height<={max_h}]/b", merge_output_format="mp4",
             outtmpl=str(folder / "src.%(ext)s"))
    if s > 0 or (dur and e < dur):
        from yt_dlp.utils import download_range_func
        o.update(download_ranges=download_range_func(None, [(s, e)]), force_keyframes_at_cuts=False)
    with YoutubeDL(o) as y:
        y.download([src])
    got = out if out.exists() else next(iter(sorted(folder.glob("src.*"))), None)
    if not got:
        raise RuntimeError("descărcarea nu a produs niciun fișier")
    return got, s, title


def _run(j):
    folder = DATA / "jobs" / j["id"]; folder.mkdir(parents=True, exist_ok=True)
    media, offset, title = _fetch(j, folder)
    total = ffprobe_duration(media)
    a = 0.0
    b = total
    if is_local(j["source"]):                           # local file: the range is applied here
        a = j["start"] or 0.0
        b = min(total, j["end"]) if j["end"] else total
        offset = 0.0
    if b - a < 20:
        raise RuntimeError("materialul e prea scurt (sub 20 s)")
    vid = "job-" + j["id"]
    db.upsert_video(vid, title[:120], "manual", time.time(), "file")
    size = cfg().get("chunk_minutes", 5) * 60
    n_chunks = max(1, math.ceil((b - a) / size))
    over = {"clips_per_chunk_max": max(1, math.ceil(j["clips"] / n_chunks)) + 1,
            "min_score": cfg().get("min_score", 0.9) * 0.6}
    t, k = a, 0
    while t < b - 20:
        k += 1
        lo, hi = 30 + 68 * (k - 1) / n_chunks, 30 + 68 * k / n_chunks
        _set(j, lo, f"analizez bucata {k}/{n_chunks} (transcriere + momente)…")
        tick = threading.Event()

        def ticker(lo=lo, hi=hi, tick=tick):          # keeps the bar moving while Whisper works
            while not tick.wait(2):
                j["progress"] = int(min(hi - 1, j["progress"] + max(0.2, (hi - j["progress"]) * 0.03)))
        threading.Thread(target=ticker, daemon=True).start()
        part = folder / f"part_{int(t):06d}.mkv"
        d = min(size, b - t)
        run([tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{t}", "-t", f"{d}", "-i", media, "-map", "0:v:0", "-map", "0:a:0?",
             "-c", "copy", part])
        d = ffprobe_duration(part)
        cid = db.add_chunk(vid, offset + t, offset + t + d, part, "file", status="processing")   # not for the Zbir worker
        ch = db.q("SELECT * FROM chunks WHERE id=?", (cid,), one=True)
        try:
            j["made"] += pipeline.process_chunk(ch, over)
        finally:
            tick.set()
        db.finish_chunk(cid, "done")
        t += size
    # keep only the best N (by viral chance) -> the rest are removed from the gallery
    rows = db.q("SELECT * FROM clips WHERE video_id=? AND status='pending' ORDER BY COALESCE(viral,0) DESC, score DESC", (vid,))
    for r in rows[j["clips"]:]:
        for ext in (".mp4", ".jpg", ".json", ".ass", ".fx.json"):
            Path(r["file"]).with_suffix(ext).unlink(missing_ok=True)
        if r["src"]:
            Path(r["src"]).unlink(missing_ok=True)
        db.x("UPDATE clips SET status='deleted' WHERE id=?", (r["id"],))
    j["made"] = min(j["made"], j["clips"])
    for f in folder.glob("part_*"):
        f.unlink(missing_ok=True)
    if not is_local(j["source"]):
        Path(media).unlink(missing_ok=True)

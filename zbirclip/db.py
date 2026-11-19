"""Persistent state (SQLite) so nothing is processed or posted twice, even across restarts.

Tables
  videos  : every live/VOD seen (id, title, start timestamp, status)
  chunks  : stream-time ranges [t0, t1) of a video that were downloaded / processed
  clips   : every rendered clip (video_id, start, end in stream time, score, file, caption, posted state)
"""
import os, shutil, sqlite3, threading, time
from pathlib import Path
from .util import DATA

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY, title TEXT, channel TEXT, start_ts REAL,
    status TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, t0 REAL, t1 REAL,
    path TEXT, kind TEXT, status TEXT, created REAL, error TEXT);
CREATE INDEX IF NOT EXISTS ix_chunks_v ON chunks(video_id, t0);
CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, start REAL, end REAL,
    score REAL, file TEXT, title TEXT, caption TEXT, text TEXT, why TEXT, status TEXT, created REAL,
    posted_at REAL, attempts INTEGER DEFAULT 0, error TEXT);
CREATE INDEX IF NOT EXISTS ix_clips_v ON clips(video_id, start);
"""


def _open_checked(path):
    """Open the db; if it is corrupt, keep it aside and restore the last hourly backup (or start fresh)."""
    def ok(p):
        c = None
        try:
            c = sqlite3.connect(p, check_same_thread=False, timeout=30)
            r = c.execute("PRAGMA quick_check").fetchone()[0]
            if r == "ok":
                return c
        except sqlite3.DatabaseError:
            pass
        if c is not None:
            try:
                c.close()       # must close, otherwise Windows keeps the file locked and it can't be quarantined
            except Exception:
                pass
        return None
    c = ok(path)
    if c:
        return c
    bad = path.with_name(f"state.db.corrupt-{int(time.time())}")
    try:
        os.replace(path, bad)
        for ext in ("-wal", "-shm"):
            if Path(str(path) + ext).exists():
                os.replace(str(path) + ext, str(bad) + ext)
    except OSError:
        pass
    bak = path.with_name("state.db.bak")
    if bak.exists():
        shutil.copyfile(bak, path)
        c = ok(path)
        if c:
            import logging
            logging.getLogger("zbir").error("baza de date era stricata (%s); am restaurat ultima copie de siguranta", bad.name)
            return c
        path.unlink(missing_ok=True)
    return sqlite3.connect(path, check_same_thread=False, timeout=30)


def backup():
    """Hourly consistent copy (sqlite backup API, safe while running) used if state.db ever gets corrupted."""
    dst = DATA / "state.db.bak"
    tmp = DATA / "state.db.bak.tmp"
    with _lock:
        b = sqlite3.connect(tmp)
        try:
            conn().backup(b)
        finally:
            b.close()
    os.replace(tmp, dst)


def conn():
    global _conn
    with _lock:
        if _conn is None:
            DATA.mkdir(parents=True, exist_ok=True)
            _conn = _open_checked(DATA / "state.db")
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            for tbl, col, typ in (("chunks", "tries", "INTEGER DEFAULT 0"), ("clips", "thumb", "TEXT"),
                                  ("clips", "url", "TEXT"), ("clips", "hook", "TEXT"), ("clips", "layout", "TEXT"), ("clips", "queued_at", "REAL"),
                                  ("clips", "viral", "INTEGER"), ("clips", "factors", "TEXT"), ("clips", "cat", "TEXT"),
                                  ("clips", "src", "TEXT"), ("clips", "version", "INTEGER DEFAULT 1"),
                                  ("clips", "style", "TEXT"), ("clips", "styles", "TEXT")):
                try:
                    _conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass       # column already exists
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            # chunks left half-done by a crash go back into the queue
            _conn.execute("UPDATE chunks SET status='queued' WHERE status='processing'")
            # a crash DURING an upload: maybe it is online already -> never re-post blindly, verify on TikTok first
            _conn.execute("UPDATE clips SET status='verify', error='aplicația s-a închis în timpul postării: verific pe TikTok' "
                          "WHERE status='posting'")
            _conn.commit()
        return _conn


def q(sql, args=(), one=False):
    with _lock:
        cur = conn().execute(sql, args)
        rows = cur.fetchall()
        conn().commit()
        return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid


# ---- videos ----
def upsert_video(vid, title, channel, start_ts, status):
    x("""INSERT INTO videos(id,title,channel,start_ts,status,updated) VALUES(?,?,?,?,?,?)
         ON CONFLICT(id) DO UPDATE SET title=excluded.title, status=excluded.status,
         start_ts=COALESCE(videos.start_ts, excluded.start_ts), updated=excluded.updated""",
      (vid, title, channel, start_ts, status, time.time()))


def video(vid):
    return q("SELECT * FROM videos WHERE id=?", (vid,), one=True)


# ---- coverage (what part of the stream timeline is already handled) ----
def covered(video_id):
    """Merged list of [t0, t1] stream-time ranges that are downloaded (queued/processing/done)."""
    rows = q("SELECT t0, t1 FROM chunks WHERE video_id=? AND status IN ('queued','processing','done') ORDER BY t0",
             (video_id,))
    out = []
    for r in rows:
        if out and r["t0"] <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], r["t1"])
        else:
            out.append([r["t0"], r["t1"]])
    return out


def gaps(video_id, t_from, t_to, min_len=30):
    """Ranges in [t_from, t_to] not yet covered."""
    res, cur = [], t_from
    for a, b in covered(video_id):
        if b <= cur:
            continue
        if a > cur and a - cur >= min_len:
            res.append([cur, min(a, t_to)])
        cur = max(cur, b)
        if cur >= t_to:
            break
    if t_to - cur >= min_len:
        res.append([cur, t_to])
    return [g for g in res if g[1] - g[0] >= min_len]


def add_chunk(video_id, t0, t1, path, kind, status="queued"):
    return x("INSERT INTO chunks(video_id,t0,t1,path,kind,status,created) VALUES(?,?,?,?,?,?,?)",
             (video_id, t0, t1, str(path), kind, status, time.time()))


def retry_or_fail(cid, error):
    """First failure -> back in the queue once; second -> error (visible in the app)."""
    r = q("SELECT tries FROM chunks WHERE id=?", (cid,), one=True)
    if r and (r["tries"] or 0) < 1:
        x("UPDATE chunks SET status='queued', tries=tries+1, error=? WHERE id=?", (error, cid))
    else:
        x("UPDATE chunks SET status='error', error=? WHERE id=?", (error, cid))


def next_chunk():
    """Live chunks first (fresh clips post fastest), then backfill/VOD, oldest first inside each kind."""
    with _lock:
        r = q("""SELECT * FROM chunks WHERE status='queued'
                 ORDER BY CASE kind WHEN 'live' THEN 0 ELSE 1 END, created LIMIT 1""", one=True)
        if r:
            x("UPDATE chunks SET status='processing' WHERE id=?", (r["id"],))
        return r


def finish_chunk(cid, status="done", error=None):
    x("UPDATE chunks SET status=?, error=? WHERE id=?", (status, error, cid))


# ---- clips / dedupe ----
def overlaps_existing(video_id, start, end, max_overlap=0.25):
    rows = q("SELECT start, end FROM clips WHERE video_id=? AND end>? AND start<?", (video_id, start, end))
    for r in rows:
        inter = min(end, r["end"]) - max(start, r["start"])
        if inter > max_overlap * min(end - start, r["end"] - r["start"]):
            return True
    return False


def add_clip(**kw):
    kw.setdefault("status", "pending"); kw.setdefault("created", time.time())
    keys = ",".join(kw); qs = ",".join("?" * len(kw))
    return x(f"INSERT INTO clips({keys}) VALUES({qs})", tuple(kw.values()))


def eff_viral(r):
    """Viral % used for ordering; old clips without a stored score get the detector-based estimate."""
    from . import viral
    v = r["viral"] if "viral" in r.keys() else None
    return int(v) if v is not None else viral.fallback(r["score"])


def norm_text(t):
    import re, unicodedata
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9 ]+", " ", t).split()


def similar(a, b, thr=0.8):
    """Near-identical transcripts (same moment cut twice, a repeated rant, a re-uploaded VOD part)."""
    import difflib
    wa, wb = norm_text(a), norm_text(b)
    if len(wa) < 6 or len(wb) < 6:
        return False
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio() >= thr


def similar_text_exists(video_id, text, start=None, window=1800, thr=0.8, exclude_id=None):
    """Any non-deleted clip (any video) with almost the same words? Same video: only within +-window s."""
    rows = q("SELECT id, video_id, start, text FROM clips WHERE status!='deleted' ORDER BY id DESC LIMIT 400")
    for r in rows:
        if r["id"] == exclude_id:
            continue
        if r["video_id"] == video_id and start is not None and abs((r["start"] or 0) - start) > window:
            continue
        if similar(text, r["text"], thr):
            return r["id"]
    return None


def post_plan(order="viral_first", auto=False, min_viral=40, limit=200):
    """Exact order the poster will use.
    1) clips you approved with 'Postează' (status queued), first-approved first - your choice always wins,
       no threshold;
    2) only if Auto-postare is ON: pending clips with viral % >= min_viral, highest viral % first
       (ties: higher detector/AI score, then newer). Clips almost identical to one already posted / queued /
       higher in the list are left out (no duplicate posts).
    Returns (plan, below): plan = ordered rows with keys pos/why, below = pending rows under the threshold."""
    manual = [dict(r) for r in q("SELECT * FROM clips WHERE status='queued' AND attempts<3 ORDER BY queued_at, created")]
    for r in manual:
        r["plan"] = "manual"
    plan, below = list(manual), []
    if auto:
        try:
            from .styles import enabled_categories
            en = enabled_categories()
        except Exception:
            en = {}
        pend = [dict(r, ev=eff_viral(r)) for r in q("SELECT * FROM clips WHERE status='pending' AND attempts<3")
                if en.get(r["style"] or "moment", True)]
        if order == "newest_first":
            pend.sort(key=lambda r: -(r["created"] or 0))
        else:     # viral_first (also the old name best_first)
            pend.sort(key=lambda r: (-r["ev"], -(r["score"] or 0), -(r["created"] or 0)))
        taken = [r["text"] for r in q("SELECT text FROM clips WHERE status IN ('posted','unconfirmed','posting')")] + \
                [r["text"] for r in manual]
        for r in pend:
            if r["ev"] < min_viral:
                below.append(r); continue
            if any(similar(r["text"], t) for t in taken):
                r["plan"] = "duplicat"; below.append(r); continue
            r["plan"] = "auto"; plan.append(r); taken.append(r["text"])
            if len(plan) >= limit:
                break
    for i, r in enumerate(plan, 1):
        r["pos"] = i
    return plan, below


def next_clip_to_post(order="viral_first", auto=False, min_viral=40):
    """Clips you pressed 'Posteaza' on (status 'queued') go first; with auto-post the best pending one by viral %."""
    plan, _ = post_plan(order, auto, min_viral, limit=1)
    return plan[0] if plan else None


def posts_since(ts):
    return q("SELECT COUNT(*) n FROM clips WHERE status='posted' AND posted_at>=?", (ts,), one=True)["n"]


def last_post_time():
    r = q("SELECT MAX(posted_at) t FROM clips WHERE status='posted'", one=True)
    return r["t"] or 0

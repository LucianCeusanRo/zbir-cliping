"""24/7 orchestrator (runs inside the Zbir Cliping app, or headless with `python -m zbirclip run`).

threads:
  watcher  : every N s asks YouTube whether the channel is live; starts recorder + chat + DVR backfill for a
             new live; when a live ends schedules VOD gap-filling; handles extra URLs / URLs added in the app.
  record   : live-edge HLS -> N-minute chunks (recorder.py)
  fill-*   : downloads everything of a live/VOD not yet covered (from the start, after restarts, dropouts)
  worker   : chunks -> clips (GPU, one at a time)
  poster   : posts clips to TikTok: the ones you approved in the app (and, if auto-post is ON, the best pending
             ones) with a minimum interval and a daily cap.
All state lives in data/state.db, so after a crash/restart nothing is processed or posted twice.
"""
import random, re, threading, time, traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from . import db, pipeline, progress, recorder, tiktok, youtube
from .util import DATA, cfg, log, sleep_until, ffprobe_duration

STOP = threading.Event()           # program exit
WATCHING = threading.Event()       # Start/Stop button in the app
WATCHING.set()
POST_PAUSED = threading.Event()    # "Pauza postare" button
ACTIVE, VOD_JOBS, SKIPPED, MANUAL_URLS = {}, {}, set(), []
ERRORS = deque(maxlen=50)          # shown in the app
STATUS = {"worker": "astept", "poster": "astept", "next_post_at": 0, "watcher": "pornesc", "last_check": 0}
_started = False


def error(msg):
    log.warning(msg)
    ERRORS.appendleft({"t": time.time(), "msg": msg})


def chunk_ranges(a, b, size):
    t = a
    while t < b - 30:
        yield t, min(b, t + size)
        t += size


def backlog():
    return db.q("SELECT COUNT(*) n FROM chunks WHERE status='queued'", one=True)["n"]


class Backfill(threading.Thread):
    """Downloads uncovered parts of a video. mode='live' uses the DVR (from start), mode='vod' the archive."""

    def __init__(self, vid, mode, rec=None, stop=None):
        super().__init__(daemon=True, name=f"fill-{mode}")
        self.vid, self.mode, self.rec = vid, mode, rec
        self.stop = stop or STOP
        self.url = f"https://www.youtube.com/watch?v={vid}"
        self.complete = False
        self.pos = None            # stream time (s) of the chunk being downloaded now
        self.total = None          # VOD duration

    def stopped(self):
        return STOP.is_set() or self.stop.is_set()

    def run(self):
        progress.bind("download")
        try:
            self._run()
        except Exception as e:
            error(f"completare {self.mode} {self.vid} oprita: {str(e)[:200]}")
        finally:
            self.pos = None
            progress.idle(detail="nimic de descărcat acum")

    def _run(self):
        c = cfg()
        size = c.get("chunk_minutes", 5) * 60
        folder = DATA / self.vid / ("dvr" if self.mode == "live" else self.mode)
        folder.mkdir(parents=True, exist_ok=True)
        dash = None
        if self.mode == "live":
            if not c.get("backfill_from_start", True):
                return
            i = 0
            while not dash and not self.stopped():     # 24/7 lives may never end -> keep trying the DVR
                try:
                    dash = youtube.DashBackfill(self.url, c.get("video_max_height", 1080))
                except Exception as e:
                    i += 1
                    if i == 3:
                        error(f"nu pot lua inceputul live-ului {self.vid} din DVR ({str(e).splitlines()[0][:120]}). "
                              "Reincerc la fiecare 10 minute; ce ramane se completeaza si din VOD dupa ce se termina live-ul.")
                    if self.rec and self.rec.ended.is_set():
                        return
                    sleep_until(self.stop, min(600, 30 * i))
            if not dash:
                return
        else:
            info = youtube.extract(self.url)
            vod_dur = float(info.get("duration") or 0)
            self.total = vod_dur
            if not vod_dur:
                raise RuntimeError("VOD-ul nu are inca durata (YouTube il proceseaza); reincerc mai tarziu")
        fails = 0
        while not self.stopped():
            v = db.video(self.vid)
            if self.mode == "live":
                if self.rec and self.rec.ended.is_set():
                    return
                now_t = time.time() - (v["start_ts"] or time.time())
                lo = max(0.0, now_t - c.get("backfill_max_hours", 12) * 3600)
                first_live = db.q("SELECT MIN(t0) t FROM chunks WHERE video_id=? AND kind='live'", (self.vid,), one=True)["t"]
                hi = first_live if first_live is not None else now_t - 60
                todo = db.gaps(self.vid, lo, max(lo, hi - 1))
                todo += [g for g in db.gaps(self.vid, hi, now_t - size - 120) if g[0] >= hi]   # recorder holes
            else:
                todo = db.gaps(self.vid, 0, min(vod_dur, c.get("vod_max_hours", 12) * 3600))
            if not todo:
                if self.mode == "vod":
                    log.info("VOD %s complet descarcat.", self.vid)
                    self.complete = True
                    return
                sleep_until(self.stop, 120); continue
            for g0, g1 in todo:
                for a, b in chunk_ranges(g0, g1, size):
                    if backlog() >= 4:
                        progress.idle(detail=f"aștept: {backlog()} bucăți descărcate așteaptă procesarea")
                    while backlog() >= 4 and not self.stopped():   # don't fill the disk ahead of the GPU
                        sleep_until(self.stop, 20)
                    if self.stopped():
                        return
                    out = folder / f"{self.mode}_{int(a):06d}.mp4"
                    self.pos = a
                    progress.stage("descarcare", 0 if dash else None,
                                   f"{'DVR' if dash else 'VOD'} [{pipeline.hms(a)} – {pipeline.hms(b)}]", video=self.vid)
                    try:
                        if dash:
                            real_a = dash.download(a, b, out)
                        else:
                            out = youtube.download_section(self.url, a, b, out, c.get("video_max_height", 1080))
                            real_a = a
                        fails = 0
                    except Exception as e:
                        fails += 1
                        error(f"descarcare {self.vid} [{pipeline.hms(a)}] esuata ({fails}): {str(e)[:150]}")
                        if dash and fails % 3 == 0:
                            try:
                                dash.refresh()
                            except Exception:
                                pass
                        if fails >= 10:
                            raise RuntimeError("prea multe erori de descarcare la rand")
                        sleep_until(self.stop, min(300, 20 * fails))
                        continue
                    d = ffprobe_duration(out)
                    db.add_chunk(self.vid, real_a, real_a + d, out, self.mode)
                    log.info("bucata %s noua: [%s - %s]", "DVR" if dash else "VOD", pipeline.hms(real_a), pipeline.hms(real_a + d))
            progress.idle(detail="am ajuns la marginea live-ului; aștept bucăți noi")
            sleep_until(self.stop, 10)


def worker():
    progress.bind("watcher")
    while not STOP.is_set():
        ch = db.next_chunk()
        if not ch:
            STATUS["worker"] = "astept bucati noi"
            progress.idle(detail="toate bucățile descărcate sunt procesate; aștept următoarea")
            sleep_until(STOP, 5); continue
        if not Path(ch["path"]).exists():
            db.finish_chunk(ch["id"], "error", "fisier lipsa"); continue
        STATUS["worker"] = f"procesez {ch['video_id']} [{pipeline.hms(ch['t0'])}]"
        t_start = time.time()
        try:
            n = pipeline.process_chunk(ch)
            db.finish_chunk(ch["id"], "done")
            progress.add_rate(ch["t1"] - ch["t0"], time.time() - t_start)
            progress.count("chunks")
            log.info("bucata %s [%s] gata: %d clipuri noi. In coada: %d bucati", ch["video_id"],
                     pipeline.hms(ch["t0"]), n, backlog())
        except Exception as e:
            log.error("eroare la procesare: %s\n%s", e, traceback.format_exc()[-1500:])
            error(f"procesare {ch['video_id']} [{pipeline.hms(ch['t0'])}] esuata: {str(e).splitlines()[0][:160]}")
            db.retry_or_fail(ch["id"], str(e)[:500])
        progress.idle()
        try:
            pipeline.cleanup_sources(ch["video_id"])
        except Exception:
            pass


BACKOFF_FILE = DATA / "post_backoff.json"
BACKOFF_STEPS_H = [1, 2, 4, 8, 12]


def backoff():
    from .util import load_json
    return load_json(BACKOFF_FILE, {}) if BACKOFF_FILE.exists() else {}


def set_backoff(msg):
    """TikTok said 'slow down' (or similar): pause posting 1h, then 2h, 4h... (persisted across restarts)."""
    from .util import save_json
    b = backoff()
    lvl = min(len(BACKOFF_STEPS_H) - 1, int(b.get("level", -1)) + 1)
    until = time.time() + BACKOFF_STEPS_H[lvl] * 3600
    save_json(BACKOFF_FILE, {"level": lvl, "until": until, "msg": msg, "since": time.time()})
    error(f"TikTok a limitat postarea: „{msg}”. Pauză automată {BACKOFF_STEPS_H[lvl]}h (până la {datetime.fromtimestamp(until):%H:%M}).")
    progress.event(f"pauză postare {BACKOFF_STEPS_H[lvl]}h: {msg}")


def clear_backoff(reset_level=False):
    from .util import save_json
    b = backoff()
    if b:
        save_json(BACKOFF_FILE, {"level": -1 if reset_level else b.get("level", -1), "until": 0, "msg": None})


def recent_captions(n=20):
    return [r["caption"] for r in db.q("SELECT caption FROM clips WHERE status IN ('posted','unconfirmed') ORDER BY posted_at DESC LIMIT ?", (n,))]


def janitor_loop():
    last_bak = 0
    while not STOP.is_set():
        try:
            pipeline.janitor()
        except Exception as e:
            log.warning("curatenie esuata: %s", e)
        if time.time() - last_bak > 3600:
            try:
                db.backup(); last_bak = time.time()
            except Exception as e:
                log.warning("copia de siguranta a bazei de date a esuat: %s", e)
        sleep_until(STOP, 600)


def midnight():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def post_one(clip):
    """Upload one clip and record the result. Returns True if posted."""
    c = cfg()
    progress.bind("post")
    progress.stage("postare", None, f"{(clip.get('hook') if hasattr(clip, 'get') else clip['hook']) or Path(clip['file']).name}"[:80])
    try:
        return _post_one(clip, c)
    finally:
        progress.idle(ch="post")


def _post_one(clip, c):
    # never repeat the exact same caption (TikTok flags repetitive automation): make a new variant
    if clip["caption"] in recent_captions():
        try:
            clip = dict(clip, caption=pipeline.retext(clip["id"])["caption"])
            log.info("descriere identica cu o postare recenta -> variantă nouă: %s", clip["caption"])
        except Exception:
            pass
    db.x("UPDATE clips SET status='posting', attempts=attempts+1 WHERE id=?", (clip["id"],))
    STATUS["poster"] = f"postez {Path(clip['file']).name}"
    log.info("POSTEZ pe TikTok: %s | %s", Path(clip["file"]).name, clip["caption"])
    try:
        ok, msg, url = tiktok.upload(Path(clip["file"]), clip["caption"])
    except Exception as e:
        ok, msg, url = False, f"eroare browser: {str(e)[:150]}", None
    if ok:
        db.x("UPDATE clips SET status='posted', posted_at=?, url=?, error=NULL WHERE id=?", (time.time(), url, clip["id"]))
        log.info("POSTAT OK %s. Azi: %d/%d", url or "", db.posts_since(midnight()), c.get("daily_post_cap", 45))
        progress.count("posted")
        b = backoff()
        if b.get("level", -1) >= 0 and not b.get("until"):
            clear_backoff(reset_level=True)        # a clean post after a pause -> back to the first step
        return True
    if "confirmarea" in msg:
        # may actually be online: never re-post automatically (no duplicates)
        db.x("UPDATE clips SET status='unconfirmed', posted_at=?, error=? WHERE id=?", (time.time(), msg, clip["id"]))
        error(f"postare neconfirmata: {msg}. Verifica in TikTok Studio; daca nu apare, apasa din nou Posteaza.")
        return True
    if msg.startswith("limitare TikTok"):
        db.x("UPDATE clips SET status='queued', attempts=MAX(0, attempts-1), error=? WHERE id=?", (msg, clip["id"]))
        set_backoff(msg.split(":", 1)[1].strip())
        return False
    r = db.q("SELECT attempts FROM clips WHERE id=?", (clip["id"],), one=True)
    final = (r["attempts"] or 0) >= 3 or "logat" in msg
    db.x("UPDATE clips SET status=?, error=? WHERE id=?", ("failed" if final else "queued", msg, clip["id"]))
    error(f"postare esuata ({Path(clip['file']).name}): {msg}" + (" - apasa din nou Posteaza dupa ce rezolvi." if final else " - reincerc."))
    return False


def verify_interrupted(posts=None):
    """Clips whose upload was cut by a crash/close: look on TikTok before ever posting them again."""
    rows = db.q("SELECT * FROM clips WHERE status='verify'")
    if not rows:
        return
    progress.stage("postare", None, f"verific pe TikTok {len(rows)} postări întrerupte", ch="post")
    for r in rows:
        res = tiktok.find_posted(r["caption"], posts)
        if res:
            db.x("UPDATE clips SET status='posted', posted_at=COALESCE(posted_at, ?), url=?, error=NULL WHERE id=?",
                 (time.time(), res if isinstance(res, str) else None, r["id"]))
            log.info("clipul #%d era deja postat (gasit pe TikTok) -> marcat postat, nu-l repostez", r["id"])
        elif res is False:
            db.x("UPDATE clips SET status='queued', queued_at=?, error=NULL WHERE id=?", (time.time(), r["id"]))
            log.info("clipul #%d nu apare pe TikTok -> il pun inapoi in coada", r["id"])
        else:
            db.x("UPDATE clips SET status='unconfirmed', error=? WHERE id=?",
                 ("nu am putut verifica pe TikTok dacă s-a postat (aplicația s-a închis în timpul postării). "
                  "Verifică în TikTok Studio; dacă nu apare, apasă „Postează iar”.", r["id"]))
    progress.idle(ch="post")


def poster():
    next_at = 0
    try:
        verify_interrupted()
    except Exception as e:
        log.warning("verificarea postarilor intrerupte a esuat: %s", e)
    while not STOP.is_set():
        c = cfg()
        auto = bool(c.get("auto_post", False))
        STATUS["next_post_at"] = 0
        if POST_PAUSED.is_set():
            STATUS["poster"] = "pauza"; sleep_until(STOP, 5); continue
        b = backoff()
        if b.get("until", 0) > time.time():
            STATUS["poster"] = f"pauză automată (TikTok) până la {datetime.fromtimestamp(b['until']):%H:%M}"
            STATUS["next_post_at"] = b["until"]
            sleep_until(STOP, 10); continue
        clip = db.next_clip_to_post(c.get("post_order", "viral_first"), auto=auto, min_viral=int(c.get("auto_post_min_viral", 40)))
        if not clip:
            STATUS["poster"] = "nimic de postat" + (" (auto-postare ON)" if auto else " (apasa Posteaza pe un clip)")
            sleep_until(STOP, 5); continue
        manual = clip["status"] == "queued"
        if not manual and datetime.now().hour in (c.get("quiet_hours") or []):
            STATUS["poster"] = "ore de liniste"; sleep_until(STOP, 60); continue
        if db.posts_since(midnight()) >= c.get("daily_post_cap", 45):
            STATUS["poster"] = f"limita zilnica atinsa ({c.get('daily_post_cap', 45)})"; sleep_until(STOP, 30); continue
        # spacing: approved clips wait at least post_min_gap_minutes, auto ones the random 30-45 min interval
        # one post at a time; auto posts: random interval between min/max + jitter, so it never looks scheduled
        lo_m, hi_m = c.get("post_interval_min_minutes", 20), c.get("post_interval_max_minutes", 30)
        jit = float(c.get("post_jitter_minutes", 4))
        gap = (c.get("manual_min_gap_minutes", 3) * 60 if manual else
               max(lo_m * 0.6, random.uniform(lo_m, hi_m) + random.uniform(-jit, jit)) * 60)
        last = db.last_post_time()
        if not next_at or manual:
            next_at = last + gap
        if time.time() < next_at:
            STATUS["next_post_at"] = next_at
            STATUS["poster"] = f"urmatoarea postare la {datetime.fromtimestamp(next_at):%H:%M}"
            sleep_until(STOP, min(5, next_at - time.time())); continue
        if not Path(clip["file"]).exists():
            db.x("UPDATE clips SET status='failed', error='fisierul video lipseste' WHERE id=?", (clip["id"],)); continue
        posted = post_one(clip)
        next_at = 0
        STATUS["poster"] = "astept"
        if not posted:
            sleep_until(STOP, 120)       # don't hammer TikTok after a failure


def register(info, status):
    db.upsert_video(info["id"], info.get("title"), info.get("channel"), youtube.start_ts(info), status)


def start_live(li):
    vid = li["id"]
    log.info("LIVE detectat: %s | %s", vid, li.get("title"))
    register(li, "live")
    folder = DATA / vid; folder.mkdir(parents=True, exist_ok=True)
    ev = threading.Event()
    rec = recorder.LiveRecorder(li, folder, ev); rec.start()
    chat = youtube.ChatRecorder(f"https://www.youtube.com/watch?v={vid}", folder, youtube.start_ts(li), rec.ended)
    chat.start()
    fill = Backfill(vid, "live", rec, ev); fill.start()
    ACTIVE[vid] = {"rec": rec, "chat": chat, "fill": fill, "stop": ev, "title": li.get("title"), "since": time.time()}


def stop_all_lives():
    for vid, a in list(ACTIVE.items()):
        a["stop"].set()
    for vid, a in list(ACTIVE.items()):
        a["rec"].ended.wait(20)
        ACTIVE.pop(vid, None)


def start_vod(vid, delay=0):
    if vid not in VOD_JOBS:
        VOD_JOBS[vid] = [None, time.time() + delay, 0]


def add_url(url):
    """URL added from the app: live -> followed; VOD -> processed."""
    MANUAL_URLS.append(url)


def _seed(urls, flt):
    for u in dict.fromkeys(urls):
        try:
            info = youtube.extract(u)
        except Exception as e:
            error(f"nu pot citi {u}: {str(e)[:150]}"); continue
        if info.get("live_status") == "is_live":
            if info["id"] not in ACTIVE:
                start_live(info)
            continue
        if flt and u not in (cfg().get("extra_urls") or []) + MANUAL_URLS and not re.search(flt, info.get("title") or ""):
            continue
        if info.get("duration"):
            register(info, "vod"); start_vod(info["id"])


def run_forever(post=True):
    from .util import migrate_settings
    migrate_settings()
    c = cfg()
    global _started
    log.info("ZBIR CLIPING pornit. Canal: %s | hashtag-uri: %s", c["channel_url"], " ".join(c["hashtags"]))
    if not _started:          # survive watcher restarts without duplicating workers
        _started = True
        from .util import kill_stale_children
        kill_stale_children()
        threading.Thread(target=worker, daemon=True, name="worker").start()
        threading.Thread(target=janitor_loop, daemon=True, name="janitor").start()
        if post:
            threading.Thread(target=poster, daemon=True, name="poster").start()
    seeds = list(c.get("extra_urls") or [])
    for e in youtube.recent_streams(c["channel_url"], 6)[: int(c.get("process_recent_vods", 0))]:
        if e.get("live_status") == "was_live":
            seeds.append(f"https://www.youtube.com/watch?v={e['id']}")
    _seed(seeds, c.get("live_title_filter") or "")
    fails = 0
    while not STOP.is_set():
        c = cfg()
        if not WATCHING.is_set():
            if ACTIVE:
                log.info("Oprit din aplicatie: opresc inregistrarea.")
                stop_all_lives()
            STATUS["watcher"] = "oprit (apasa Start)"
            sleep_until(STOP, 2); continue
        if MANUAL_URLS:
            urls = MANUAL_URLS[:]; _seed(urls, ""); del MANUAL_URLS[:len(urls)]
        STATUS["watcher"] = "verific daca Zbir e live..."
        try:
            info = youtube.current_live(c["channel_url"])
            fails = 0
        except Exception as e:
            fails += 1; info = None
            if fails % 5 == 1:
                error(f"nu pot verifica live-ul (internet/YouTube): {str(e)[:150]}")
        STATUS["last_check"] = time.time()
        if info and info["id"] not in ACTIVE:
            flt = c.get("live_title_filter") or ""
            if flt and not re.search(flt, info.get("title") or "") and info["id"] not in [youtube.video_id(u) for u in c.get("extra_urls") or []]:
                if info["id"] not in SKIPPED:
                    log.info("live %s ignorat (titlul nu contine %s): %s", info["id"], flt, info.get("title"))
                    SKIPPED.add(info["id"])
            else:
                start_live(info)
        for vid in list(ACTIVE):
            if ACTIVE[vid]["rec"].ended.is_set():
                log.info("live %s terminat: completez golurile din VOD peste 20 min", vid)
                db.upsert_video(vid, None, None, None, "ended")
                start_vod(vid, delay=20 * 60)
                del ACTIVE[vid]
        for vid, job in VOD_JOBS.items():
            th, not_before, tries = job
            if (th is None or not th.is_alive()) and time.time() >= not_before and tries < 12:
                if th is not None and th.complete:
                    continue
                job[0] = Backfill(vid, "vod"); job[0].start(); job[2] += 1
                job[1] = time.time() + 30 * 60        # retry later if the VOD isn't ready yet
        STATUS["watcher"] = ("inregistrez: " + ", ".join(ACTIVE)) if ACTIVE else "Zbir nu e live acum, verific din nou in curand"
        sleep_until(STOP, c.get("check_live_every_sec", 60))


_last_auto = [None]


def note_auto(auto, source="config"):
    """Log every Auto-postare change (from the app button, Settings, or an edited config file)."""
    auto = bool(auto)
    if _last_auto[0] is not None and _last_auto[0] != auto:
        msg = f"Auto-postare {'PORNITĂ (ON)' if auto else 'OPRITĂ (OFF)'} ({source})"
        log.info(msg); progress.event(msg)
    _last_auto[0] = auto


def _merged_len(rows, lo, hi):
    iv = sorted((max(lo, r["t0"]), min(hi, r["t1"])) for r in rows if r["t1"] > lo and r["t0"] < hi)
    tot, cur = 0.0, None
    for a, b in iv:
        if cur and a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
    return tot + (cur[1] - cur[0] if cur else 0.0)


def video_progress(vid, title=None, live=True, fill=None, now=None):
    """How much of a live/VOD is downloaded and processed, where the backfill is, ETA to catch the live edge."""
    c = cfg()
    now = now or time.time()
    v = db.video(vid) or {}
    rows = db.q("SELECT t0, t1, status, kind FROM chunks WHERE video_id=?", (vid,))
    if live:
        total = max(0.0, now - (v["start_ts"] if v and v["start_ts"] else now))
    else:
        total = (fill.total if fill is not None and fill.total else 0) or max([r["t1"] for r in rows] or [0])
    lo = max(0.0, total - float(c.get("backfill_max_hours", 12)) * 3600) if live else 0.0
    span = max(1.0, total - lo)
    processed = _merged_len([r for r in rows if r["status"] in ("done", "error")], lo, total)
    downloaded = _merged_len(rows, lo, total)
    first_live = min([r["t0"] for r in rows if r["kind"] == "live"] or [None]) if any(r["kind"] == "live" for r in rows) else None
    remaining = max(0.0, span - processed)
    eta = progress.eta_catch_up(remaining, live)
    return {"id": vid, "title": title or (v["title"] if v else None) or vid, "live": live,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "total": round(total), "window_start": round(lo), "processed": round(processed), "downloaded": round(downloaded),
            "pct": round(min(100.0, 100 * processed / span), 1), "pct_downloaded": round(min(100.0, 100 * downloaded / span), 1),
            "backfill_pos": None if fill is None or fill.pos is None else round(fill.pos),
            "live_rec_from": None if first_live is None else round(first_live),
            "remaining": round(remaining), "eta": None if eta is None else round(eta),
            "chunks_queued": sum(1 for r in rows if r["status"] == "queued"),
            "chunks_done": sum(1 for r in rows if r["status"] == "done")}


def progress_snapshot():
    """Everything the 'Progres' panel shows (polled every second)."""
    c = cfg()
    auto = bool(c.get("auto_post", False))
    note_auto(auto)
    vids = [video_progress(vid, a.get("title"), True, a.get("fill")) for vid, a in list(ACTIVE.items())]
    for vid, job in list(VOD_JOBS.items()):
        th = job[0]
        if th is not None and th.is_alive():
            vids.append(video_progress(vid, None, False, th))
    p = progress.snapshot()
    ch = p["channels"]
    w = ch.get("watcher", {})
    plan, below = db.post_plan(c.get("post_order", "viral_first"), auto, int(c.get("auto_post_min_viral", 40)), limit=500)
    nxt = STATUS.get("next_post_at") or 0
    return {"videos": vids, "stages": ch, "session": p["session"], "events": p["events"], "speed": p["speed"],
            "queue": {"chunks": backlog(),
                      "clips_to_render": max(0, int(w.get("clip_m") or 0) - int(w.get("clip_n") or 0)) if w.get("stage") not in (None, "astept") else 0,
                      "to_post": len(plan), "manual": sum(1 for r in plan if r["plan"] == "manual"),
                      "below_threshold": sum(1 for r in below if r.get("plan") != "duplicat"),
                      "next_post_at": nxt, "next_post_in": max(0, round(nxt - time.time())) if nxt else None,
                      "next_clip": ({"id": plan[0]["id"], "hook": plan[0]["hook"], "viral": db.eff_viral(plan[0])} if plan else None)},
            "auto_post": auto, "post_paused": POST_PAUSED.is_set(), "watching": WATCHING.is_set(),
            "posted_today": db.posts_since(midnight()), "daily_cap": int(c.get("daily_post_cap", 45)),
            "min_viral": int(c.get("auto_post_min_viral", 40)), "poster": STATUS.get("poster"),
            "watcher": STATUS.get("watcher"), "interval": [c.get("post_interval_min_minutes", 20), c.get("post_interval_max_minutes", 30)],
            "backoff": (lambda b: b if b.get("until", 0) > time.time() else None)(backoff()), "jitter": c.get("post_jitter_minutes", 4),
            "now": time.time()}


def snapshot():
    """Everything the dashboard shows."""
    lives = []
    for vid, a in ACTIVE.items():
        lives.append({"id": vid, "title": a["title"], "url": f"https://www.youtube.com/watch?v={vid}",
                      "recording": not a["rec"].ended.is_set(), "since": a["since"],
                      "chunks": db.q("SELECT COUNT(*) n FROM chunks WHERE video_id=?", (vid,), one=True)["n"]})
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
    ch = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM chunks GROUP BY status")}
    return {"watching": WATCHING.is_set(), "post_paused": POST_PAUSED.is_set(), "lives": lives, "status": STATUS,
            "clips": counts, "chunks": ch, "posted_today": db.posts_since(midnight()), "errors": list(ERRORS)[:15],
            "auto_post": bool(cfg().get("auto_post", False)), "daily_cap": cfg().get("daily_post_cap", 45)}

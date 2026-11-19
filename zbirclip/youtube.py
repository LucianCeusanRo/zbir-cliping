"""Everything YouTube: find Zbir's current live, list recent VODs, download parts of the stream.

Timeline convention: every time in the program is "stream time" = seconds since the live started
(YouTube's release_timestamp / actualStartTime). That makes live chunks, backfill chunks and the VOD
line up, so dedupe across restarts works.
"""
import concurrent.futures as cf, json, re, threading, time
from pathlib import Path
import requests
from .util import cfg, log, run, tool, ytdlp_opts, ffprobe_duration, spawn, forget

_YDL_LOCK = threading.Lock()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


class _QuietLogger:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): log.debug("yt-dlp: %s", str(m)[:200])


def extract(url, flat=False, **extra):
    """yt-dlp extract_info with our options; tries the default clients, then mweb (works when web is bot-checked)."""
    from yt_dlp import YoutubeDL
    tries = [None, ["mweb"], ["tv", "web_safari"]]
    last = None
    for clients in tries:
        o = dict(ytdlp_opts(), logger=_QuietLogger(), **extra)
        if flat:
            o["extract_flat"] = "in_playlist"
        if clients and "extractor_args" not in o:
            o["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with YoutubeDL(o) as y:
                return y.sanitize_info(y.extract_info(url, download=False)) if not extra.get("live_from_start") \
                    else y.extract_info(url, download=False)
        except Exception as e:
            last = e
            msg = str(e)
            if "not currently live" in msg or "will begin" in msg or "Premieres" in msg:
                raise
            log.debug("yt-dlp %s failed with clients %s: %s", url, clients, msg[:200])
    raise last


def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/)([\w-]{11})", url)
    return m.group(1) if m else None


def current_live(channel_url):
    """Return info dict of the channel's running live, or None. Uses <channel>/live which redirects to it."""
    url = channel_url.rstrip("/") + "/live"
    try:
        info = extract(url)
    except Exception as e:
        log.debug("no live: %s", str(e)[:150])
        return None
    if info.get("live_status") == "is_live":
        return info
    return None


def recent_streams(channel_url, n=5):
    """Recent entries of the channel's Live tab: [{'id','title','live_status'}]."""
    try:
        info = extract(channel_url.rstrip("/") + "/streams", flat=True, playlistend=n)
    except Exception as e:
        log.warning("nu pot citi lista de live-uri: %s", str(e)[:200])
        return []
    return [{"id": e.get("id"), "title": e.get("title"), "live_status": e.get("live_status")}
            for e in (info.get("entries") or []) if e.get("id")]


def start_ts(info):
    return info.get("release_timestamp") or info.get("timestamp")


def hls_url(info, max_h):
    """(video_url, audio_url_or_None) for live-edge recording. Some YouTube clients give muxed HLS (audio+video in
    one playlist), others give video-only HLS plus a separate audio playlist - handle both."""
    fm = [f for f in info.get("formats", []) if "m3u8" in (f.get("protocol") or "")]
    vids = [f for f in fm if f.get("vcodec") not in (None, "none") and (f.get("height") or 0) <= max_h]
    if not vids:
        return None, None
    muxed = [f for f in vids if f.get("acodec") not in (None, "none")]
    key = lambda f: (f.get("height") or 0, f.get("tbr") or 0)
    if muxed:
        return max(muxed, key=key)["url"], None
    auds = [f for f in fm if f.get("vcodec") in (None, "none") and f.get("acodec") != "none"]
    auds.sort(key=lambda f: ("high" in str(f.get("format_note", "")).lower(), f.get("abr") or f.get("tbr") or 0))
    return max(vids, key=key)["url"], (auds[-1]["url"] if auds else None)


# ---------------------------------------------------------------------------------------------
#  Backfill of an ongoing live (the part streamed before we started) via YouTube's DASH "from start"
#  fragments. Each fragment is addressed by its sequence number (?sq=N) and lasts target_duration
#  seconds, so stream time t lives in fragment int(t / target_duration). Same mechanism yt-dlp uses
#  for --live-from-start, but we fetch only the ranges we still need, in parallel, and in small chunks.
# ---------------------------------------------------------------------------------------------
class DashBackfill:
    def __init__(self, url, max_h):
        self.url, self.max_h = url, max_h
        self.fetched_at = 0
        self.v = self.a = None
        self.refresh()

    def refresh(self):
        info = extract(self.url, live_from_start=True)
        fs = [f for f in info.get("formats", []) if f.get("is_from_start") or "dash" in (f.get("protocol") or "")]
        vids = [f for f in fs if f.get("vcodec") not in (None, "none") and (f.get("height") or 0) <= self.max_h
                and str(f.get("vcodec", "")).startswith("avc1")]
        auds = [f for f in fs if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        if not vids or not auds:
            raise RuntimeError("YouTube nu ofera formate DASH 'from start' pentru acest live")
        vids.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        auds.sort(key=lambda f: (f.get("format_id") == "140", f.get("abr") or 0))
        self.v, self.a = vids[-1], auds[-1]
        self.frag = float(self.v.get("target_duration") or 2.0)  # seconds per sequence number
        self.fetched_at = time.time()
        self.start_ts = start_ts(info)
        log.info("backfill DASH: %sp, fragment %.1fs", self.v.get("height"), self.frag)

    def head_seq(self):
        r = requests.head(self.v["url"], headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        return int(r.headers.get("X-Head-Seqnum", "-1"))

    def _get(self, base, sq, s):
        u = base + ("&" if "?" in base else "?") + f"sq={sq}"
        for i in range(5):
            try:
                r = s.get(u, headers={"User-Agent": UA}, timeout=30)
                if r.status_code == 200 and r.content:
                    return r.content
            except requests.RequestException:
                pass
            time.sleep(1 + i * 2)
        raise RuntimeError(f"fragment {sq} indisponibil")

    def download(self, t0, t1, out: Path):
        if time.time() - self.fetched_at > 1800:   # signed URLs expire, refresh every 30 min
            self.refresh()
        a, b = int(t0 // self.frag), int(t1 // self.frag) + 1
        s = requests.Session()
        parts = {}
        from . import progress
        n_all, done = 2 * max(1, b - a), [0]
        lk = threading.Lock()

        def get(url, sq):
            d = self._get(url, sq, s)
            with lk:
                done[0] += 1
                progress.pct(100 * done[0] / n_all, detail=f"fragment {done[0]}/{n_all}")
            return d
        for kind, f in (("v", self.v), ("a", self.a)):
            with cf.ThreadPoolExecutor(8) as ex:
                data = list(ex.map(lambda sq: get(f["url"], sq), range(a, b)))
            p = out.with_suffix(f".{kind}.mp4")
            with open(p, "wb") as fh:
                for d in data:
                    fh.write(d)
            parts[kind] = p
        run([tool("ffmpeg"), "-y", "-v", "error", "-i", parts["v"], "-i", parts["a"], "-map", "0:v:0", "-map", "1:a:0",
             "-c", "copy", "-movflags", "+faststart", out])
        for p in parts.values():
            p.unlink(missing_ok=True)
        real_t0 = a * self.frag
        if ffprobe_duration(out) < (t1 - t0) * 0.5:
            raise RuntimeError("backfill: fisier prea scurt")
        return real_t0


def download_section(url, t0, t1, out: Path, max_h):
    """VOD (finished live) section download: yt-dlp --download-sections, reliable for archives."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func
    o = dict(ytdlp_opts(), logger=_QuietLogger(), format=f"bv*[height<={max_h}][vcodec^=avc1]+ba/b[height<={max_h}]/b",
             merge_output_format="mp4", outtmpl=str(out.with_suffix(".%(ext)s")),
             download_ranges=download_range_func(None, [(t0, t1)]), force_keyframes_at_cuts=False)
    with YoutubeDL(o) as y:
        y.download([url])
    mp4 = out.with_suffix(".mp4")
    if not mp4.exists():
        cands = list(out.parent.glob(out.stem + ".*"))
        if not cands:
            raise RuntimeError("download_section: nimic descarcat")
        cands[0].rename(mp4)
    return mp4


# ---------------------------------------------------------------------------------------------
#  Live chat (activity signal). yt-dlp writes the chat as JSON lines while the live runs.
# ---------------------------------------------------------------------------------------------
CHAT_HYPE = re.compile(r"(?i)(haha|hah|ahah|xd|lol|lmao|kek|🤣|😂|💀|🔥|clip|clipp|n-?am cuvinte|morr?|mor de ras|ce tare|"
                       r"gg|w+\b|bravo|nebun|ps5|giveaway)")


class ChatRecorder(threading.Thread):
    """Records live chat of one video to data/<vid>/chat.jsonl -> per-second counts in stream time."""

    def __init__(self, url, folder: Path, start_ts, stop):
        super().__init__(daemon=True, name="chat")
        self.url, self.folder, self.start_ts, self.stop = url, folder, start_ts, stop
        self.proc = None

    def run(self):
        import subprocess, sys
        cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
               "--no-warnings", "-q", "-o", str(self.folder / "chat"), self.url]
        c = cfg()
        if c.get("youtube_cookies_from_browser"):
            cmd[3:3] = ["--cookies-from-browser", c["youtube_cookies_from_browser"]]
        while not self.stop.is_set():
            try:
                self.proc = spawn(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while self.proc.poll() is None and not self.stop.is_set():
                    self.stop.wait(5)
                if self.proc.poll() is None:
                    self.proc.terminate()
                forget(self.proc)
            except Exception as e:
                log.debug("chat recorder: %s", e)
            self.stop.wait(30)


def chat_counts(folder: Path, start_ts, t0, t1):
    """Messages per second (weighted, hype messages count double) for stream-time window [t0, t1)."""
    import numpy as np
    n = max(1, int(t1 - t0))
    c = np.zeros(n)
    for f in folder.glob("chat*.live_chat.json*"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                act = json.loads(ln)["replayChatItemAction"]
            except Exception:
                continue
            t = None
            if "videoOffsetTimeMsec" in act:
                t = int(act["videoOffsetTimeMsec"]) / 1000
            body = json.dumps(act.get("actions", []), ensure_ascii=False)
            if t is None and start_ts:
                m = re.search(r'"timestampUsec":\s*"(\d+)"', body)
                if m:
                    t = int(m.group(1)) / 1e6 - start_ts
            if t is None or not (t0 <= t < t1):
                continue
            c[int(t - t0)] += 2 if CHAT_HYPE.search(body) else 1
    return c

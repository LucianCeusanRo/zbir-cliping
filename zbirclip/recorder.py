"""Live-edge recorder: ffmpeg reads the live HLS and cuts it into N-minute .ts chunks.

Each finished chunk is registered in the DB with its stream-time range, so it is processed once.
If ffmpeg dies (URL expiry, network drop, stream end) we re-resolve the URL and continue; whatever was
missed in between is a "gap" that the backfill worker fills from the DVR / VOD later.
"""
import subprocess, threading, time
from datetime import datetime
from pathlib import Path
from . import db, youtube
from .util import cfg, log, tool, ffprobe_duration, spawn, forget


class LiveRecorder(threading.Thread):
    def __init__(self, info, folder: Path, stop):
        super().__init__(daemon=True, name="record")
        self.info, self.folder, self.stop = info, folder, stop
        self.vid = info["id"]
        self.start_ts = youtube.start_ts(info) or time.time()
        self.ended = threading.Event()
        (folder / "live").mkdir(parents=True, exist_ok=True)

    def run(self):
        c = cfg()
        seg = int(c.get("chunk_minutes", 5) * 60)
        fails = 0
        info = self.info
        while not self.stop.is_set():
            url, aurl = youtube.hls_url(info, c.get("video_max_height", 1080))
            if not url:
                log.warning("live %s: nu gasesc HLS", self.vid)
            else:
                t_begin = time.time()
                self._record(url, aurl, seg)
                if time.time() - t_begin > 120:
                    fails = 0
            self._register_finished(final=True)
            if self.stop.is_set():
                break
            # still live? refresh info (new signed URL); else stop
            try:
                info = youtube.extract(f"https://www.youtube.com/watch?v={self.vid}")
            except Exception as e:
                log.warning("live %s: verificare esuata: %s", self.vid, str(e)[:120])
                info = None
            if not info or info.get("live_status") != "is_live":
                log.info("LIVE-ul %s s-a terminat.", self.vid)
                break
            fails += 1
            self.stop.wait(min(60, 5 * fails))
        self.ended.set()

    def _record(self, url, aurl, seg):
        out = self.folder / "live" / "rec_%Y%m%d_%H%M%S.ts"
        net = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10", "-user_agent", youtube.UA]
        cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", *net, "-i", url]
        if aurl:   # separate audio playlist
            cmd += [*net, "-i", aurl, "-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
                "-strftime", "1", str(out)]
        log.info("inregistrez LIVE %s (bucati de %d min)", self.vid, seg // 60)
        p = spawn(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while p.poll() is None and not self.stop.is_set():
                self.stop.wait(10)
                self._register_finished()
            if p.poll() is None:
                try:
                    p.stdin.write(b"q"); p.stdin.flush()   # graceful: closes the current segment
                    p.wait(15)
                except Exception:
                    p.kill()
        finally:
            forget(p)

    def _register_finished(self, final=False):
        files = sorted((self.folder / "live").glob("rec_*.ts"))
        if not final:
            files = files[:-1]          # the newest file is still being written
        known = {r["path"] for r in db.q("SELECT path FROM chunks WHERE video_id=?", (self.vid,))}
        for f in files:
            if str(f) in known:
                continue
            d = ffprobe_duration(f)
            if d < 10:
                f.unlink(missing_ok=True)
                continue
            wall = datetime.strptime(f.stem[4:], "%Y%m%d_%H%M%S").timestamp()
            t0 = max(0.0, wall - self.start_ts - 3)      # ~3 s HLS latency
            db.add_chunk(self.vid, t0, t0 + d, f, "live")
            log.info("bucata live noua: %s [%s - %s]", f.name, hms(t0), hms(t0 + d))


def hms(t):
    t = int(t); return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"

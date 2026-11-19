"""CLI:  python -m zbirclip <command>

  app                 aplicatia Zbir Cliping (fereastra + tot motorul) - ce porneste scurtatura de pe Desktop
  run                 24/7 fara fereastra (motor + aplicatia web la http://127.0.0.1:8777)
  run --no-post       la fel, dar fara postare (doar face clipuri in data/clips)
  login               deschide browserul ca sa te loghezi o data pe TikTok
  file <video>        proceseaza un fisier video local (test)
  url <link>          proceseaza un VOD de YouTube (o singura data)
  post-test <clip>    posteaza un singur clip (verificare)
  status              ce e in coada / ce s-a postat
"""
import argparse, sys, threading, time
from pathlib import Path
from .util import setup_logging, cfg, log, ffprobe_duration, DATA


def cmd_file(path, vid=None):
    from . import db, pipeline
    p = Path(path).resolve()
    vid = vid or ("file-" + p.stem[:20])
    db.upsert_video(vid, p.stem, "local", time.time(), "file")
    dur = ffprobe_duration(p)
    size = cfg().get("chunk_minutes", 5) * 60
    from .util import run, tool
    folder = DATA / vid; folder.mkdir(parents=True, exist_ok=True)
    t = 0.0
    while t < dur - 30:
        if not db.gaps(vid, t, min(dur, t + size) - 1, 10):
            t += size; continue
        out = folder / f"file_{int(t):06d}.mkv"
        run([tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{t}", "-t", f"{size}", "-i", p, "-map", "0:v:0", "-map", "0:a:0?",
             "-c", "copy", out])
        d = ffprobe_duration(out)
        cid = db.add_chunk(vid, t, t + d, out, "file")
        ch = db.q("SELECT * FROM chunks WHERE id=?", (cid,), one=True)
        db.x("UPDATE chunks SET status='processing' WHERE id=?", (cid,))
        n = pipeline.process_chunk(ch)
        db.finish_chunk(cid, "done")
        log.info("bucata %s gata: %d clipuri", pipeline.hms(t), n)
        t += size
    for r in db.q("SELECT * FROM clips WHERE video_id=? ORDER BY score DESC", (vid,)):
        print(f"{r['score']:5.1f}  {pipeline.hms(r['start'])}  {r['file']}\n       {r['caption']}")


def cmd_url(url):
    from . import app, db, youtube
    info = youtube.extract(url)
    app.register(info, "vod")
    threading.Thread(target=app.worker, daemon=True, name="worker").start()
    if info.get("live_status") == "is_live":
        print("Este LIVE acum: foloseste `run` (urmareste live-ul in timp real).")
        return
    b = app.Backfill(info["id"], "vod"); b.start(); b.join()
    while app.backlog() or db.q("SELECT COUNT(*) n FROM chunks WHERE status='processing'", one=True)["n"]:
        time.sleep(5)
    app.STOP.set()


def cmd_status():
    from . import db
    print("Bucati:", {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM chunks GROUP BY status")})
    print("Clipuri:", {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM clips GROUP BY status")})
    for r in db.q("SELECT * FROM clips ORDER BY created DESC LIMIT 15"):
        print(f"  [{r['status']:<11}] scor {r['score']:5.1f}  {Path(r['file']).name}  {r['caption'][:70]}")


def main():
    setup_logging()
    ap = argparse.ArgumentParser(prog="zbirclip")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--no-post", action="store_true")
    sub.add_parser("app")
    sub.add_parser("login")
    f = sub.add_parser("file"); f.add_argument("path"); f.add_argument("--id")
    u = sub.add_parser("url"); u.add_argument("link")
    pt = sub.add_parser("post-test"); pt.add_argument("clip"); pt.add_argument("--caption")
    sub.add_parser("status")
    upd = sub.add_parser("update"); upd.add_argument("--force", action="store_true")
    sub.add_parser("version")
    stp = sub.add_parser("selftest"); stp.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.cmd == "app":
        from . import desktop; desktop.main()
    elif a.cmd == "run":
        from . import app, web, desktop
        from .util import _install_signal_handlers
        _install_signal_handlers()
        try:
            web.serve()
        except OSError:
            sys.exit("Zbir Cliping ruleaza deja (portul aplicatiei e ocupat). Inchide-l intai sau schimba dashboard_port.")
        try:
            desktop.engine(post=not a.no_post)
        except KeyboardInterrupt:
            app.STOP.set(); app.stop_all_lives(); print("Oprit.")
    elif a.cmd == "login":
        from . import tiktok; tiktok.login()
    elif a.cmd == "file":
        cmd_file(a.path, a.id)
    elif a.cmd == "url":
        cmd_url(a.link)
    elif a.cmd == "post-test":
        from . import tiktok
        cap = a.caption or ("Test " + " ".join(cfg()["hashtags"]))
        print(tiktok.upload(Path(a.clip), cap))
    elif a.cmd == "selftest":
        from . import selftest; sys.exit(selftest.main(quick=a.quick))
    elif a.cmd == "update":
        from . import updater; sys.exit(updater.main_cli())
    elif a.cmd == "version":
        from . import updater; print(updater.local_version())
    elif a.cmd == "status":
        cmd_status()


if __name__ == "__main__":
    main()

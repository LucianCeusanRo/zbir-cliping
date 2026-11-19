"""Shared helpers: config, paths, logging, subprocess, yt-dlp args, CUDA dll setup."""
import json, logging, os, shutil, subprocess, sys, time
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
from pathlib import Path

ROOT = Path(os.environ.get("ZBIR_HOME", Path(__file__).resolve().parent.parent))
DATA_POINTER = ROOT / "data_location.txt"     # where the data folder lives (set from Settings); never in git/updates
DATA_MOVE = ROOT / "data_move_pending.json"   # {"from": old, "to": new}: moved at the next start, before anything opens


def _resolve_data():
    if os.environ.get("ZBIR_DATA"):
        return Path(os.environ["ZBIR_DATA"])
    try:
        p = DATA_POINTER.read_text(encoding="utf-8").strip()
        if p and Path(Path(p).anchor or ".").exists():
            return Path(p)
    except OSError:
        pass
    if os.name == "nt" and not os.environ.get("ZBIR_HOME"):   # Luci's PC: data lives on F: (big disk) when that folder exists
        f = Path(r"F:\ZbirCliping\data")
        if f.exists():
            return f
    return ROOT / "data"


def _remap_db_paths(dbf, old, new):
    """The db stores absolute file paths: point them at the new folder."""
    import sqlite3
    if not dbf.exists():
        return
    con = sqlite3.connect(str(dbf))
    try:
        for table, cols in (("clips", ("file", "thumb", "src")), ("chunks", ("path",))):
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for col in cols:
                if col in have:
                    con.execute(f"UPDATE {table} SET {col} = ? || substr({col}, ?) WHERE {col} LIKE ? || '%'",
                                (new, len(old) + 1, old))
        con.commit()
    finally:
        con.close()


def request_data_move(new_path, move=True):
    """Validate the new data folder now; the move itself happens on the next start (nothing is open then)."""
    p = Path(str(new_path).strip().strip('"'))
    if not str(p) or not p.is_absolute():
        raise ValueError("alege o cale completă, ex. F:\\ZbirCliping\\data")
    if not Path(p.anchor).exists():
        raise ValueError(f"discul {p.anchor} nu există")
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".zbir_write_test"
        t.write_text("ok", encoding="utf-8"); t.unlink()
    except OSError as e:
        raise ValueError(f"nu pot scrie în {p}: {e}")
    if p.resolve() == DATA.resolve():
        raise ValueError("acesta e deja folderul de date")
    try:
        p.resolve().relative_to(DATA.resolve())
        raise ValueError("folderul nou nu poate fi în interiorul celui vechi")
    except ValueError as e:
        if "interiorul" in str(e):
            raise
    free = shutil.disk_usage(p).free
    used = sum(f.stat().st_size for f in DATA.rglob("*") if f.is_file()) if move and DATA.exists() else 0
    if move and used > free * 0.95:
        raise ValueError(f"nu e destul loc pe {p.anchor}: trebuie {used / 1e9:.1f} GB, liber {free / 1e9:.1f} GB")
    if move:
        DATA_MOVE.write_text(json.dumps({"from": str(DATA), "to": str(p)}), encoding="utf-8")
    else:
        DATA_POINTER.write_text(str(p), encoding="utf-8")
    return {"path": str(p), "move": move, "size_gb": round(used / 1e9, 2), "free_gb": round(free / 1e9, 1)}


def _do_pending_move():
    """Runs at import (app not started yet): move data -> new folder, verify, then switch the pointer."""
    if not DATA_MOVE.exists() or os.environ.get("ZBIR_DATA"):
        return
    try:
        j = json.loads(DATA_MOVE.read_text(encoding="utf-8"))
        src, dst = Path(j["from"]), Path(j["to"])
        dst.mkdir(parents=True, exist_ok=True)
        if src.exists() and src.resolve() != dst.resolve():
            for item in list(src.iterdir()):
                target = dst / item.name
                if target.exists():
                    continue                      # never overwrite: keep what is already in the new folder
                shutil.move(str(item), str(target))
        _remap_db_paths(dst / "state.db", str(src), str(dst))
        DATA_POINTER.write_text(str(dst), encoding="utf-8")
        (dst / "MUTAT_AICI.txt").write_text(f"Datele Zbir Cliping au fost mutate din {src} la {time.ctime()}\n", encoding="utf-8")
    except Exception as e:                        # keep the old location; the app shows the error
        (ROOT / "data_move_error.txt").write_text(str(e), encoding="utf-8")
    finally:
        try:
            DATA_MOVE.unlink()
        except OSError:
            pass


_do_pending_move()
DATA = _resolve_data()
FONTS = Path(os.environ.get("ZBIR_FONTS", ROOT / "fonts"))
log = logging.getLogger("zbir")


def setup_logging():
    DATA.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(threadName)-9s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt)
    try:
        sh.stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(DATA / "zbirclip.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"); fh.setFormatter(fmt)
    quiet = logging.Filter(); quiet.filter = lambda r: "HTTP Request:" not in r.getMessage() and "HF_TOKEN" not in r.getMessage()
    sh.addFilter(quiet); fh.addFilter(quiet)
    root.addHandler(sh); root.addHandler(fh)
    for noisy in ("faster_whisper", "httpx", "urllib3", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_cfg = None
_cfg_stamp = None
SETTINGS = DATA / "settings.json"     # changes made from the app (override config.yaml)


LOCAL_CONFIG = ROOT / "config.local.yaml"   # your own hand edits; updates never touch it (nor data/settings.json)


def _cfg_paths():
    return Path(os.environ.get("ZBIR_CONFIG", ROOT / "config.yaml")), SETTINGS


def cfg():
    """config.yaml + data/settings.json overrides; re-read automatically when either file changes."""
    global _cfg, _cfg_stamp
    p, sp = _cfg_paths()
    lp = LOCAL_CONFIG if "ZBIR_CONFIG" not in os.environ else Path(str(p) + ".local-none")
    stamp = tuple((f.stat().st_mtime_ns, f.stat().st_size) if f.exists() else 0 for f in (p, lp, sp))
    if _cfg is None or stamp != _cfg_stamp:
        import yaml
        c = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if lp.exists():
            try:
                c.update(yaml.safe_load(lp.read_text(encoding="utf-8")) or {})
            except Exception as e:
                log.warning("config.local.yaml invalid, il ignor: %s", e)
        over = load_json(sp, {}) if sp.exists() else {}
        c.update(over or {})
        def _tags(lst):
            out = []
            for t in lst or []:
                t = "#" + str(t).strip().lstrip("#")
                if len(t) > 1 and t.lower() not in [o.lower() for o in out]:
                    out.append(t)
            return out
        # mandatory contest hashtag, always FIRST
        c["hashtags"] = ["#zbirgta6"] + [t for t in _tags(c.get("hashtags")) if t.lower() != "#zbirgta6"]
        c["extra_hashtags"] = [t for t in _tags(c.get("extra_hashtags")) if t.lower() != "#zbirgta6"]
        _cfg, _cfg_stamp = c, stamp
    return _cfg


def save_settings(changes):
    cur = load_json(SETTINGS, {}) if SETTINGS.exists() else {}
    cur.update(changes)
    DATA.mkdir(parents=True, exist_ok=True)
    save_json(SETTINGS, cur)
    global _cfg
    _cfg = None          # never trust the mtime alone (coarse timestamps on some disks)
    return cfg()


BIN = ROOT / "bin"            # setup.bat drops ffmpeg/ffprobe/deno here (no winget, no PATH changes needed)
if BIN.is_dir() and str(BIN) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")   # yt-dlp finds deno.exe this way


ALLOWED_HOSTS = ("youtube.com", "youtu.be", "kick.com", "twitch.tv", "tiktok.com")
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


def migrate_settings():
    """One-time: settings saved by old versions contain the OLD default pace (30/day, 30-45 min) and
    post_order best_first -> drop exactly those values so the new defaults (Normal 45/day, 20-30 min,
    viral first) apply. Anything you changed yourself stays."""
    cur = load_json(SETTINGS, None)
    if not isinstance(cur, dict) or cur.get("_migrated", 0) >= 2:
        return False
    old = {"daily_post_cap": 30, "post_interval_min_minutes": 30, "post_interval_max_minutes": 45, "post_order": "best_first"}
    for k, v in old.items():
        if k in cur and cur[k] == v:
            cur.pop(k)
    cur["_migrated"] = 2
    save_json(SETTINGS, cur)
    global _cfg
    _cfg = None
    return True


def check_url(url, youtube_only=False):
    """Only plain https/http links to known stream sites reach yt-dlp (no options, no file://, no internal hosts)."""
    from urllib.parse import urlparse
    u = (url or "").strip()
    if not u or any(ch.isspace() for ch in u) or u.startswith("-") or len(u) > 500:
        raise ValueError("link invalid")
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("linkul trebuie să înceapă cu https://")
    host = p.hostname.lower()
    allowed = YOUTUBE_HOSTS if youtube_only else ALLOWED_HOSTS
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise ValueError("accept doar linkuri de pe " + ", ".join(allowed))
    return u


def tool(name):
    local = BIN / (name + (".exe" if sys.platform == "win32" else ""))
    if local.exists():
        return str(local)               # our own copy first (known to have NVENC)
    p = shutil.which(name)
    if not p:
        raise SystemExit(f"{name} nu a fost gasit. Ruleaza setup.bat (instaleaza ffmpeg).")
    return p


def run(cmd, check=True, capture=True, timeout=None):
    r = subprocess.run([str(c) for c in cmd], text=True, encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
                       timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:300]}\n{(r.stderr or '')[-1500:]}")
    return r


def ytdlp_opts():
    """Options dict for the yt_dlp Python API (shared by every YouTube call)."""
    c = cfg()
    o = {"quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30, "retries": 10,
         "fragment_retries": 20, "extractor_retries": 3}
    if c.get("youtube_cookies_from_browser"):
        o["cookiesfrombrowser"] = (c["youtube_cookies_from_browser"],)
    if c.get("youtube_cookies_file"):
        o["cookiefile"] = c["youtube_cookies_file"]
    o["js_runtimes"] = {"deno": {}, "node": {}, "bun": {}}   # YouTube needs a JS runtime (setup.bat installs deno)
    if os.environ.get("ZBIR_YT_CLIENT"):
        o["extractor_args"] = {"youtube": {"player_client": os.environ["ZBIR_YT_CLIENT"].split(",")}}
    return o


def setup_cuda_dlls():
    """On Windows, pip's nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin. Add them."""
    if sys.platform != "win32":
        return
    try:
        import nvidia
        base = Path(list(nvidia.__path__)[0])
    except Exception:
        return
    for b in base.glob("*/bin"):
        try:
            os.add_dll_directory(str(b))
        except Exception:
            pass
        os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


# ---- child processes (ffmpeg recorder, chat downloader): never leave orphans behind ----
_children = {}
_children_lock = __import__("threading").Lock()
PIDS = DATA / "children.json"


def spawn(cmd, **kw):
    import atexit
    p = subprocess.Popen([str(c) for c in cmd], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)
    with _children_lock:
        if not _children:
            atexit.register(kill_children)
            _install_signal_handlers()
        _children[p.pid] = p
        _write_pids()
    return p


def run_ffmpeg_progress(cmd, total_s, on_pct=None, timeout=1800):
    """Run an ffmpeg command (cmd[0] = ffmpeg) and call on_pct(0..100) while it encodes (parses -progress)."""
    import tempfile, threading
    cmd = [str(cmd[0]), "-progress", "pipe:1", "-nostats", *[str(c) for c in cmd[1:]]]
    err = tempfile.TemporaryFile()
    p = spawn(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL)
    killer = threading.Timer(timeout, p.kill); killer.daemon = True; killer.start()
    try:
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if on_pct and total_s and line.startswith("out_time_us=") and line[12:].lstrip("-").isdigit():
                try:
                    on_pct(min(100.0, max(0.0, int(line[12:]) / 1e6 / total_s * 100)))
                except Exception:
                    pass
        p.wait()
    finally:
        killer.cancel(); forget(p)
    if p.returncode != 0:
        err.seek(0)
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)[:300]}\n{err.read().decode('utf-8', 'replace')[-1500:]}")
    err.close()


def _install_signal_handlers():
    import signal, threading
    if threading.current_thread() is not threading.main_thread():
        return _pending_signals.append(1)

    def h(sig, frame):
        kill_children()
        raise SystemExit(0)
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), h)
            except Exception:
                pass


_pending_signals = []


def forget(p):
    with _children_lock:
        _children.pop(p.pid, None); _write_pids()


def _write_pids():
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        PIDS.write_text(json.dumps(list(_children)), encoding="utf-8")
    except OSError:
        pass


def kill_children():
    for p in list(_children.values()):
        try:
            p.kill()
        except Exception:
            pass
    _children.clear(); _write_pids()


def kill_stale_children():
    """After a crash/hard kill: stop ffmpeg/yt-dlp processes left running by the previous run."""
    try:
        pids = json.loads(PIDS.read_text(encoding="utf-8"))
    except Exception:
        return
    for pid in pids:
        try:
            pid = int(pid)
            if sys.platform == "win32":     # only if that PID is still an ffmpeg/python of ours (PIDs get reused)
                r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                   text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if not any(n in r.stdout.lower() for n in ("ffmpeg", "python")):
                    continue
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore") if Path(f"/proc/{pid}").exists() else ""
                if not any(n in cmdline for n in ("ffmpeg", "yt_dlp")):
                    continue
                os.kill(pid, 9)
            log.info("am oprit un proces ramas de la rularea anterioara (pid %s)", pid)
        except Exception:
            pass
    _write_pids()


def save_json(path, obj):
    """Atomic: write a temp file, fsync, then replace (a crash never leaves a half-written settings file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1))
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    for i in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:          # Windows: a reader holds the file for a moment
            time.sleep(0.1 * (i + 1))
    else:
        os.replace(tmp, path)
    try:
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    except OSError:
        pass


def load_json(path, default=None):
    """Corrupt/partial file -> falls back to the last good .bak copy, then to default."""
    for p in (Path(path), Path(str(path) + ".bak")):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return default


def ffprobe_duration(path):
    try:
        r = run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", path])
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def sleep_until(stop_event, secs):
    """Sleep that wakes up early on shutdown."""
    end = time.time() + secs
    while not stop_event.is_set() and time.time() < end:
        stop_event.wait(min(1.0, end - time.time()))

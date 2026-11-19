"""In-app updates from GitHub (no git, no token needed on the PC).

check : https://raw.githubusercontent.com/<repo>/main/version.txt  (fallback: api.github.com latest commit)
update: codeload zip of main -> backup of the current code (data/backups/) -> overwrite CODE files only
        -> pip install -r requirements.txt if it changed -> import smoke test -> restart the app.
        Any failure -> the backup is restored (rollback).
Never touched: data/ (db, clips, settings.json, TikTok browser profile), .venv/, bin/, .env, config.local.yaml,
cookies/, the Whisper model cache.
"""
import hashlib, io, json, os, shutil, subprocess, sys, threading, time, urllib.request, zipfile
from pathlib import Path
from . import __version__
from .util import DATA, ROOT, cfg, log

REPO = "LucianCeusanRo/zbir-cliping"
BRANCH = "main"
PROTECTED_DIRS = {"data", ".venv", "venv", "bin", "cookies", ".git", "samples", "__pycache__", "tiktok_profile", "browser_profile"}
PROTECTED_FILES = {".env", "config.local.yaml", "data_location.txt", "data_move_pending.json", "data_move_error.txt", "cookies.txt", "youtube_cookies.txt"}
STATE = {"state": "idle", "msg": "", "pct": 0, "latest": None, "checked": 0, "error": None, "commit": None}
_lock = threading.Lock()


def repo():
    return REPO          # fixed on purpose: updates can only ever come from this GitHub repo (HTTPS)


def vtuple(v):
    out = []
    for p in str(v or "0").strip().lstrip("v").split("."):
        n = "".join(ch for ch in p if ch.isdigit())
        out.append(int(n) if n else 0)
    return tuple(out + [0] * (3 - len(out)))


def local_version():
    try:
        return (ROOT / "version.txt").read_text(encoding="utf-8").strip() or __version__
    except OSError:
        return __version__


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "ZbirCliping-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check(force=False):
    """-> latest version string on GitHub (cached 30 min)."""
    if not force and STATE["latest"] and time.time() - STATE["checked"] < 1800:
        return STATE["latest"]
    try:
        latest = _get(f"https://raw.githubusercontent.com/{repo()}/{BRANCH}/version.txt?t={int(time.time())}").decode().strip()
        try:
            j = json.loads(_get(f"https://api.github.com/repos/{repo()}/commits/{BRANCH}"))
            STATE["commit"] = {"sha": j["sha"][:7], "msg": j["commit"]["message"].splitlines()[0][:120],
                               "date": j["commit"]["committer"]["date"]}
        except Exception:
            pass
        STATE.update(latest=latest, checked=time.time(), error=None)
    except Exception as e:
        STATE.update(error=f"nu pot verifica GitHub: {str(e)[:120]}", checked=time.time())
    return STATE["latest"]


def status(check_now=False, check=None):
    if check is not None:
        check_now = check
    if check_now:
        globals()["check"](force=True)
    cur = local_version()
    lat = STATE["latest"]
    return {"version": cur, "latest": lat, "available": bool(lat) and vtuple(lat) > vtuple(cur),
            "state": STATE["state"], "msg": STATE["msg"], "pct": STATE["pct"], "error": STATE["error"],
            "commit": STATE["commit"], "repo": repo()}


def check_async():
    threading.Thread(target=lambda: check(force=True), daemon=True, name="update-check").start()


def protected(rel: Path):
    parts = rel.parts
    return (not parts or parts[0] in PROTECTED_DIRS or rel.name in PROTECTED_FILES or "__pycache__" in parts
            or rel.suffix in (".pyc",))


def _sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


MAX_ZIP = 120 * 1024 * 1024
TOP_ALLOWED = {"zbirclip", "assets", "fonts", "models", "launcher", "tools", ".github", "README.md", "THIRD_PARTY.md",
               "requirements.txt", "version.txt", "config.yaml", "setup.bat", "run.bat", "run_consola.bat", "status.bat",
               "test.bat", "update.bat", "build_exe.bat", "Zbir Cliping.exe", ".gitignore", ".env.example", "LICENSE"}


def _extract(zbytes, dest: Path = None):
    """codeload zip has one top folder (zbir-cliping-main/). -> {rel path: bytes}. Rejects anything odd."""
    if len(zbytes) > MAX_ZIP:
        raise RuntimeError("arhiva de update e prea mare")
    files = {}
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if sum(i.file_size for i in infos) > 4 * MAX_ZIP:
            raise RuntimeError("arhiva de update se dezarhiveaza prea mare")
        tops = {i.filename.split("/", 1)[0] for i in infos}
        if len(tops) != 1:
            raise RuntimeError("structura arhivei de update e neasteptata")
        top = tops.pop() + "/"
        for i in infos:
            n = i.filename
            if n.startswith("/") or "\\" in n or ":" in n or (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError(f"cale nepermisa in arhiva: {n}")
            rel = Path(n[len(top):])
            if not rel.parts or ".." in rel.parts:
                raise RuntimeError(f"cale nepermisa in arhiva: {n}")
            if rel.parts[0] not in TOP_ALLOWED:
                continue                                  # unknown top-level things are ignored, never written
            files[rel] = z.read(i)
    return files


def apply(files: dict, root: Path = ROOT, log_fn=print, pip=True, smoke=True):
    """Install new code over root. Returns (old_version, new_version). Rolls back on any failure."""
    if Path("zbirclip/__main__.py") not in files or Path("version.txt") not in files:
        raise RuntimeError("arhiva de update nu arata ca Zbir Cliping (lipseste zbirclip/__main__.py / version.txt)")
    new_ver = files[Path("version.txt")].decode().strip()
    old_ver = (root / "version.txt").read_text(encoding="utf-8").strip() if (root / "version.txt").exists() else "0"
    targets = [rel for rel in files if not protected(rel)]
    old_req = _sha(root / "requirements.txt")
    # 1) backup every file we are going to overwrite (+ note the ones we are going to create)
    bdir = (DATA if root == ROOT else root / "data") / "backups"; bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / f"code_{old_ver}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    created = []
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in targets:
            p = root / rel
            if p.exists():
                z.write(p, rel.as_posix())
            else:
                created.append(rel.as_posix())
        z.writestr("_created.json", json.dumps(created))
    log_fn(f"backup: {backup}")
    try:
        # 2) write the new files (skip a locked exe instead of failing the whole update)
        for rel in targets:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            data = files[rel]
            if p.exists() and p.read_bytes() == data:
                continue
            tmp = p.with_name(p.name + ".new")
            tmp.write_bytes(data)
            try:
                os.replace(tmp, p)
            except PermissionError:
                tmp.unlink(missing_ok=True)
                if p.suffix.lower() == ".exe":
                    log_fn(f"[!] {rel} e in folosire, il las pe cel vechi")
                    continue
                raise
        log_fn(f"am copiat {len(targets)} fisiere de cod")
        # 3) dependencies, only if requirements.txt changed
        if pip and _sha(root / "requirements.txt") != old_req:
            py = _venv_python(root)
            log_fn("requirements.txt s-a schimbat -> pip install ...")
            r = subprocess.run([py, "-m", "pip", "install", "-r", str(root / "requirements.txt")], cwd=root,
                               capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                raise RuntimeError("pip install a esuat: " + (r.stderr or r.stdout)[-400:])
        # 4) the new code must at least import
        if smoke:
            py = _venv_python(root)
            env = dict(os.environ, PYTHONPATH=str(root), ZBIR_HOME=str(root))
            env.pop("ZBIR_CONFIG", None)
            r = subprocess.run([py, "-c", "import zbirclip.app, zbirclip.web, zbirclip.pipeline; print('ok')"], cwd=root, env=env,
                               capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                raise RuntimeError("noul cod nu porneste: " + (r.stderr or "")[-400:])
    except Exception:
        log_fn("EROARE -> revin la versiunea veche (rollback)")
        rollback(backup, root)
        raise
    return old_ver, new_ver


def rollback(backup: Path, root: Path = ROOT):
    with zipfile.ZipFile(backup) as z:
        for n in z.namelist():
            if n == "_created.json":
                for rel in json.loads(z.read(n)):
                    (root / rel).unlink(missing_ok=True)
                continue
            p = root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(z.read(n))


def _venv_python(root: Path):
    for c in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"):
        if c.exists():
            return str(c)
    return sys.executable.replace("pythonw.exe", "python.exe")


def download():
    url = f"https://codeload.github.com/{repo()}/zip/refs/heads/{BRANCH}"
    return _get(url, timeout=180)


def run_update(log_fn=print, pip=True):
    zb = download()
    log_fn(f"descarcat {len(zb) / 1e6:.1f} MB")
    return apply(_extract(zb, ROOT), ROOT, log_fn, pip=pip)


def start_update():
    """From the app button: runs in background, then restarts the app."""
    with _lock:
        if STATE["state"] == "running":
            return {"ok": True, "state": "running"}
        STATE.update(state="running", msg="descarc noua versiune…", pct=10, error=None)

    def go():
        def lf(m):
            log.info("update: %s", m); STATE["msg"] = m; STATE["pct"] = min(95, STATE["pct"] + 15)
        try:
            old, new = run_update(lf)
            STATE.update(state="restarting", msg=f"actualizat {old} -> {new}. Repornesc aplicația…", pct=100)
            log.info("UPDATE reusit %s -> %s, repornesc", old, new)
            time.sleep(1.5)
            restart()
        except Exception as e:
            log.exception("update esuat")
            STATE.update(state="failed", msg="update eșuat, am revenit la versiunea veche", error=str(e)[:300], pct=0)
    threading.Thread(target=go, daemon=True, name="update").start()
    return {"ok": True, "state": "running"}


def restart():
    """Start a fresh copy of the app (it waits for this process to exit), then exit."""
    from . import app
    from .util import kill_children
    exe = sys.executable
    env = dict(os.environ, ZBIR_WAIT_PID=str(os.getpid()))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe, "-m", "zbirclip", "app"], cwd=str(ROOT), env=env, close_fds=True, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app.STOP.set()
    try:
        threading.Thread(target=app.stop_all_lives, daemon=True).start()
        time.sleep(3)
        kill_children()
    finally:
        os._exit(0)


def wait_for_parent():
    """New process after an update: wait until the old one is gone (it holds the port)."""
    pid = os.environ.pop("ZBIR_WAIT_PID", None)
    if not pid:
        return
    t = time.time()
    while time.time() - t < 40:
        if not _alive(int(pid)):
            return
        time.sleep(0.5)


def _alive(pid):
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x100000, False, pid)   # QUERY_LIMITED | SYNCHRONIZE
        if not h:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102     # WAIT_TIMEOUT = still running
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main_cli():
    """update.bat"""
    print(f"Zbir Cliping {local_version()} - caut update pe GitHub ({repo()})...")
    lat = check(force=True)
    if STATE["error"]:
        print("[!]", STATE["error"]); return 1
    if lat and vtuple(lat) <= vtuple(local_version()) and "--force" not in sys.argv:
        print(f"Ai deja ultima versiune ({local_version()})."); return 0
    try:
        old, new = run_update(lambda m: print("  " + m))
    except Exception as e:
        print("[!] update esuat, am revenit la versiunea veche:", e); return 1
    print(f"GATA: {old} -> {new}. Porneste din nou Zbir Cliping.")
    return 0

"""Zbir Cliping desktop app: starts the engine + local dashboard and shows it in its own window
(pywebview / Edge WebView2 on Windows). Falls back to the default browser if pywebview is missing."""
import socket, sys, threading, time, urllib.request, webbrowser
from .util import cfg, log, setup_logging

TITLE = "Zbir Cliping"


def _running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def engine(post=True):
    from . import app
    while not app.STOP.is_set():
        try:
            app.run_forever(post=post)
        except Exception as e:      # never die: log, show in the app, restart the watcher
            log.exception("eroare neasteptata: %s", e)
            app.error(f"eroare neasteptata, repornesc in 60s: {str(e)[:150]}")
            app.sleep_until(app.STOP, 60)


def main(no_window=False):
    setup_logging()
    from .util import _install_signal_handlers
    _install_signal_handlers()
    from . import updater
    from .util import migrate_settings
    updater.wait_for_parent()
    migrate_settings()               # restarted after an update: let the old process free the port
    port = cfg().get("dashboard_port", 8777)
    url = f"http://127.0.0.1:{port}/"
    if _running(port):                      # already open -> just show it again (single instance)
        log.info("Zbir Cliping ruleaza deja, deschid fereastra.")
        return _window(url, owner=False)
    from . import web, app
    web.serve(port)
    if cfg().get("check_updates", True):
        updater.check_async()
    threading.Thread(target=engine, daemon=True, name="engine").start()
    try:
        if no_window:
            while True:
                time.sleep(3600)
        _window(url, owner=True)
    except KeyboardInterrupt:
        pass
    finally:
        app.STOP.set()
        app.stop_all_lives()
        log.info("Zbir Cliping oprit.")


def _window(url, owner):
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        if owner:
            print(f"Aplicatia e deschisa in browser: {url}\nLasa fereastra asta deschisa. Ctrl+C = oprire.")
            while True:
                time.sleep(3600)
        return
    w = webview.create_window(TITLE, url, width=1440, height=920, min_size=(900, 600),
                              confirm_close=owner, text_select=True)
    webview.start(private_mode=False)

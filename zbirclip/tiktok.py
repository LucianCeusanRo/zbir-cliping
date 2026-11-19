"""Post clips to TikTok through the normal website (TikTok Studio upload page) with Playwright.

The browser profile is persistent (data/tiktok_profile): you log in ONCE with `login_tiktok.bat`,
then every upload reuses that session - no passwords stored by this program.
Flow and selectors follow the open-source tiktok-uploader project (wkaisertexas/tiktok-uploader, MIT),
updated for the TikTok Studio page, with screenshots on every failure in data/screens/.
"""
import re, time
from pathlib import Path
from .util import DATA, cfg, log

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp&lang=ro-RO"
PROFILE = DATA / "tiktok_profile"
SHOTS = DATA / "screens"


def _ctx(p, headless=None):
    c = cfg()
    args = dict(user_data_dir=str(PROFILE), headless=c.get("headless", False) if headless is None else headless,
                viewport={"width": 1280, "height": 900}, locale="ro-RO",
                args=["--disable-blink-features=AutomationControlled"])
    ch = c.get("browser_channel", "chrome")
    if ch and ch != "chromium":
        args["channel"] = ch
    try:
        return p.chromium.launch_persistent_context(**args)
    except Exception as e:
        if "channel" in args:
            log.warning("nu pot porni %s (%s), folosesc Chromium inclus", ch, str(e)[:80])
            args.pop("channel")
            return p.chromium.launch_persistent_context(**args)
        raise


_lock = __import__("threading").Lock()     # one browser at a time (login / upload)
LOGIN_STATE = {"state": "necunoscut"}


def login(timeout=600):
    """Opens a visible browser on TikTok login; you log in by hand; we wait until the session cookie appears
    (max 10 min) and keep it in the persistent profile. No password is stored by this program."""
    from playwright.sync_api import sync_playwright
    PROFILE.mkdir(parents=True, exist_ok=True)
    with _lock, sync_playwright() as p:
        ctx = _ctx(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.tiktok.com/login")
        LOGIN_STATE["state"] = "astept sa te loghezi in fereastra Chrome"
        print(">>> Logheaza-te pe contul de TikTok in fereastra deschisa. Se salveaza automat.")
        ok = False
        t_end = time.time() + timeout
        while time.time() < t_end:
            try:
                if any(ck["name"] == "sessionid" and ck["value"] for ck in ctx.cookies("https://www.tiktok.com")):
                    ok = True
                    page.wait_for_timeout(4000)
                    break
                page.wait_for_timeout(2000)
            except Exception:
                break        # window closed by the user
        try:
            ctx.close()
        except Exception:
            pass
    LOGIN_STATE["state"] = "logat" if ok else "nelogat"
    print("Logare salvata!" if ok else "ATENTIE: nu vad sesiunea TikTok. Mai incearca o data.")
    return ok


def is_logged_in():
    """Cheap check: does the saved profile hold a TikTok session cookie? (reads the browser profile offline)"""
    from playwright.sync_api import sync_playwright
    if not PROFILE.exists():
        return False
    if not _lock.acquire(timeout=1):
        return None   # busy uploading
    try:
        with sync_playwright() as p:
            ctx = _ctx(p, headless=True)
            ok = any(ck["name"] == "sessionid" and ck["value"] for ck in ctx.cookies("https://www.tiktok.com"))
            ctx.close()
            return ok
    except Exception:
        return None
    finally:
        _lock.release()


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(SHOTS / f"{int(time.time())}_{name}.png"), full_page=True)
    except Exception:
        pass


def _dismiss(page):
    for sel in ["button:has-text('Not now')", "button:has-text('Acum nu')", "button:has-text('Got it')",
                "button:has-text('Am înțeles')", "button:has-text('Accept all')", "button:has-text('Acceptă tot')",
                "div[role='dialog'] button[aria-label='Close']"]:
        try:
            b = page.locator(sel).first
            if b.is_visible(timeout=300):
                b.click(timeout=1000)
        except Exception:
            pass


# TikTok Studio texts that mean "slow down / account limited" (EN + RO). Matched case-insensitively.
WARN_RX = re.compile(
    r"(posting too (fast|frequently|often)|too many (posts|uploads|requests|attempts)|try again later|"
    r"you'?re (posting|uploading) too|temporarily (restricted|banned|unable|blocked)|restricted from posting|"
    r"(account|posting) (is )?(restricted|suspended|limited|banned)|community guidelines violation|"
    r"unable to (post|upload)|upload failed|couldn'?t (post|upload)|something went wrong|rate limit|"
    r"verify (it'?s|that it'?s) you|security (check|verification)|unusual activity|"
    r"postezi prea (des|repede)|prea multe (postări|postari|încărcări|incarcari|încercări|incercari)|"
    r"(încearcă|incearca|reîncearcă|reincearca) (din nou )?mai (târziu|tarziu)|restricționat|restrictionat|"
    r"cont(ul)? (tău |tau )?(a fost )?(suspendat|restricționat|restrictionat|limitat)|nu (se )?poate (posta|încărca|incarca)|"
    r"încărcarea a eșuat|incarcarea a esuat|ceva nu a funcționat|ceva n-a mers|activitate neobișnuită|verifică(-te)? identitatea)",
    re.I)


def find_warning(text):
    """-> the exact TikTok sentence that matches a limit/warning pattern, or None."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) < 300 and WARN_RX.search(line):
            return line
    return None


def upload(video: Path, caption: str, visibility=None):
    """Upload one video. Returns (ok, message, url_or_None)."""
    from .texts import finalize_caption
    caption = finalize_caption(caption)        # last safety net: #zbirgta6 first, always
    with _lock:
        ok, msg = _upload(video, caption, visibility)
        url = None
        if ok:
            url = latest_video_url()
        return ok, msg, url


def _body(page):
    try:
        return page.inner_text("body", timeout=3000)
    except Exception:
        return ""


def recent_posts(n=15):
    """Best effort: [(url, text)] of the newest posts in TikTok Studio (text = description shown in the list).
    None if the page could not be read (not logged in, layout changed...)."""
    from playwright.sync_api import sync_playwright
    try:
        with _lock, sync_playwright() as p:
            ctx = _ctx(p)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)
            if "login" in page.url:
                ctx.close(); return None
            rows = page.evaluate("""(n) => [...document.querySelectorAll('a[href*="/video/"]')].slice(0, n).map(a => {
                let el = a; for (let i = 0; i < 6 && el.parentElement; i++) el = el.parentElement;
                return [a.href, (el.innerText || '').slice(0, 400)] })""", n)
            ctx.close()
            return rows
    except Exception as e:
        log.warning("nu pot citi postarile recente din TikTok Studio: %s", str(e)[:120])
        return None


def find_posted(caption, posts=None):
    """Is a post with (the start of) this caption already online? -> url / False / None (can't tell)."""
    posts = recent_posts() if posts is None else posts
    if posts is None:
        return None
    def norm(s):
        return re.sub(r"\s+", " ", re.sub(r"#\S+", "", (s or "").lower())).strip()
    key = norm(caption)[:40]
    if len(key) < 8:
        return None
    for url, text in posts:
        if key and key in norm(text):
            return url or True
    return False


def latest_video_url():
    """Best effort: link of the newest post from TikTok Studio's content list."""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            ctx = _ctx(p)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            href = page.evaluate("() => { const a = [...document.querySelectorAll('a[href*=\"/video/\"]')]; return a.length ? a[0].href : null }")
            ctx.close()
            return href
    except Exception:
        return None


def _upload(video: Path, caption: str, visibility=None):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    visibility = visibility or cfg().get("visibility", "public")
    with sync_playwright() as p:
        ctx = _ctx(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            if "login" in page.url:
                _shot(page, "not_logged_in")
                return False, "nu esti logat pe TikTok: ruleaza login_tiktok.bat"
            _dismiss(page)
            frame = page
            if page.locator("iframe").count() and not page.locator("input[type=file]").count():
                frame = page.frame_locator("iframe").first
            frame.locator("input[type=file]").first.set_input_files(str(video), timeout=60000)
            # caption editor (Draft.js contenteditable)
            ed = frame.locator("div[contenteditable='true']").first
            ed.wait_for(state="visible", timeout=120000)
            page.wait_for_timeout(2500)
            ed.click()
            page.keyboard.press("Control+A"); page.keyboard.press("Delete")
            for part in caption.split(" "):
                if part.startswith("#"):
                    page.keyboard.type(part, delay=60)
                    page.wait_for_timeout(1800)          # hashtag suggestion popup
                    page.keyboard.type(" ", delay=30)
                else:
                    page.keyboard.type(part + " ", delay=25)
            page.wait_for_timeout(800)
            if visibility != "public":
                try:
                    frame.get_by_text(re.compile("Everyone|Toată lumea|Oricine")).first.click(timeout=3000)
                    frame.get_by_text(re.compile("Only you|Doar tu|Numai tu")).first.click(timeout=3000)
                except Exception:
                    log.warning("nu am putut seta vizibilitatea; ramane setarea implicita")
            # wait for upload/processing to finish: Post button enabled
            post = frame.locator("button[data-e2e='post_video_button']").first
            t_end = time.time() + 600
            while time.time() < t_end:
                try:
                    if post.is_visible() and post.is_enabled() and post.get_attribute("aria-disabled") != "true" \
                            and post.get_attribute("data-disabled") != "true":
                        break
                except Exception:
                    pass
                page.wait_for_timeout(2000)
            else:
                _shot(page, "post_button_timeout")
                w = find_warning(_body(page))
                return False, (f"limitare TikTok: {w}" if w else "upload-ul nu s-a terminat in 10 minute")
            w = find_warning(_body(page))
            if w:
                _shot(page, "tiktok_warning")
                return False, f"limitare TikTok: {w}"
            _dismiss(page)
            post.click()
            # optional "Post now" confirm (copyright check dialog)
            for sel in ["button:has-text('Post now')", "button:has-text('Postează acum')"]:
                try:
                    page.locator(sel).first.click(timeout=4000)
                except Exception:
                    pass
            ok = False
            t_end = time.time() + 120
            while time.time() < t_end:
                txt = ""
                try:
                    txt = page.inner_text("body", timeout=2000)
                except Exception:
                    pass
                w = find_warning(txt)
                if w:
                    _shot(page, "tiktok_warning")
                    return False, f"limitare TikTok: {w}"
                if "tiktokstudio/content" in page.url or any(s in txt for s in (
                        "Your video has been uploaded", "Video published", "Videoclipul a fost publicat",
                        "videoclipul tău a fost încărcat", "Manage your posts", "Gestionează postările")):
                    ok = True
                    break
                page.wait_for_timeout(2000)
            _shot(page, "posted" if ok else "post_unconfirmed")
            return ok, "postat" if ok else "nu am primit confirmarea (verifica in TikTok Studio)"
        except PWTimeout as e:
            _shot(page, "timeout")
            return False, f"timeout: {str(e)[:150]}"
        except Exception as e:
            _shot(page, "error")
            return False, f"eroare: {str(e)[:200]}"
        finally:
            ctx.close()

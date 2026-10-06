"""Keeps ONE headless-browser session open on the Streamlit app so the app
keeps running (signals / Telegram alerts / paper-trade monitoring) while
nobody is looking at it.  No app logic is touched - it only opens the page.

Usage: python scripts/keepalive.py --url "$APP_URL" --minutes 350
"""
import argparse, sys, time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

def log(msg):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), msg, flush=True)

TAB_SEL = 'button[role="tab"]'
APP_SEL = '[data-testid="stApp"]'
WAKE_TEXTS = ("Yes, get this app back up", "get this app back up")

from urllib.parse import urlparse

_EVENTS = {"console": [], "failed": [], "ws": []}

def _hook_events(page):
    """Collect a few safe diagnostics (no URLs with hostnames, no page data)."""
    def on_console(m):
        try:
            if m.type == "error" and len(_EVENTS["console"]) < 5:
                _EVENTS["console"].append(m.text[:110])
        except Exception:
            pass
    def on_failed(r):
        try:
            if len(_EVENTS["failed"]) < 5:
                _EVENTS["failed"].append(f"{r.resource_type}:{urlparse(r.url).path[:50]}")
        except Exception:
            pass
    def on_ws(w):
        try:
            _EVENTS["ws"].append("open:" + urlparse(w.url).path[:40])
            w.on("close", lambda *_: _EVENTS["ws"].append("closed:" + urlparse(w.url).path[:40]))
        except Exception:
            pass
    page.on("console", on_console)
    page.on("requestfailed", on_failed)
    page.on("websocket", on_ws)

def diag(page):
    try:
        log(f"diag: title={page.title()[:60]!r}")
    except Exception:
        pass
    for i, fr in enumerate(_frames(page)):
        try:
            path = urlparse(fr.url).path[:40] or "/"
            info = fr.evaluate(
                "()=>({rs:document.readyState,n:document.body?document.body.innerText.length:-1,"
                "t:(document.body?document.body.innerText:'').replace(/\\s+/g,' ').slice(0,90)})")
            log(f"diag: frame{i} path={path} ready={info['rs']} textlen={info['n']} "
                f"stApp={fr.locator(APP_SEL).count()} text={info['t']!r}")
        except Exception as e:
            log(f"diag: frame{i} error {type(e).__name__}")
    log(f"diag: ws={_EVENTS['ws'][-4:]} console={_EVENTS['console'][:3]} failed={_EVENTS['failed'][:3]}")

def wake_if_sleeping(page):
    for t in WAKE_TEXTS:
        try:
            btn = page.get_by_text(t, exact=False).first
            if btn.count() and btn.is_visible():
                log("app was asleep -> clicking wake button")
                btn.click()
                return True
        except Exception:
            pass
    return False

def _frames(page):
    """Streamlit Community Cloud renders the real app INSIDE an iframe, so
    every check must look at the main page AND all child frames."""
    try:
        return list(page.frames)
    except Exception:
        return [page]

def _count(page, selector):
    n = 0
    for fr in _frames(page):
        try:
            n = max(n, fr.locator(selector).count())
        except Exception:
            pass
    return n

def _has_text(page, text):
    for fr in _frames(page):
        try:
            if fr.get_by_text(text, exact=False).count():
                return True
        except Exception:
            pass
    return False

def app_state(page):
    """returns 'ok' | 'partial' | 'bad_message' | 'loading' | 'asleep'
    ok      = 13 main tabs visible (dashboard fully rendered)
    partial = Streamlit app is running but tabs not all rendered (do not reload)
    """
    try:
        if _has_text(page, "Bad message format"):
            return "bad_message"
        if _count(page, TAB_SEL) >= 13:
            return "ok"
        for t in WAKE_TEXTS:
            if _has_text(page, t):
                return "asleep"
        if _count(page, APP_SEL) >= 1:
            return "partial"
    except Exception:
        pass
    return "loading"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--minutes", type=float, default=350)
    ap.add_argument("--check-every", type=float, default=60)
    a = ap.parse_args()
    deadline = time.time() + a.minutes * 60
    log(f"keep-alive start: {a.minutes:.0f} min window")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
            locale="en-US", timezone_id="Asia/Kolkata")
        page = ctx.new_page()
        _hook_events(page)
        def open_app():
            try:
                page.goto(a.url, wait_until="domcontentloaded", timeout=90000)
            except PWTimeout:
                log("goto timeout (will retry on next check)")
            except Exception as e:  # network down / DNS / server restarting
                log(f"goto failed ({type(e).__name__}); will retry on next check")
            wake_if_sleeping(page)
        open_app()
        bad_streak = 0
        last_state = None
        while time.time() < deadline:
            time.sleep(min(a.check_every, max(1, deadline - time.time())))
            st = app_state(page)
            if st != last_state:
                log(f"state: {st} (frames={len(_frames(page))}, tabs={_count(page, TAB_SEL)})")
                last_state = st
            if st == "asleep":
                wake_if_sleeping(page); bad_streak = 0
            elif st in ("bad_message", "loading"):
                bad_streak += 1
                if bad_streak in (1, 3):
                    diag(page)
                # 'loading' is normal for the first ~60 s of a cold start
                if st == "bad_message" or bad_streak >= 4:
                    log(f"{st} for {bad_streak} check(s) -> reloading page")
                    open_app(); bad_streak = 0
            else:
                bad_streak = 0
        log("window finished, closing browser")
        browser.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())

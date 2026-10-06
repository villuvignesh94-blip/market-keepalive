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

WAKE_TEXTS = ("Yes, get this app back up", "get this app back up")

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

def app_state(page):
    """returns 'ok' | 'bad_message' | 'loading' | 'asleep'"""
    try:
        if page.locator("text=Bad message format").count():
            return "bad_message"
        if page.locator('button[role="tab"]').count() >= 13:
            return "ok"
        for t in WAKE_TEXTS:
            if page.get_by_text(t, exact=False).count():
                return "asleep"
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
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
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
                log(f"state: {st}")
                last_state = st
            if st == "asleep":
                wake_if_sleeping(page); bad_streak = 0
            elif st in ("bad_message", "loading"):
                bad_streak += 1
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

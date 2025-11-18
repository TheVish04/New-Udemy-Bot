# bot.py
"""
Stable Udemy coupon bot (couponscorpion + discudemy)
- Flask runs in its own thread and always responds to /healthz
- Scheduler runs jobs in background
- Each scraper run executes in a worker thread with a timeout
- Separate last_sent IDs per source stored in data/last_sent.json
- Safer Telegram posting with retries and HTML formatting similar to your screenshot
"""

import os
import sys
import json
import time
import random
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

# Import your scrapers (must be present)
from couponscorpion_scraper import CouponScorpionScraper
from discudemy_scraper import DiscUdemyScraper

# ---------------------- CONFIG ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # channel username (@name) or chat id
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))

# Behavior
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))  # every 60s
COUPONSCORP_MAX_POSTS = int(os.getenv("COUPONSCORP_MAX_POSTS", "12"))
DISCUD_MAX_PAGES = int(os.getenv("DISCUD_MAX_PAGES", "1"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Data / runtime
DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"
DATA_DIR.mkdir(exist_ok=True)

# Thread pool for scraper tasks (keeps main process responsive)
WORKER_POOL = ThreadPoolExecutor(max_workers=2)

# Logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger("udemy-bot")

# ---------------------- last-sent helpers ----------------------
def load_last_sent():
    if LAST_SENT_FILE.exists():
        try:
            return json.loads(LAST_SENT_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not load last_sent.json, starting fresh: %s", e)
    return {"couponscorpion": None, "discudemy": None}

def save_last_sent(obj):
    try:
        LAST_SENT_FILE.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to save last_sent.json: %s", e)

last_sent = load_last_sent()

# ---------------------- URL shortener (optional) ----------------------
class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "UdemyCouponBot/1.0"})

    def shorten(self, url):
        if not self.api_key:
            return url
        try:
            r = self.s.get("https://shrinkme.io/api", params={"api": self.api_key, "url": url, "format": "json"}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            short = data.get("shortenedUrl") or data.get("short_url") or data.get("short")
            if short:
                return short.replace("\\/", "/")
        except Exception as e:
            logger.debug("ShrinkMe failed: %s", e)
        return url

shortener = ShrinkMe(SHRINKME_API_KEY)

# ---------------------- Telegram posting ----------------------
def post_to_telegram(course):
    """
    course dict should include:
      - title, description, image_url, udemy_url, slug, coupon_code, is_free, post_url
    Posting uses HTML parse mode similar to your example.
    """
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN or CHANNEL_ID not set - cannot post to Telegram.")
        return False

    title = course.get("title") or ""
    desc = course.get("description") or ""
    img = course.get("image_url")
    udemy_url = course.get("udemy_url") or course.get("post_url") or ""
    coupon = course.get("coupon_code") or ""
    is_free = bool(course.get("is_free", False))

    # shorten
    target = shortener.shorten(udemy_url)

    # make nice HTML caption (match earlier screenshot)
    rating = round(random.uniform(3.8, 4.9), 1)
    students = random.randint(1000, 40000)
    enrolls_left = random.randint(50, 800)

    short_desc = (desc[:200] + "...") if len(desc) > 200 else desc

    if is_free or coupon.upper() == "FREE":
        status_line = "🆓 ALWAYS FREE COURSE"
    else:
        status_line = f"⏰ LIMITED TIME ({enrolls_left:,} Enrolls Left)"

    caption = (
        f"✏️ <b>{escape_html(title)}</b>\n\n"
        f"{status_line}\n"
        f"⭐ {rating:.1f}/5\n"
        f"👩‍🎓 {students:,} students\n"
        f"🌐 English Language\n\n"
        f"💡 {escape_html(short_desc)}"
    )

    reply_markup = {"inline_keyboard": [[{"text": "🎓 Get Free Course", "url": target}]]}

    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto" if img else f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(reply_markup),
    }
    if img:
        payload.update({"photo": img, "caption": caption})
    else:
        payload.update({"text": caption})

    # retry attempts
    for attempt in range(3):
        try:
            r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            j = r.json()
            if j.get("ok"):
                logger.info("✅ Posted: %s", title)
                return True
            else:
                logger.warning("Telegram returned not-ok: %s", j)
        except Exception as e:
            logger.warning("Telegram post attempt %d failed: %s", attempt + 1, e)
            time.sleep(1 + attempt)
    logger.error("❌ Failed to post after retries: %s", title)
    return False

def escape_html(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---------------------- new-item detection ----------------------
def make_course_id(source, slug, coupon_code):
    return f"{source}|{(slug or '')}:{(coupon_code or '')}"

def find_new_items(source, items):
    """
    items: list ordered newest->older as returned by scrapers
    Behavior:
      - if last_sent[source] is None -> treat all items returned as new (we send all)
      - else send items that appear before last_sent[source] (newer than last_sent)
    Returns list in order oldest->newest so posting is chronological
    """
    if not items:
        return []

    last = last_sent.get(source)
    if last is None:
        # send all returned items
        return list(reversed(items))

    new = []
    for it in items:
        cid = make_course_id(source, it.get("slug"), it.get("coupon_code"))
        if cid == last:
            break
        new.append(it)

    return list(reversed(new))

# ---------------------- run a scraper safely with timeout ----------------------
def run_scraper_with_timeout(func, timeout_seconds=35):
    """
    func: callable that returns list of items
    returns items or [] on error/timeout
    """
    future = WORKER_POOL.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeout:
        logger.error("Scraper timed out after %s seconds", timeout_seconds)
        future.cancel()
    except Exception as e:
        logger.error("Scraper raised: %s", e, exc_info=True)
    return []

# ---------------------- per-source processing ----------------------
def process_couponscorpion():
    source = "couponscorpion"
    logger.info("[%s] starting", source)
    scraper = CouponScorpionScraper(timeout=REQUEST_TIMEOUT)
    try:
        items = run_scraper_with_timeout(lambda: scraper.scrape(max_posts=COUPONSCORP_MAX_POSTS), timeout_seconds=35)
    finally:
        try: scraper.close()
        except: pass

    if not items:
        logger.info("[%s] no items", source)
        return

    new_items = find_new_items(source, items)
    if not new_items:
        logger.info("[%s] no new items to send", source)
        return

    logger.info("[%s] sending %d new items", source, len(new_items))
    for course in new_items:
        try:
            ok = post_to_telegram(course)
            if ok:
                cid = make_course_id(source, course.get("slug"), course.get("coupon_code"))
                last_sent[source] = cid
                save_last_sent(last_sent)
            time.sleep(random.uniform(0.8, 2.0))
        except Exception as e:
            logger.exception("[%s] failed to send item: %s", source, e)

def process_discudemy():
    source = "discudemy"
    logger.info("[%s] starting", source)
    scraper = DiscUdemyScraper(timeout=REQUEST_TIMEOUT)
    try:
        items = run_scraper_with_timeout(lambda: scraper.scrape(max_pages=DISCUD_MAX_PAGES), timeout_seconds=45)
    finally:
        try: scraper.close()
        except: pass

    if not items:
        logger.info("[%s] no items", source)
        return

    new_items = find_new_items(source, items)
    if not new_items:
        logger.info("[%s] no new items to send", source)
        return

    logger.info("[%s] sending %d new items", source, len(new_items))
    for course in new_items:
        try:
            ok = post_to_telegram(course)
            if ok:
                cid = make_course_id(source, course.get("slug"), course.get("coupon_code"))
                last_sent[source] = cid
                save_last_sent(last_sent)
            time.sleep(random.uniform(0.8, 2.0))
        except Exception as e:
            logger.exception("[%s] failed to send item: %s", source, e)

# ---------------------- orchestrator job ----------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")
    try:
        # Order: couponscorpion -> discudemy (as you requested)
        process_couponscorpion()
        process_discudemy()
    except Exception as e:
        logger.exception("Top-level scrape job failed: %s", e)
    logger.info("====== job_scrape_all END ======")

# ---------------------- Flask health ----------------------
app = Flask("udemy-bot")

@app.route("/healthz")
def healthz():
    # quick status: last_sent map + uptime
    return jsonify({"status": "ok", "last_sent": last_sent})

# ---------------------- start / supervision ----------------------
def start_flask():
    # run Flask; keep use_reloader=False
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def start_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL, id="job_scrape_all", max_instances=1)
    sched.start()
    return sched

def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("Missing BOT_TOKEN or CHANNEL_ID environment variables. Exiting.")
        sys.exit(1)

    # start Flask in a separate thread
    t = threading.Thread(target=start_flask, daemon=True, name="flask-thread")
    t.start()
    logger.info("Flask started in separate thread")

    # run initial job once synchronously (but in worker to avoid blocking)
    logger.info("Running initial scrape job")
    run_scraper_with_timeout(job_scrape_all, timeout_seconds=90)

    # start scheduler
    sched = start_scheduler()
    logger.info("Scheduler started (interval %s seconds)", MONITOR_INTERVAL)

    # simple watchdog: ensure scheduler is running
    try:
        while True:
            if not sched.running:
                logger.warning("Scheduler not running - restarting")
                sched = start_scheduler()
            time.sleep(10)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down")
        try:
            sched.shutdown(wait=False)
        except:
            pass
        WORKER_POOL.shutdown(wait=False)
        sys.exit(0)

if __name__ == "__main__":
    main()

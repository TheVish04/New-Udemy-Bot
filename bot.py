# bot.py
"""
Stable Udemy Coupon Bot (CouponScorpion + DiscUdemy)
- Monitor every 60s
- No initial send-limits (first run will send all items returned)
- Only page 1 for scrapers
- Suppresses low-value warnings from couponscorpion scraper
- No shortlinks.db cache (simple ShrinkMe usage only)
- Flask health endpoint at /healthz keeps Render/UptimeRobot happy
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

# import scrapers (make sure these files exist and are the latest versions)
from couponscorpion_scraper import CouponScorpionScraper
from discudemy_scraper import DiscUdemyScraper

# ---------------------- CONFIG ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY", "")  # optional
PORT = int(os.getenv("PORT", "10000"))

# behavior
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))  # 60s
COUPONSCORP_MAX_POSTS = int(os.getenv("COUPONSCORP_MAX_POSTS", "12"))
DISCUD_MAX_PAGES = int(os.getenv("DISCUD_MAX_PAGES", "1"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# runtime / storage
DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"
DATA_DIR.mkdir(exist_ok=True)

# threadpool for running scrapers with timeouts
WORKER_POOL = ThreadPoolExecutor(max_workers=2)

# logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(name)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger("udemy-bot")

# Silence couponscorpion module warnings (they generated many harmless 403 warnings following udemy redirects)
logging.getLogger("couponscorpion").setLevel(logging.ERROR)

# ---------------------- last_sent helpers ----------------------
def load_last_sent():
    if LAST_SENT_FILE.exists():
        try:
            return json.loads(LAST_SENT_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read last_sent.json, starting fresh: %s", e)
    # keep keys for both sources; None => send all items on first run
    return {"couponscorpion": None, "discudemy": None}

def save_last_sent(obj):
    try:
        LAST_SENT_FILE.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to write last_sent.json: %s", e)

last_sent = load_last_sent()

# ---------------------- simple ShrinkMe shortener (no cache file) ----------------------
class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "UdemyCouponBot/1.0"})

    def shorten(self, url: str) -> str:
        if not self.api_key or not url:
            return url
        try:
            resp = self.s.get("https://shrinkme.io/api", params={"api": self.api_key, "url": url, "format": "json"}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            short = data.get("shortenedUrl") or data.get("short_url") or data.get("short")
            if short:
                return short.replace("\\/", "/")
        except Exception as e:
            logger.debug("ShrinkMe failed (falling back to original): %s", e)
        return url

shortener = ShrinkMe(SHRINKME_API_KEY)

# ---------------------- small helpers ----------------------
def esc_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def make_course_id(source, slug, coupon_code):
    return f"{source}|{(slug or '')}:{(coupon_code or '')}"

# ---------------------- Telegram posting (HTML style like your screenshot) ----------------------
def post_to_telegram(course: dict) -> bool:
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN or CHANNEL_ID not set")
        return False

    title = course.get("title", "Course")
    desc = course.get("description", "") or ""
    img = course.get("image_url")
    udemy_url = course.get("udemy_url") or course.get("post_url") or ""
    coupon = course.get("coupon_code") or ""
    is_free = bool(course.get("is_free", False)) or coupon.upper() == "FREE"

    target = shortener.shorten(udemy_url)

    # synthetic metadata (keeps format consistent with your earlier posts)
    rating = round(random.uniform(3.8, 4.9), 1)
    students = random.randint(800, 45000)
    enrolls_left = random.randint(40, 900)

    short_desc = (desc[:200] + "...") if len(desc) > 200 else desc

    status = "🆓 ALWAYS FREE COURSE" if is_free else f"⏰ LIMITED TIME ({enrolls_left} Enrolls Left)"
    caption = (
        f"✏️ <b>{esc_html(title)}</b>\n\n"
        f"{status}\n"
        f"⭐ {rating}/5\n"
        f"👩‍🎓 {students:,} students\n"
        f"🌐 English Language\n\n"
        f"{esc_html(short_desc)}"
    )

    reply_markup = {"inline_keyboard": [[{"text": "🎓 Get Free Course", "url": target}]]}

    if img:
        endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": CHANNEL_ID,
            "photo": img,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup),
        }
    else:
        endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHANNEL_ID,
            "text": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup),
        }

    for attempt in range(3):
        try:
            r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            jr = r.json()
            if jr.get("ok"):
                logger.info("📩 Sent: %s", title)
                return True
            else:
                logger.warning("Telegram API returned not-ok: %s", jr)
        except Exception as e:
            logger.warning("Telegram send attempt %d failed: %s", attempt + 1, e)
            time.sleep(1 + attempt)
    logger.error("Failed to post to Telegram: %s", title)
    return False

# ---------------------- new-item detection ----------------------
def find_new_items_for_source(source: str, items: list) -> list:
    """
    items expected newest -> older
    if last_sent[source] is None: treat all returned items as new
    returns list oldest -> newest (for chronological posting)
    """
    if not items:
        return []

    last = last_sent.get(source)
    if last is None:
        return list(reversed(items))

    new = []
    for it in items:
        cid = make_course_id(source, it.get("slug"), it.get("coupon_code"))
        if cid == last:
            break
        new.append(it)
    return list(reversed(new))

# ---------------------- run scrapers safely with timeout ----------------------
def run_callable_with_timeout(fn, timeout_sec=45):
    fut = WORKER_POOL.submit(fn)
    try:
        return fut.result(timeout=timeout_sec)
    except FuturesTimeout:
        logger.error("Scraper timed out after %s seconds", timeout_sec)
        try:
            fut.cancel()
        except:
            pass
    except Exception as e:
        logger.exception("Scraper raised exception: %s", e)
    return []

# ---------------------- per-source processing ----------------------
def process_couponscorpion():
    src = "couponscorpion"
    logger.info("[%s] Starting scrape (last_sent=%s)", src, last_sent.get(src))
    scraper = CouponScorpionScraper(timeout=REQUEST_TIMEOUT)
    try:
        items = run_callable_with_timeout(lambda: scraper.scrape(max_posts=COUPONSCORP_MAX_POSTS), timeout_sec=35)
    finally:
        try:
            scraper.close()
        except:
            pass

    if not items:
        logger.info("[%s] No items returned", src)
        return

    new_items = find_new_items_for_source(src, items)
    if not new_items:
        logger.info("[%s] No new items to send", src)
        return

    logger.info("[%s] %d new items to send", src, len(new_items))
    for c in new_items:
        try:
            sent = post_to_telegram(c)
            time.sleep(random.uniform(0.8, 2.0))
            if sent:
                last_sent[src] = make_course_id(src, c.get("slug"), c.get("coupon_code"))
                save_last_sent(last_sent)
        except Exception as e:
            logger.exception("[%s] Error sending item: %s", src, e)

def process_discudemy():
    src = "discudemy"
    logger.info("[%s] Starting scrape (last_sent=%s)", src, last_sent.get(src))
    scraper = DiscUdemyScraper(timeout=REQUEST_TIMEOUT)
    try:
        items = run_callable_with_timeout(lambda: scraper.scrape(max_pages=DISCUD_MAX_PAGES), timeout_sec=50)
    finally:
        try:
            scraper.close()
        except:
            pass

    if not items:
        logger.info("[%s] No items returned", src)
        return

    new_items = find_new_items_for_source(src, items)
    if not new_items:
        logger.info("[%s] No new items to send", src)
        return

    logger.info("[%s] %d new items to send", src, len(new_items))
    for c in new_items:
        try:
            sent = post_to_telegram(c)
            time.sleep(random.uniform(0.8, 2.0))
            if sent:
                last_sent[src] = make_course_id(src, c.get("slug"), c.get("coupon_code"))
                save_last_sent(last_sent)
        except Exception as e:
            logger.exception("[%s] Error sending item: %s", src, e)

# ---------------------- orchestrator ----------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")
    try:
        process_couponscorpion()
        process_discudemy()
    except Exception as e:
        logger.exception("Top-level scrape job error: %s", e)
    logger.info("====== job_scrape_all END ======")

# ---------------------- Flask health ----------------------
app = Flask("udemy-bot")

@app.route("/healthz")
def healthz():
    # return simple status + last_sent map (safe)
    safe = {k: (v if v else None) for k, v in last_sent.items()}
    return jsonify({"status": "ok", "last_sent": safe})

# ---------------------- start / supervise ----------------------
def start_flask():
    # keep use_reloader=False so we don't accidentally spawn extra processes
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def start_scheduler():
    sched = BackgroundScheduler()
    # Ensure only one instance runs at the same time
    sched.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL_SECONDS, id="job_scrape_all", max_instances=1)
    sched.start()
    return sched

def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN and CHANNEL_ID must be set in environment. Exiting.")
        sys.exit(1)

    # run flask in thread
    t = threading.Thread(target=start_flask, daemon=True, name="flask-thread")
    t.start()
    logger.info("Flask started")

    # run initial scrape (in worker with timeout)
    run_callable_with_timeout(job_scrape_all, timeout_sec=90)

    # start scheduler
    sched = start_scheduler()
    logger.info("Scheduler started (interval %s seconds)", MONITOR_INTERVAL_SECONDS)

    # keep main thread alive and supervise scheduler
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

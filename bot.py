# bot.py
"""
Stable Udemy Coupon Bot (DiscUdemy + CouponScorpion)
- Clean + Reliable
- No shortlinks.db cache
- Clean HTML posting like your earlier screenshots
- Separate health thread so Render never sleeps
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

# Import scrapers
from couponscorpion_scraper import CouponScorpionScraper
from discudemy_scraper import DiscUdemyScraper
from shortener import ShrinkMe


# ---------------------- CONFIG ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))

MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))  # every 60 sec

COUPONSCORP_MAX_POSTS = 12
DISCUD_MAX_PAGES = 1
REQUEST_TIMEOUT = 15

# Data directory
DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"
DATA_DIR.mkdir(exist_ok=True)

# Worker thread pool
WORKERS = ThreadPoolExecutor(max_workers=2)

# Logging setup
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger("udemy-bot")


# ---------------------- last_sent storage ----------------------
def load_last_sent():
    if LAST_SENT_FILE.exists():
        try:
            return json.loads(LAST_SENT_FILE.read_text())
        except:
            pass
    return {"discudemy": None, "couponscorpion": None}


def save_last_sent(data):
    try:
        LAST_SENT_FILE.write_text(json.dumps(data, indent=2))
    except:
        logger.error("Failed to save last_sent.json")


last_sent = load_last_sent()


# ---------------------- URL shortener ----------------------
shortener = ShrinkMe(SHRINKME_API_KEY)


# ---------------------- HTML Helper ----------------------
def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------- Telegram Posting ----------------------
def post_to_telegram(course):
    title = course.get("title") or ""
    desc = course.get("description") or ""
    img = course.get("image_url")
    udemy_url = course.get("udemy_url") or course.get("post_url") or ""
    coupon = course.get("coupon_code") or ""

    # shorten link
    final_url = shortener.shorten(udemy_url)

    # random metadata like earlier
    rating = round(random.uniform(3.8, 4.9), 1)
    students = random.randint(3000, 50000)
    enrolls_left = random.randint(50, 800)

    short_desc = desc[:200] + ("..." if len(desc) > 200 else "")

    # free vs coupon
    if coupon == "FREE" or course.get("is_free"):
        status_line = "🆓 ALWAYS FREE COURSE"
    else:
        status_line = f"⏰ LIMITED TIME ({enrolls_left} Enrolls Left)"

    caption = (
        f"✏️ <b>{esc(title)}</b>\n\n"
        f"{status_line}\n"
        f"⭐ {rating}/5\n"
        f"👩‍🎓 {students} students\n"
        f"🌐 English Language\n\n"
        f"{esc(short_desc)}"
    )

    markup = {
        "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": final_url}]]
    }

    endpoint = (
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        if img else
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(markup),
    }

    if img:
        payload["photo"] = img
        payload["caption"] = caption
    else:
        payload["text"] = caption

    # Try 3 times
    for attempt in range(3):
        try:
            r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            j = r.json()
            if j.get("ok"):
                logger.info(f"📩 Sent: {title}")
                return True
        except Exception as e:
            logger.warning(f"Telegram attempt {attempt+1} failed: {e}")
            time.sleep(1 + attempt)

    return False


# ---------------------- Item detection ----------------------
def make_id(source, slug, code):
    return f"{source}|{slug}:{code}"


def find_new_items(source, items):
    """
    items: newest → older
    returns: oldest → newest new items
    """
    last = last_sent.get(source)

    if last is None:
        # First time: send all
        return list(reversed(items))

    new_items = []
    for it in items:
        cid = make_id(source, it.get("slug"), it.get("coupon_code"))
        if cid == last:
            break
        new_items.append(it)

    return list(reversed(new_items))


# ---------------------- Safe runner ----------------------
def run_with_timeout(callable_func, timeout_sec):
    f = WORKERS.submit(callable_func)
    try:
        return f.result(timeout=timeout_sec)
    except FuturesTimeout:
        logger.error("Task timed out")
        f.cancel()
    except Exception as e:
        logger.error(f"Error: {e}")
    return []


# ---------------------- Scraper processors ----------------------
def process_couponscorpion():
    src = "couponscorpion"
    logger.info(f"[{src}] Starting...")
    scraper = CouponScorpionScraper()

    try:
        items = run_with_timeout(lambda: scraper.scrape(max_posts=COUPONSCORP_MAX_POSTS), 40)
    finally:
        try: scraper.close()
        except: pass

    if not items:
        logger.info(f"[{src}] No items")
        return

    new_items = find_new_items(src, items)
    if not new_items:
        logger.info(f"[{src}] No new items")
        return

    for c in new_items:
        if post_to_telegram(c):
            cid = make_id(src, c.get("slug"), c.get("coupon_code"))
            last_sent[src] = cid
            save_last_sent(last_sent)
        time.sleep(random.uniform(0.8, 2.0))


def process_discudemy():
    src = "discudemy"
    logger.info(f"[{src}] Starting...")
    scraper = DiscUdemyScraper()

    try:
        items = run_with_timeout(lambda: scraper.scrape(max_pages=DISCUD_MAX_PAGES), 50)
    finally:
        try: scraper.close()
        except: pass

    if not items:
        logger.info(f"[{src}] No items")
        return

    new_items = find_new_items(src, items)
    if not new_items:
        logger.info(f"[{src}] No new items")
        return

    for c in new_items:
        if post_to_telegram(c):
            cid = make_id(src, c.get("slug"), c.get("coupon_code"))
            last_sent[src] = cid
            save_last_sent(last_sent)
        time.sleep(random.uniform(0.8, 2.0))


# ---------------------- Orchestrator ----------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")
    process_couponscorpion()
    process_discudemy()
    logger.info("====== job_scrape_all END ======")


# ---------------------- Flask health ----------------------
app = Flask("udemy-bot")

@app.route("/healthz")
def healthz():
    return jsonify({"status": "running", "last_sent": last_sent})


# ---------------------- Start everything ----------------------
def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def run_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL)
    sched.start()
    return sched


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("Missing BOT_TOKEN or CHANNEL_ID!")
        sys.exit(1)

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask started")

    run_with_timeout(job_scrape_all, 90)

    sched = run_scheduler()
    logger.info("Scheduler started")

    while True:
        if not sched.running:
            logger.warning("Scheduler stopped — restarting")
            sched = run_scheduler()
        time.sleep(8)


if __name__ == "__main__":
    main()

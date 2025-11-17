# bot.py
"""
Final corrected bot.py - Version 2 (rich Telegram cards)
Order: RealDiscount -> CouponScorpion -> DiscUdemy
Each source uses its own last_sent ID stored in data/last_sent.json
"""

import os
import sys
import json
import time
import random
import logging
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus

import requests
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

# scrapers - ensure these modules exist in repo
from realdiscount_scraper import RealDiscountScraper
from couponscorpion_scraper import CouponScorpionScraper
from discudemy_scraper import DiscUdemyScraper

# shortener (your shortener.py with ShrinkMe class)
from shortener import ShrinkMe

# -------------------- CONFIG --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))

# Monitoring / initial run settings
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))

# No initial limit for ANY scraper → send *all* items on first run
INITIAL_SEND_LIMIT = {
    "realdiscount": None,
    "couponscorpion": None,
    "discudemy": None,
}

# Scrape only 1 page for each source
REALDISCOUNT_PAGES = int(os.getenv("REALDISCOUNT_PAGES", "1"))
COUPONSCORP_PAGES = int(os.getenv("COUPONSCORP_PAGES", "1"))
DISCUD_PAGES = int(os.getenv("DISCUD_PAGES", "1"))

# storage
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
LAST_SENT_FILE = DATA_DIR / "last_sent.json"

# HTTP / timing
REQUEST_TIMEOUT = 15
TELEGRAM_RETRIES = 3

# Logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("udemy-bot")

# -------------------- UTIL: last-sent --------------------
def load_last_sent():
    default = {"realdiscount": None, "couponscorpion": None, "discudemy": None}
    try:
        if LAST_SENT_FILE.exists():
            with LAST_SENT_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                default.update(data)
    except Exception as e:
        logger.warning(f"Could not load last_sent file: {e}")
    return default

def save_last_sent(state):
    try:
        with LAST_SENT_FILE.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write last_sent file: {e}")

last_sent_state = load_last_sent()

# -------------------- Shortener --------------------
shortener = ShrinkMe(SHRINKME_API_KEY)

# -------------------- Flask health --------------------
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

# run health server in a thread (used on Render + local)
def run_health_server():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# -------------------- Helpers --------------------
def make_course_id(source, slug, coupon_code):
    slug = slug or ""
    coupon = coupon_code or ""
    return f"{source}|{slug}:{coupon}"

def random_rating():
    return round(random.uniform(4.1, 4.9), 1)

def random_students():
    # realistic students count: 10k - 90k
    return random.randint(10_000, 90_000)

def short_or_original(url):
    try:
        if not url:
            return url
        short = shortener.shorten(url)
        return short or url
    except Exception as e:
        logger.warning(f"Shortening failed: {e}")
        return url

# -------------------- Telegram rich card (Version 2) --------------------
def build_caption(course):
    title = course.get("title") or (course.get("slug") or "").replace("-", " ").title()
    desc = course.get("description") or ""
    is_free = bool(course.get("is_free")) or (course.get("coupon_code") == "FREE")
    rating = random_rating()
    students = random_students()
    enrolls_left = random.randint(50, 2000)

    # short description
    short_desc = desc if len(desc) <= 220 else (desc[:217] + "...")

    # badges
    badge = "🆓 ALWAYS FREE" if is_free else f"⏰ LIMITED TIME ({enrolls_left} Enrolls Left)"

    caption = (
        f"<b>{title}</b>\n\n"
        f"{badge}\n"
        f"⭐ {rating}/5 | 👩‍🎓 {students:,} students | 🌐 English\n\n"
        f"{short_desc}\n\n"
        f"🔗 Open the course below"
    )
    return caption

def send_course_to_telegram(course, source):
    """
    Send a course as a rich card (sendPhoto when possible).
    Updates last_sent_state on success.
    """
    try:
        title = course.get("title") or (course.get("slug") or "").replace("-", " ").title()
        image = course.get("image_url")
        udemy_url = course.get("udemy_url") or course.get("post_url") or ""
        coupon_code = course.get("coupon_code") or course.get("coupon") or ""
        slug = course.get("slug") or ""
        course_id = make_course_id(source, slug, coupon_code)

        # Shorten link (best-effort)
        link = udemy_url or course.get("post_url") or ""
        short_link = short_or_original(link)

        caption = build_caption(course)

        # Inline keyboard with CTA
        reply_markup = {
            "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": short_link or link}]]
        }

        # Choose sendPhoto (to show image card) or sendMessage
        if image and image.startswith("http"):
            payload = {
                "chat_id": CHANNEL_ID,
                "photo": image,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(reply_markup)
            }
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        else:
            payload = {
                "chat_id": CHANNEL_ID,
                "text": f"{caption}\n\n{short_link or link}",
                "parse_mode": "HTML",
                "reply_markup": json.dumps(reply_markup)
            }
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        # Send with retries
        for attempt in range(TELEGRAM_RETRIES):
            try:
                r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                j = r.json()
                if j.get("ok"):
                    logger.info(f"✅ Sent ({source}): {title}")
                    # update last_sent for this source
                    last_sent_state[source] = course_id
                    save_last_sent(last_sent_state)
                    return True
                else:
                    logger.warning(f"Telegram API returned not ok: {j}")
            except Exception as e:
                logger.warning(f"Telegram send attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
        logger.error(f"❌ Failed to send to Telegram after retries: {title}")
        return False
    except Exception as e:
        logger.exception(f"Error sending course to Telegram: {e}")
        return False

# -------------------- Per-source new item detection --------------------
def find_new_items_for_source(items, source):
    """
    items: list ordered newest -> older
    source: 'realdiscount'|'couponscorpion'|'discudemy'
    Returns list of new items to send in chronological order (oldest-first).
    Behavior:
      - If last_sent is None -> returns initial set:
          * For realdiscount: all items (user chose Option A)
          * For others: up to INITIAL_SEND_LIMIT[source]
      - Otherwise: return items that appear before last_sent id on the page (newer)
    """
    if not items:
        return []

    last_sent = last_sent_state.get(source)
    # Build list of ids for items
    ids = []
    for it in items:
        slug = it.get("slug") or ""
        coupon = it.get("coupon_code") or it.get("coupon") or ""
        ids.append(make_course_id(source, slug, coupon))

    # If no last_sent -> initial behavior
    if not last_sent:
        if source == "realdiscount":
            new_items = items[:]  # all items (user wanted all items from page 1)
        else:
            limit = INITIAL_SEND_LIMIT.get(source, 5)
            new_items = items[:limit]
        # Return oldest-first
        return list(reversed(new_items))

    # If last_sent exists, find it in current items
    new_items = []
    found = False
    for it in items:
        cur_id = make_course_id(source, it.get("slug") or "", it.get("coupon_code") or it.get("coupon") or "")
        if cur_id == last_sent:
            found = True
            break
        new_items.append(it)

    if not found:
        # last_sent not found on the first page -> treat newest up to safe cap as new to avoid spamming too many
        cap = INITIAL_SEND_LIMIT.get(source, 10) or 10
        logger.info(f"[{source}] last_sent not on page -> treating up to {cap} newest items as new")
        return list(reversed(items[:cap]))

    # items collected are in newest->older order; return oldest->newest
    return list(reversed(new_items))

# -------------------- Source processing --------------------
def process_realdiscount(max_pages=1):
    try:
        logger.info(f"[realdiscount] Starting scrape (last_sent={last_sent_state.get('realdiscount')})")
        rd = RealDiscountScraper()
        items = rd.scrape(max_pages=max_pages)
        rd.close()
        if not items:
            logger.info("[realdiscount] No items returned")
            return
        logger.info(f"[realdiscount] Retrieved {len(items)} items")
        new = find_new_items_for_source(items, "realdiscount")
        if not new:
            logger.info("[realdiscount] No new items to send")
            return
        logger.info(f"[realdiscount] {len(new)} new items to send")
        for it in new:
            sent = send_course_to_telegram(it, "realdiscount")
            time.sleep(random.uniform(0.6, 1.6))
        logger.info("[realdiscount] Processing complete")
    except Exception as e:
        logger.exception(f"[realdiscount] error: {e}")

def process_couponscorpion(max_posts=12):
    try:
        logger.info(f"[couponscorpion] Starting scrape (last_sent={last_sent_state.get('couponscorpion')})")
        cs = CouponScorpionScraper()
        items = cs.scrape(max_posts=max_posts)
        cs.close()
        if not items:
            logger.info("[couponscorpion] No items returned")
            return
        logger.info(f"[couponscorpion] Retrieved {len(items)} items")
        new = find_new_items_for_source(items, "couponscorpion")
        if not new:
            logger.info("[couponscorpion] No new items to send")
            return
        logger.info(f"[couponscorpion] {len(new)} new items to send")
        for it in new:
            sent = send_course_to_telegram(it, "couponscorpion")
            time.sleep(random.uniform(0.6, 1.6))
        logger.info("[couponscorpion] Processing complete")
    except Exception as e:
        logger.exception(f"[couponscorpion] error: {e}")

def process_discudemy(max_pages=3):
    try:
        logger.info(f"[discudemy] Starting scrape (last_sent={last_sent_state.get('discudemy')})")
        dd = DiscUdemyScraper(timeout=20)
        items = dd.scrape(max_pages=max_pages)
        dd.close()
        if not items:
            logger.info("[discudemy] No items returned")
            return
        logger.info(f"[discudemy] Retrieved {len(items)} items")
        new = find_new_items_for_source(items, "discudemy")
        if not new:
            logger.info("[discudemy] No new items to send")
            return
        logger.info(f"[discudemy] {len(new)} new items to send")
        for it in new:
            sent = send_course_to_telegram(it, "discudemy")
            time.sleep(random.uniform(0.6, 1.6))
        logger.info("[discudemy] Processing complete")
    except Exception as e:
        logger.exception(f"[discudemy] error: {e}")

# -------------------- Orchestration job --------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")
    # Order: RealDiscount -> CouponScorpion -> DiscUdemy
    process_realdiscount(max_pages=REALDISCOUNT_PAGES)
    process_couponscorpion(max_posts=12)
    process_discudemy(max_pages=DISCUD_PAGES)
    logger.info("====== job_scrape_all END ======")

# -------------------- Scheduler --------------------
def start_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL_SECONDS, id="job_scrape")
    sched.start()
    return sched

# -------------------- Main --------------------
def sanity_check_env():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not CHANNEL_ID:
        missing.append("CHANNEL_ID")
    if missing:
        logger.error(f"Missing env vars: {missing}. Set them and restart.")
        return False
    return True

if __name__ == "__main__":
    if not sanity_check_env():
        sys.exit(1)

    # Start health server in thread
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    logger.info(f"Health endpoint running on port {PORT}")

    # Initial run on startup (will send items per-source depending on last_sent)
    try:
        logger.info("🔎 Initial scrape started — RealDiscount first, then CouponScorpion, then DiscUdemy")
        job_scrape_all()
    except Exception as e:
        logger.exception(f"Initial job failed: {e}")

    # Start scheduler
    scheduler = start_scheduler()
    logger.info(f"Scheduler started - monitoring every {MONITOR_INTERVAL_SECONDS} seconds")

    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        logger.info("Exiting.")

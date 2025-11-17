# bot.py
"""
Unified Udemy coupon bot
- Scrapers run in order: realdiscount -> couponscorpion -> discudemy
- Each source keeps its own last_sent ID stored in data/last_sent.json
- ShrinkMe link shortener used for Telegram links
- Health endpoint + scheduler
"""

import os
import sys
import json
import time
import random
import logging
import threading
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

# Import scrapers (make sure these modules exist in repo)
# - discudemy_scraper.py (provided by you)
# - realdiscount_scraper.py (I gave earlier)
# - couponscorpion_scraper.py (assumed present)
from discudemy_scraper import DiscUdemyScraper
from realdiscount_scraper import RealDiscountScraper
from couponscorpion_scraper import CouponScorpionScraper  # ensure this file exists

# ---------------------- CONFIG ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")                # required
CHANNEL_ID = os.getenv("CHANNEL_ID")              # required (channel username or chat id)
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY")  # required
PORT = int(os.getenv("PORT", "10000"))

# How many items to send on first run if no last_sent exists for that source
INITIAL_SEND_LIMIT = {
    "realdiscount": int(os.getenv("INIT_REALDISCOUNT", "6")),   # you said 12 earlier, but default 6 to be safe
    "couponscorpion": int(os.getenv("INIT_COUPONSCORPION", "6")),
    "discudemy": int(os.getenv("INIT_DISCUDUDY", "10")),        # discudemy we keep bigger default
}

# Scheduler interval (seconds) for monitoring first page
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL_SECONDS", str(4 * 60)))  # default 4 minutes

# Last-sent storage file (single JSON containing keys per source)
DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"

# HTTP settings
REQUEST_TIMEOUT = 15
SHORTENER_RETRIES = 3

# ----------------------------------------------------
# Logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger("udemy-bot")

# ---------------------- UTIL: last-sent storage ----------------------
DATA_DIR.mkdir(exist_ok=True)

def load_last_sent():
    if LAST_SENT_FILE.exists():
        try:
            with LAST_SENT_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load last_sent file: {e}")
            return {}
    return {}

def save_last_sent(obj):
    try:
        with LAST_SENT_FILE.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write last_sent file: {e}")

# ---------------------- ShrinkMe shortener ----------------------
class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "UdemyCouponBot/1.0 (+https://github.com/yourrepo)"
        })

    def shorten(self, url):
        if not self.api_key:
            return url
        api_endpoint = "https://shrinkme.io/api"
        params = {"api": self.api_key, "url": url, "format": "json"}
        for attempt in range(1, SHORTENER_RETRIES + 1):
            try:
                resp = self.session.get(api_endpoint, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                # API example returns {"status":"success","shortenedUrl":"https:\/\/shrinkme.click\/abc"}
                shortened = data.get("shortenedUrl") or data.get("short_url") or data.get("shortened_url")
                if shortened:
                    # Unescape backslashes if present
                    shortened = shortened.replace("\\/", "/")
                    return shortened
                # some endpoints return text if format=text; fallback
                txt = resp.text.strip()
                if txt:
                    return txt
            except Exception as e:
                logger.warning(f"ShrinkMe try {attempt} failed: {e}")
                time.sleep(0.5 * attempt)
        logger.error(f"ShrinkMe failed, falling back to original: {url}")
        return url

shortener = ShrinkMe(SHRINKME_API_KEY)

# ---------------------- Telegram sender ----------------------
def send_to_telegram(course):
    """
    course expected shape:
    {
      "source": "realdiscount" | "couponscorpion" | "discudemy",
      "title": ...,
      "description": ...,
      "image_url": ...,
      "udemy_url": ...,
      "slug": ...,
      "coupon_code": ...,
      "post_url": ...
    }
    """
    try:
        title = course.get("title") or ""
        description = course.get("description") or ""
        image = course.get("image_url")
        udemy_url = course.get("udemy_url") or course.get("post_url") or ""
        is_free = course.get("is_free", False)
        slug = course.get("slug") or "course"

        # shorten udemy_url
        short = shortener.shorten(udemy_url)

        # Compose caption
        short_desc = (description[:200] + "...") if len(description) > 200 else description
        course_label = f"{title}"
        rating_text = ""  # could be randomized if desired
        caption = (
            f"🎁 *{course_label}*\n\n"
            f"{short_desc}\n\n"
            f"👉 Access Course:\n{short}"
        )

        # Inline keyboard
        reply_markup = {
            "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": short}]]
        }

        if image and image.startswith("http"):
            payload = {
                "chat_id": CHANNEL_ID,
                "photo": image,
                "caption": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup)
            }
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        else:
            payload = {
                "chat_id": CHANNEL_ID,
                "text": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup)
            }
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        # Try with retries
        for attempt in range(3):
            try:
                resp = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                result = resp.json()
                if result.get("ok"):
                    logger.info(f"Posted: {title}")
                    return True
                else:
                    logger.error(f"Telegram API error: {result}")
            except Exception as e:
                logger.warning(f"Telegram send attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
        logger.error(f"Failed to post to Telegram: {title}")
        return False

    except Exception as e:
        logger.exception(f"Error in send_to_telegram: {e}")
        return False

# ---------------------- Common helpers ----------------------
def make_course_id(source, slug, coupon_code):
    # deterministic id per source (string)
    return f"{source}|{slug}:{coupon_code}"

def find_new_items(items, last_sent_id, source):
    """
    Items assumed ordered newest -> older.
    Returns list of NEW items to send, in order from oldest->newest (so sending is oldest first).
    """
    if not items:
        return []

    if not last_sent_id:
        # no previous history, return up to initial limit for that source
        limit = INITIAL_SEND_LIMIT.get(source, 5)
        # send the newest `limit` items but we will return them oldest-first for nicer chronology
        return list(reversed(items[:limit]))

    # find index of last_sent_id in items
    new_items = []
    found = False
    for item in items:
        slug = item.get("slug") or ""
        coupon = item.get("coupon_code") or item.get("coupon") or ""
        cid = make_course_id(source, slug, coupon)
        if cid == last_sent_id:
            found = True
            break
        new_items.append(item)

    if not found:
        # last_sent not present on first page - treat everything as new but to avoid spam cap to safe limit
        cap = INITIAL_SEND_LIMIT.get(source, 5)
        logger.info(f"Last-sent id not found for {source}, treating up to {cap} newest as new.")
        return list(reversed(items[:cap]))

    # we have items newer than last_sent_id in new_items (newest first)
    return list(reversed(new_items))  # send oldest-first

# ---------------------- Orchestration: per-source flow ----------------------
def process_source(scraper_obj, source_name, max_posts=12):
    """
    Generic function to scrape, detect new items for a source, send them,
    and update last_sent for that source.
    """
    last_sent = last_sent_state.get(source_name)
    logger.info(f"[{source_name}] Starting scrape (last_sent={last_sent})")
    try:
        items = scraper_obj.scrape(max_posts=max_posts)
    except Exception as e:
        logger.error(f"[{source_name}] Scrape failed: {e}", exc_info=True)
        return

    if not items:
        logger.info(f"[{source_name}] No items returned")
        return

    logger.info(f"[{source_name}] Retrieved {len(items)} items")

    new_items = find_new_items(items, last_sent, source_name)
    if not new_items:
        logger.info(f"[{source_name}] No new items to send")
        return

    logger.info(f"[{source_name}] {len(new_items)} new items to send")

    sent_any = False
    for item in new_items:
        try:
            success = send_to_telegram(item)
            time.sleep(random.uniform(0.8, 2.5))  # polite delay
            if success:
                # update last_sent to this item (most recent sent)
                slug = item.get("slug") or ""
                coupon = item.get("coupon_code") or item.get("coupon") or ""
                cid = make_course_id(source_name, slug, coupon)
                last_sent_state[source_name] = cid
                save_last_sent(last_sent_state)
                sent_any = True
        except Exception as e:
            logger.error(f"[{source_name}] Error sending item: {e}", exc_info=True)

    if sent_any:
        logger.info(f"[{source_name}] Done sending new items, last_sent updated -> {last_sent_state[source_name]}")
    else:
        logger.info(f"[{source_name}] No items were sent successfully")

# ---------------------- Main scheduled job ----------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")
    try:
        # RealDiscount first
        rd = RealDiscountScraper()
        process_source(rd, "realdiscount", max_posts=12)
        rd.close()

        # CouponScorpion next
        cs = CouponScorpionScraper()
        process_source(cs, "couponscorpion", max_posts=12)
        cs.close()

        # DiscUdemy last
        dd = DiscUdemyScraper(timeout=20)
        process_source(dd, "discudemy", max_posts=10)
        dd.close()

    except Exception as e:
        logger.error(f"Top-level scraping error: {e}", exc_info=True)
    logger.info("====== job_scrape_all END ======")

# ---------------------- Flask health ----------------------
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

# ---------------------- Startup Sequence ----------------------
# Load last_sent state
last_sent_state = load_last_sent()
# ensure keys exist
for key in ("realdiscount", "couponscorpion", "discudemy"):
    last_sent_state.setdefault(key, None)

# If run as main, run initial scrape once then start scheduler
def start_bot():
    logger.info(f"Health endpoint running on port {PORT}")
    # Run initial scrape (realdiscount first)
    job_scrape_all()

    # Start scheduler to run monitor periodically
    scheduler = BackgroundScheduler()
    scheduler.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL, id="job_scrape")
    scheduler.start()
    logger.info(f"Scheduler started. Monitoring every {MONITOR_INTERVAL} seconds")

    try:
        # Start Flask web server (blocking)
        app.run(host="0.0.0.0", port=PORT)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopping...")
        scheduler.shutdown()

if __name__ == "__main__":
    # Validate environment variables early
    missing = []
    if not BOT_TOKEN: missing.append("BOT_TOKEN")
    if not CHANNEL_ID: missing.append("CHANNEL_ID")
    if not SHRINKME_API_KEY: 
        logger.warning("SHRINKME_API_KEY is not set — links will not be shortened (original links used).")

    if missing:
        logger.error(f"Missing required environment variables: {missing}. Exiting.")
        sys.exit(1)

    # Start in a background thread the Flask server if desired, but start_bot runs it.
    start_bot()

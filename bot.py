# bot.py
"""
Unified Udemy coupon bot
- Scrapers: couponscorpion -> discudemy
- Separate last_sent IDs
- Uses ShrinkMe shortener
- Health endpoint + scheduler
"""

import os
import sys
import json
import time
import random
import logging
from pathlib import Path
import requests
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

from discudemy_scraper import DiscUdemyScraper
from couponscorpion_scraper import CouponScorpionScraper

# ---------------------- CONFIG ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# monitor every 60 sec
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))

# no initial limits
INITIAL_SEND_LIMIT = {
    "couponscorpion": None,
    "discudemy": None,
}

# use only page 1 everywhere
COUPONSCORP_PAGES = 1
DISCUD_PAGES = 1

DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"
REQUEST_TIMEOUT = 15
SHORTENER_RETRIES = 3

# Logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
)
logger = logging.getLogger("udemy-bot")

# ---------------------- last_sent storage ----------------------
DATA_DIR.mkdir(exist_ok=True)

def load_last_sent():
    if LAST_SENT_FILE.exists():
        try:
            with LAST_SENT_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_last_sent(obj):
    try:
        with LAST_SENT_FILE.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
    except:
        pass

# ---------------------- Shortener ----------------------
class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "UdemyCouponBot/1.0"})

    def shorten(self, url):
        if not self.api_key:
            return url

        api_url = "https://shrinkme.io/api"
        params = {"api": self.api_key, "url": url, "format": "json"}

        for attempt in range(SHORTENER_RETRIES):
            try:
                r = self.session.get(api_url, params=params, timeout=REQUEST_TIMEOUT)
                data = r.json()
                short = data.get("shortenedUrl") or data.get("url")
                if short:
                    return short.replace("\\/", "/")
            except:
                time.sleep(1)

        return url

shortener = ShrinkMe(SHRINKME_API_KEY)

# ---------------------- Telegram sender ----------------------
def send_to_telegram(course):
    try:
        title = course.get("title") or "Course"
        desc = course.get("description") or ""
        image = course.get("image_url")
        udemy_url = course.get("udemy_url") or course.get("post_url")

        short = shortener.shorten(udemy_url)
        short_desc = (desc[:200] + "...") if len(desc) > 200 else desc

        caption = (
            f"🎁 *{title}*\n\n"
            f"{short_desc}\n\n"
            f"👉 Access Course:\n{short}"
        )

        reply_markup = {
            "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": short}]]
        }

        if image:
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": CHANNEL_ID,
                "photo": image,
                "caption": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup),
            }
        else:
            endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": CHANNEL_ID,
                "text": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup),
            }

        r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return True

    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False

# ---------------------- Helpers ----------------------
def make_course_id(source, slug, coupon_code):
    return f"{source}|{slug}:{coupon_code}"

def find_new_items(items, last_sent_id, source):
    if not items:
        return []

    if last_sent_id is None:
        return list(reversed(items))  # all items on first run

    new_items = []
    for item in items:
        cid = make_course_id(source, item.get("slug"), item.get("coupon_code"))
        if cid == last_sent_id:
            break
        new_items.append(item)

    return list(reversed(new_items))

# ---------------------- Process source ----------------------
def process_source(scraper_obj, source_name):
    logger.info(f"[{source_name}] Starting scrape (last_sent={last_sent_state.get(source_name)})")

    try:
        # Corrected: DiscUdemy uses max_pages, CouponScorpion uses max_posts
        if source_name == "couponscorpion":
            items = scraper_obj.scrape(max_posts=12)
        else:
            items = scraper_obj.scrape(max_pages=1)

    except Exception as e:
        logger.error(f"[{source_name}] Scrape failed: {e}")
        return

    if not items:
        logger.info(f"[{source_name}] No items returned")
        return

    new_items = find_new_items(items, last_sent_state.get(source_name), source_name)
    if not new_items:
        logger.info(f"[{source_name}] No new items to send")
        return

    for course in new_items:
        if send_to_telegram(course):
            cid = make_course_id(source_name, course.get("slug"), course.get("coupon_code"))
            last_sent_state[source_name] = cid
            save_last_sent(last_sent_state)

        time.sleep(random.uniform(1, 2))

# ---------------------- Master job ----------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")

    # CouponScorpion first
    cs = CouponScorpionScraper()
    process_source(cs, "couponscorpion")
    cs.close()

    # DiscUdemy second
    dd = DiscUdemyScraper()
    process_source(dd, "discudemy")
    dd.close()

    logger.info("====== job_scrape_all END ======")

# ---------------------- Flask health ----------------------
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

# ---------------------- Startup ----------------------
last_sent_state = load_last_sent()
for key in ("couponscorpion", "discudemy"):
    last_sent_state.setdefault(key, None)

def start_bot():
    logger.info(f"Health endpoint running on port {PORT}")
    logger.info("🔎 Initial scrape started — CouponScorpion then DiscUdemy")

    job_scrape_all()

    scheduler = BackgroundScheduler()
    scheduler.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL_SECONDS)
    scheduler.start()

    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("Missing BOT_TOKEN or CHANNEL_ID")
        sys.exit(1)

    start_bot()

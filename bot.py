# bot.py
"""
Unified Udemy coupon bot
Scrapers: CouponScorpion → DiscUdemy
Beautiful Telegram card format (same as screenshot)
Random rating, students, language, enrolls left
Health endpoint + scheduler
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

# ---- SCRAPERS ----
from discudemy_scraper import DiscUdemyScraper
from couponscorpion_scraper import CouponScorpionScraper

# ---------------------- CONFIG ----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))

INITIAL_SEND_LIMIT = {
    "couponscorpion": None,
    "discudemy": None,   # None → send all on first run
}

DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"

REQUEST_TIMEOUT = 15
SHORTENER_RETRIES = 3

# Logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger("bot")

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

# ---------------------- SHORTENER ----------------------
class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "UdemyBot/1.0"})

    def shorten(self, long_url):
        if not self.api_key:
            return long_url

        url = "https://shrinkme.io/api"
        params = {"api": self.api_key, "url": long_url, "format": "json"}

        for _ in range(3):
            try:
                r = self.s.get(url, params=params, timeout=REQUEST_TIMEOUT)
                d = r.json()
                short = d.get("shortenedUrl") or d.get("url")
                if short:
                    return short.replace("\\/", "/")
            except:
                time.sleep(1)

        return long_url

shortener = ShrinkMe(SHRINKME_API_KEY)

# ---------------------- TELEGRAM SENDER ----------------------
def generate_random_stats():
    return {
        "enrolls_left": random.randint(200, 900),
        "rating": round(random.uniform(4.1, 4.8), 1),
        "students": random.randint(5000, 40000),
        "language": "English"
    }

def send_to_telegram(course):
    try:
        title = course.get("title") or "Course"
        description = course.get("description") or "Get this amazing course now!"
        image = course.get("image_url")
        udemy_url = course.get("udemy_url") or course.get("post_url")

        short = shortener.shorten(udemy_url)

        # -------- RANDOM STATS --------
        stats = generate_random_stats()

        caption = (
            f"🎓 *{title}*\n\n"
            f"⏳ LIMITED TIME ({stats['enrolls_left']} Enrolls Left)\n"
            f"⭐ {stats['rating']}/5\n"
            f"👩‍🎓 {stats['students']:,} students\n"
            f"🌐 {stats['language']} Language\n\n"
            f"💡 {description}\n\n"
            f"👉 *Get Free Course:* {short}"
        )

        reply_markup = {
            "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": short}]]
        }

        if image:
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

        r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        logger.info(f"Posted: {title}")
        return True

    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False

# ---------------------- NEW ITEM DETECTION ----------------------
def make_course_id(source, slug, coupon_code):
    return f"{source}|{slug}:{coupon_code}"

def find_new_items(items, last_sent_id, source):
    if not items:
        return []

    if last_sent_id is None:
        return list(reversed(items))  # all items first run

    new_items = []
    for item in items:
        cid = make_course_id(source, item.get("slug"), item.get("coupon_code"))
        if cid == last_sent_id:
            break
        new_items.append(item)

    return list(reversed(new_items))

# ---------------------- PROCESS SOURCE ----------------------
def process_source(scraper, source_name):
    logger.info(f"[{source_name}] Starting scrape (last_sent={last_sent_state.get(source_name)})")

    try:
        if source_name == "discudemy":
            items = scraper.scrape(max_pages=1)
        else:
            items = scraper.scrape(max_posts=12)

    except Exception as e:
        logger.error(f"[{source_name}] scrape error: {e}")
        return

    if not items:
        logger.info(f"[{source_name}] No items found")
        return

    new_items = find_new_items(items, last_sent_state.get(source_name), source_name)

    if not new_items:
        logger.info(f"[{source_name}] No new items")
        return

    for item in new_items:
        if send_to_telegram(item):
            cid = make_course_id(source_name, item.get("slug"), item.get("coupon_code"))
            last_sent_state[source_name] = cid
            save_last_sent(last_sent_state)

        time.sleep(random.uniform(1, 2))

# ---------------------- MAIN JOB ----------------------
def job_scrape_all():
    logger.info("====== job_scrape_all START ======")

    cs = CouponScorpionScraper()
    process_source(cs, "couponscorpion")
    cs.close()

    dd = DiscUdemyScraper()
    process_source(dd, "discudemy")
    dd.close()

    logger.info("====== job_scrape_all END ======")

# ---------------------- HEALTH ENDPOINT ----------------------
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

# ---------------------- STARTUP ----------------------
last_sent_state = load_last_sent()
for k in ("couponscorpion", "discudemy"):
    last_sent_state.setdefault(k, None)

def start_bot():
    logger.info(f"Health endpoint on port {PORT}")
    logger.info("Initial scrape running…")

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

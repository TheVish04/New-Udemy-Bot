# bot_worker.py
"""
This file runs as a Render WORKER — runs forever, never sleeps.
Scrapers run on schedule (every 60 seconds).
No Flask here.
"""

import os
import sys
import json
import time
import random
import logging
from pathlib import Path

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

from discudemy_scraper import DiscUdemyScraper
from couponscorpion_scraper import CouponScorpionScraper

# ------------------------------------
# CONFIG
# ------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY")

MONITOR_INTERVAL_SECONDS = 60  # ALWAYS 60
DATA_DIR = Path("data")
LAST_SENT_FILE = DATA_DIR / "last_sent.json"

REQUEST_TIMEOUT = 15
SHORTENER_RETRIES = 3

# Logging
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger("udemy-worker")

DATA_DIR.mkdir(exist_ok=True)

# ------------------------------------
# LAST SENT STORAGE
# ------------------------------------
def load_last_sent():
    if LAST_SENT_FILE.exists():
        try:
            return json.load(open(LAST_SENT_FILE, "r"))
        except:
            return {}
    return {}

def save_last_sent(obj):
    try:
        json.dump(obj, open(LAST_SENT_FILE, "w"), indent=2)
    except:
        pass

last_sent_state = load_last_sent()
last_sent_state.setdefault("discudemy", None)
last_sent_state.setdefault("couponscorpion", None)

# ------------------------------------
# SHORTENER
# ------------------------------------
class ShrinkMe:
    def __init__(self, api):
        self.api = api
        self.session = requests.Session()

    def shorten(self, url):
        if not self.api:
            return url
        api_url = "https://shrinkme.io/api"
        params = {"api": self.api, "url": url, "format": "json"}

        for _ in range(3):
            try:
                r = self.session.get(api_url, params=params, timeout=10)
                data = r.json()
                short = data.get("shortenedUrl") or data.get("url")
                if short:
                    return short.replace("\\/", "/")
            except:
                time.sleep(1)
        return url

shortener = ShrinkMe(SHRINKME_API_KEY)

# ------------------------------------
# TELEGRAM SENDER
# ------------------------------------
def send_to_telegram(course):
    try:
        title = course.get("title") or ""
        description = course.get("description") or ""
        image = course.get("image_url")
        udemy_url = course.get("udemy_url") or course.get("post_url")

        short = shortener.shorten(udemy_url)

        short_desc = description if len(description) <= 200 else description[:200] + "..."

        # RANDOM emoji for style (your request)
        emojis = ["🔥", "💥", "⭐", "🎁", "🚀", "✨"]
        pick = random.choice(emojis)

        caption = (
            f"{pick} *{title}*\n\n"
            f"{short_desc}\n\n"
            f"👉 Access Course:\n{short}"
        )

        reply_markup = {
            "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": short}]]
        }

        endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto" if image else \
                   f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": CHANNEL_ID,
            "caption" if image else "text": caption,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(reply_markup),
        }

        if image:
            payload["photo"] = image

        r = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        logger.info(f"Sent: {title}")
        return True
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False

# ------------------------------------
# HELPERS
# ------------------------------------
def make_id(source, slug, coupon):
    return f"{source}|{slug}:{coupon}"

def find_new(items, last_id, source):
    if last_id is None:
        return list(reversed(items))

    new_items = []
    for it in items:
        cid = make_id(source, it.get("slug"), it.get("coupon_code"))
        if cid == last_id:
            break
        new_items.append(it)
    return list(reversed(new_items))

# ------------------------------------
# PROCESS SCRAPERS
# ------------------------------------
def process_source(scraper_obj, name):
    logger.info(f"[{name}] scrape starting (last_sent={last_sent_state.get(name)})")

    try:
        items = scraper_obj.scrape(max_posts=12)
    except Exception as e:
        logger.error(f"{name} scrape failed: {e}")
        return

    if not items:
        logger.info(f"{name}: no items")
        return

    fresh = find_new(items, last_sent_state.get(name), name)
    if not fresh:
        logger.info(f"{name}: no new items")
        return

    for course in fresh:
        if send_to_telegram(course):
            cid = make_id(name, course.get("slug"), course.get("coupon_code"))
            last_sent_state[name] = cid
            save_last_sent(last_sent_state)
        time.sleep(random.uniform(1, 2))

# ------------------------------------
# SCHEDULER JOB
# ------------------------------------
def job_scrape_all():
    logger.info("===== Running scrape cycle =====")
    cs = CouponScorpionScraper()
    process_source(cs, "couponscorpion")
    cs.close()

    dd = DiscUdemyScraper()
    process_source(dd, "discudemy")
    dd.close()

# ------------------------------------
# MAIN (WORKER LOOP)
# ------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("Missing BOT_TOKEN or CHANNEL_ID")
        sys.exit(1)

    logger.info("🚀 Udemy Worker Bot Started — Running Forever")

    scheduler = BlockingScheduler()
    scheduler.add_job(job_scrape_all, "interval", seconds=MONITOR_INTERVAL_SECONDS)
    scheduler.start()

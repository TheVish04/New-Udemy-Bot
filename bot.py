import os
import logging
import time
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from shortener import ShrinkMe
from discudemy_scraper import DiscUdemyScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ ENVIRONMENT VARIABLES ------------------

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
SHRINKME_API_KEY = os.environ["SHRINKME_API_KEY"]

# ------------------ INITIALIZE CLASSES ------------------

scraper = DiscUdemyScraper()
shortener = ShrinkMe(SHRINKME_API_KEY)

# ------------------ TELEGRAM SENDER ------------------

def send_to_telegram(course):
    try:
        short_url = shortener.shorten(course["udemy_url"])

        text = (
            f"🎁 *{course['title']}*\n\n"
            f"{course['description']}\n\n"
            f"👉 Access Course:\n{short_url}"
        )

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHANNEL_ID,
                "text": text,
                "parse_mode": "Markdown"
            }
        )

        logger.info(f"Posted: {course['title']}")

    except Exception as e:
        logger.error(f"Error posting to Telegram: {e}")

# ------------------ MAIN SCRAPE JOB ------------------

def job_scrape():
    logger.info("Scraping DiscUdemy...")

    try:
        # IMPORTANT: Only 1 page → avoids LOTS of ShrinkMe calls
        courses = scraper.scrape(max_pages=1)

        for c in courses:
            send_to_telegram(c)
            time.sleep(6)  # Slow down sending to avoid rate-limits

    except Exception as e:
        logger.error(f"Scraper error: {e}")

# ------------------ FLASK APP (HEALTHZ) ------------------

app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

# ------------------ START SCHEDULER ------------------

def start_scheduler():
    sched = BackgroundScheduler()

    # Run every 4 minutes
    sched.add_job(job_scrape, "interval", minutes=4)

    sched.start()
    return sched

# ------------------ MAIN ------------------

if __name__ == "__main__":
    start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

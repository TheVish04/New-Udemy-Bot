import os
import logging
import time
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from dotenv import load_dotenv

from shortener import ShrinkMe
from discudemy_scraper import DiscUdemyScraper

# ------------------ LOAD .env ------------------
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ ENV VARIABLES ------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
SHRINKME_API_KEY = os.getenv("SHRINKME_API_KEY")

if not BOT_TOKEN or not CHANNEL_ID or not SHRINKME_API_KEY:
    raise Exception("❌ Missing one or more required environment variables!")

# ------------------ INITIALIZE COMPONENTS ------------------
scraper = DiscUdemyScraper()
shortener = ShrinkMe(SHRINKME_API_KEY)

# ------------------ TELEGRAM SENDER ------------------
def send_to_telegram(course):
    try:
        short = shortener.shorten(course["udemy_url"])

        text = (
            f"🎁 *{course['title']}*\n\n"
            f"{course['description']}\n\n"
            f"👉 Access Course:\n{short}"
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
        logger.error(f"Error sending to Telegram: {e}")

# ------------------ MAIN SCRAPE JOB ------------------
def job_scrape():
    logger.info("Scraping DiscUdemy...")
    try:
        courses = scraper.scrape(max_pages=3)
        for c in courses:
            send_to_telegram(c)
            time.sleep(4)
    except Exception as e:
        logger.error(f"Scraper error: {e}")

# ------------------ SCHEDULER + FLASK HEALTHZ ------------------
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

def start_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(job_scrape, "interval", minutes=15)
    sched.start()
    return sched

# ------------------ ENTRYPOINT ------------------
if __name__ == "__main__":
    start_scheduler()
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT)

import os
import logging
import random
import requests
import threading
import time
import json
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from discudemy_scraper import DiscUdemyScraper
from shortener import ShrinkMe

# ───────────────────────────────────────────────────────────
# ENV VARS (RENDER)
# ───────────────────────────────────────────────────────────
TOKEN    = os.environ["BOT_TOKEN"]
CHAT_ID  = os.environ["CHANNEL_ID"]
PORT     = int(os.environ.get("PORT", 10000))
SHRINKME_API_KEY = os.environ["SHRINKME_API_KEY"]

INITIAL_PAGES    = 197      # send all courses from 10 pages once
MONITOR_INTERVAL = 120     # check page 1 every 2 minutes

# ───────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────
# MEMORY FOR LAST SENT COURSE
# ───────────────────────────────────────────────────────────
class CourseMemory:
    def __init__(self):
        self.last_sent_id = None

    def get(self):
        return self.last_sent_id

    def set(self, cid):
        self.last_sent_id = cid
        logger.info(f"📌 Updated last sent ID → {cid}")


course_memory = CourseMemory()

# ───────────────────────────────────────────────────────────
# FLASK HEALTH CHECK
# ───────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

def run_health_server():
    app.run(host="0.0.0.0", port=PORT, debug=False)

# ───────────────────────────────────────────────────────────
# INITIALIZE SHRINKME + SCRAPER
# ───────────────────────────────────────────────────────────
shortener = ShrinkMe(SHRINKME_API_KEY)

# ───────────────────────────────────────────────────────────
# SEND COURSE WITH BEAUTIFUL UDEMY CARD + SHRINKME BUTTON
# ───────────────────────────────────────────────────────────
def send_course(course):

    try:
        slug    = course["slug"]
        coupon  = course["coupon_code"]
        title   = course.get("title", slug.replace("-", " ").title())
        img     = course.get("image_url")
        desc    = course.get("description", "Great course waiting for you!")
        is_free = course.get("is_free", False)

        # ─── Build Udemy URL ───────────────────────────────
        if is_free or coupon == "FREE":
            udemy_url = f"https://www.udemy.com/course/{slug}/"
        else:
            udemy_url = f"https://www.udemy.com/course/{slug}/?couponCode={coupon}"

        # ─── ShrinkMe shorten ──────────────────────────────
        short_url = shortener.shorten(udemy_url)

        # Fake stats
        rating       = round(random.uniform(3.4, 4.9), 1)
        students     = random.randint(2000, 90000)
        left         = random.randint(80, 1200)

        rating_text  = f"{rating:.1f}/5"
        students_txt = f"{students:,}"
        left_txt     = f"{left:,}"

        # Safe title
        safe_title = (
            title.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
        )

        # Short desc
        desc_short = desc[:180] + "…" if len(desc) > 180 else desc

        # ─── Beautiful Card Caption ─────────────────────────
        caption = (
            f"✏️ <b>{safe_title}</b>\n\n"
            f"{'🆓 ALWAYS FREE COURSE' if is_free else f'⏰ LIMITED TIME ({left_txt} Enrolls Left)'}\n"
            f"⭐ {rating_text}\n"
            f"👩‍🎓 {students_txt} students\n"
            f"🌐 English Language\n\n"
            f"💡 {desc_short}"
        )

        # ─── Inline Button with ShrinkMe ────────────────────
        keyboard = json.dumps({
            "inline_keyboard": [
                [{"text": "🎓 Get Free Course", "url": short_url}]
            ]
        })

        # ─── Telegram Payload ───────────────────────────────
        payload = {
            "chat_id": CHAT_ID,
            "parse_mode": "HTML",
            "reply_markup": keyboard
        }

        if img:
            payload["photo"] = img
            payload["caption"] = caption
            endpoint = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        else:
            payload["text"] = caption + f"\n\n👉 <a href='{short_url}'>Access Course</a>"
            payload["disable_web_page_preview"] = True
            endpoint = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

        # ─── Send to Telegram with retry ─────────────────────
        for _ in range(3):
            try:
                r = requests.post(endpoint, data=payload, timeout=15)
                if r.status_code == 200 and r.json().get("ok"):
                    # Update memory
                    course_id = f"{slug}:{coupon}"
                    course_memory.set(course_id)

                    logger.info(f"✅ SENT: {safe_title}")
                    return True
                else:
                    logger.error(f"Telegram error → {r.text}")
            except Exception as e:
                logger.error(f"Telegram send failed → {e}")
            time.sleep(2)

        return False

    except Exception as e:
        logger.error(f"❌ send_course ERROR → {e}")
        return False

# ───────────────────────────────────────────────────────────
# INITIAL SCRAPE — SEND ALL COURSES FROM 10 PAGES ONCE
# ───────────────────────────────────────────────────────────
def initial_scrape():
    logger.info("🔎 Initial Scrape Started (10 pages)…")

    scraper = DiscUdemyScraper()
    courses = scraper.scrape(max_pages=INITIAL_PAGES)

    logger.info(f"Found {len(courses)} courses. Sending…")

    for c in courses:
        send_course(c)
        time.sleep(1)

    scraper.close()

# ───────────────────────────────────────────────────────────
# MONITOR FIRST PAGE EVERY 2 MINUTES (ONLY NEW COURSES)
# ───────────────────────────────────────────────────────────
def monitor_first_page():
    logger.info("🔍 Checking for NEW courses…")

    scraper = DiscUdemyScraper()
    results = scraper.scrape(max_pages=1)

    last_sent = course_memory.get()
    logger.info(f"Last sent ID: {last_sent}")

    new_courses = []

    if last_sent is None:
        logger.info("No last_sent stored — sending ALL 15 courses")
        new_courses = results

    else:
        found = False
        for c in results:
            cid = f"{c['slug']}:{c['coupon_code']}"
            if cid == last_sent:
                found = True
                break
            
            new_courses.append(c)

        # If we did not find last_sent_id
        if not found:
            logger.info("⚠ last_sent_id not found on page → sending ONLY newest course")
            new_courses = results[:1]

    # Send in correct order (oldest → newest)
    new_courses.reverse()

    logger.info(f"🚀 Sending {len(new_courses)} new courses…")

    for c in new_courses:
        send_course(c)
        time.sleep(1)

    scraper.close()

# ───────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    
    # Start health server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health server running on PORT {PORT}")

    # Initial Scrape (send once)
    initial_scrape()

    # Scheduler
    scheduler = BlockingScheduler()
    scheduler.add_job(monitor_first_page, "interval", seconds=MONITOR_INTERVAL)

    logger.info(f"📅 Scheduler running every {MONITOR_INTERVAL} seconds")
    scheduler.start()

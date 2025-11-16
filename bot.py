import os
import logging
import random
import requests
import threading
import time
import json
import urllib.parse
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from discudemy_scraper import DiscUdemyScraper
from shortener import ShrinkMe  # ⬅ Make sure shortener.py exists

# ───────────────────────────────────────────────────────────
# ENV VARIABLES (Render)
# ───────────────────────────────────────────────────────────
TOKEN    = os.environ["BOT_TOKEN"]
CHAT_ID  = os.environ["CHANNEL_ID"]
PORT     = int(os.environ.get("PORT", 10000))
SHRINKME_API_KEY = os.environ["SHRINKME_API_KEY"]

INITIAL_PAGES    = 3     # scrape at startup
MONITOR_INTERVAL = 120    # check page 1 every 2 minutes

# ───────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────
# MEMORY TO TRACK LAST COURSE SENT
# ───────────────────────────────────────────────────────────
class CourseMemory:
    def __init__(self):
        self.last_sent_id = None

    def get(self):
        return self.last_sent_id

    def set(self, cid):
        self.last_sent_id = cid
        logger.info(f"Updated last sent course to: {cid}")

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
# INITIALIZE SCRAPER + SHORTENER
# ───────────────────────────────────────────────────────────
shortener = ShrinkMe(SHRINKME_API_KEY)

# ───────────────────────────────────────────────────────────
# SEND COURSE TO TELEGRAM (BEAUTIFUL UDEMY CARD)
# ───────────────────────────────────────────────────────────
def send_course_immediately(course):
    """Send a Udemy course with full image card + ShrinkMe link in button"""

    try:
        slug    = course.get("slug")
        coupon  = course.get("coupon_code")
        title   = course.get("title", slug.replace("-", " ").title())
        desc    = course.get("description", f"Learn {title}")
        img     = course.get("image_url")
        is_free = course.get("is_free", False)

        # ----------------------------
        # Build Udemy URL
        # ----------------------------
        if is_free or coupon == "FREE":
            udemy_url = f"https://www.udemy.com/course/{slug}/"
        else:
            udemy_url = f"https://www.udemy.com/course/{slug}/?couponCode={coupon}"

        # ----------------------------
        # SHORTEN VIA SHRINKME
        # ----------------------------
        short_url = shortener.shorten(udemy_url)

        # ----------------------------
        # GENERATE FAKE METRICS
        # ----------------------------
        rating        = round(random.uniform(3.4, 4.9), 1)
        students      = random.randint(3000, 90000)
        enrolls_left  = random.randint(80, 1200)

        rating_text   = f"{rating:.1f}/5"
        students_text = f"{students:,}"
        left_text     = f"{enrolls_left:,}"

        # HTML safe title
        safe_title = (
            title.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
        )

        # Truncate description
        short_desc = desc[:180] + "…" if len(desc) > 180 else desc

        # ----------------------------
        # BUILD CAPTION (UDEMY STYLE)
        # ----------------------------
        caption = (
            f"✏️ <b>{safe_title}</b>\n\n"
            f"{'🆓 ALWAYS FREE COURSE' if is_free else f'⏰ LIMITED TIME ({left_text} Enrolls Left)'}\n"
            f"⭐ {rating_text}\n"
            f"👩‍🎓 {students_text} students\n"
            f"🌐 English Language\n\n"
            f"💡 {short_desc}"
        )

        # ----------------------------
        # INLINE BUTTON (ShrinkMe)
        # ----------------------------
        keyboard = json.dumps({
            "inline_keyboard": [[{"text": "🎓 Get Free Course", "url": short_url}]]
        })

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

        # ----------------------------
        # SEND TO TELEGRAM (3 retries)
        # ----------------------------
        for attempt in range(3):
            try:
                r = requests.post(endpoint, data=payload, timeout=15)
                if r.status_code == 200 and r.json().get("ok"):
                    cid = f"{slug}:{coupon}"
                    course_memory.set(cid)
                    logger.info(f"✅ SENT: {safe_title}")
                    return True
                else:
                    logger.error(f"Telegram error: {r.text}")
            except Exception as e:
                logger.error(f"Telegram send failed: {e}")
            time.sleep(2)

        logger.error(f"❌ FAILED SENDING: {safe_title}")
        return False

    except Exception as e:
        logger.error(f"send_course_immediately error: {e}", exc_info=True)
        return False

# ───────────────────────────────────────────────────────────
# INITIAL SCRAPE (10 PAGES)
# ───────────────────────────────────────────────────────────
def scrape_and_send_initial_courses():
    logger.info("🔎 Initial scraping started...")

    scraper = DiscUdemyScraper()
    courses = scraper.scrape(max_pages=INITIAL_PAGES)

    logger.info(f"Found {len(courses)} courses. Sending…")

    for course in courses:
        send_course_immediately(course)
        time.sleep(1)

    scraper.close()

# ───────────────────────────────────────────────────────────
# MONITOR FIRST PAGE EVERY 2 MINUTES
# ───────────────────────────────────────────────────────────
def monitor_first_page():
    logger.info("🔍 Monitoring page 1…")

    scraper = DiscUdemyScraper()
    results = scraper.scrape(max_pages=1)

    last_sent = course_memory.get()
    logger.info(f"Last sent: {last_sent}")

    new_courses = []

    if last_sent is None:
        new_courses = results
    else:
        found = False
        for c in results:
            cid = f"{c['slug']}:{c['coupon_code']}"
            if cid == last_sent:
                found = True
                continue
            if not found:
                new_courses.append(c)

        if not found:
            new_courses = results

    if new_courses:
        logger.info(f"🚀 {len(new_courses)} new courses found!")

        for c in reversed(new_courses):
            send_course_immediately(c)
            time.sleep(1)

    scraper.close()

# ───────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start health server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health server running @ {PORT}")

    # Initial scrape
    scrape_and_send_initial_courses()

    # Schedule monitor job
    scheduler = BlockingScheduler()
    scheduler.add_job(monitor_first_page, "interval", seconds=MONITOR_INTERVAL)

    logger.info("Scheduler started.")
    scheduler.start()

# bot.py (updated — integrates DiscUdemy + CouponScorpion)
import os
import logging
import random
import requests
import threading
import time
import json
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask

# existing DiscUdemy scraper
from discudemy_scraper import DiscUdemyScraper
# new CouponScorpion scraper
from couponscorpion_scraper import CouponScorpionScraper
# shortener
from shortener import ShrinkMe

# ───────────────────────────────────────────────────────────
# ENV VARS
# ───────────────────────────────────────────────────────────
TOKEN    = os.environ["BOT_TOKEN"]
CHAT_ID  = os.environ["CHANNEL_ID"]
PORT     = int(os.environ.get("PORT", 10000))
SHRINKME_API_KEY = os.environ["SHRINKME_API_KEY"]

INITIAL_PAGES    = 3      # DiscUdemy initial pages
MONITOR_INTERVAL = 60     # seconds (2 minutes)

# ───────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────
# MEMORY FOR LAST SENT COURSE (generic id)
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
# INITIALIZE SCRAPERS + SHORTENER
# ───────────────────────────────────────────────────────────
shortener = ShrinkMe(SHRINKME_API_KEY)
discudemy = DiscUdemyScraper()
coupon_scorpion = CouponScorpionScraper()

# ───────────────────────────────────────────────────────────
# SENDING FUNCTION (keeps Udemy-styled card + ShrinkMe button)
# This function will build an ID for memory:
#  - For CouponScorpion use its post_url (unique)
#  - For DiscUdemy use slug:coupon_code (legacy)
# ───────────────────────────────────────────────────────────
def build_course_id(course):
    # If post_url exists (couponscorpion), use that as the id
    if course.get("post_url"):
        return f"post|{course['post_url']}"
    # else fallback to udemy slug + coupon
    slug = course.get("slug") or ""
    code = course.get("coupon_code") or ""
    return f"udemy|{slug}:{code}"

def send_course(course):
    try:
        # common fields
        title = course.get("title") or (course.get("slug") or "Untitled").replace("-", " ").title()
        desc  = course.get("description") or f"Learn {title} with this comprehensive course!"
        img   = course.get("image_url")
        is_free = bool(course.get("is_free")) or (course.get("coupon_code") == "FREE")

        # Build final udemy url if present; else try to create from slug
        udemy_url = course.get("udemy_url")
        if not udemy_url:
            slug = course.get("slug")
            if slug:
                if is_free:
                    udemy_url = f"https://www.udemy.com/course/{slug}/"
                else:
                    # if coupon code exists, attach it
                    code = course.get("coupon_code")
                    if code and code != "FREE":
                        udemy_url = f"https://www.udemy.com/course/{slug}/?couponCode={code}"
                    else:
                        udemy_url = f"https://www.udemy.com/course/{slug}/"

        # Shorten final URL via ShrinkMe (shorten redirect to your choice; here we shorten udemy_url directly)
        short_url = shortener.shorten(udemy_url)

        # Build card content
        rating = round(random.uniform(3.4, 4.9), 1)
        students = random.randint(2000, 70000)
        left = random.randint(80, 1200)

        safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        short_desc = desc[:180] + "…" if len(desc) > 180 else desc

        caption = (
            f"✏️ <b>{safe_title}</b>\n\n"
            f"{'🆓 ALWAYS FREE COURSE' if is_free else f'⏰ LIMITED TIME ({left:,} Enrolls Left)'}\n"
            f"⭐ {rating:.1f}/5\n"
            f"👩‍🎓 {students:,} students\n"
            f"🌐 English Language\n\n"
            f"💡 {short_desc}"
        )

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

        for attempt in range(3):
            try:
                r = requests.post(endpoint, data=payload, timeout=15)
                if r.status_code == 200 and r.json().get("ok"):
                    # update memory id
                    cid = build_course_id(course)
                    course_memory.set(cid)
                    logger.info(f"✅ Sent: {title} (id={cid})")
                    return True
                else:
                    logger.error(f"Telegram API error: {r.status_code} {r.text}")
            except Exception as e:
                logger.error(f"Telegram request failed: {e}")
            time.sleep(2)

        logger.error(f"Failed to send: {title}")
        return False

    except Exception as e:
        logger.error(f"send_course error: {e}", exc_info=True)
        return False

# ───────────────────────────────────────────────────────────
# INITIAL SCRAPE: DiscUdemy (10 pages) + CouponScorpion (page 1)
# ───────────────────────────────────────────────────────────
def initial_scrape():
    logger.info("🔎 Running initial scrape: DiscUdemy (10 pages) + CouponScorpion (page 1)")

    all_courses = []

    try:
        d_results = discudemy.scrape(max_pages=INITIAL_PAGES)
        logger.info(f"DiscUdemy returned {len(d_results)} courses")
        all_courses.extend(d_results)
    except Exception as e:
        logger.error(f"DiscUdemy initial error: {e}")

    try:
        cs_results = coupon_scorpion.scrape(max_pages=1)
        logger.info(f"CouponScorpion returned {len(cs_results)} courses")
        all_courses.extend(cs_results)
    except Exception as e:
        logger.error(f"CouponScorpion initial error: {e}")

    logger.info(f"Total initial courses to send: {len(all_courses)}")
    for c in all_courses:
        send_course(c)
        time.sleep(1)  # small delay

# ───────────────────────────────────────────────────────────
# MONITOR FIRST PAGE: DiscUdemy page1 + CouponScorpion page1
# Only send new items above last_sent_id.
# Deduplication uses course id generated by `build_course_id`.
# ───────────────────────────────────────────────────────────
def monitor_first_page():
    logger.info("🔍 Monitoring page 1 for new courses (both sources)")

    results = []

    try:
        d_results = discudemy.scrape(max_pages=1)
        results.extend(d_results)
    except Exception as e:
        logger.error(f"DiscUdemy monitor error: {e}")

    try:
        cs_results = coupon_scorpion.scrape(max_pages=1)
        # couponscorpion items already include post_url; they are appended
        results.extend(cs_results)
    except Exception as e:
        logger.error(f"CouponScorpion monitor error: {e}")

    if not results:
        logger.info("No results found on page 1")
        return

    last_sent = course_memory.get()
    logger.info(f"Last sent ID: {last_sent}")

    # collect new items (items above last_sent)
    new_items = []
    if last_sent is None:
        logger.info("No last_sent stored — considering all as new")
        new_items = results
    else:
        found = False
        for item in results:
            cid = build_course_id(item)
            if cid == last_sent:
                found = True
                break
            new_items.append(item)

        if not found:
            logger.info("last_sent_id not found on page 1 → send only newest item on combined page")
            new_items = results[:1]

    # send in chronological order (oldest -> newest)
    new_items.reverse()
    logger.info(f"Found {len(new_items)} new items to send")

    for c in new_items:
        send_course(c)
        time.sleep(1)

# ───────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # start health server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health server started on port {PORT}")

    # initial scrape
    initial_scrape()

    # scheduler
    scheduler = BlockingScheduler()
    scheduler.add_job(monitor_first_page, "interval", seconds=MONITOR_INTERVAL)
    logger.info(f"Scheduler started — interval {MONITOR_INTERVAL} seconds")
    scheduler.start()

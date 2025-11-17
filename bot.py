# bot.py — Final integrated bot (CouponScorpion first -> DiscUdemy)
import os
import logging
import random
import requests
import threading
import time
import json
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask

# scrapers & shortener (ensure these modules exist in project)
from discudemy_scraper import DiscUdemyScraper
from couponscorpion_scraper import CouponScorpionScraper
from shortener import ShrinkMe

# ───────────────────────────────────────────────────────────
# Configuration / Environment
# ───────────────────────────────────────────────────────────
TOKEN    = os.environ["BOT_TOKEN"]
CHAT_ID  = os.environ["CHANNEL_ID"]
PORT     = int(os.environ.get("PORT", 10000))
SHRINKME_API_KEY = os.environ["SHRINKME_API_KEY"]

# Behaviour settings (as you chose)
COUPONSCORPION_MAX_POSTS = 12
DISCUDEMY_PAGES_INITIAL = 3
SEND_DELAY_SECONDS = 1
MONITOR_INTERVAL_SECONDS = 60

# Persistence file
LAST_SENT_FILE = "last_sent.json"

# ───────────────────────────────────────────────────────────
# Logging
# ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────
# Persistence helpers for last_sent_id
# ───────────────────────────────────────────────────────────
def load_last_sent():
    try:
        if os.path.exists(LAST_SENT_FILE):
            with open(LAST_SENT_FILE, "r") as f:
                data = json.load(f)
                return data.get("last_sent_id")
    except Exception as e:
        logger.warning(f"Could not load last_sent.json: {e}")
    return None

def save_last_sent(last_id):
    try:
        with open(LAST_SENT_FILE, "w") as f:
            json.dump({"last_sent_id": last_id}, f)
    except Exception as e:
        logger.warning(f"Could not save last_sent.json: {e}")

# Initialize memory from disk
_initial_last = load_last_sent()

class CourseMemory:
    def __init__(self, initial=None):
        self.last_sent_id = initial
    def get(self):
        return self.last_sent_id
    def set(self, cid):
        self.last_sent_id = cid
        save_last_sent(cid)
        logger.info(f"📌 Updated last sent ID → {cid}")

course_memory = CourseMemory(initial=_initial_last)

# ───────────────────────────────────────────────────────────
# Flask health-check
# ───────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

def run_health_server():
    # run in background thread (non-blocking)
    app.run(host="0.0.0.0", port=PORT, debug=False)

# ───────────────────────────────────────────────────────────
# Initialize scrapers and shortener
# ───────────────────────────────────────────────────────────
shortener = ShrinkMe(SHRINKME_API_KEY)
discudemy = DiscUdemyScraper()
coupon_scorpion = CouponScorpionScraper()

# ───────────────────────────────────────────────────────────
# Helpers: build unique ID for a course (used for dedupe)
# For couponscorpion we use post_url; for discudemy use udemy|slug:coupon
# ───────────────────────────────────────────────────────────
def build_course_id(course):
    # couponscorpion posts include 'post_url'
    if course.get("post_url"):
        return f"couponscorpion|{course['post_url']}"
    # else use udemy slug + coupon
    slug = course.get("slug") or ""
    code = course.get("coupon_code") or ""
    return f"udemy|{slug}:{code}"

# ───────────────────────────────────────────────────────────
# Send a course as Udemy-style card with ShrinkMe CTA button
# ───────────────────────────────────────────────────────────
def send_course(course):
    try:
        title = course.get("title") or (course.get("slug") or "Untitled").replace("-", " ").title()
        desc  = course.get("description") or f"Learn {title} with this comprehensive course!"
        img   = course.get("image_url")
        is_free = bool(course.get("is_free")) or (course.get("coupon_code") == "FREE")

        # Build final Udemy URL if provided or construct from slug
        udemy_url = course.get("udemy_url")
        if not udemy_url:
            slug = course.get("slug")
            code = course.get("coupon_code")
            if slug:
                if is_free or code == "FREE":
                    udemy_url = f"https://www.udemy.com/course/{slug}/"
                else:
                    udemy_url = f"https://www.udemy.com/course/{slug}/"
                    if code:
                        udemy_url += f"?couponCode={code}"

        # Shorten with ShrinkMe (shorten target = udemy_url)
        short_url = shortener.shorten(udemy_url)

        # Compose card
        rating = round(random.uniform(3.4, 4.9), 1)
        students = random.randint(2000, 90000)
        left = random.randint(80, 1200)
        safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        desc_short = desc[:180] + "…" if len(desc) > 180 else desc

        caption = (
            f"✏️ <b>{safe_title}</b>\n\n"
            f"{'🆓 ALWAYS FREE COURSE' if is_free else f'⏰ LIMITED TIME ({left:,} Enrolls Left)'}\n"
            f"⭐ {rating:.1f}/5\n"
            f"👩‍🎓 {students:,} students\n"
            f"🌐 English Language\n\n"
            f"💡 {desc_short}"
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

        # Attempt send with retries
        for attempt in range(3):
            try:
                r = requests.post(endpoint, data=payload, timeout=15)
                if r.status_code == 200 and r.json().get("ok"):
                    cid = build_course_id(course)
                    course_memory.set(cid)
                    logger.info(f"✅ Sent: {title} (id={cid})")
                    return True
                else:
                    logger.error(f"Telegram API error: {r.status_code} {r.text}")
            except Exception as e:
                logger.error(f"Telegram send attempt {attempt+1} failed: {e}")
            time.sleep(2)

        logger.error(f"❌ Failed to send course after retries: {title}")
        return False

    except Exception as e:
        logger.error(f"send_course fatal error: {e}", exc_info=True)
        return False

# ───────────────────────────────────────────────────────────
# Startup initial scrape:
#  1) CouponScorpion first (max posts set)
#  2) DiscUdemy second (pages set)
# Send combined list newest->oldest but avoid duplicates using course_memory
# ───────────────────────────────────────────────────────────
def initial_scrape():
    logger.info("🔎 Initial scrape started — CouponScorpion first, then DiscUdemy")

    combined = []

    # 1) CouponScorpion
    try:
        cs_items = coupon_scorpion.scrape(max_posts=COUPONSCORPION_MAX_POSTS)
        logger.info(f"CouponScorpion returned {len(cs_items)} items")
        combined.extend(cs_items)
    except Exception as e:
        logger.error(f"CouponScorpion initial scraping error: {e}", exc_info=True)

    # 2) DiscUdemy
    try:
        d_items = discudemy.scrape(max_pages=DISCUDEMY_PAGES_INITIAL)
        logger.info(f"DiscUdemy returned {len(d_items)} items")
        combined.extend(d_items)
    except Exception as e:
        logger.error(f"DiscUdemy initial scraping error: {e}", exc_info=True)

    # dedupe by id while preserving order (first occurrences kept)
    seen = set()
    unique = []
    for item in combined:
        cid = build_course_id(item)
        if cid not in seen:
            seen.add(cid)
            unique.append(item)

    logger.info(f"Total unique initial courses to send: {len(unique)}")

    # send from newest -> oldest: the scrapers list newer items first; we send as-is but we can reverse if you want oldest first.
    for course in unique:
        # if last_sent exists and equals this id, we should stop sending older ones (but initial run we usually want to send everything)
        send_course(course)
        time.sleep(SEND_DELAY_SECONDS)

# ───────────────────────────────────────────────────────────
# Monitor function — check page1 of both sources and send only new items above last_sent_id
# Behavior:
#  - merges discudemy page1 then couponscorpion page1 (order preserved)
#  - finds items above last_sent_id and sends them (oldest->newest)
#  - if last_sent_id not found -> only send the very newest item to avoid mass reposts
# ───────────────────────────────────────────────────────────
def monitor_first_page():
    logger.info("🔍 Monitor job started — scraping page1 of both sources")

    results = []

    # DiscUdemy page1
    try:
        d_results = discudemy.scrape(max_pages=1)
        results.extend(d_results)
    except Exception as e:
        logger.error(f"DiscUdemy monitor scrape error: {e}")

    # CouponScorpion page1 (max_posts small; will return top posts)
    try:
        cs_results = coupon_scorpion.scrape(max_posts=COUPONSCORPION_MAX_POSTS)
        # couponscorpion results are in newest-first order
        results.extend(cs_results)
    except Exception as e:
        logger.error(f"CouponScorpion monitor scrape error: {e}")

    if not results:
        logger.info("No items found on monitor scraping")
        return

    last_sent = course_memory.get()
    logger.info(f"Monitor: last_sent_id = {last_sent}")

    new_items = []
    if last_sent is None:
        logger.info("No last_sent recorded — treating whole page as new")
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
            logger.info("last_sent_id not found on page — sending only the newest item to be safe")
            new_items = results[:1]

    # send oldest -> newest
    new_items.reverse()
    logger.info(f"Monitor: {len(new_items)} new items will be sent")
    for course in new_items:
        send_course(course)
        time.sleep(SEND_DELAY_SECONDS)

# ───────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 1) start health server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health endpoint running on port {PORT}")

    # 2) initial scrape (CouponScorpion first, then DiscUdemy)
    try:
        initial_scrape()
    except Exception as e:
        logger.error(f"Initial scrape failed: {e}", exc_info=True)

    # 3) scheduler for monitor job
    scheduler = BlockingScheduler()
    scheduler.add_job(monitor_first_page, "interval", seconds=MONITOR_INTERVAL_SECONDS, id="monitor-first")
    logger.info(f"Scheduler started — monitor interval {MONITOR_INTERVAL_SECONDS} seconds")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown")

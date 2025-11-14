import os
import logging
import random
import requests
import threading
import time
import json
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from bs4 import BeautifulSoup
import urllib.parse

# Import our lightweight scraper module
from discudemy_scraper import DiscUdemyScraper

# ─── CONFIG ────────────────────────────────────────────────
TOKEN             = '7918306173:AAFFIedi9d4R8XDA0AlsOin8BCfJRJeNGWE'
CHAT_ID           = '@udemyfreecourses2080'
INITIAL_PAGES     = 10  # Initial scrape for first 10 pages
MONITOR_INTERVAL  = 120  # Monitor first page every 2 minutes (in seconds)
BASE_REDIRECT_URL = 'https://udemyfreecoupons2080.blogspot.com'
PORT              = 10000  # health-check endpoint port
MAX_RETRY_ATTEMPTS = 3
# ────────────────────────────────────────────────────────────

# List of user agents to rotate through
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
]

# ─── LOGGING & SCHEDULER SETUP ─────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger    = logging.getLogger(__name__)
scheduler = BlockingScheduler(timezone="UTC")
# ────────────────────────────────────────────────────────────

# Simple memory to track last sent course
class CourseMemory:
    def __init__(self):
        self.last_sent_course_id = None  # Will store slug:coupon_code of last sent course
        self.lock = threading.Lock()
    
    def get_last_sent_id(self):
        with self.lock:
            return self.last_sent_course_id
    
    def update_last_sent_id(self, course_id):
        with self.lock:
            self.last_sent_course_id = course_id
            logger.info(f"Updated last sent course ID to: {course_id}")

# Create memory instance
course_memory = CourseMemory()

# ─── FLASK HEALTH CHECK ────────────────────────────────────
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.route("/")
def root():
    return "Udemy Coupon Bot is running", 404

def run_health_server():
    app.run(host="0.0.0.0", port=PORT, debug=False)

# ─── TELEGRAM SENDER ───────────────────────────────────────
def send_course_immediately(coupon_data):
    """Send a single course immediately to Telegram"""
    try:
        slug = coupon_data.get('slug')
        coupon = coupon_data.get('coupon_code')
        title = coupon_data.get('title', slug.replace('-', ' ').title())
        img = coupon_data.get('image_url')
        desc = coupon_data.get('description', f'Learn {title} with this comprehensive course!')
        is_free = coupon_data.get('is_free', False)
        
        # Create course identifier
        course_id = f"{slug}:{coupon}"
        
        # Create redirect URL - handle free courses differently
        if is_free or coupon == "FREE":
            # For free courses, link directly to the course without coupon code
            redirect_url = f"{BASE_REDIRECT_URL}?udemy_url=" + urllib.parse.quote(
                f"https://www.udemy.com/course/{slug}/", safe=''
            )
        else:
            # For coupon courses, include the coupon code
            redirect_url = f"{BASE_REDIRECT_URL}?udemy_url=" + urllib.parse.quote(
                f"https://www.udemy.com/course/{slug}/?couponCode={coupon}", safe=''
            )

        # Generate realistic random rating and students
        rating = round(random.uniform(3.0, 4.9), 1)
        students = random.randint(100, 50000)
        enrolls_left = random.randint(50, 1000)

        # Format the description to a maximum of 180 characters with ellipsis
        short_desc = (desc[:177] + '...') if len(desc) > 180 else desc

        # Build HTML caption with structured format
        rating_text = f"{rating:.1f}/5"
        students_text = f"{students:,}"
        enrolls_left_text = f"{enrolls_left:,}"

        # Clean up title to prevent HTML parsing issues
        title = title.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        
        # Different caption for free vs coupon courses
        if is_free or coupon == "FREE":
            caption = (
                f"✏️ <b>{title}</b>\n\n"
                f"🆓 ALWAYS FREE COURSE\n"
                f"⭐ {rating_text}\n"
                f"👩‍🎓 {students_text} students\n"
                f"🌐 English Language\n\n"
                f"💡 {short_desc}"
            )
        else:
            caption = (
                f"✏️ <b>{title}</b>\n\n"
                f"⏰ LIMITED TIME ({enrolls_left_text} Enrolls Left)\n"
                f"⭐ {rating_text}\n"
                f"👩‍🎓 {students_text} students\n"
                f"🌐 English Language\n\n"
                f"💡 {short_desc}"
            )

        payload = {
            'chat_id':    CHAT_ID,
            'caption':    caption,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps({
                'inline_keyboard': [[{
                    'text': '🎓 Get Free Course' if is_free or coupon == "FREE" else '🎓 Get Free Course',
                    'url':  redirect_url
                }]]
            })
        }

        # Choose sendPhoto vs sendMessage
        if img and img.startswith('http'):
            payload['photo'] = img
            api_endpoint = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        else:
            payload['text'] = caption
            api_endpoint = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            payload.pop('caption', None)

        # Send to Telegram with retry
        max_telegram_attempts = 3
        for telegram_attempt in range(max_telegram_attempts):
            try:
                resp = requests.post(api_endpoint, data=payload, timeout=15)
                resp.raise_for_status()
                result = resp.json()
                if result.get('ok'):
                    course_type = "free" if is_free or coupon == "FREE" else "coupon"
                    logger.info(f"✅ Successfully sent {course_type} course: {title} ({course_id})")
                    # Update memory with the last sent course
                    course_memory.update_last_sent_id(course_id)
                    return True
                else:
                    logger.error(f"Telegram API error: {result}")
                    if telegram_attempt < max_telegram_attempts - 1:
                        time.sleep(2)
            except Exception as e:
                logger.error(f"Telegram API request failed (attempt {telegram_attempt+1}/{max_telegram_attempts}): {e}")
                if telegram_attempt < max_telegram_attempts - 1:
                    time.sleep(2)
                    continue
        
        logger.error(f"❌ Failed to send course after all attempts: {title}")
        return False
                
    except Exception as e:
        logger.error(f"Failed to send course: {e}", exc_info=True)
        return False

# ─── SCRAPING FUNCTIONS ────────────────────────────────────
def scrape_and_send_initial_courses():
    """Scrape first 10 pages and send all courses immediately"""
    logger.info(f"Starting initial scrape of {INITIAL_PAGES} pages...")
    
    scraper = None
    try:
        scraper = DiscUdemyScraper(timeout=20)
        results = scraper.scrape(max_pages=INITIAL_PAGES)
        
        if not results:
            logger.warning("No courses found during initial scraping")
            return
        
        logger.info(f"Found {len(results)} courses in initial scrape. Sending immediately...")
        
        sent_count = 0
        for coupon_data in results:
            if coupon_data.get('slug') and coupon_data.get('coupon_code'):
                # Send immediately
                if send_course_immediately(coupon_data):
                    sent_count += 1
                    # Small delay to avoid rate limiting
                    time.sleep(1)
                else:
                    logger.warning(f"Failed to send course: {coupon_data.get('slug')}")
        
        logger.info(f"Initial scrape complete. Sent {sent_count} out of {len(results)} courses")
        
    except Exception as e:
        logger.error(f"Error during initial scraping: {e}", exc_info=True)
    finally:
        if scraper:
            try:
                scraper.close()
            except Exception as e:
                logger.warning(f"Error closing scraper: {e}")

def monitor_first_page():
    """Monitor first page for new courses and send them immediately"""
    logger.info("Monitoring first page for new courses...")
    
    scraper = None
    try:
        scraper = DiscUdemyScraper(timeout=20)
        results = scraper.scrape(max_pages=1)  # Only scrape first page
        
        if not results:
            logger.info("No courses found on first page")
            return
        
        last_sent_id = course_memory.get_last_sent_id()
        logger.info(f"Found {len(results)} courses on first page. Last sent ID: {last_sent_id}")
        
        # Find new courses (courses that come after the last sent one)
        new_courses = []
        if last_sent_id is None:
            # If no previous course, send all
            new_courses = results
        else:
            # Find courses that come after the last sent course
            found_last_sent = False
            for coupon_data in results:
                course_id = f"{coupon_data.get('slug')}:{coupon_data.get('coupon_code')}"
                if course_id == last_sent_id:
                    found_last_sent = True
                    continue
                if not found_last_sent:
                    # This course is newer than the last sent one
                    new_courses.append(coupon_data)
            
            # If we didn't find the last sent course, it means all courses are new
            if not found_last_sent:
                logger.info("Last sent course not found on first page, all courses are considered new")
                new_courses = results
        
        if new_courses:
            logger.info(f"Found {len(new_courses)} new courses. Sending immediately...")
            sent_count = 0
            
            # Send new courses in reverse order (newest first)
            for coupon_data in reversed(new_courses):
                if coupon_data.get('slug') and coupon_data.get('coupon_code'):
                    if send_course_immediately(coupon_data):
                        sent_count += 1
                        # Small delay to avoid rate limiting
                        time.sleep(1)
                    else:
                        logger.warning(f"Failed to send new course: {coupon_data.get('slug')}")
            
            logger.info(f"Sent {sent_count} new courses")
        else:
            logger.info("No new courses found on first page")
        
    except Exception as e:
        logger.error(f"Error during first page monitoring: {e}", exc_info=True)
    finally:
        if scraper:
            try:
                scraper.close()
            except Exception as e:
                logger.warning(f"Error closing scraper: {e}")

# ─── MAIN ───────────────────────────────────────────────────
if __name__ == '__main__':
    # 1) Start health-check server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health-check listening on port {PORT}")

    # 2) Run initial scrape and send all courses from first 10 pages
    scrape_and_send_initial_courses()
    
    # 3) Schedule monitoring job for first page every 2 minutes
    scheduler.add_job(
        func=monitor_first_page,
        trigger="interval",
        seconds=MONITOR_INTERVAL,
        id="monitor_job",
        replace_existing=True
    )

    logger.info(f"Scheduler configured - Monitoring first page every {MONITOR_INTERVAL} seconds")
    
    # 4) Start the scheduler (this will block)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown complete")

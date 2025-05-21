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

# Import our scraper module
from discudemy_scraper import DiscUdemyScraper

# ─── CONFIG ────────────────────────────────────────────────
TOKEN             = '7918306173:AAFFIedi9d4R8XDA0AlsOin8BCfJRJeNGWE'
CHAT_ID           = '@udemyfreecourses2080'
SCRAPE_INTERVAL   = 3600  # Scrape every hour (in seconds)
POST_INTERVAL     = random.randint(600, 900)  # Post every 10-15 minutes (in seconds)
BASE_REDIRECT_URL = 'https://udemyfreecoupons2080.blogspot.com'
PORT              = 10000  # health-check endpoint port
MAX_PAGES         = 1  # Number of pages to scrape from discudemy
MAX_RETRY_ATTEMPTS = 3  # Number of times to retry scraping if it fails
# ────────────────────────────────────────────────────────────

# List of user agents to rotate through
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
]

# Fallback coupons if scraping fails
STATIC_COUPONS = [
    ('the-complete-python-bootcamp-from-zero-to-expert', 'ST6MT60525G3')
]

# ─── LOGGING & SCHEDULER SETUP ─────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger    = logging.getLogger(__name__)
scheduler = BlockingScheduler(timezone="UTC")
# ────────────────────────────────────────────────────────────

# Database to store and manage coupons
class CouponDatabase:
    def __init__(self):
        self.coupons = []
        self.posted_slugs = set()  # Track slugs that have already been posted
        self.lock = threading.Lock()  # For thread safety
    
    def update_coupons(self, new_coupons):
        with self.lock:
            # Filter out already posted courses
            filtered_coupons = [(slug, code) for slug, code in new_coupons 
                              if slug not in self.posted_slugs]
            
            # Update our database with new coupons
            self.coupons.extend(filtered_coupons)
            logger.info(f"Added {len(filtered_coupons)} new coupons to database")
    
    def get_next_coupon(self):
        with self.lock:
            if not self.coupons:
                logger.warning("No coupons available in database, using fallback")
                return STATIC_COUPONS[0]
                
            # Get the next available coupon
            coupon = self.coupons.pop(0)
            
            # Add the slug to posted set
            self.posted_slugs.add(coupon[0])
            
            logger.info(f"Selected coupon: {coupon[0]}, {len(self.coupons)} remaining")
            return coupon

# Create our coupon database
coupon_db = CouponDatabase()

# ─── FLASK HEALTH CHECK ────────────────────────────────────
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

def run_health_server():
    app.run(host="0.0.0.0", port=PORT)

# ─── DISCUDEMY SCRAPER ───────────────────────────────────
def scrape_discudemy():
    """Scrape DiscUdemy for fresh coupons and update the database with retry mechanism"""
    retry_count = 0
    while retry_count < MAX_RETRY_ATTEMPTS:
        try:
            logger.info(f"Starting DiscUdemy scraper for {MAX_PAGES} pages (attempt {retry_count + 1}/{MAX_RETRY_ATTEMPTS})")
            
            scraper = DiscUdemyScraper(headless=True)
            try:
                results = scraper.scrape(max_pages=MAX_PAGES)
                
                if not results:
                    logger.warning("No coupons found during scraping")
                    retry_count += 1
                    time.sleep(60)  # Wait a minute before retrying
                    continue
                    
                # Convert to list of (slug, coupon_code) tuples
                coupons = [(item['slug'], item['coupon_code']) for item in results 
                           if item['slug'] and item['coupon_code']]
                    
                logger.info(f"Successfully scraped {len(coupons)} valid coupons")
                
                # Update our database
                coupon_db.update_coupons(coupons)
                return  # Success - exit the retry loop
                
            except Exception as e:
                logger.error(f"Error during scraping attempt {retry_count + 1}: {e}", exc_info=True)
                retry_count += 1
                time.sleep(60)  # Wait before retry
            finally:
                try:
                    scraper.close()
                except:
                    pass
                
        except Exception as e:
            logger.error(f"Failed to initialize scraper (attempt {retry_count + 1}): {e}", exc_info=True)
            retry_count += 1
            time.sleep(60)  # Wait before retry
    
    logger.error(f"All {MAX_RETRY_ATTEMPTS} scraping attempts failed")

# ─── UDEMY SCRAPER ─────────────────────────────────────────
def fetch_course_details(slug):
    """
    Scrape Udemy course page with improved error handling
    """
    url = f"https://www.udemy.com/course/{slug}/"
    
    # Use a random user agent and add more browser-like headers
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    # Add a delay to avoid being rate-limited (random between 1-3 seconds)
    time.sleep(random.uniform(1, 3))
    
    session = requests.Session()
    
    try:
        # Try to get the course page with timeout and retries
        retries = 3
        for attempt in range(retries):
            try:
                resp = session.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                break
            except (requests.RequestException, Exception) as e:
                if attempt < retries - 1:
                    logger.warning(f"Request failed (attempt {attempt+1}/{retries}): {e}")
                    time.sleep(2)
                    continue
                raise
                
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Extract Open Graph metadata
        og_data = {}
        for meta in soup.find_all('meta', property=lambda x: x and x.startswith('og:')):
            og_data[meta['property']] = meta.get('content', '')
        
        # Get title, thumbnail, and description
        title = og_data.get('og:title')
        thumbnail = og_data.get('og:image')
        description = og_data.get('og:description')
        
        # If any of the key data is missing, raise an exception
        if not title or not thumbnail or not description:
            raise ValueError("Missing required metadata from course page")
            
        logger.info(f"Successfully scraped course: {slug}")
        
    except Exception as e:
        logger.warning(f"Scraping Udemy failed for {slug}—using fallback: {str(e)}")
        title = slug.replace('-', ' ').title()
        thumbnail = None
        description = 'Check out this course for exciting content!'

    # Close the session
    session.close()

    # Generate random rating and students
    rating = round(random.uniform(4.0, 5.0), 1)  # Higher ratings look better
    students = random.randint(5000, 100000)      # More students look better

    return title, thumbnail, description, rating, students

# ─── TELEGRAM SENDER ───────────────────────────────────────
def send_coupon():
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # Get the next coupon
            slug, coupon = coupon_db.get_next_coupon()
            
            # Create redirect URL
            redirect_url = f"{BASE_REDIRECT_URL}?udemy_url=" + urllib.parse.quote(
                f"https://www.udemy.com/course/{slug}/?couponCode={coupon}", safe=''
            )

            # Get course details with retry mechanism
            retries = 3
            title, img, desc, rating, students = None, None, None, None, None
            
            while retries > 0:
                try:
                    title, img, desc, rating, students = fetch_course_details(slug)
                    break
                except Exception as e:
                    logger.warning(f"Error fetching course details (attempt {4-retries}/3): {str(e)}")
                    retries -= 1
                    time.sleep(2)  # Wait 2 seconds before retry
            
            # If all retries failed, use fallback values
            if not title:
                title = slug.replace('-', ' ').title()
                img = None
                desc = 'Check out this course for exciting content!'
                rating = round(random.uniform(3.5, 5.0), 1)
                students = random.randint(5000, 100000)

            # Generate a random number for enrolls left (between 1 and 1000)
            enrolls_left = random.randint(1, 1000)

            # Format the description to a maximum of 200 characters with ellipsis
            short_desc = (desc[:197] + '...') if len(desc) > 200 else desc

            # Build HTML caption with structured format
            rating_text = f"{rating:.1f}/5"
            students_text = f"{students:,}"
            enrolls_left_text = f"{enrolls_left:,}"

            # Clean up title to prevent HTML parsing issues
            title = title.replace("<", "&lt;").replace(">", "&gt;")
            
            # Create a more enticing course topic
            course_topic = title.split(' - ')[0].lower().replace('full course', '').strip()
            if not course_topic:
                course_topic = "this topic"

            caption = (
                f"📚✏️ <b>{title}</b>\n"
                f"🏅 <b>CERTIFIED</b>\n"
                f"⏰ ASAP ({enrolls_left_text} Enrolls Left)\n"
                f"⭐ {rating_text}    👩‍🎓 {students_text} students\n"
                f"🌐 English (US)\n\n"
                f"💡 Learn everything you need to know as a {course_topic} beginner.\n"
                f"Become a {course_topic} expert!\n\n"
                f"🔗 <a href='{redirect_url}'>Enroll Now</a>"
            )

            payload = {
                'chat_id':    CHAT_ID,
                'caption':    caption,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps({
                    'inline_keyboard': [[{
                        'text': '🎓 Enroll Now',
                        'url':  redirect_url
                    }]]
                })
            }

            # choose sendPhoto vs sendMessage
            if img:
                payload['photo'] = img
                api_endpoint = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
            else:
                payload['text'] = caption
                api_endpoint = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                payload.pop('caption')

            # send to Telegram with retry
            for telegram_attempt in range(3):
                try:
                    resp = requests.post(api_endpoint, data=payload, timeout=15)
                    resp.raise_for_status()
                    result = resp.json()
                    if result.get('ok'):
                        logger.info(f"Sent course card: {slug}")
                        return  # Success
                    else:
                        logger.error(f"Telegram API error: {result}")
                        time.sleep(2)
                except Exception as e:
                    logger.error(f"Telegram API request failed (attempt {telegram_attempt+1}/3): {e}")
                    time.sleep(2)
                    continue
            
            # If we get here, all Telegram attempts failed
            raise Exception("All Telegram API attempts failed")
                
        except Exception as e:
            logger.error(f"Failed to send coupon (attempt {attempt+1}/{max_attempts}): {e}", exc_info=True)
            if attempt < max_attempts - 1:
                time.sleep(5)  # Wait before retry
            else:
                logger.error("All attempts to send coupon failed")

# ─── MAIN ───────────────────────────────────────────────────
if __name__ == '__main__':
    # 1) start health-check server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health-check listening on port {PORT}")

    # 2) run the initial scraper
    logger.info("Starting initial scrape...")
    scrape_discudemy()

    # 3) send first coupon immediately
    logger.info("Sending first coupon...")
    send_coupon()

    # 4) schedule periodic scraping
    scheduler.add_job(
        scrape_discudemy,
        'interval',
        seconds=SCRAPE_INTERVAL,
        next_run_time=datetime.now() + timedelta(seconds=SCRAPE_INTERVAL),
    )

    # 5) schedule periodic posting
    scheduler.add_job(
        send_coupon,
        'interval',
        seconds=POST_INTERVAL,
        next_run_time=datetime.now() + timedelta(seconds=POST_INTERVAL),
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
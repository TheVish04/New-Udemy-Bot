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
SCRAPE_INTERVAL   = 60  # Scrape every hour (in seconds)
POST_INTERVAL     = random.randint(60, 61)  # Post every 10-15 minutes (in seconds)
BASE_REDIRECT_URL = 'https://udemyfreecoupons2080.blogspot.com'
PORT              = 10000  # health-check endpoint port
MAX_PAGES         = 5  # Increased since it's now faster without Selenium
MAX_RETRY_ATTEMPTS = 3  # Reduced since HTTP requests are more reliable
MIN_COUPONS_THRESHOLD = 1  # Minimum coupons needed to start posting
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

# Database to store and manage coupons
class CouponDatabase:
    def __init__(self):
        self.coupons = []
        self.posted_coupon_identifiers = set()  # Track slug:couponcode combinations that have been posted
        self.lock = threading.Lock()  # For thread safety
        self.last_successful_scrape = None
    
    def _get_coupon_identifier(self, coupon):
        """Generate unique identifier from slug and coupon code"""
        slug = coupon.get('slug', '')
        coupon_code = coupon.get('coupon_code', '')
        return f"{slug}:{coupon_code}"
    
    def update_coupons(self, new_coupons):
        with self.lock:
            # Filter out already posted coupon identifiers (slug:couponcode combinations)
            filtered_coupons = [coupon for coupon in new_coupons 
                              if self._get_coupon_identifier(coupon) not in self.posted_coupon_identifiers]
            
            # Update our database with new coupons
            self.coupons.extend(filtered_coupons)
            self.last_successful_scrape = datetime.now()
            logger.info(f"Added {len(filtered_coupons)} new coupons to database. Total: {len(self.coupons)}")
    
    def get_next_coupon(self):
        with self.lock:
            if not self.coupons:
                logger.warning("No coupons available in database")
                return None
                
            # Get the next available coupon
            coupon = self.coupons.pop(0)
            
            # Add the coupon identifier (slug:couponcode) to posted set
            coupon_identifier = self._get_coupon_identifier(coupon)
            self.posted_coupon_identifiers.add(coupon_identifier)
            
            logger.info(f"Selected coupon: {coupon_identifier} for {coupon.get('slug')}, {len(self.coupons)} remaining")
            return coupon
    
    def has_enough_coupons(self):
        with self.lock:
            return len(self.coupons) >= MIN_COUPONS_THRESHOLD
    
    def get_coupon_count(self):
        with self.lock:
            return len(self.coupons)

# Create our coupon database
coupon_db = CouponDatabase()

# ─── FLASK HEALTH CHECK ────────────────────────────────────
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.route("/")
def root():
    return "Udemy Coupon Bot is running", 404  # Return 404 as expected by logs

def run_health_server():
    app.run(host="0.0.0.0", port=PORT, debug=False)

# ─── DISCUDEMY SCRAPER ───────────────────────────────────
def scrape_discudemy():
    """Scrape DiscUdemy for fresh coupons and update the database"""
    retry_count = 0
    base_delay = 30  # Start with 30 seconds delay between retries
    
    while retry_count < MAX_RETRY_ATTEMPTS:
        scraper = None
        try:
            logger.info(f"Starting DiscUdemy scraper for {MAX_PAGES} pages (attempt {retry_count + 1}/{MAX_RETRY_ATTEMPTS})")
            
            # Create lightweight scraper (no Selenium!)
            scraper = DiscUdemyScraper(timeout=20)
            
            # Add delay before scraping if retrying
            if retry_count > 0:
                delay = base_delay * (retry_count + 1)
                delay = min(delay, 120)  # Cap at 2 minutes
                logger.info(f"Waiting {delay} seconds before retry...")
                time.sleep(delay)
            
            results = scraper.scrape(max_pages=MAX_PAGES)
            
            if not results:
                logger.warning(f"No coupons found during scraping attempt {retry_count + 1}")
                retry_count += 1
                continue
                
            # Results now contain full course information
            valid_coupons = [item for item in results 
                           if item.get('slug') and item.get('coupon_code')]
                
            if not valid_coupons:
                logger.warning(f"No valid coupons extracted from results on attempt {retry_count + 1}")
                retry_count += 1
                continue
                
            logger.info(f"Successfully scraped {len(valid_coupons)} valid coupons with course info")
            
            # Update our database
            coupon_db.update_coupons(valid_coupons)
            return  # Success - exit the retry loop
            
        except Exception as e:
            logger.error(f"Error during scraping attempt {retry_count + 1}: {e}", exc_info=True)
            retry_count += 1
        finally:
            # Always clean up the scraper
            if scraper:
                try:
                    scraper.close()
                except Exception as e:
                    logger.warning(f"Error closing scraper: {e}")
                
    logger.error(f"All {MAX_RETRY_ATTEMPTS} scraping attempts failed")

# ─── TELEGRAM SENDER ───────────────────────────────────────
def send_coupon():
    try:
        # Check if we have enough coupons
        if not coupon_db.has_enough_coupons():
            logger.warning(f"Not enough coupons available ({coupon_db.get_coupon_count()}), skipping this cycle")
            return
        
        # Get the next coupon
        coupon_data = coupon_db.get_next_coupon()
        if not coupon_data:
            logger.warning("No coupon available, skipping this cycle")
            return
            
        slug = coupon_data.get('slug')
        coupon = coupon_data.get('coupon_code')
        title = coupon_data.get('title', slug.replace('-', ' ').title())
        img = coupon_data.get('image_url')
        desc = coupon_data.get('description', f'Learn {title} with this comprehensive course!')
        
        # Create redirect URL
        redirect_url = f"{BASE_REDIRECT_URL}?udemy_url=" + urllib.parse.quote(
            f"https://www.udemy.com/course/{slug}/?couponCode={coupon}", safe=''
        )

        # Generate realistic random rating and students
        rating = round(random.uniform(3.0, 4.9), 1)  # Higher ratings look better
        students = random.randint(100, 50000)      # More students look better

        # Generate a random number for enrolls left (between 50 and 2000)
        enrolls_left = random.randint(50, 1000)

        # Format the description to a maximum of 180 characters with ellipsis
        short_desc = (desc[:177] + '...') if len(desc) > 180 else desc

        # Build HTML caption with structured format
        rating_text = f"{rating:.1f}/5"
        students_text = f"{students:,}"
        enrolls_left_text = f"{enrolls_left:,}"

        # Clean up title to prevent HTML parsing issues
        title = title.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        
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
                    'text': '🎓 Get Free Course',
                    'url':  redirect_url
                }]]
            })
        }

        # Choose sendPhoto vs sendMessage
        if img and img.startswith('http'):
            payload['photo'] = img
            api_endpoint = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
            logger.info(f"Sending with image: {img}")
        else:
            payload['text'] = caption
            api_endpoint = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            payload.pop('caption', None)
            logger.info("Sending without image (no valid image URL)")

        # Send to Telegram with retry
        max_telegram_attempts = 3
        for telegram_attempt in range(max_telegram_attempts):
            try:
                resp = requests.post(api_endpoint, data=payload, timeout=15)
                resp.raise_for_status()
                result = resp.json()
                if result.get('ok'):
                    logger.info(f"Successfully sent course card: {slug}:{coupon}")
                    return  # Success
                else:
                    logger.error(f"Telegram API error: {result}")
                    if telegram_attempt < max_telegram_attempts - 1:
                        time.sleep(3)
            except Exception as e:
                logger.error(f"Telegram API request failed (attempt {telegram_attempt+1}/{max_telegram_attempts}): {e}")
                if telegram_attempt < max_telegram_attempts - 1:
                    time.sleep(3)
                    continue
        
        # If we get here, all Telegram attempts failed
        logger.error("All Telegram API attempts failed for this coupon")
                
    except Exception as e:
        logger.error(f"Failed to send coupon: {e}", exc_info=True)

# ─── MAIN ───────────────────────────────────────────────────
if __name__ == '__main__':
    # 1) Start health-check server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health-check listening on port {PORT}")

    # 2) Run initial scraper and wait for sufficient coupons
    logger.info("Starting initial scrape...")
    scrape_discudemy()
    
    # Wait for initial coupons before starting to post
    initial_wait_time = 0
    max_initial_wait = 900  # Wait up to 15 minutes for first coupons
    
    while not coupon_db.has_enough_coupons() and initial_wait_time < max_initial_wait:
        logger.info(f"Waiting for sufficient coupons... Current: {coupon_db.get_coupon_count()}, Need: {MIN_COUPONS_THRESHOLD}")
        time.sleep(60)  # Wait 1 minute
        initial_wait_time += 60
        
        # Try scraping again if we don't have enough coupons
        if initial_wait_time % 300 == 0:  # Every 5 minutes
            logger.info("Retrying scrape to get more coupons...")
            scrape_discudemy()
    
    if not coupon_db.has_enough_coupons():
        logger.error(f"Could not get enough coupons after {max_initial_wait} seconds. Current: {coupon_db.get_coupon_count()}")
        # Continue anyway - the scheduled scrapes might get more coupons
    else:
        logger.info(f"Initial scraping complete. Ready to start posting with {coupon_db.get_coupon_count()} coupons")

    # 3) Schedule the jobs
    scheduler.add_job(
        func=scrape_discudemy,
        trigger="interval",
        seconds=SCRAPE_INTERVAL,
        id="scraper_job",
        replace_existing=True
    )
    
    scheduler.add_job(
        func=send_coupon,
        trigger="interval",
        seconds=POST_INTERVAL,
        id="poster_job",
        replace_existing=True
    )

    logger.info(f"Scheduler configured - Scraping every {SCRAPE_INTERVAL}s, Posting every {POST_INTERVAL}s")
    
    # 4) Start the scheduler (this will block)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown complete")
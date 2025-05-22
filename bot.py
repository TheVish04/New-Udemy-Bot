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
SCRAPE_AND_POST_INTERVAL = 600  # Scrape and post every 5 minutes (300 seconds)
BASE_REDIRECT_URL = 'https://udemyfreecoupons2080.blogspot.com'
PORT              = 10000  # health-check endpoint port
MAX_PAGES         = 1  # Only scrape first page
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

# ─── UDEMY SCRAPER ─────────────────────────────────────────
def fetch_course_details(slug):
    """
    Scrape Udemy course page with improved error handling and retries
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
        'DNT': '1',
        'Sec-GPC': '1',
    }
    
    # Add a longer random delay to avoid being rate-limited
    time.sleep(random.uniform(3, 8))
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        # Try to get the course page with timeout and retries
        retries = 2  # Reduced retries to avoid excessive blocking
        for attempt in range(retries):
            try:
                # Add longer delay between retry attempts
                if attempt > 0:
                    time.sleep(random.uniform(5, 10))
                    
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                
                # Check if we got a valid response
                if resp.status_code == 200 and len(resp.content) > 1000:
                    break
                else:
                    raise requests.RequestException(f"Invalid response: {resp.status_code}")
                    
            except Exception as e:
                if attempt < retries - 1:
                    wait_time = random.uniform(8, 15)  # Longer wait between retries
                    logger.warning(f"Request failed for {slug} (attempt {attempt+1}/{retries}): {e}. Waiting {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    continue
                raise
                
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Extract Open Graph metadata
        og_data = {}
        for meta in soup.find_all('meta', property=lambda x: x and x.startswith('og:')):
            og_data[meta['property']] = meta.get('content', '')
        
        # Get title, thumbnail, and description
        title = og_data.get('og:title', '').strip()
        thumbnail = og_data.get('og:image', '').strip()
        description = og_data.get('og:description', '').strip()
        
        # Fallback extraction if OG tags are missing
        if not title:
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
        
        if not description:
            desc_meta = soup.find('meta', attrs={'name': 'description'})
            if desc_meta:
                description = desc_meta.get('content', '').strip()
        
        # If any of the key data is still missing, use reasonable defaults
        if not title:
            title = slug.replace('-', ' ').title()
        if not description:
            description = f'Learn {title} with this comprehensive course!'
            
        # Clean up title and description
        title = title.replace(' | Udemy', '').strip()
        if len(title) > 100:
            title = title[:97] + '...'
            
        logger.info(f"Successfully scraped course: {slug}")
        
    except Exception as e:
        logger.warning(f"Scraping Udemy failed for {slug}: {str(e)}")
        # Use fallback values
        title = slug.replace('-', ' ').title()
        thumbnail = None
        description = f'Learn {title} with this comprehensive course!'

    # Close the session
    session.close()

    # Generate realistic random rating and students
    rating = round(random.uniform(4.2, 4.9), 1)  # Higher ratings look better
    students = random.randint(10000, 150000)      # More students look better

    return title, thumbnail, description, rating, students

# ─── TELEGRAM SENDER ───────────────────────────────────────
def send_coupon_to_telegram(slug, coupon):
    """Send a single coupon to Telegram"""
    try:
        # Create redirect URL
        redirect_url = f"{BASE_REDIRECT_URL}?udemy_url=" + urllib.parse.quote(
            f"https://www.udemy.com/course/{slug}/?couponCode={coupon}", safe=''
        )

        # Get course details with retry mechanism
        title, img, desc, rating, students = fetch_course_details(slug)

        # Generate a random number for enrolls left (between 50 and 2000)
        enrolls_left = random.randint(50, 2000)

        # Format the description to a maximum of 180 characters with ellipsis
        short_desc = (desc[:177] + '...') if len(desc) > 180 else desc

        # Build HTML caption with structured format
        rating_text = f"{rating:.1f}/5"
        students_text = f"{students:,}"
        enrolls_left_text = f"{enrolls_left:,}"

        # Clean up title to prevent HTML parsing issues
        title = title.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        
        caption = (
            f"📚✏️ <b>{title}</b>\n"
            f"🏅 <b>CERTIFIED COURSE</b>\n"
            f"⏰ LIMITED TIME ({enrolls_left_text} Enrolls Left)\n"
            f"⭐ {rating_text}    👩‍🎓 {students_text} students\n"
            f"🌐 English Language\n\n"
            f"💡 {short_desc}\n\n"
            f"🔗 <a href='{redirect_url}'>Enroll Now for FREE</a>"
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
                    logger.info(f"Successfully sent course card: {slug}")
                    return True  # Success
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
        logger.error(f"All Telegram API attempts failed for coupon: {slug}")
        return False
                
    except Exception as e:
        logger.error(f"Failed to send coupon {slug}: {e}", exc_info=True)
        return False

# ─── SCRAPE AND POST FUNCTION ──────────────────────────────
def scrape_and_post():
    """Scrape DiscUdemy and immediately post all found coupons"""
    retry_count = 0
    base_delay = 30  # Start with 30 seconds delay between retries
    
    while retry_count < MAX_RETRY_ATTEMPTS:
        scraper = None
        try:
            logger.info(f"Starting DiscUdemy scraper for page 1 (attempt {retry_count + 1}/{MAX_RETRY_ATTEMPTS})")
            
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
                
            # Convert to list of (slug, coupon_code) tuples
            coupons = [(item['slug'], item['coupon_code']) for item in results 
                       if item.get('slug') and item.get('coupon_code')]
                
            if not coupons:
                logger.warning(f"No valid coupons extracted from results on attempt {retry_count + 1}")
                retry_count += 1
                continue
                
            logger.info(f"Successfully scraped {len(coupons)} valid coupons")
            
            # Send each coupon immediately
            successful_posts = 0
            for slug, coupon_code in coupons:
                try:
                    if send_coupon_to_telegram(slug, coupon_code):
                        successful_posts += 1
                    # Small delay between posts to avoid rate limiting
                    time.sleep(random.uniform(8, 15))
                except Exception as e:
                    logger.error(f"Error sending coupon {slug}: {e}")
                    continue
            
            logger.info(f"Successfully posted {successful_posts}/{len(coupons)} coupons to Telegram")
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

# ─── MAIN ───────────────────────────────────────────────────
if __name__ == '__main__':
    # 1) Start health-check server
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info(f"Health-check listening on port {PORT}")

    # 2) Run initial scrape and post
    logger.info("Starting initial scrape and post...")
    scrape_and_post()

    # 3) Schedule the job to run every 5 minutes
    scheduler.add_job(
        func=scrape_and_post,
        trigger="interval",
        seconds=SCRAPE_AND_POST_INTERVAL,
        id="scrape_and_post_job",
        replace_existing=True
    )

    logger.info(f"Scheduler configured - Scraping and posting every {SCRAPE_AND_POST_INTERVAL} seconds ({SCRAPE_AND_POST_INTERVAL//60} minutes)")
    
    # 4) Start the scheduler (this will block)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown complete")
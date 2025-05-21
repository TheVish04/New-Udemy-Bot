import os
import time
import random
from urllib.parse import urlparse, parse_qs
import logging

# Configure logging
logger = logging.getLogger(__name__)

# Suppress TensorFlow-Lite delegate messages (if any)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"  # only errors

# Import Selenium components
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

class DiscUdemyScraper:
    BASE = "https://www.discudemy.com"
    LISTING = "/all/{}"

    def __init__(self, headless=True, timeout=15):
        # Suppress ChromeDriver logging by sending it to null
        try:
            service = Service(ChromeDriverManager(log_level=0).install())
            service.log_path = os.devnull
        except Exception as e:
            # If webdriver_manager fails (it might in container environments),
            # try with default Chrome path
            logger.warning(f"Webdriver manager failed, falling back to default Chrome path: {e}")
            service = Service()
            service.log_path = os.devnull

        opts = Options()
        if headless:
            opts.add_argument("--headless")
            opts.add_argument("--disable-dev-shm-usage")  # Required for Docker containers
            opts.add_argument("--no-sandbox")  # Required for Docker containers
            
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument('--disable-extensions')
        opts.add_argument('--disable-dev-shm-usage') 
        opts.add_argument('--disable-browser-side-navigation')
        opts.add_argument('--ignore-certificate-errors')
        opts.add_argument('--ignore-ssl-errors')
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )

        try:
            self.driver = webdriver.Chrome(service=service, options=opts)
            self.wait = WebDriverWait(self.driver, timeout)
            logger.info("Initialized Chrome WebDriver successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Chrome WebDriver: {e}")
            raise

    def close(self):
        if hasattr(self, 'driver') and self.driver:
            try:
                self.driver.quit()
                logger.info("Chrome WebDriver closed successfully")
            except Exception as e:
                logger.warning(f"Error closing WebDriver: {e}")

    def get_detail_urls(self, page_num: int):
        url = f"{self.BASE}{self.LISTING.format(page_num)}"
        try:
            self.driver.get(url)
            logger.info(f"Loaded listing page {page_num}")
        except WebDriverException as e:
            logger.error(f"Connection error loading listing page {page_num}: {e}")
            return []

        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.card-header")))
        except TimeoutException:
            logger.warning(f"Timeout loading listing page {page_num}")
            return []

        anchors = self.driver.find_elements(By.CSS_SELECTOR, "a.card-header")
        urls = []
        for a in anchors:
            try:
                href = a.get_attribute("href")
                if href and href.startswith(self.BASE) and "/go/" not in href:
                    urls.append(href)
            except Exception as e:
                logger.warning(f"Error extracting href: {e}")
                continue
                
        logger.info(f"Page {page_num}: Found {len(urls)} detail URLs")
        return list(set(urls))

    def extract_coupon(self, detail_url: str):
        logger.info(f"Processing detail URL: {detail_url}")
        try:
            self.driver.get(detail_url)
        except WebDriverException as e:
            logger.error(f"Connection reset on detail page {detail_url}: {e}")
            return None

        try:
            take = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.discBtn")))
        except TimeoutException:
            logger.warning(f"No Take Course button at {detail_url}")
            return None

        go_link = take.get_attribute("href") or ""
        if not go_link:
            try:
                take.click()
                time.sleep(2)
                go_link = self.driver.current_url
            except WebDriverException as e:
                logger.error(f"Error clicking Take Course at {detail_url}: {e}")
                return None
        else:
            try:
                self.driver.get(go_link)
            except WebDriverException as e:
                logger.error(f"Connection reset when navigating to coupon at {detail_url}: {e}")
                return None

        try:
            self.wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.ui.segment a[href*='udemy.com/course']")
            ))
        except TimeoutException:
            logger.warning(f"Timeout on go-page for {detail_url}")
            return None

        try:
            udemy_anchor = self.driver.find_element(
                By.CSS_SELECTOR, "div.ui.segment a[href*='udemy.com/course']"
            )
            udemy_url = udemy_anchor.get_attribute("href")
        except NoSuchElementException:
            logger.warning(f"No Udemy link at go-page for {detail_url}")
            return None

        parsed = urlparse(udemy_url)
        parts  = parsed.path.strip("/").split("/")
        slug   = parts[parts.index("course")+1] if "course" in parts else parts[-1]
        code   = parse_qs(parsed.query).get("couponCode", [""])[0]

        if not slug or not code:
            logger.warning(f"Missing slug or coupon code from {udemy_url}")
            return None
            
        logger.info(f"Extracted coupon - Slug: {slug}, Code: {code}")
        return {
            "detail_url":  detail_url,
            "go_link":     go_link,
            "udemy_url":   udemy_url,
            "slug":        slug,
            "coupon_code": code
        }

    def scrape(self, max_pages=5, delay_range=(1,3)):
        results = []
        try:
            for p in range(1, max_pages+1):
                try:
                    details = self.get_detail_urls(p)
                    for d in details:
                        try:
                            time.sleep(random.uniform(*delay_range))
                            info = self.extract_coupon(d)
                            if info and info['slug'] and info['coupon_code']:
                                results.append(info)
                        except Exception as e:
                            logger.error(f"Error extracting coupon from {d}: {e}", exc_info=True)
                            continue
                    # Wait between pages to avoid rate limiting
                    time.sleep(random.uniform(2,5))
                except Exception as e:
                    logger.error(f"Error scraping page {p}: {e}", exc_info=True)
                    continue
        except Exception as e:
            logger.error(f"Scraping process failed: {e}", exc_info=True)
            
        logger.info(f"Scraping complete. Retrieved {len(results)} valid coupons")
        return results
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
import time
import random
from urllib.parse import urlparse, parse_qs
import logging

logger = logging.getLogger(__name__)

class RealDiscountScraper:
    def __init__(self):
        self.base_url = "https://www.real.discount"
        self.courses_url = f"{self.base_url}/courses"
        self.driver = None
        self.wait = None
        
    def _setup_driver(self):
        """Setup Selenium WebDriver"""
        if self.driver:
            return  # Already setup
            
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--disable-popup-blocking")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
            
            self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
            self.wait = WebDriverWait(self.driver, 15)
            logger.info("RealDiscount WebDriver setup complete")
        except Exception as e:
            logger.error(f"Failed to setup WebDriver: {e}")
            raise
    
    def close(self):
        """Close the browser"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("RealDiscount WebDriver closed")
            except Exception as e:
                logger.warning(f"Error closing WebDriver: {e}")
            finally:
                self.driver = None
                self.wait = None
    
    def get_course_links(self, page_num=1):
        """Get all course links from the first page only"""
        try:
            self._setup_driver()
            
            logger.info(f"Fetching courses from Real.Discount page {page_num}...")
            
            # Navigate to the courses page
            self.driver.get(self.courses_url)
            
            # Wait for courses to load
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".MuiLink-root")))
            time.sleep(3)  # Additional wait for JS to render
            
            # Find all course cards with links
            course_links = []
            link_elements = self.driver.find_elements(By.CSS_SELECTOR, '.MuiLink-root[href^="/offer/"]')
            
            for link in link_elements:
                try:
                    href = link.get_attribute('href')
                    if href and '/offer/' in href:
                        # Convert relative URLs to absolute
                        if href.startswith('/'):
                            href = f"{self.base_url}{href}"
                        course_links.append(href)
                except Exception:
                    continue
            
            logger.info(f"Found {len(course_links)} courses on Real.Discount page {page_num}")
            return course_links
            
        except TimeoutException:
            logger.error(f"Timeout waiting for courses on Real.Discount page {page_num}")
            return []
        except Exception as e:
            logger.error(f"Error fetching courses from Real.Discount page {page_num}: {e}")
            return []
    
    def extract_coupon_details(self, course_url):
        """Extract the Udemy course slug and coupon code from a course page"""
        try:
            logger.info(f"Processing Real.Discount: {course_url}")
            self.driver.get(course_url)
            
            # Wait for the page to load
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.MuiButton-root')))
            time.sleep(2)  # Additional wait for JS rendering
            
            # Extract course title
            try:
                title_element = self.driver.find_element(By.CSS_SELECTOR, 'h1.MuiTypography-root')
                title = title_element.text.strip()
            except NoSuchElementException:
                title = "Unknown Title"
            
            # Extract description
            try:
                desc_elements = self.driver.find_elements(By.CSS_SELECTOR, 'p.MuiTypography-root, div.MuiTypography-body1')
                description = ""
                for elem in desc_elements:
                    text = elem.text.strip()
                    if text and len(text) > 50:  # Get substantial description
                        description = text
                        break
                if not description:
                    description = f'Learn {title} with this comprehensive course!'
            except:
                description = f'Learn {title} with this comprehensive course!'
            
            # Extract image
            try:
                img_elements = self.driver.find_elements(By.CSS_SELECTOR, 'img[src*="udemy"], img[src*="img-c"]')
                image_url = ""
                for img in img_elements:
                    src = img.get_attribute('src')
                    if src and ('udemy' in src or 'img-c' in src):
                        image_url = src
                        break
            except:
                image_url = ""
            
            # Multiple methods to find the "Get Course" button
            udemy_link = None
            
            # Method 1: Direct "Get Course" text approach
            try:
                get_course_elements = self.driver.find_elements(By.XPATH, '//*[text()="Get Course"]')
                for element in get_course_elements:
                    # Try to find parent or ancestor that is an <a> tag
                    current = element
                    for _ in range(4):  # Check up to 4 levels up
                        try:
                            current = current.find_element(By.XPATH, '..')
                            if current.tag_name == 'a':
                                link = current.get_attribute('href')
                                if link and "udemy.com" in link:
                                    udemy_link = link
                                    break
                        except:
                            break
                    if udemy_link:
                        break
            except Exception:
                pass
            
            # Method 2: Look for any Udemy links in buttons
            if not udemy_link:
                try:
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, '.MuiButtonBase-root')
                    for button in buttons:
                        # If the button itself is a link
                        if button.tag_name == 'a':
                            link = button.get_attribute('href')
                            if link and "udemy.com" in link:
                                udemy_link = link
                                break
                        
                        # Or if the button contains a link
                        try:
                            link_element = button.find_element(By.TAG_NAME, 'a')
                            link = link_element.get_attribute('href')
                            if link and "udemy.com" in link:
                                udemy_link = link
                                break
                        except:
                            pass
                except Exception:
                    pass
            
            # Method 3: Just look for any element with a Udemy link
            if not udemy_link:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="udemy.com"]')
                    for link_element in links:
                        link = link_element.get_attribute('href')
                        if link:
                            udemy_link = link
                            break
                except Exception:
                    pass
            
            if not udemy_link:
                logger.warning(f"No Udemy link found for: {course_url}")
                return None
            
            # Parse the URL to extract slug and coupon code
            parsed_url = urlparse(udemy_link)
            path_parts = parsed_url.path.strip('/').split('/')
            
            # Get the slug (course identifier from the URL path)
            if 'course' in path_parts:
                course_index = path_parts.index('course')
                if len(path_parts) > course_index + 1:
                    slug = path_parts[course_index + 1]
                else:
                    slug = "unknown"
            else:
                slug = path_parts[-1] if path_parts else "unknown"
            
            # Get coupon code from query parameters
            query_params = parse_qs(parsed_url.query)
            coupon_code = query_params.get('couponCode', [''])[0]
            
            if not slug or not coupon_code:
                logger.warning(f"Missing slug ({slug}) or coupon code ({coupon_code}) from {udemy_link}")
                return None
            
            return {
                'title': title,
                'description': description,
                'image_url': image_url,
                'detail_url': course_url,
                'udemy_url': udemy_link,
                'slug': slug,
                'coupon_code': coupon_code
            }
            
        except TimeoutException:
            logger.error(f"Timeout loading Real.Discount course page: {course_url}")
            return None
        except Exception as e:
            logger.error(f"Error processing Real.Discount {course_url}: {e}")
            return None
    
    def scrape(self, max_pages=1, delay_min=1, delay_max=3):
        """Scrape courses from Real.Discount (only first page)"""
        try:
            logger.info(f"Starting Real.Discount scraper for {max_pages} page(s)")
            
            all_courses = []
            
            # Only scrape the first page as requested
            for page in range(1, max_pages + 1):
                course_links = self.get_course_links(page)
                logger.info(f"Found {len(course_links)} courses on Real.Discount page {page}")
                
                for link in course_links:
                    # Add a random delay between requests
                    time.sleep(random.uniform(delay_min, delay_max))
                    course_data = self.extract_coupon_details(link)
                    if course_data:
                        all_courses.append(course_data)
                
                logger.info(f"Completed Real.Discount page {page}/{max_pages}")
                # Add a delay between pages (though we only do 1 page)
                if page < max_pages:
                    time.sleep(random.uniform(2, 5))
            
            logger.info(f"Real.Discount scraping complete. Found {len(all_courses)} valid coupons")
            return all_courses
            
        except Exception as e:
            logger.error(f"Error during Real.Discount scraping: {e}")
            return []
        finally:
            self.close()
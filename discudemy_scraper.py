import requests
import time
import random
import logging
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urljoin
import re

logger = logging.getLogger(__name__)

class DiscUdemyScraper:
    BASE = "https://www.discudemy.com"
    LISTING = "/all/{}"
    
    def __init__(self, timeout=15):
        self.timeout = timeout
        self.session = requests.Session()
        
        # Set browser-like headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        })
        
    def close(self):
        """Close the session"""
        if hasattr(self, 'session'):
            self.session.close()
            logger.info("HTTP session closed")
    
    def get_detail_urls(self, page_num: int):
        """Get course detail URLs from listing page"""
        url = f"{self.BASE}{self.LISTING.format(page_num)}"
        
        try:
            # Add random delay to avoid rate limiting
            time.sleep(random.uniform(1, 3))
            
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            logger.info(f"Loaded listing page {page_num}")
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find all course links
            course_links = soup.find_all('a', class_='card-header')
            urls = []
            
            for link in course_links:
                href = link.get('href')
                if href:
                    # Make sure it's a full URL
                    if href.startswith('/'):
                        href = urljoin(self.BASE, href)
                    
                    # Skip redirect links
                    if '/go/' not in href and href.startswith(self.BASE):
                        urls.append(href)
            
            # Remove duplicates
            urls = list(set(urls))
            logger.info(f"Page {page_num}: Found {len(urls)} detail URLs")
            return urls
            
        except Exception as e:
            logger.error(f"Error loading listing page {page_num}: {e}")
            return []
    
    def extract_coupon(self, detail_url: str):
        """Extract coupon information from course detail page"""
        logger.info(f"Processing detail URL: {detail_url}")
        
        try:
            # Add delay between requests
            time.sleep(random.uniform(2, 4))
            
            response = self.session.get(detail_url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find the "Take Course" button
            take_button = soup.find('a', class_='discBtn')
            if not take_button:
                logger.warning(f"No Take Course button found at {detail_url}")
                return None
            
            go_link = take_button.get('href')
            if not go_link:
                logger.warning(f"No href in Take Course button at {detail_url}")
                return None
            
            # Make sure it's a full URL
            if go_link.startswith('/'):
                go_link = urljoin(self.BASE, go_link)
            
            # Try to extract Udemy URL from the go_link parameters first
            try:
                parsed_go = urlparse(go_link)
                go_params = parse_qs(parsed_go.query)
                
                if 'go' in go_params:
                    possible_udemy_url = go_params['go'][0]
                    if 'udemy.com/course' in possible_udemy_url and 'couponCode=' in possible_udemy_url:
                        return self._parse_udemy_url(possible_udemy_url, detail_url, go_link)
            except Exception as e:
                logger.debug(f"Direct URL parsing failed: {e}")
            
            # If direct extraction failed, follow the go_link
            try:
                time.sleep(random.uniform(1, 2))
                go_response = self.session.get(go_link, timeout=self.timeout)
                go_response.raise_for_status()
                
                go_soup = BeautifulSoup(go_response.content, 'html.parser')
                
                # Look for Udemy link in various places
                udemy_link = None
                
                # Method 1: Look for direct link in anchor tags
                udemy_anchors = go_soup.find_all('a', href=re.compile(r'udemy\.com/course'))
                if udemy_anchors:
                    udemy_link = udemy_anchors[0].get('href')
                
                # Method 2: Look in the page source with regex
                if not udemy_link:
                    page_text = go_response.text
                    udemy_matches = re.findall(r'https://www\.udemy\.com/course/[^"\'>\s]+couponCode=[^"\'>\s]+', page_text)
                    if udemy_matches:
                        udemy_link = udemy_matches[0]
                
                # Method 3: Look for any Udemy course URL and try to construct coupon URL
                if not udemy_link:
                    course_matches = re.findall(r'https://www\.udemy\.com/course/([^/"\'>\s]+)', page_text)
                    coupon_matches = re.findall(r'couponCode=([^"\'>\s&]+)', page_text)
                    
                    if course_matches and coupon_matches:
                        slug = course_matches[0]
                        code = coupon_matches[0]
                        udemy_link = f"https://www.udemy.com/course/{slug}/?couponCode={code}"
                
                if udemy_link:
                    return self._parse_udemy_url(udemy_link, detail_url, go_link)
                else:
                    logger.warning(f"No Udemy link found in go page for {detail_url}")
                    return None
                    
            except Exception as e:
                logger.error(f"Error following go_link {go_link}: {e}")
                return None
                
        except Exception as e:
            logger.error(f"Error processing detail URL {detail_url}: {e}")
            return None
    
    def _parse_udemy_url(self, udemy_url, detail_url, go_link):
        """Parse Udemy URL to extract slug and coupon code"""
        try:
            parsed = urlparse(udemy_url)
            
            # Extract slug from path
            path_parts = parsed.path.strip("/").split("/")
            if "course" in path_parts:
                course_index = path_parts.index("course")
                if course_index + 1 < len(path_parts):
                    slug = path_parts[course_index + 1]
                else:
                    slug = None
            else:
                slug = path_parts[-1] if path_parts else None
            
            # Extract coupon code from query parameters
            query_params = parse_qs(parsed.query)
            code = query_params.get("couponCode", [""])[0]
            
            if not slug or not code:
                logger.warning(f"Missing slug ({slug}) or coupon code ({code}) from {udemy_url}")
                return None
            
            logger.info(f"Extracted coupon - Slug: {slug}, Code: {code}")
            return {
                "detail_url": detail_url,
                "go_link": go_link,
                "udemy_url": udemy_url,
                "slug": slug,
                "coupon_code": code
            }
            
        except Exception as e:
            logger.error(f"Error parsing Udemy URL {udemy_url}: {e}")
            return None
    
    def scrape(self, max_pages=5, delay_range=(2, 5)):
        """Scrape coupons from multiple pages"""
        results = []
        
        try:
            for page_num in range(1, max_pages + 1):
                try:
                    # Get detail URLs for this page
                    detail_urls = self.get_detail_urls(page_num)
                    
                    # Process each detail URL
                    for detail_url in detail_urls:
                        try:
                            coupon_info = self.extract_coupon(detail_url)
                            if coupon_info and coupon_info.get('slug') and coupon_info.get('coupon_code'):
                                results.append(coupon_info)
                            
                            # Random delay between requests
                            time.sleep(random.uniform(*delay_range))
                            
                        except Exception as e:
                            logger.error(f"Error processing {detail_url}: {e}")
                            continue
                    
                    # Delay between pages
                    if page_num < max_pages:
                        time.sleep(random.uniform(3, 7))
                        
                except Exception as e:
                    logger.error(f"Error processing page {page_num}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
        finally:
            self.close()
        
        logger.info(f"Scraping complete. Retrieved {len(results)} valid coupons")
        return results
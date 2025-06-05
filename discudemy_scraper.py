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
            time.sleep(random.uniform(1, 2))
            
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
            
            # Remove duplicates while preserving order
            seen = set()
            unique_urls = []
            for url in urls:
                if url not in seen:
                    seen.add(url)
                    unique_urls.append(url)
            
            logger.info(f"Page {page_num}: Found {len(unique_urls)} detail URLs")
            return unique_urls
            
        except Exception as e:
            logger.error(f"Error loading listing page {page_num}: {e}")
            return []
    
    def extract_coupon(self, detail_url: str):
        """Extract coupon information from course detail page"""
        try:
            # Add delay between requests
            time.sleep(random.uniform(1, 2))
            
            response = self.session.get(detail_url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract course information from the detail page
            course_info = {}
            
            # Extract title
            title_tag = soup.find('h1') or soup.find('h2') or soup.find('title')
            if title_tag:
                course_info['title'] = title_tag.get_text(strip=True)
            
            # Extract image
            img_tags = soup.find_all(['img', 'amp-img'])
            for img in img_tags:
                src = img.get('src', '')
                if src and ('udemy' in src or 'img-c' in src) and 'course' in src:
                    course_info['image_url'] = src
                    break
            
            # Extract description
            desc_elements = soup.find_all(['p', 'div'], class_=re.compile(r'description|summary|content'))
            if desc_elements:
                for elem in desc_elements:
                    text = elem.get_text(strip=True)
                    if text and len(text) > 50:  # Get a substantial description
                        course_info['description'] = text
                        break
            
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
                        result = self._parse_udemy_url(possible_udemy_url, detail_url, go_link)
                        if result:
                            result.update(course_info)
                            return result
            except Exception as e:
                logger.debug(f"Direct URL parsing failed: {e}")
            
            # If direct extraction failed, follow the go_link
            try:
                time.sleep(random.uniform(0.5, 1))
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
                    result = self._parse_udemy_url(udemy_link, detail_url, go_link)
                    if result:
                        result.update(course_info)
                        return result
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
    
    def scrape(self, max_pages=5, delay_range=(1, 2)):
        """Scrape coupons from multiple pages with course information from DiscUdemy"""
        results = []
        
        try:
            for page_num in range(1, max_pages + 1):
                try:
                    logger.info(f"Processing page {page_num}/{max_pages}")
                    
                    # Get detail URLs for this page
                    detail_urls = self.get_detail_urls(page_num)
                    
                    if not detail_urls:
                        logger.warning(f"No detail URLs found on page {page_num}")
                        continue
                    
                    # Process each detail URL
                    page_results = []
                    for i, detail_url in enumerate(detail_urls):
                        try:
                            logger.info(f"Processing course {i+1}/{len(detail_urls)} on page {page_num}")
                            coupon_info = self.extract_coupon(detail_url)
                            
                            if coupon_info and coupon_info.get('slug') and coupon_info.get('coupon_code'):
                                # Ensure we have at least basic course info
                                if not coupon_info.get('title'):
                                    coupon_info['title'] = coupon_info['slug'].replace('-', ' ').title()
                                if not coupon_info.get('description'):
                                    coupon_info['description'] = f"Learn {coupon_info['title']} with this comprehensive course!"
                                
                                page_results.append(coupon_info)
                                logger.info(f"✅ Successfully extracted: {coupon_info['slug']}")
                            else:
                                logger.warning(f"❌ Failed to extract valid coupon from {detail_url}")
                            
                            # Random delay between requests (shorter for single page monitoring)
                            if max_pages == 1:
                                time.sleep(random.uniform(0.5, 1))  # Faster for single page
                            else:
                                time.sleep(random.uniform(*delay_range))
                            
                        except Exception as e:
                            logger.error(f"Error processing {detail_url}: {e}")
                            continue
                    
                    results.extend(page_results)
                    logger.info(f"Page {page_num} complete. Got {len(page_results)} valid coupons")
                    
                    # Delay between pages (only if processing multiple pages)
                    if page_num < max_pages and max_pages > 1:
                        time.sleep(random.uniform(2, 4))
                        
                except Exception as e:
                    logger.error(f"Error processing page {page_num}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
        finally:
            self.close()
        
        logger.info(f"Scraping complete. Retrieved {len(results)} valid coupons from {max_pages} page(s)")
        return results
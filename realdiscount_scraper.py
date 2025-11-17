# realdiscount_scraper.py
import requests
import time
import random
import logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

logger = logging.getLogger("realdiscount")
logger.setLevel(logging.INFO)

class RealDiscountScraper:
    BASE = "https://www.real.discount"
    COURSES_PATH = "/courses"   # listing page

    def __init__(self, timeout=12, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        # realistic browser headers
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": self.BASE + "/",
        })

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def _sleep_polite(self, a=0.4, b=1.0):
        time.sleep(random.uniform(a, b))

    def _get_soup(self, url, tries=3):
        for attempt in range(1, tries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                text = resp.text
                # basic bot-detection check: if HTML challenge or empty, retry
                if not text or text.strip().startswith("<!DOCTYPE html>") and len(text) < 500:
                    # Could be a challenge page; wait and retry
                    logger.debug(f"Short/empty response for {url} attempt {attempt}")
                    time.sleep(0.8 * attempt)
                    continue
                return BeautifulSoup(text, "html.parser")
            except requests.RequestException as e:
                logger.debug(f"_get_soup attempt {attempt} failed for {url}: {e}")
                time.sleep(0.9 * attempt)
                continue
        raise RuntimeError(f"Failed to GET {url}")

    def _collect_offer_urls_from_listing(self):
        """
        Returns list of full URLs to offer pages from /courses (page 1 only)
        """
        url = urljoin(self.BASE, self.COURSES_PATH)
        try:
            soup = self._get_soup(url)
        except Exception as e:
            logger.warning(f"Error loading RealDiscount courses page: {e}")
            return []

        candidates = []
        # Typical card anchor structure: <a href="/offer/slug-12345"> ... </a>
        # Search for anchors under grid items
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            # only keep internal offer links
            if href.startswith("/offer/") or "/offer/" in href:
                full = urljoin(self.BASE, href)
                candidates.append(full)

        # dedupe preserving order
        seen = set()
        unique = []
        for u in candidates:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        logger.info(f"RealDiscount listing: found {len(unique)} candidate post URLs")
        return unique

    def _extract_from_offer(self, offer_url):
        """
        Parse an offer page to extract title, description, image, and final Udemy URL if present.
        Returns dict or None.
        """
        try:
            soup = self._get_soup(offer_url)
        except Exception as e:
            logger.warning(f"Error loading offer page {offer_url}: {e}")
            return None

        item = {
            "source": "realdiscount",
            "post_url": offer_url,
            "title": None,
            "image_url": None,
            "description": None,
            "udemy_url": None,
            "slug": None,
            "coupon_code": "",
            "is_free": False
        }

        # Title - look for h1
        h1 = soup.find("h1")
        if h1:
            item["title"] = h1.get_text(strip=True)

        # Image - look for meta og:image then img tags
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            item["image_url"] = og.get("content")
        else:
            img = soup.find("img")
            if img and img.get("src"):
                item["image_url"] = urljoin(self.BASE, img.get("src"))

        # Description - try .lead or first large paragraph
        desc = None
        sel = soup.select_one(".lead, .post-content, .entry-content, .desc")
        if sel:
            desc = sel.get_text(" ", strip=True)
        else:
            p = soup.find("p")
            if p:
                desc = p.get_text(" ", strip=True)
        if desc:
            item["description"] = desc

        # Slug from URL
        try:
            item["slug"] = offer_url.rstrip("/").split("/")[-1]
        except Exception:
            item["slug"] = None

        # Try to find the GET COURSE / coupon button which should contain or redirect to Udemy link
        # Common patterns: <a class="btn btn-success" href="...">Get Course</a>
        udemy_candidate = None
        # 1) anchors with text containing 'get' and 'course' or 'coupon'
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True).lower()
            href = a["href"].strip()
            if "udemy.com/course" in href:
                udemy_candidate = href
                break
            if "get course" in txt or "get coupon" in txt or "get coupon code" in txt or "get deal" in txt or "get code" in txt:
                udemy_candidate = urljoin(self.BASE, href)
                break

        # 2) If no candidate yet, try anchors with btn classes
        if not udemy_candidate:
            for cls in ("btn-success", "btn-lg", "offer-btn", "btn", "btn-primary"):
                a = soup.find("a", class_=lambda v: v and cls in v)
                if a and a.get("href"):
                    href = a.get("href").strip()
                    udemy_candidate = urljoin(self.BASE, href)
                    break

        # 3) Fallback: find any anchor that links to udemy
        if not udemy_candidate:
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if "udemy.com/course" in href:
                    udemy_candidate = href
                    break

        # If we found a candidate, follow it (allow redirects) to get final udemy url
        if udemy_candidate:
            try:
                # polite delay before following external link
                self._sleep_polite(0.4, 1.0)
                resp = self.session.get(udemy_candidate, timeout=self.timeout, allow_redirects=True)
                resp.raise_for_status()
                final = resp.url
                # If final contains udemy, use it
                if "udemy.com" in final:
                    item["udemy_url"] = final
                    # try to detect coupon param
                    parsed = urlparse(final)
                    if "couponCode" in parsed.query or "coupon" in parsed.query:
                        item["coupon_code"] = parsed.query
                    else:
                        # no coupon param -> treat as FREE heuristic
                        item["coupon_code"] = "FREE"
                        item["is_free"] = True
                else:
                    # final is not udemy (could be intermediary), still set fallback
                    item["udemy_url"] = final
            except Exception as e:
                logger.warning(f"Failed to follow coupon href {udemy_candidate}: {e}")
                # fallback: set candidate itself
                item["udemy_url"] = udemy_candidate
        else:
            logger.info(f"No coupon/get-course link found on {offer_url}")

        return item

    def scrape(self, max_pages=1):
        """
        Scrape the /courses listing (only page 1 supported). Returns list of course dicts.
        max_pages parameter kept for compatibility with other scrapers (but RealDiscount only has page 1).
        """
        try:
            offers = self._collect_offer_urls_from_listing()
        except Exception as e:
            logger.warning(f"RealDiscount listing error: {e}")
            offers = []

        results = []
        count = 0
        for offer_url in offers:
            # Only page 1 supported: break if we have covered a reasonable amount (but user requested all from page)
            try:
                course = self._extract_from_offer(offer_url)
                if course:
                    results.append(course)
                    count += 1
                # polite delay between offer pages
                self._sleep_polite(0.5, 1.2)
            except Exception as e:
                logger.debug(f"Error extracting {offer_url}: {e}")
                continue

        logger.info(f"RealDiscount scrape complete: {len(results)} items")
        return results

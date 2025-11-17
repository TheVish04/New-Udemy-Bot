# realdiscount_scraper.py
import requests
import time
import random
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class RealDiscountScraper:
    """
    Scraper for https://www.real.discount/courses (latest page).
    - scrape(max_posts=12) returns up to max_posts newest courses from the latest page.
    - Each returned item shape:
      {
        "source": "realdiscount",
        "post_url": "...",
        "title": "...",
        "image_url": "...",
        "description": "...",
        "udemy_url": "...",
        "slug": "...",
        "coupon_code": "...",
        "is_free": True/False
      }
    Notes:
    - We include expired coupons per your request.
    - We follow direct links from "Get Course" button which usually contain Udemy final URLs or a redirect to them.
    """

    BASE = "https://www.real.discount"

    def __init__(self, timeout=15, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        })

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def _sleep(self, a=0.5, b=1.0):
        time.sleep(random.uniform(a, b))

    def _get_soup(self, url, allow_redirects=True, tries=2):
        for attempt in range(tries):
            try:
                self._sleep()
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=allow_redirects)
                resp.raise_for_status()
                return BeautifulSoup(resp.content, "html.parser"), resp
            except Exception as e:
                logger.debug(f"_get_soup attempt {attempt+1} failed for {url}: {e}")
                time.sleep(0.8 + attempt)
                continue
        raise RuntimeError(f"Failed to GET {url}")

    def _collect_post_urls_from_latest(self):
        """
        Scrape the latest courses page and collect item links.
        We'll visit the listing page (https://www.real.discount/courses?page=1 or base /courses/)
        and collect article/item links (hrefs to offer pages).
        """
        list_url = f"{self.BASE}/courses"
        try:
            soup, _ = self._get_soup(list_url)
        except Exception as e:
            logger.error(f"Error loading RealDiscount courses page: {e}")
            return []

        # The site uses a grid; each card usually has an anchor to the offer detail page.
        candidates = []
        # Try to match anchors inside grid elements first
        # Many anchors contain '/offer/' or '/course/' in the path
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("/"):
                full = urljoin(self.BASE, href)
            else:
                full = href
            # keep only internal offer links with at least two path segments
            if full.startswith(self.BASE):
                path_parts = urlparse(full).path.strip("/").split("/")
                if len(path_parts) >= 2 and any(p in full for p in ("/offer/", "/courses/", "/course/")):
                    candidates.append(full)

        # fallback: keep anchors that look like offer pages but dedupe
        seen = set()
        unique = []
        for u in candidates:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        logger.info(f"RealDiscount listing: found {len(unique)} candidate post URLs")
        return unique

    def _find_get_course_link(self, soup):
        """
        On an offer page, find the 'Get Course' / 'Get Coupon' button href.
        The site uses a big button with an <a href="..."> containing the udemy link.
        """
        # 1) Look for obvious buttons
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True).lower()
            if "get course" in txt or "get coupon" in txt or "get deal" in txt or "get coupon code" in txt or "get tiket" in txt or "get offer" in txt:
                return urljoin(self.BASE, a["href"])

        # 2) Look for anchors that point directly to udemy.com
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "udemy.com/course" in href or "udemy.com" in href:
                return urljoin(self.BASE, href)

        # 3) Look for anchor with classes (Material UI buttons)
        # e.g., <a class="MuiButtonBase-root ..." href="...">Get Course</a>
        for a in soup.find_all("a", class_=True, href=True):
            cls = " ".join(a.get("class", []))
            if "MuiButton" in cls or "get-course" in cls or "offer-button" in cls or "button" in cls:
                # prefer those whose text contains 'get'
                if "get" in a.get_text(" ", strip=True).lower():
                    return urljoin(self.BASE, a["href"])

        # 4) fallback - first external link on page
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "real.discount" not in href:
                return href

        return None

    def _follow_and_get_final(self, href):
        """
        Follow href and return the final destination URL (may be Udemy).
        If following fails, return original.
        """
        try:
            self._sleep(0.2, 0.7)
            resp = self.session.get(href, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp.url
        except Exception as e:
            # still return href because final URL might be encoded in the href itself
            logger.warning(f"Failed to follow {href}: {e}")
            return href

    def _parse_udemy_url(self, url):
        """
        If URL points to udemy, extract slug and coupon code (couponCode param).
        Otherwise return (None, "", False).
        """
        try:
            parsed = urlparse(url)
            if "udemy.com" not in (parsed.netloc or ""):
                return (None, "", False)

            path_parts = parsed.path.strip("/").split("/")
            slug = None
            if "course" in path_parts:
                i = path_parts.index("course")
                if i + 1 < len(path_parts):
                    slug = path_parts[i + 1]
            elif path_parts:
                slug = path_parts[-1]

            qs = parse_qs(parsed.query)
            code = qs.get("couponCode") or qs.get("coupon") or qs.get("promo")
            if code:
                return (slug, code[0], False)

            # no code -> consider free
            return (slug, "FREE", True)
        except Exception:
            return (None, "", False)

    def _extract_from_post(self, post_url):
        """
        Visit offer page and extract data.
        """
        try:
            soup, _ = self._get_soup(post_url)
        except Exception as e:
            logger.error(f"Error loading RealDiscount post {post_url}: {e}")
            return None

        item = {
            "source": "realdiscount",
            "post_url": post_url,
            "title": None,
            "image_url": None,
            "description": None,
            "udemy_url": None,
            "slug": None,
            "coupon_code": "",
            "is_free": False
        }

        # Title: typical h1/h2
        h = soup.find(["h1", "h2"])
        if h:
            item["title"] = h.get_text(strip=True)

        # Image: try og:image first
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            item["image_url"] = og["content"]
        else:
            # fallback: first img inside article/main
            main = soup.find("main") or soup
            img = main.find("img")
            if img and img.get("src"):
                item["image_url"] = urljoin(self.BASE, img.get("src"))

        # Description: first large paragraph or og:description
        ogd = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if ogd and ogd.get("content"):
            item["description"] = ogd["content"]
        else:
            if main:
                ps = main.find_all("p")
                for p in ps:
                    txt = p.get_text(" ", strip=True)
                    if txt and len(txt) > 30:
                        item["description"] = txt
                        break

        # Find the Get Course link
        get_href = self._find_get_course_link(soup)
        if get_href:
            final = self._follow_and_get_final(get_href)
            item["udemy_url"] = final

            slug, code, is_free = self._parse_udemy_url(final)
            item["slug"] = slug
            item["coupon_code"] = code or ""
            item["is_free"] = bool(is_free)
        else:
            logger.info(f"No Get Course link found on {post_url}")

        # If title missing, derive from slug
        if not item["title"] and item["slug"]:
            item["title"] = item["slug"].replace("-", " ").title()

        return item

    def scrape(self, max_posts=12):
        """
        Scrape latest RealDiscount posts and return up to max_posts items.
        """
        try:
            posts = self._collect_post_urls_from_latest()
        except Exception as e:
            logger.error(f"Failed to collect RealDiscount posts: {e}")
            posts = []

        results = []
        count = 0
        for post in posts:
            if count >= max_posts:
                break
            try:
                course = self._extract_from_post(post)
                if course:
                    results.append(course)
                    count += 1
            except Exception as e:
                logger.error(f"Error extracting {post}: {e}")
                continue

        logger.info(f"RealDiscount scrape complete: {len(results)} items")
        return results

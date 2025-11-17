# couponscorpion_scraper.py (final, robust version)
import requests
import time
import random
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class CouponScorpionScraper:
    """
    Final robust scraper for couponscorpion.com (homepage latest Udemy posts).
    - scrape(max_posts=12) will return up to max_posts newest posts from homepage.
    - Each returned item is a dict:
      {
        "source": "couponscorpion",
        "post_url": ...,
        "title": ...,
        "image_url": ...,
        "description": ...,
        "udemy_url": ...,
        "slug": ...,
        "coupon_code": ...,
        "is_free": True/False
      }
    """
    BASE = "https://couponscorpion.com"

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

    # small polite random sleep to avoid hammering the site
    def _sleep(self, a=0.6, b=1.2):
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

    def _collect_post_urls_from_homepage(self):
        """
        Collect candidate post URLs from the homepage.
        Filters out obvious non-post links and tries to keep posts with >=2 path segments.
        """
        url = self.BASE + "/"
        try:
            soup, _ = self._get_soup(url)
        except Exception as e:
            logger.error(f"Error loading CouponScorpion homepage: {e}")
            return []

        candidates = []

        # Primary: look for article blocks with an anchor
        for article in soup.find_all("article"):
            try:
                a = article.find("a", href=True)
                if not a:
                    continue
                href = a["href"].strip()
                if href.startswith("/"):
                    href = urljoin(self.BASE, href)
                if href.startswith(self.BASE):
                    # require at least two path parts to exclude category/home links
                    parts = urlparse(href).path.strip("/").split("/")
                    if len(parts) >= 2:
                        candidates.append(href)
            except Exception:
                continue

        # Fallback: scan anchors in main content area
        if not candidates:
            main = soup.find("main") or soup
            for a in main.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith("/"):
                    href = urljoin(self.BASE, href)
                if href.startswith(self.BASE):
                    parts = urlparse(href).path.strip("/").split("/")
                    if len(parts) >= 2:
                        candidates.append(href)

        # dedupe while preserving order
        seen = set()
        unique = []
        for u in candidates:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        logger.info(f"CouponScorpion homepage: found {len(unique)} candidate post URLs")
        return unique

    def _find_coupon_button_on_post(self, soup):
        """
        Heuristics to find the GET COUPON CODE button href on a post page.
        Returns a fully qualified href (may be internal redirect script or direct udemy link).
        """
        # 1) anchors with obvious text
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True).lower()
            href = a["href"]
            if any(k in txt for k in ("get coupon", "get coupon code", "coupon code", "redeem", "buy now", "get deal", "get offer", "get course")):
                return urljoin(self.BASE, href)

        # 2) anchors that contain udemy link directly
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "udemy.com/course" in href:
                return urljoin(self.BASE, href)

        # 3) anchors whose href contains well-known redirect patterns
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(substr in href for substr in ("/scripts/udemy", "coupon.php", "/out/", "/go/", "coupon.php", "udemy-redirect")):
                return urljoin(self.BASE, href)

        # 4) anchors with classes used on many posts
        classes_try = ["btn_offer_block", "re_track_btn", "btn_offer", "btn-offer", "offer-btn", "rh_button_wrapper"]
        for cls in classes_try:
            a = soup.find("a", class_=lambda v: v and cls in v)
            if a and a.get("href"):
                return urljoin(self.BASE, a["href"])

        # 5) container-based lookup (price/button wrappers)
        containers = soup.find_all(class_=lambda v: v and ("price" in v or "button" in v or "offer" in v))
        for c in containers:
            a = c.find("a", href=True)
            if a:
                return urljoin(self.BASE, a["href"])

        # nothing found
        return None

    def _follow_and_get_final(self, href):
        """
        Follow href (allow_redirects=True) and return final destination URL.
        If following fails, return the original href.
        """
        try:
            self._sleep(0.4, 0.9)
            resp = self.session.get(href, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            final = resp.url
            return final
        except Exception as e:
            logger.warning(f"Failed to follow coupon href {href}: {e}")
            return href

    def _parse_udemy_url(self, url):
        """
        Parse a Udemy URL to extract slug and coupon code.
        Returns (slug, coupon_code, is_free)
        """
        try:
            parsed = urlparse(url)
            if "udemy.com" not in (parsed.netloc or ""):
                return (None, "", False)

            path_parts = parsed.path.strip("/").split("/")
            slug = None
            if "course" in path_parts:
                idx = path_parts.index("course")
                if idx + 1 < len(path_parts):
                    slug = path_parts[idx + 1]
            elif path_parts:
                slug = path_parts[-1]

            qs = parse_qs(parsed.query)
            code = qs.get("couponCode") or qs.get("coupon") or qs.get("promo") or qs.get("p")
            if code:
                return (slug, code[0], False)

            # If no coupon param present, treat as FREE (heuristic)
            return (slug, "FREE", True)
        except Exception:
            return (None, "", False)

    def _extract_from_post(self, post_url):
        """
        Open a post page and extract structured course info.
        """
        try:
            soup, _ = self._get_soup(post_url)
        except Exception as e:
            logger.error(f"Error opening post {post_url}: {e}")
            return None

        item = {
            "source": "couponscorpion",
            "post_url": post_url,
            "title": None,
            "image_url": None,
            "description": None,
            "udemy_url": None,
            "slug": None,
            "coupon_code": "",
            "is_free": False
        }

        # Title
        h = soup.find(["h1", "h2"])
        if h:
            item["title"] = h.get_text(strip=True)

        # Image - prefer og:image
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            item["image_url"] = og.get("content")
        else:
            fig = soup.find("figure")
            if fig:
                img = fig.find("img")
                if img and img.get("src"):
                    item["image_url"] = img.get("src")

        # Description - first non-empty paragraph inside content/article
        content = soup.find("div", class_=lambda v: v and ("entry-content" in v or "post-content" in v or "content" in v))
        if not content:
            content = soup.find("article")
        if content:
            for p in content.find_all("p"):
                txt = p.get_text(strip=True)
                if txt:
                    item["description"] = txt
                    break

        # Find coupon button href
        coupon_href = self._find_coupon_button_on_post(soup)
        if coupon_href:
            if coupon_href.startswith("/"):
                coupon_href = urljoin(self.BASE, coupon_href)
            item["coupon_link_raw"] = coupon_href

            # follow redirects to get final target (often udemy)
            final = self._follow_and_get_final(coupon_href)
            item["udemy_url"] = final

            slug, code, is_free = self._parse_udemy_url(final)
            item["slug"] = slug
            item["coupon_code"] = code or ""
            item["is_free"] = bool(is_free)
        else:
            logger.info(f"No coupon button found on post {post_url}")

        # fallback slug from post_url if still missing
        if not item["slug"]:
            try:
                item["slug"] = urlparse(post_url).path.strip("/").split("/")[-1]
            except Exception:
                item["slug"] = None

        if not item["title"] and item["slug"]:
            item["title"] = item["slug"].replace("-", " ").title()

        return item

    def scrape(self, max_posts=12):
        """
        Scrape homepage and return up to max_posts newest Udemy posts.
        """
        try:
            posts = self._collect_post_urls_from_homepage()
        except Exception as e:
            logger.error(f"Failed to collect posts from homepage: {e}")
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
                logger.error(f"Error extracting post {post}: {e}")
                continue

        logger.info(f"CouponScorpion scrape complete: {len(results)} items")
        return results

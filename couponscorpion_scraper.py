# couponscorpion_scraper.py (final, robust version – warnings fixed)
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
        url = self.BASE + "/"
        try:
            soup, _ = self._get_soup(url)
        except Exception as e:
            logger.error(f"Error loading CouponScorpion homepage: {e}")
            return []

        candidates = []

        for article in soup.find_all("article"):
            try:
                a = article.find("a", href=True)
                if not a:
                    continue
                href = a["href"].strip()
                if href.startswith("/"):
                    href = urljoin(self.BASE, href)
                if href.startswith(self.BASE):
                    parts = urlparse(href).path.strip("/").split("/")
                    if len(parts) >= 2:
                        candidates.append(href)
            except Exception:
                continue

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

        seen = set()
        unique = []
        for u in candidates:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        logger.info(f"CouponScorpion homepage: found {len(unique)} candidate post URLs")
        return unique

    def _find_coupon_button_on_post(self, soup):
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True).lower()
            href = a["href"]
            if any(k in txt for k in (
                "get coupon", "coupon code", "redeem", "get course", "offer")):
                return urljoin(self.BASE, href)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "udemy.com/course" in href:
                return urljoin(self.BASE, href)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(sub in href for sub in ("/scripts/udemy", "/out/", "/go/", "coupon.php")):
                return urljoin(self.BASE, href)

        return None

    def _follow_and_get_final(self, href):
        """Follow redirects to find real Udemy URL. Silenced warnings."""
        try:
            self._sleep(0.4, 0.9)
            resp = self.session.get(href, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp.url
        except Exception as e:
            logger.debug(f"Redirect follow failed for {href}: {e}")
            return href

    def _parse_udemy_url(self, url):
        try:
            parsed = urlparse(url)
            if "udemy.com" not in (parsed.netloc or ""):
                return (None, "", False)

            path_parts = parsed.path.strip("/").split("/")
            slug = None
            if "course" in path_parts:
                slug = path_parts[path_parts.index("course") + 1]

            qs = parse_qs(parsed.query)
            code = qs.get("couponCode") or qs.get("coupon") or qs.get("promo")

            if code:
                return (slug, code[0], False)
            return (slug, "FREE", True)
        except Exception:
            return (None, "", False)

    def _extract_from_post(self, post_url):
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

        h = soup.find(["h1", "h2"])
        if h:
            item["title"] = h.get_text(strip=True)

        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            item["image_url"] = og.get("content")

        content = soup.find("article") or soup
        for p in content.find_all("p"):
            txt = p.get_text(strip=True)
            if txt:
                item["description"] = txt
                break

        coupon_href = self._find_coupon_button_on_post(soup)
        if coupon_href:
            final = self._follow_and_get_final(coupon_href)
            item["udemy_url"] = final
            slug, code, is_free = self._parse_udemy_url(final)
            item["slug"] = slug
            item["coupon_code"] = code
            item["is_free"] = is_free

        if not item["slug"]:
            item["slug"] = urlparse(post_url).path.strip("/").split("/")[-1]

        if not item["title"]:
            item["title"] = item["slug"].replace("-", " ").title()

        return item

    def scrape(self, max_posts=12):
        try:
            posts = self._collect_post_urls_from_homepage()
        except Exception as e:
            logger.error(f"Failed to collect posts from homepage: {e}")
            posts = []

        results = []
        for post in posts[:max_posts]:
            try:
                course = self._extract_from_post(post)
                if course:
                    results.append(course)
            except Exception as e:
                logger.error(f"Error extracting post {post}: {e}")

        logger.info(f"CouponScorpion scrape complete: {len(results)} items")
        return results

import requests
import time
import random
import logging
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urljoin
import re

logger = logging.getLogger("discudemy")


class DiscUdemyScraper:
    BASE = "https://www.discudemy.com"
    LISTING = "/all/{}"

    def __init__(self, timeout=15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
        })

    def close(self):
        try:
            self.session.close()
        except:
            pass

    # --------------------------------------------------------
    # Get listing page -> course detail URLs
    # --------------------------------------------------------
    def get_detail_urls(self, page_num: int):
        url = f"{self.BASE}{self.LISTING.format(page_num)}"
        try:
            time.sleep(random.uniform(0.3, 0.8))
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"Listing error: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        links = []

        for a in soup.find_all("a", class_="card-header"):
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = urljoin(self.BASE, href)
            if "/go/" not in href and href.startswith(self.BASE):
                links.append(href)

        # de-dupe
        final = []
        seen = set()
        for x in links:
            if x not in seen:
                final.append(x)
                seen.add(x)

        return final

    # --------------------------------------------------------
    # Extract actual course page info
    # --------------------------------------------------------
    def extract_coupon(self, detail_url: str):
        try:
            time.sleep(random.uniform(0.3, 0.7))
            r = self.session.get(detail_url, timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"Detail page error: {e}")
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        course = {}

        # -------- TITLE --------
        title_tag = soup.find("h1") or soup.find("h2") or soup.title
        if title_tag:
            course["title"] = title_tag.get_text(strip=True)

        # -------- IMAGE (fixed: PERFECT extraction) --------
        # DiscUdemy always has Udemy thumbnails like:
        # https://img-c.udemycdn.com/course/<size>/xxxx.jpg
        img_url = None

        for img in soup.find_all(["img", "amp-img"]):
            src = img.get("src", "")
            if "img-c.udemycdn.com" in src and "course" in src:
                img_url = src
                break

        # fallback: look in meta tags
        if not img_url:
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                if "udemycdn" in og["content"]:
                    img_url = og["content"]

        # final fallback: None
        course["image_url"] = img_url

        # -------- DESCRIPTION --------
        desc = None
        blocks = soup.find_all(["p", "div"], class_=re.compile(r"desc|summary|content"))
        for b in blocks:
            txt = b.get_text(strip=True)
            if txt and len(txt) > 50:
                desc = txt
                break

        course["description"] = desc

        # -------- TAKE COURSE BUTTON --------
        btn = soup.find("a", class_="discBtn")
        if not btn or not btn.get("href"):
            logger.error(f"No Take Course button at {detail_url}")
            return None

        go_link = btn["href"]
        if go_link.startswith("/"):
            go_link = urljoin(self.BASE, go_link)

        # Try direct parameter extraction
        parsed = urlparse(go_link)
        params = parse_qs(parsed.query)

        if "go" in params:
            possible_url = params["go"][0]
            if "udemy.com/course" in possible_url:
                return self._finalize(possible_url, detail_url, go_link, course)

        # Follow go link
        try:
            time.sleep(random.uniform(0.2, 0.5))
            r2 = self.session.get(go_link, timeout=self.timeout)
            r2.raise_for_status()
        except:
            return None

        html = r2.text

        # Direct udemy URLs
        match = re.search(r"https://www\.udemy\.com/course/[^\"'>\s]+", html)
        if match:
            return self._finalize(match.group(0), detail_url, go_link, course)

        logger.warning("No udemy link found")
        return None

    # --------------------------------------------------------
    # Build output dictionary
    # --------------------------------------------------------
    def _finalize(self, udemy_url, detail_url, go_link, course):
        parsed = urlparse(udemy_url)
        parts = parsed.path.strip("/").split("/")

        slug = None
        if "course" in parts:
            slug = parts[parts.index("course") + 1]
        else:
            slug = parts[-1]

        qs = parse_qs(parsed.query)
        code = qs.get("couponCode", [""])[0]

        if not slug:
            return None

        course.update({
            "detail_url": detail_url,
            "go_link": go_link,
            "udemy_url": udemy_url,
            "slug": slug,
            "coupon_code": code if code else "FREE",
            "is_free": not bool(code),
        })

        if not course.get("description"):
            course["description"] = f"Learn {slug.replace('-', ' ').title()}!"

        if not course.get("title"):
            course["title"] = slug.replace("-", " ").title()

        return course

    # --------------------------------------------------------
    # MAIN SCRAPER
    # --------------------------------------------------------
    def scrape(self, max_pages=1):
        results = []

        for page in range(1, max_pages + 1):
            detail_urls = self.get_detail_urls(page)
            for u in detail_urls:
                item = self.extract_coupon(u)
                if item:
                    results.append(item)
                time.sleep(random.uniform(0.2, 0.5))

            if page < max_pages:
                time.sleep(random.uniform(1, 2))

        return results

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive'
        })

    def close(self):
        try:
            self.session.close()
        except:
            pass

    # -------------------------------------------------------
    # Get all detail links from /all/page
    # -------------------------------------------------------
    def get_detail_urls(self, page_num):
        url = f"{self.BASE}{self.LISTING.format(page_num)}"
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            cards = soup.select("a.card-header")
            urls = []

            for a in cards:
                href = a.get("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = urljoin(self.BASE, href)
                # filter only discudemy internal pages
                if href.startswith(self.BASE) and "/go/" not in href:
                    urls.append(href)

            # remove duplicates
            final_urls = []
            seen = set()
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    final_urls.append(u)

            logger.info(f"Page {page_num}: Found {len(final_urls)} detail URLs")
            return final_urls

        except Exception as e:
            logger.error(f"Error loading listing page {page_num}: {e}")
            return []

    # -------------------------------------------------------
    # Extract coupon from detail page
    # -------------------------------------------------------
    def extract_coupon(self, detail_url):
        try:
            r = self.session.get(detail_url, timeout=self.timeout)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # title
            title_tag = soup.find("h1") or soup.find("h2") or soup.title
            title = title_tag.get_text(strip=True) if title_tag else None

            # image extraction
            img = None
            for im in soup.find_all("img"):
                src = im.get("src", "")
                # detect udemy / course image format
                if "udemy" in src or "course" in src or "img-c" in src:
                    img = src
                    break

            # description (fallback safe)
            desc = None
            for p in soup.find_all("p"):
                txt = p.get_text(strip=True)
                if len(txt) > 60:
                    desc = txt
                    break

            # find the “Take Course” button
            btn = soup.find("a", class_="discBtn")
            if not btn or not btn.get("href"):
                logger.warning(f"No take-course button at {detail_url}")
                return None

            go_link = btn["href"]
            if go_link.startswith("/"):
                go_link = urljoin(self.BASE, go_link)

            # First try: parse udemy URL from ?go= param
            parsed = urlparse(go_link)
            q = parse_qs(parsed.query)

            if "go" in q:
                direct = q["go"][0]
                if "udemy.com/course" in direct:
                    return self.build_record(direct, detail_url, go_link, title, desc, img)

            # Otherwise follow go_link
            try:
                rr = self.session.get(go_link, timeout=self.timeout)
                rr.raise_for_status()
            except Exception as e:
                logger.warning(f"Go-link failed for {detail_url}: {e}")
                return None

            html = rr.text

            # regex detect whole coupon URL
            m = re.search(r"https://www\.udemy\.com/course/[^\"'>\s]+couponCode=[^\"'>\s]+", html)
            if m:
                return self.build_record(m.group(0), detail_url, go_link, title, desc, img)

            # detect normal course link
            m2 = re.search(r"https://www\.udemy\.com/course/[^\"'>\s]+", html)
            if m2:
                return self.build_record(m2.group(0), detail_url, go_link, title, desc, img)

            logger.warning(f"No udemy link for detail {detail_url}")
            return None

        except Exception as e:
            logger.error(f"Error parsing detail {detail_url}: {e}")
            return None

    # -------------------------------------------------------
    # Helper: build final dictionary
    # -------------------------------------------------------
    def build_record(self, udemy_url, detail_url, go_link, title, desc, img):
        parsed = urlparse(udemy_url)
        q = parse_qs(parsed.query)
        code = q.get("couponCode", [""])[0]

        slug = None
        parts = parsed.path.strip("/").split("/")
        if "course" in parts:
            i = parts.index("course")
            if i + 1 < len(parts):
                slug = parts[i + 1]
        if not slug:
            slug = parts[-1]

        if not slug:
            return None

        item = {
            "detail_url": detail_url,
            "go_link": go_link,
            "udemy_url": udemy_url,
            "slug": slug,
            "coupon_code": code if code else "FREE",
            "is_free": (code == ""),
            "title": title or slug.replace("-", " ").title(),
            "description": desc or ("Learn " + slug.replace("-", " ").title()),
            "image_url": img
        }
        if code:
            logger.info(f"Coupon course → {slug} | {code}")
        else:
            logger.info(f"Free course → {slug}")
        return item

    # -------------------------------------------------------
    # Main scrape (page 1 only)
    # -------------------------------------------------------
    def scrape(self, max_pages=1):
        all_items = []

        for page in range(1, max_pages + 1):
            logger.info(f"Processing page {page}/{max_pages}")
            detail_urls = self.get_detail_urls(page)
            if not detail_urls:
                continue

            for idx, durl in enumerate(detail_urls):
                logger.info(f"Course {idx+1}/{len(detail_urls)}")
                info = self.extract_coupon(durl)
                if info:
                    all_items.append(info)

                # tiny pause to avoid blocking; safe for render
                time.sleep(random.uniform(0.25, 0.6))

        logger.info(f"Scrape complete. {len(all_items)} courses total.")
        return all_items

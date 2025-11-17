# couponscorpion_scraper.py
import requests
import time
import random
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, quote

logger = logging.getLogger(__name__)

class CouponScorpionScraper:
    BASE = "https://couponscorpion.com"
    CATEGORY = "/category/udemy-free-100-discount/"  # main listing page

    def __init__(self, timeout=15, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })

    def close(self):
        try:
            self.session.close()
        except:
            pass

    def _get_soup(self, url):
        time.sleep(random.uniform(0.6, 1.2))
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return BeautifulSoup(r.content, "html.parser"), r

    def _extract_post_items_from_listing(self, page=1):
        """
        Return a list of post URLs found on the listing page.
        We only implement page=1 for your requirement.
        """
        if page <= 1:
            url = urljoin(self.BASE, self.CATEGORY)
        else:
            url = urljoin(self.BASE, f"{self.CATEGORY}page/{page}/")

        try:
            soup, _ = self._get_soup(url)
        except Exception as e:
            logger.error(f"Error loading CouponScorpion listing page {page}: {e}")
            return []

        posts = []
        # Posts appear inside <article class="col_item offer_grid ..."> or similar
        # We search for article elements that have post links
        for article in soup.find_all("article"):
            try:
                a = article.find("a", href=True)
                if not a: 
                    continue
                post_url = a["href"]
                # normalize to absolute
                if post_url.startswith("/"):
                    post_url = urljoin(self.BASE, post_url)
                posts.append(post_url)
            except Exception:
                continue

        # dedupe while preserving order
        seen = set()
        unique = []
        for p in posts:
            if p not in seen:
                seen.add(p)
                unique.append(p)

        logger.info(f"CouponScorpion listing: found {len(unique)} posts")
        return unique

    def _extract_from_post(self, post_url):
        """
        Follow a post page and extract:
        - post_url (original)
        - title
        - image_url (if any)
        - description (short)
        - final udemy_url (if available) and if coupon code present
        - slug (derived from udemy_url or post_url)
        - coupon_code or "FREE"
        - is_free boolean
        """
        try:
            soup, resp = self._get_soup(post_url)
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
        h = soup.find(["h1", "h2", "h3"])
        if h:
            item["title"] = h.get_text(strip=True)

        # Try to get a main image from the article (figure img or og:image)
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            item["image_url"] = og_image.get("content")
        else:
            img = soup.find("figure")
            if img:
                img_tag = img.find("img")
                if img_tag and img_tag.get("src"):
                    item["image_url"] = img_tag.get("src")

        # Short description - first paragraph in content
        content = soup.find("div", class_="entry-content")
        if not content:
            # fallback to main article area
            content = soup.find("article")
        if content:
            p = content.find("p")
            if p:
                text = p.get_text(strip=True)
                item["description"] = text if text else None

        # Find the coupon / button link.
        # In your screenshots button anchor had class btn_offer_block or href with '/scripts/udemy' etc.
        coupon_link = None

        # Common approach: find anchor buttons under .rh_button_wrapper or .rh_price_wrapper
        for selector in [
            ("a", {"class": lambda v: v and "btn_offer_block" in v}),
            ("a", {"class": lambda v: v and "btn_offer_block" in v}),
            ("a", {"href": lambda v: v and "/scripts/udemy" in v}),
            ("a", {"href": lambda v: v and "udemy.com/course" in v}),
            ("a", {"class": lambda v: v and "btn_offer_block" in v})
        ]:
            tagname, attrs = selector
            found = content.find(tagname, attrs=attrs) if content else None
            if found and found.get("href"):
                coupon_link = found.get("href")
                break

        # If not found inside content, search entire page for 'GET COUPON' anchors
        if not coupon_link:
            anchors = soup.find_all("a", href=True)
            for a in anchors:
                txt = a.get_text(" ", strip=True).lower()
                href = a["href"]
                if "get coupon" in txt or "get coupon code" in txt or "/scripts/udemy" in href or "udemy.com/course" in href:
                    coupon_link = href
                    break

        # Normalize coupon_link
        if coupon_link:
            if coupon_link.startswith("/"):
                coupon_link = urljoin(self.BASE, coupon_link)
            item["coupon_link_raw"] = coupon_link

            # If coupon_link is a direct udemy link, parse it directly
            if "udemy.com/course" in coupon_link:
                udemy_url = coupon_link
            else:
                # Follow the coupon_link to get the final redirect (follow once)
                try:
                    time.sleep(random.uniform(0.4, 1.0))
                    resp2 = self.session.get(coupon_link, timeout=self.timeout, allow_redirects=True)
                    final = resp2.url
                    udemy_url = final
                except Exception as e:
                    logger.warning(f"Could not follow coupon link {coupon_link}: {e}")
                    udemy_url = coupon_link  # fallback

            item["udemy_url"] = udemy_url

            # Try to parse udemy slug / coupon code
            try:
                parsed = urlparse(udemy_url)
                if "udemy.com" in parsed.netloc:
                    path_parts = parsed.path.strip("/").split("/")
                    slug = None
                    if "course" in path_parts:
                        idx = path_parts.index("course")
                        if idx + 1 < len(path_parts):
                            slug = path_parts[idx + 1]
                    else:
                        if path_parts:
                            slug = path_parts[-1]
                    item["slug"] = slug

                    qs = parse_qs(parsed.query)
                    code = qs.get("couponCode") or qs.get("coupon")
                    if code:
                        item["coupon_code"] = code[0]
                        item["is_free"] = False
                    else:
                        # If udemy url has no coupon, and price shows $0 on page, treat as free
                        # check for $0 on post page
                        price_node = soup.find(text=lambda x: x and "$0" in x)
                        if price_node:
                            item["coupon_code"] = "FREE"
                            item["is_free"] = True
                        else:
                            # If link or page suggests free, mark free
                            if "/free" in udemy_url or "free" in udemy_url.lower():
                                item["coupon_code"] = "FREE"
                                item["is_free"] = True
                else:
                    # Not directly udemy; keep coupon_link as-is and mark coupon_code empty
                    item["coupon_code"] = ""
            except Exception:
                pass

        else:
            logger.warning(f"No coupon link found for {post_url}")

        # Final fallback slug if still missing: derive from post_url
        if not item.get("slug"):
            try:
                parsed_post = urlparse(post_url)
                slug_candidate = parsed_post.path.strip("/").split("/")[-1]
                item["slug"] = slug_candidate
            except:
                item["slug"] = None

        # Normalize fields
        if item["title"] is None:
            item["title"] = item["slug"].replace("-", " ").title() if item.get("slug") else "Untitled Course"

        return item

    def scrape(self, max_pages=1):
        """
        Scrape first `max_pages` pages of couponscorpion category (we'll only run with max_pages=1).
        Returns a list of course dicts (same shape as DiscUdemy).
        """
        results = []

        for p in range(1, max_pages + 1):
            try:
                post_urls = self._extract_post_items_from_listing(page=p)
                for post in post_urls:
                    try:
                        course = self._extract_from_post(post)
                        if course:
                            results.append(course)
                    except Exception as e:
                        logger.error(f"Error extracting post {post}: {e}")
                        continue
            except Exception as e:
                logger.error(f"Error scraping page {p}: {e}")
                continue

        logger.info(f"CouponScorpion scrape complete: {len(results)} items")
        return results

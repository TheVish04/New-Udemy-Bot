# realdiscount_scraper.py
import requests
import logging
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

logger = logging.getLogger("realdiscount")

class RealDiscountScraper:
    API_TEMPLATE = "https://www.real.discount/api-web/all-courses/?page={page}"

    def __init__(self, timeout=12):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; UdemyBot/1.0)"
        })

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def _fetch_offer_udemy_link(self, offer_url):
        """Follow the offer page and try to extract the Udemy 'Get Course' button href"""
        try:
            resp = self.session.get(offer_url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # find any <a> whose href contains 'udemy.com/course'
            a = soup.find("a", href=lambda h: h and "udemy.com/course" in h)
            if a:
                return a.get("href")
        except Exception as e:
            logger.warning(f"Failed to follow offer page {offer_url}: {e}")
        return None

    def parse_api_item(self, item):
        """
        Convert API item (dict) into standard course dict used by bot.
        We'll try to get udemy_url from item fields, otherwise follow offer page.
        """
        try:
            title = item.get("title") or item.get("name") or ""
            slug = item.get("slug") or item.get("id") or title.replace(" ", "-").lower()
            thumbnail = item.get("thumbnail") or item.get("image") or ""
            description = item.get("description") or ""
            offer_url = item.get("offer_url") or item.get("url") or ""
            # Some API items may contain external vendor url; try these keys:
            udemy_url = item.get("external_link") or item.get("vendor_url") or None

            # If not found, follow the offer page
            if not udemy_url and offer_url:
                udemy_url = self._fetch_offer_udemy_link(offer_url)

            # If still not found, keep the offer_url as fallback
            if not udemy_url:
                udemy_url = offer_url

            coupon = item.get("coupon", "") or item.get("couponCode", "") or ""
            is_free = item.get("price", 1) == 0

            return {
                "title": title.strip(),
                "slug": slug,
                "image_url": thumbnail,
                "description": description.strip(),
                "udemy_url": udemy_url,
                "coupon_code": coupon if coupon else ("FREE" if is_free else ""),
                "is_free": is_free
            }
        except Exception as e:
            logger.exception(f"parse_api_item error: {e}")
            return None

    def scrape(self, max_pages=1):
        results = []
        try:
            for p in range(1, max_pages + 1):
                url = self.API_TEMPLATE.format(page=p)
                try:
                    resp = self.session.get(url, timeout=self.timeout)
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get("results") or data.get("data") or []
                    for it in items:
                        parsed = self.parse_api_item(it)
                        if parsed:
                            results.append(parsed)
                except Exception as e:
                    logger.warning(f"Error loading RealDiscount API page {p}: {e}")
                    continue
                time.sleep(0.6)
        finally:
            self.close()
        logger.info(f"RealDiscount scrape complete: {len(results)} items")
        return results

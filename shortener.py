import requests
import urllib.parse
import shelve
import logging
import time
import random

logger = logging.getLogger(__name__)
CACHE = "shortlinks.db"


class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_base = "https://shrinkme.io/api"

    def shorten(self, long_url):
        if not long_url:
            return long_url

        # ---------------- CACHE CHECK ----------------
        try:
            with shelve.open(CACHE) as db:
                if long_url in db:
                    return db[long_url]
        except:
            pass

        # ---------------- BUILD API URL ----------------
        api_url = (
            f"{self.api_base}"
            f"?api={self.api_key}"
            f"&url={urllib.parse.quote(long_url)}"
            f"&format=json"
        )

        # Try 4 times with exponential backoff
        for attempt in range(4):
            try:
                # Random delay to avoid detection
                delay = random.uniform(0.8, 1.6)
                time.sleep(delay)

                r = requests.get(api_url, timeout=15)

                # If empty → rate limited
                if not r.text.strip():
                    logger.warning(f"ShrinkMe empty response, attempt {attempt+1}")
                    time.sleep(1.5 + attempt)
                    continue

                # If HTML → Cloudflare / rate limit
                if r.text.strip().startswith("<!DOCTYPE"):
                    logger.warning("ShrinkMe returned HTML. Rate-limited. Retrying...")
                    time.sleep(1.5 + attempt)
                    continue

                # Attempt JSON
                data = r.json()

                short = (
                    data.get("shortenedUrl")
                    or data.get("url")
                    or data.get("short")
                )

                if short:
                    # Save in cache
                    try:
                        with shelve.open(CACHE) as db:
                            db[long_url] = short
                    except:
                        pass

                    return short

                logger.warning(f"ShrinkMe invalid JSON: {data}")

            except Exception as e:
                logger.warning(f"ShrinkMe attempt {attempt+1} failed: {e}")
                time.sleep(1.5 + attempt)

        # Fallback
        logger.error(f"ShrinkMe failed. Using original link: {long_url}")
        return long_url

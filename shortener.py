# shortener.py
import requests
import urllib.parse
import shelve
import logging
import time

logger = logging.getLogger(__name__)
CACHE = "shortlinks.db"

class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_base = "https://shrinkme.io/st"

    def shorten(self, long_url):
        if not long_url:
            return long_url

        # CACHE CHECK
        try:
            with shelve.open(CACHE) as db:
                if long_url in db:
                    return db[long_url]
        except:
            pass

        api_url = (
            f"{self.api_base}"
            f"?api={self.api_key}"
            f"&url={urllib.parse.quote(long_url)}"
        )

        for attempt in range(2):
            try:
                r = requests.get(api_url, timeout=10)
                data = r.json()

                short = data.get("shortenedUrl") or data.get("url")

                if short:
                    try:
                        with shelve.open(CACHE) as db:
                            db[long_url] = short
                    except:
                        pass

                    return short

            except Exception as e:
                logger.warning(f"ShrinkMe try {attempt+1} failed: {e}")
                time.sleep(1)

        logger.error(f"ShrinkMe failed, falling back to original for: {long_url}")
        return long_url

import requests
import urllib.parse
import logging
import time
import random

logger = logging.getLogger(__name__)

class ShrinkMe:
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_base = "https://shrinkme.io/api"

    def shorten(self, long_url):
        if not long_url:
            return long_url

        # If no API key → return original URL
        if not self.api_key:
            return long_url

        api_url = (
            f"{self.api_base}"
            f"?api={self.api_key}"
            f"&url={urllib.parse.quote(long_url)}"
            f"&format=json"
        )

        # Try up to 4 times
        for attempt in range(4):
            try:
                # small delay to avoid rate limit
                time.sleep(random.uniform(0.6, 1.2))

                r = requests.get(api_url, timeout=15)

                # Empty or HTML → retry
                if not r.text.strip() or r.text.strip().startswith("<!DOCTYPE"):
                    time.sleep(1 + attempt)
                    continue

                # Parse JSON
                data = r.json()

                short = (
                    data.get("shortenedUrl")
                    or data.get("url")
                    or data.get("short")
                )

                if short:
                    return short.replace("\\/", "/")

            except Exception:
                time.sleep(1 + attempt)

        # If failed → return original
        return long_url

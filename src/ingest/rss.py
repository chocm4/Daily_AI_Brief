
import time
import requests
import feedparser
from typing import List

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def _fetch(url: str, timeout: int = 20) -> bytes:
    r = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=timeout)
    r.raise_for_status()
    return r.content

def fetch_all_rss(feeds: List[dict], max_items_per_feed: int, log=None) -> List[dict]:
    all_entries = []
    for f in feeds:
        url = f["url"]
        name = f.get("name", url)
        weight = float(f.get("weight", 1.0))
        region = f.get("region", "")

        if log:
            log.info(f"Fetching RSS: {name}")

        try:
            content = _fetch(url)
            parsed = feedparser.parse(content)
            entries = parsed.entries[:max_items_per_feed] if getattr(parsed, "entries", None) else []
        except Exception as e:
            if log:
                log.warning(f"RSS fetch failed: {name} | {e}")
            entries = []

        for e in entries:
            all_entries.append({
                "feed_name": name,
                "feed_url": url,
                "feed_weight": weight,
                "feed_region": region,
                "entry": e,
            })

        time.sleep(0.15)  # 과도한 요청 방지

    return all_entries

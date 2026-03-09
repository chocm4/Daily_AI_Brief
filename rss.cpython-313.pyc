import datetime as dt
import re
from dateutil import parser as dtparser
from dateutil import tz

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)         # HTML 제거
    s = s.replace("\u00a0", " ")
    s = _WS_RE.sub(" ", s).strip()  # 공백 정리
    return s

def normalize_entries(raw_entries: list[dict], cfg: dict, log=None) -> list[dict]:
    include_desc = bool(cfg.get("rss", {}).get("include_description", False))
    desc_max = int(cfg.get("rss", {}).get("description_max_chars", 0) or 0)

    tzname = cfg.get("app", {}).get("timezone", "Asia/Seoul")
    local_tz = tz.gettz(tzname)

    lookback = int(cfg.get("rss", {}).get("lookback_hours", 0) or 0)
    now_local = dt.datetime.now(tz=local_tz)
    cutoff = (now_local - dt.timedelta(hours=lookback)) if lookback > 0 else None

    items = []
    for i, wrapped in enumerate(raw_entries, start=1):
        e = wrapped["entry"]
        title = getattr(e, "title", "").strip()
        link = getattr(e, "link", "").strip()
        published = getattr(e, "published", None) or getattr(e, "updated", None) or ""
        summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""

        try:
            published_dt = dtparser.parse(published) if published else None
            if published_dt:
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=local_tz)
                else:
                    published_dt = published_dt.astimezone(local_tz)
        except Exception:
            published_dt = None

        if cutoff and published_dt and published_dt < cutoff:
            continue
        if not title or not link:
            continue

        desc = _clean_text(summary)
        if include_desc and desc_max > 0 and len(desc) > desc_max:
            desc = desc[:desc_max].rstrip() + "…"
        if not include_desc:
            desc = ""

        items.append({
            "id": f"N{i}",
            "title": title,
            "link": link,
            "published": published_dt.isoformat() if published_dt else "",
            "source": wrapped.get("feed_name", ""),
            "region": wrapped.get("feed_region", ""),
            "source_weight": wrapped.get("feed_weight", 1.0),
            "description": desc,
            "tags": [],
            "score": 0.0,
        })

    if log:
        log.info(f"Normalized RSS items: {len(items)}")
    return items

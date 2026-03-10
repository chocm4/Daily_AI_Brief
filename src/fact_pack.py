import datetime as dt
from collections import defaultdict
from dateutil import parser as dtparser
from dateutil import tz

from src.market.risk_radar import build_risk_radar

KST = tz.gettz("Asia/Seoul")
UTC = tz.UTC


def _prev_business_day(d: dt.date) -> dt.date:
    x = d - dt.timedelta(days=1)
    while x.weekday() >= 5:
        x -= dt.timedelta(days=1)
    return x


def _to_kst(published_iso: str, region: str) -> dt.datetime | None:
    if not published_iso:
        return None
    try:
        t = dtparser.parse(published_iso)
    except Exception:
        return None

    if t.tzinfo is None:
        if (region or "").upper() == "KR":
            t = t.replace(tzinfo=KST)
        else:
            t = t.replace(tzinfo=UTC)

    return t.astimezone(KST)


def _latest_in_lookback(items: list[dict], region_hint: str, cutoff: dt.datetime) -> list[dict]:
    keep = []
    for n in items:
        t = _to_kst(n.get("published", ""), region_hint)
        if t is None:
            continue
        if t >= cutoff:
            keep.append(n)
    keep = sorted(
        keep,
        key=lambda x: _to_kst(x.get("published", ""), x.get("region", region_hint)) or dt.datetime(1970, 1, 1, tzinfo=KST),
        reverse=True,
    )
    return keep


def _slim_news(n: dict) -> dict:
    return {
        "id": n.get("id"),
        "event_id": n.get("event_id"),
        "cluster_id": n.get("cluster_id"),
        "title": n.get("title", ""),
        "representative_title": n.get("representative_title", n.get("title", "")),
        "representative_source": n.get("representative_source", n.get("source", "")),
        "source": n.get("source", ""),
        "published": n.get("published", ""),
        "url": n.get("link", ""),
        "tags": n.get("tags", []),
        "score": n.get("score", 0.0),
        "score_breakdown": n.get("score_breakdown", {}),
        "region": n.get("region", ""),
        "event_type": n.get("event_type", "general_market"),
        "secondary_event_types": n.get("secondary_event_types", []),
        "event_labels": n.get("event_labels", []),
        "impact_scope": n.get("impact_scope", "secondary"),
        "korea_relevance": n.get("korea_relevance", "low"),
        "korea_relevance_score": n.get("korea_relevance_score", 0.0),
        "korea_relevance_breakdown": n.get("korea_relevance_breakdown", {}),
        "cluster_mentions": n.get("cluster_mentions", n.get("mentions", 1)),
        "cluster_source_count": n.get("cluster_source_count", len(n.get("mention_sources") or [])),
        "entities": n.get("entities", []),
        "market_links": n.get("market_links", []),
        "mention_sources": n.get("mention_sources", []),
    }


def _build_event_pack(items: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for n in items or []:
        groups[str(n.get("event_id") or n.get("id"))].append(n)

    events = []
    for event_id, group in groups.items():
        rep = sorted(
            group,
            key=lambda x: (
                {"market_moving": 3, "sector_moving": 2, "secondary": 1}.get(x.get("impact_scope", "secondary"), 0),
                float(x.get("korea_relevance_score") or 0.0),
                int(x.get("cluster_mentions") or 1),
                float(x.get("score") or 0.0),
            ),
            reverse=True,
        )[0]
        sources = []
        for x in group:
            for s in (x.get("mention_sources") or []):
                if s not in sources:
                    sources.append(s)
        news_ids = [x.get("id") for x in group if x.get("id")]
        supporting_sources = [x.get("source") for x in group if x.get("source")]
        events.append(
            {
                "event_id": event_id,
                "theme": rep.get("representative_title") or rep.get("title"),
                "event_type": rep.get("event_type", "general_market"),
                "secondary_event_types": rep.get("secondary_event_types", []),
                "event_labels": rep.get("event_labels", []),
                "impact_scope": rep.get("impact_scope", "secondary"),
                "region": rep.get("region", ""),
                "summary": rep.get("title", ""),
                "news_ids": news_ids,
                "representative_news_id": rep.get("id"),
                "supporting_news_ids": [x for x in news_ids if x != rep.get("id")],
                "source_count": int(rep.get("cluster_source_count") or len(sources)),
                "mention_count": int(rep.get("cluster_mentions") or len(group)),
                "entities": rep.get("entities", []),
                "market_links": rep.get("market_links", []),
                "korea_relevance": rep.get("korea_relevance", "low"),
                "korea_relevance_score": rep.get("korea_relevance_score", 0.0),
                "korea_relevance_breakdown": rep.get("korea_relevance_breakdown", {}),
                "sources": sources,
                "representative_source": rep.get("source", ""),
                "supporting_sources": supporting_sources,
                "published": rep.get("published", ""),
                "score": rep.get("score", 0.0),
                "score_breakdown": rep.get("score_breakdown", {}),
            }
        )

    events.sort(
        key=lambda x: (
            {"market_moving": 3, "sector_moving": 2, "secondary": 1}.get(x.get("impact_scope", "secondary"), 0),
            float(x.get("korea_relevance_score") or 0.0),
            int(x.get("mention_count") or 0),
            int(x.get("source_count") or 0),
            float(x.get("score") or 0.0),
        ),
        reverse=True,
    )

    for idx, ev in enumerate(events, start=1):
        ev["driver_rank"] = idx
        ev["narrative_priority"] = "high" if idx <= 3 else ("medium" if idx <= 7 else "low")
    return events


def build_fact_pack(asof: dt.date, top_news: list[dict], market: list[dict] | None, cfg: dict) -> dict:
    market = market or []
    top_news = top_news or []

    news_kr = [n for n in top_news if (n.get("region") == "KR")]
    news_gl = [n for n in top_news if (n.get("region") != "KR")]

    news_kr_slim = [_slim_news(n) for n in news_kr]
    news_gl_slim = [_slim_news(n) for n in news_gl]

    now_kst = dt.datetime.now(tz=KST)
    lookback_hours = int((cfg.get("rss", {}) or {}).get("lookback_hours", 36))
    cutoff = now_kst - dt.timedelta(hours=lookback_hours)

    news_kr_session = _latest_in_lookback(news_kr_slim, "KR", cutoff)
    news_overnight = _latest_in_lookback(news_gl_slim, "GLOBAL", cutoff)

    if len(news_kr_session) < 10:
        news_kr_session = sorted(news_kr_slim, key=lambda x: float(x.get("score", 0.0)), reverse=True)[: min(18, len(news_kr_slim))]
    if len(news_overnight) < 10:
        news_overnight = sorted(news_gl_slim, key=lambda x: float(x.get("score", 0.0)), reverse=True)[: min(18, len(news_gl_slim))]

    prev_bd = _prev_business_day(asof)
    event_pack = _build_event_pack(top_news)

    return {
        "asof": asof.isoformat(),
        "timezone": cfg.get("app", {}).get("timezone", "Asia/Seoul"),
        "news_kr": news_kr_slim,
        "news_global": news_gl_slim,
        "events": event_pack,
        "events_top": event_pack[:12],
        "session": {
            "asof": asof.isoformat(),
            "kr_date_ref": prev_bd.isoformat(),
            "latest_now_kst": now_kst.isoformat(timespec="minutes"),
            "lookback_hours": lookback_hours,
            "latest_window_kst": f"{cutoff.strftime('%Y-%m-%d %H:%M')}~{now_kst.strftime('%Y-%m-%d %H:%M')}",
            "note": "최근 lookback_hours 기준으로 KR/GLOBAL 최신 뉴스를 구성",
        },
        "news_kr_session": news_kr_session,
        "news_overnight": news_overnight,
        "market": market,
        "risk_radar_rules": build_risk_radar(market),
        "notes": {
            "policy": "No verbatim quotes. Paraphrase. Attach source IDs to any news-driven claim.",
            "rss_only": True,
            "session_logic": "Rolling lookback (no fixed cut-offs).",
            "timezone_assumption_if_missing": "KR->KST, GLOBAL->UTC",
            "analyst_style": "관찰-해석-시사점 구조. 근거 없는 단정 금지.",
        },
    }

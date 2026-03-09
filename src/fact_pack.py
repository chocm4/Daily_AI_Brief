import datetime as dt
from dateutil import parser as dtparser
from dateutil import tz

from src.market.risk_radar import build_risk_radar

KST = tz.gettz("Asia/Seoul")
UTC = tz.UTC


def _prev_business_day(d: dt.date) -> dt.date:
    x = d - dt.timedelta(days=1)
    while x.weekday() >= 5:  # Sat/Sun
        x -= dt.timedelta(days=1)
    return x


def _to_kst(published_iso: str, region: str) -> dt.datetime | None:
    if not published_iso:
        return None
    try:
        t = dtparser.parse(published_iso)
    except Exception:
        return None

    # tz 없는 경우: KR은 KST로, 그 외는 UTC로 가정(현실적인 타협)
    if t.tzinfo is None:
        if (region or "").upper() == "KR":
            t = t.replace(tzinfo=KST)
        else:
            t = t.replace(tzinfo=UTC)

    return t.astimezone(KST)


def _in_window(t: dt.datetime | None, start: dt.datetime, end: dt.datetime) -> bool:
    if t is None:
        return False
    return (start <= t) and (t <= end)


def build_fact_pack(asof: dt.date, top_news: list[dict], market: list[dict] | None, cfg: dict) -> dict:
    market = market or []
    top_news = top_news or []

    # KR / GLOBAL 뉴스 분리
    news_kr = [n for n in top_news if (n.get("region") == "KR")]
    news_gl = [n for n in top_news if (n.get("region") != "KR")]

    # ---- slim: report/llm에 필요한 필드만 ----
    def slim(n: dict) -> dict:
        return {
            "id": n.get("id"),
            "title": n.get("title", ""),
            "source": n.get("source", ""),
            "published": n.get("published", ""),
            "url": n.get("link", ""),
            "tags": n.get("tags", []),
            "score": n.get("score", 0.0),
            "region": n.get("region", ""),
        }

    news_kr_slim = [slim(n) for n in news_kr]
    news_gl_slim = [slim(n) for n in news_gl]

    # =========================================================
    # fixed cut-off 제거, rolling lookback(최근 N시간) 적용
    # =========================================================
    now_kst = dt.datetime.now(tz=KST)
    lookback_hours = int((cfg.get("rss", {}) or {}).get("lookback_hours", 36))
    cutoff = now_kst - dt.timedelta(hours=lookback_hours)

    def latest_in_lookback(items: list[dict], region_hint: str) -> list[dict]:
        keep = []
        for n in items:
            t = _to_kst(n.get("published", ""), region_hint)
            if t is None:
                continue
            if t >= cutoff:
                keep.append(n)

        # 최신순 정렬
        keep = sorted(
            keep,
            key=lambda x: _to_kst(x.get("published", ""), x.get("region", region_hint))
            or dt.datetime(1970, 1, 1, tzinfo=KST),
            reverse=True,
        )
        return keep

    # ✅ 기존 이름은 유지하되 의미를 바꿈:
    # - news_kr_session: 최근 N시간 KR 뉴스(최신순)
    # - news_overnight: 최근 N시간 GLOBAL 뉴스(최신순)
    news_kr_session = latest_in_lookback(news_kr_slim, "KR")
    news_overnight = latest_in_lookback(news_gl_slim, "GLOBAL")

    # 너무 비면(피드 타임스탬프가 빈약한 경우) fallback: 점수순 상위로 채우기
    if len(news_kr_session) < 12:
        news_kr_session = sorted(news_kr_slim, key=lambda x: x.get("score", 0.0), reverse=True)[
            : min(20, len(news_kr_slim))
        ]
    if len(news_overnight) < 12:
        news_overnight = sorted(news_gl_slim, key=lambda x: x.get("score", 0.0), reverse=True)[
            : min(20, len(news_gl_slim))
        ]

    # (참고) prev_bd는 표시용 메타에만 사용
    prev_bd = _prev_business_day(asof)

    return {
        "asof": asof.isoformat(),
        "timezone": cfg.get("app", {}).get("timezone", "Asia/Seoul"),

        # 전체 top 뉴스(근거 목록)
        "news_kr": news_kr_slim,
        "news_global": news_gl_slim,

        # ✅ rolling 최신 묶음(보고서가 우선 사용)
        "session": {
            "asof": asof.isoformat(),
            "kr_date_ref": prev_bd.isoformat(),
            "latest_now_kst": now_kst.isoformat(timespec="minutes"),
            "lookback_hours": lookback_hours,
            "latest_window_kst": f"{cutoff.strftime('%Y-%m-%d %H:%M')}~{now_kst.strftime('%Y-%m-%d %H:%M')}",
            "note": "Fixed cut-off 없이 최근 lookback_hours 기준으로 KR/GLOBAL 최신 뉴스를 구성",
        },
        "news_kr_session": news_kr_session,
        "news_overnight": news_overnight,

        "market": market,
        "risk_radar_rules": build_risk_radar(market),

        "notes": {
            "policy": "No verbatim quotes. Paraphrase. Attach source IDs to any news-driven claim.",
            "rss_only": True,
            "session_logic": "Rolling lookback (no fixed 18:30/09:00 cut-offs). KR/GLOBAL both filtered by rss.lookback_hours.",
            "timezone_assumption_if_missing": "KR->KST, GLOBAL->UTC",
        },
    }
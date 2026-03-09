import copy

KR_EXCLUDE = [
    "야구", "축구", "농구", "배구", "골프", "선수", "감독", "경기",
    "드라마", "영화", "아이돌", "가수", "배우", "콘서트", "예능",
]
GLOBAL_EXCLUDE = [
    "celebrity", "movie", "tv", "drama", "football", "soccer", "baseball", "nba", "olympic",
]

KR_MARKET_HINT = [
    "코스피", "코스닥", "증시", "주가", "상장", "공모", "ipo", "실적", "어닝", "가이던스",
    "외국인", "기관", "개인", "수급", "공매도", "반도체", "2차전지", "배터리", "자동차", "조선",
    "방산", "바이오", "은행", "보험",
]
EN_MARKET_HINT = [
    "stock", "stocks", "shares", "equity", "earnings", "guidance", "nasdaq", "s&p", "dow", "index",
]
KR_BRIEF_HINT = [
    "한은", "금통위", "기재부", "통계청", "정부", "금융위", "금감원", "물가", "cpi", "ppi", "고용",
    "환율", "달러", "금리", "국채", "채권", "유가", "원자재", "금", "관세", "제재", "중동", "우크라", "중국", "미국", "연준",
]
EN_BRIEF_HINT = [
    "fed", "fomc", "ecb", "boj", "rate", "rates", "yield", "treasury", "inflation", "cpi", "ppi", "jobs",
    "payroll", "pmi", "gdp", "dollar", "fx", "oil", "crude", "gold", "commodity", "tariff", "sanction", "middle east", "china",
]


def _has_any(text: str, kws: list[str]) -> bool:
    t = (text or "").lower()
    return any((k or "").lower() in t for k in kws)


def _looks_non_market(text: str, region: str) -> bool:
    t = text or ""
    if region == "KR":
        return any(k in t for k in KR_EXCLUDE)
    tl = t.lower()
    return any(k in tl for k in GLOBAL_EXCLUDE)


def _is_event_keep(it: dict) -> bool:
    return str(it.get("impact_scope") or "") in {"market_moving", "sector_moving"}


def filter_market_news(items: list[dict], cfg: dict, log=None) -> list[dict]:
    before = len(items)
    kept = []
    for it in items:
        title = it.get("title", "")
        desc = it.get("description", "")
        text = f"{title} {desc}".strip()
        region = (it.get("region", "") or "").upper()
        region_key = "KR" if region.startswith("KR") else "GLOBAL"

        if _is_event_keep(it):
            kept.append(it)
            continue

        if _looks_non_market(text, region_key):
            if region_key == "KR" and not _has_any(text, KR_MARKET_HINT):
                continue
            if region_key == "GLOBAL" and not _has_any(text, EN_MARKET_HINT):
                continue
        kept.append(it)

    if log:
        log.info(f"MarketFilter(market_only): {before} -> {len(kept)}")
    return kept


def filter_brief_news(items: list[dict], cfg: dict, log=None) -> list[dict]:
    before = len(items)
    bonus = float((cfg.get("rss") or {}).get("brief_bonus", 0.25))
    kept = []
    for it in items:
        title = it.get("title", "")
        desc = it.get("description", "")
        text = f"{title} {desc}".strip()
        region = (it.get("region", "") or "").upper()
        region_key = "KR" if region.startswith("KR") else "GLOBAL"

        if _looks_non_market(text, region_key):
            if region_key == "KR":
                if not (_has_any(text, KR_MARKET_HINT) or _has_any(text, KR_BRIEF_HINT)):
                    continue
            else:
                if not (_has_any(text, EN_MARKET_HINT) or _has_any(text, EN_BRIEF_HINT)):
                    continue

        x = copy.deepcopy(it)
        has_hint = False
        if region_key == "KR":
            has_hint = _has_any(text, KR_BRIEF_HINT) or _has_any(text, KR_MARKET_HINT)
        else:
            has_hint = _has_any(text, EN_BRIEF_HINT) or _has_any(text, EN_MARKET_HINT)

        if has_hint:
            x["score"] = float(x.get("score", 0.0)) + bonus
        if str(x.get("impact_scope") or "") == "market_moving":
            x["score"] = float(x.get("score", 0.0)) + 0.4
        if str(x.get("korea_relevance") or "") == "high":
            x["score"] = float(x.get("score", 0.0)) + 0.25

        kept.append(x)

    if log:
        log.info(f"BriefFilter(macro+policy): {before} -> {len(kept)}")
    return kept

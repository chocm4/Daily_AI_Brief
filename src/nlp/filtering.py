
import copy

# ---- hard exclude (명백한 비시장) ----
KR_EXCLUDE = [
    "야구","축구","농구","배구","골프","선수","감독","경기",
    "드라마","영화","아이돌","가수","배우","콘서트","예능",
]
GLOBAL_EXCLUDE = [
    "celebrity","movie","tv","drama","football","soccer","baseball","nba","olympic",
]

# ---- market hint (주식/금융시장 직접) ----
KR_MARKET_HINT = [
    "코스피","코스닥","증시","주가","상장","공모","IPO","실적","어닝","가이던스",
    "외국인","기관","개인","수급","공매도",
    "반도체","2차전지","배터리","자동차","조선","방산","바이오","은행","보험",
]
EN_MARKET_HINT = [
    "stock","stocks","shares","equity","earnings","guidance",
    "nasdaq","s&p","dow","index",
]

# ---- macro/policy hint (주식 외 시황/퀀트 필수) ----
KR_BRIEF_HINT = [
    "한은","금통위","기재부","통계청","정부","금융위","금감원",
    "물가","CPI","PPI","고용","실업","임금","수출","수입","무역","경기",
    "환율","달러","금리","국채","채권","유가","원자재","금","은",
    "관세","제재","중동","우크라","중국","미국","연준","FOMC","정책",
]
EN_BRIEF_HINT = [
    "fed","fomc","ecb","boj","rate","rates","yield","treasury",
    "inflation","cpi","ppi","jobs","payroll","pmi","gdp",
    "dollar","fx","oil","crude","gold","commodity",
    "tariff","sanction","geopolitic","election","policy",
]

def _has_any(title: str, keywords: list[str]) -> bool:
    t = (title or "")
    tl = t.lower()
    for k in keywords:
        if k.lower() in tl if k.isascii() else k in t:
            return True
    return False

def _looks_non_market(title: str, region: str) -> bool:
    t = title or ""
    if region == "KR":
        return any(k in t for k in KR_EXCLUDE)
    else:
        tl = t.lower()
        return any(k in tl for k in GLOBAL_EXCLUDE)

def filter_market_news(items: list[dict], cfg: dict, log=None) -> list[dict]:
    """주식시장 중심(스포츠/연예 제거). 단, 제목에 시장 힌트가 섞여 있으면 살림."""
    before = len(items)
    kept = []
    for it in items:
        title = it.get("title","")
        region = (it.get("region","") or "").upper()
        region_key = "KR" if region.startswith("KR") else "GLOBAL"

        if _looks_non_market(title, region_key):
            # 비시장인데 시장 힌트가 없으면 제거
            if region_key == "KR" and not _has_any(title, KR_MARKET_HINT):
                continue
            if region_key == "GLOBAL" and not _has_any(title, EN_MARKET_HINT):
                continue
        kept.append(it)

    if log:
        log.info(f"MarketFilter(market_only): {before} -> {len(kept)}")
    return kept

def filter_brief_news(items: list[dict], cfg: dict, log=None) -> list[dict]:
    """
    'Market Brief'용: 주식 직접 + 매크로/정책/FX/원자재/금리 이슈를 넓게 포함.
    단, 스포츠/연예는 제거.
    그리고 brief 힌트가 있으면 score에 소폭 가산점(선별시 상위로 오도록).
    """
    before = len(items)
    bonus = float((cfg.get("rss") or {}).get("brief_bonus", 0.25))

    kept = []
    for it in items:
        title = it.get("title","")
        region = (it.get("region","") or "").upper()
        region_key = "KR" if region.startswith("KR") else "GLOBAL"

        # 명백 비시장은 제거(단, 시장/매크로 힌트 있으면 살림)
        if _looks_non_market(title, region_key):
            if region_key == "KR":
                if not (_has_any(title, KR_MARKET_HINT) or _has_any(title, KR_BRIEF_HINT)):
                    continue
            else:
                if not (_has_any(title, EN_MARKET_HINT) or _has_any(title, EN_BRIEF_HINT)):
                    continue

        # 복사본에 가산점 적용(원본 score 오염 방지)
        x = copy.deepcopy(it)
        has_hint = False
        if region_key == "KR":
            has_hint = _has_any(title, KR_BRIEF_HINT) or _has_any(title, KR_MARKET_HINT)
        else:
            has_hint = _has_any(title, EN_BRIEF_HINT) or _has_any(title, EN_MARKET_HINT)

        if has_hint:
            try:
                x["score"] = float(x.get("score", 0.0)) + bonus
            except Exception:
                pass

        kept.append(x)

    if log:
        log.info(f"BriefFilter(macro+policy): {before} -> {len(kept)}")
    return kept

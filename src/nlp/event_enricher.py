import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List, Tuple


EVENT_RULES = [
    ("central_bank", ["fomc", "fed", "ecb", "boj", "pboc", "한은", "한국은행", "금통위", "기준금리", "금리 결정"]),
    ("macro_data", ["cpi", "ppi", "payroll", "ism", "pmi", "gdp", "inflation", "retail sales", "고용", "물가", "수출", "수입", "무역", "경기선행", "산업생산"]),
    ("policy", ["tariff", "관세", "행정명령", "재정", "추경", "예산", "세제", "보조금", "policy", "정부", "금융위", "금감원"]),
    ("regulation", ["regulation", "probe", "antitrust", "반독점", "규제", "조사", "제재", "상장폐지", "공매도"]),
    ("geopolitics", ["war", "missile", "attack", "ceasefire", "sanction", "중동", "우크라", "러시아", "이스라엘", "중국", "대만", "북한"]),
    ("earnings", ["earnings", "guidance", "revenue", "profit", "실적", "영업이익", "순이익", "가이던스", "어닝"]),
    ("capital_flow", ["buyback", "dividend", "fund flow", "etf", "issuance", "자사주", "배당", "유상증자", "회사채", "수급", "순매수"]),
    ("supply_chain", ["semiconductor", "chip", "반도체", "foundry", "hbm", "ai", "battery", "배터리", "shipbuilding", "조선", "automaker", "자동차"]),
    ("credit_liquidity", ["liquidity", "default", "credit", "downgrade", "cp", "유동성", "신용", "부도", "차환"]),
    ("mna_deal", ["m&a", "merger", "acquisition", "deal", "인수", "합병", "매각", "지분 투자"]),
]

ENTITY_RULES = {
    "US": ["fed", "fomc", "us", "u.s.", "미국", "워싱턴", "트럼프", "연준"],
    "China": ["china", "중국", "beijing", "pboc"],
    "Japan": ["japan", "일본", "boj", "도쿄"],
    "Europe": ["ecb", "europe", "eu", "유럽"],
    "Korea": ["korea", "한국", "kospi", "kosdaq", "원화", "한은"],
    "Semiconductor": ["semiconductor", "chip", "반도체", "hbm", "foundry", "dram"],
    "AI": ["ai", "인공지능", "gpu"],
    "Auto": ["automaker", "ev", "자동차", "전기차"],
    "Battery": ["battery", "배터리", "2차전지"],
    "Shipbuilding": ["shipbuilding", "조선", "shipyard"],
    "Defense": ["defense", "방산", "missile"],
    "Biotech": ["biotech", "pharma", "바이오", "제약"],
    "Banks": ["bank", "은행", "insurance", "보험", "brokerage", "증권"],
    "FX": ["fx", "dollar", "won", "환율", "원화", "달러"],
    "Rates": ["yield", "treasury", "금리", "국채", "채권"],
    "Oil": ["oil", "crude", "wti", "brent", "유가", "원유"],
}

DIRECT_KR = [
    "한국", "korea", "kospi", "kosdaq", "원화", "won", "한국은행", "한은", "코스피", "코스닥",
    "삼성전자", "sk하이닉스", "현대차", "기아", "셀트리온", "naver", "카카오",
]
TRANSMISSION_KR = [
    "반도체", "semiconductor", "자동차", "battery", "배터리", "shipbuilding", "조선", "방산",
    "관세", "tariff", "수출", "무역", "연준", "fed", "금리", "yield", "중국", "china",
]
GENERIC_GLOBAL = ["oil", "원유", "달러", "dollar", "ecb", "boj", "유럽", "japan", "banks", "금융"]

PRIMARY_SOURCE_HINTS = {
    "Fed Press Releases (all)",
    "Fed Speeches & Testimony",
    "금융위원회 보도자료",
    "Reuters.com - Financial News",
    "Bloomberg - Market",
    "Bloomberg - Economy",
}


def _text(item: Dict[str, Any]) -> str:
    return f"{item.get('title', '')} {item.get('description', '')}".strip().lower()


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _classify_event_labels(text: str) -> Tuple[str, List[str]]:
    labels: List[str] = []
    for event_type, kws in EVENT_RULES:
        if any(kw.lower() in text for kw in kws):
            labels.append(event_type)
    if not labels:
        labels = ["general_market"]
    primary = labels[0]
    secondary = labels[1:3]
    return primary, secondary


def _extract_entities(text: str) -> List[str]:
    out = []
    for name, kws in ENTITY_RULES.items():
        if any(kw.lower() in text for kw in kws):
            out.append(name)
    return out[:8]


def _count_hits(text: str, keywords: List[str]) -> int:
    return sum(1 for kw in keywords if kw.lower() in text)


def _korea_relevance(text: str, region: str, cfg: Dict[str, Any] | None = None) -> tuple[float, str, Dict[str, float]]:
    sc = ((cfg or {}).get("news_scoring") or {})
    weights = (sc.get("korea_relevance_weights") or {})
    direct_w = float(weights.get("direct", 0.18))
    transmission_w = float(weights.get("transmission", 0.08))
    generic_w = float(weights.get("generic", 0.03))
    kr_region_base = float(weights.get("kr_region_base", 0.25))

    breakdown = {"region_base": 0.0, "direct": 0.0, "transmission": 0.0, "generic": 0.0}
    score = 0.0
    if (region or "").upper() == "KR":
        score += kr_region_base
        breakdown["region_base"] = kr_region_base

    direct_hits = _count_hits(text, DIRECT_KR)
    trans_hits = _count_hits(text, TRANSMISSION_KR)
    generic_hits = _count_hits(text, GENERIC_GLOBAL)

    if direct_hits:
        add = min(0.42, direct_hits * direct_w)
        score += add
        breakdown["direct"] = round(add, 3)
    if trans_hits:
        add = min(0.24, trans_hits * transmission_w)
        score += add
        breakdown["transmission"] = round(add, 3)
    if generic_hits:
        add = min(0.09, generic_hits * generic_w)
        score += add
        breakdown["generic"] = round(add, 3)

    score = min(score, 1.0)
    if score >= 0.66:
        label = "high"
    elif score >= 0.36:
        label = "medium"
    else:
        label = "low"
    return round(score, 3), label, breakdown


def _market_links(event_type: str, entities: List[str], text: str, secondary_types: List[str] | None = None) -> List[str]:
    secondary_types = secondary_types or []
    all_types = {event_type, *secondary_types}
    links: List[str] = []
    if all_types & {"central_bank", "macro_data"}:
        links.extend(["rates", "fx", "equities"])
    if all_types & {"policy", "regulation", "geopolitics"}:
        links.extend(["risk_sentiment", "exporters"])
    if all_types & {"earnings", "supply_chain"}:
        links.extend(["sector_rotation", "earnings_revision"])
    if "Semiconductor" in entities or "AI" in entities:
        links.extend(["semiconductors", "growth"])
    if "Banks" in entities:
        links.extend(["financials"])
    if "Oil" in entities:
        links.extend(["oil", "inflation"])
    if "FX" in entities:
        links.extend(["usdkrw"])
    if "Rates" in entities:
        links.extend(["bond_yields"])
    if "배당" in text or "buyback" in text or "자사주" in text:
        links.extend(["shareholder_return"])
    seen: List[str] = []
    for x in links:
        if x not in seen:
            seen.append(x)
    return seen[:8]


def _impact_scope(event_type: str, korea_label: str, mentions: int, source_count: int, market_links: List[str]) -> str:
    strong_event = event_type in {"central_bank", "macro_data", "policy", "geopolitics", "credit_liquidity"}
    if strong_event and (korea_label in {"high", "medium"} or len(market_links) >= 3):
        return "market_moving"
    if korea_label == "high" and (mentions >= 2 or source_count >= 2):
        return "market_moving"
    if event_type in {"earnings", "supply_chain", "capital_flow", "regulation", "mna_deal"}:
        return "sector_moving"
    return "secondary"


def _cluster_groups(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        groups[str(it.get("cluster_id") or it.get("id") or "NA")].append(it)
    return groups


def _latest_dt(group: List[Dict[str, Any]]) -> dt.datetime | None:
    latest = None
    for x in group:
        t = _parse_dt(x.get("published"))
        if t and (latest is None or t > latest):
            latest = t
    return latest


def _source_priority(source: str) -> int:
    return 1 if source in PRIMARY_SOURCE_HINTS else 0


def _choose_representative(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    def _rank(x: Dict[str, Any]):
        published = _parse_dt(x.get("published"))
        ts = published.timestamp() if published else 0.0
        return (
            _source_priority(str(x.get("source") or "")),
            float(x.get("score") or 0.0),
            ts,
            int(x.get("mentions") or 1),
        )

    return sorted(group, key=_rank, reverse=True)[0]


def pre_enrich_event_metadata(items: List[Dict[str, Any]], cfg: Dict[str, Any] | None = None, log=None) -> List[Dict[str, Any]]:
    if not items:
        return items

    cluster_groups = _cluster_groups(items)
    cluster_meta: Dict[str, Dict[str, Any]] = {}
    sorted_clusters = sorted(cluster_groups.items(), key=lambda kv: (-max(int(x.get("mentions", 1) or 1) for x in kv[1]), kv[0]))

    for idx, (cluster_id, group) in enumerate(sorted_clusters, start=1):
        mentions = sum(int(x.get("mentions", 1) or 1) for x in group)
        sources = sorted({str(s).strip() for x in group for s in (x.get("mention_sources") or [x.get("source")]) if str(s).strip()})
        representative = group[0]
        latest_dt = _latest_dt(group)
        text = " ".join(_text(x) for x in group)
        event_type, secondary_types = _classify_event_labels(text)
        entities = _extract_entities(text)
        kr_score, kr_label, kr_breakdown = _korea_relevance(text, representative.get("region", ""), cfg=cfg)
        market_links = _market_links(event_type, entities, text, secondary_types)
        cluster_meta[cluster_id] = {
            "event_id": f"E{idx:02d}",
            "cluster_id": cluster_id,
            "representative_title": representative.get("title", ""),
            "event_type": event_type,
            "secondary_event_types": secondary_types,
            "event_labels": [event_type] + secondary_types,
            "entities": entities,
            "market_links": market_links,
            "cluster_mentions": mentions,
            "cluster_source_count": len(sources),
            "cluster_sources": sources,
            "cluster_latest_published": latest_dt.isoformat() if latest_dt else representative.get("published", ""),
            "korea_relevance_score": kr_score,
            "korea_relevance": kr_label,
            "korea_relevance_breakdown": kr_breakdown,
            "impact_scope": _impact_scope(event_type, kr_label, mentions, len(sources), market_links),
        }

    enriched: List[Dict[str, Any]] = []
    for it in items:
        meta = cluster_meta[str(it.get("cluster_id") or it.get("id") or "NA")]
        x = dict(it)
        x.update(meta)
        enriched.append(x)

    if log:
        log.info(f"Event pre-enrichment done: clusters={len(cluster_meta)}, items={len(enriched)}")
    return enriched


def finalize_event_metadata(items: List[Dict[str, Any]], cfg: Dict[str, Any] | None = None, log=None) -> List[Dict[str, Any]]:
    if not items:
        return items

    cluster_groups = _cluster_groups(items)
    sorted_clusters = sorted(
        cluster_groups.items(),
        key=lambda kv: (
            -max(float(x.get("score") or 0.0) for x in kv[1]),
            -max(int(x.get("cluster_mentions") or x.get("mentions") or 1) for x in kv[1]),
            kv[0],
        ),
    )

    cluster_meta: Dict[str, Dict[str, Any]] = {}
    for idx, (cluster_id, group) in enumerate(sorted_clusters, start=1):
        representative = _choose_representative(group)
        mentions = max(int(representative.get("cluster_mentions") or 0), sum(int(x.get("mentions", 1) or 1) for x in group))
        sources = sorted({str(s).strip() for x in group for s in (x.get("mention_sources") or [x.get("source")]) if str(s).strip()})
        latest_dt = _latest_dt(group)
        text = " ".join(_text(x) for x in group)
        event_type = str(representative.get("event_type") or "general_market")
        secondary_types = list(representative.get("secondary_event_types") or [])
        entities = list(representative.get("entities") or _extract_entities(text))
        kr_score, kr_label, kr_breakdown = _korea_relevance(text, representative.get("region", ""), cfg=cfg)
        market_links = _market_links(event_type, entities, text, secondary_types)
        impact_scope = _impact_scope(event_type, kr_label, mentions, len(sources), market_links)
        cluster_meta[cluster_id] = {
            "event_id": f"E{idx:02d}",
            "cluster_id": cluster_id,
            "representative_title": representative.get("title", ""),
            "representative_source": representative.get("source", ""),
            "event_type": event_type,
            "secondary_event_types": secondary_types,
            "event_labels": [event_type] + secondary_types,
            "entities": entities,
            "market_links": market_links,
            "cluster_mentions": mentions,
            "cluster_source_count": len(sources),
            "cluster_sources": sources,
            "cluster_latest_published": latest_dt.isoformat() if latest_dt else representative.get("published", ""),
            "korea_relevance_score": kr_score,
            "korea_relevance": kr_label,
            "korea_relevance_breakdown": kr_breakdown,
            "impact_scope": impact_scope,
        }

    enriched: List[Dict[str, Any]] = []
    for it in items:
        meta = cluster_meta[str(it.get("cluster_id") or it.get("id") or "NA")]
        x = dict(it)
        x.update(meta)
        enriched.append(x)

    if log:
        log.info(f"Event finalization done: clusters={len(cluster_meta)}, items={len(enriched)}")
    return enriched


# backward compatibility

def enrich_event_metadata(items: List[Dict[str, Any]], log=None) -> List[Dict[str, Any]]:
    return pre_enrich_event_metadata(items, cfg=None, log=log)

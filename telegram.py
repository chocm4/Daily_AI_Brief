import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List


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

KOREA_HIGH = [
    "한국", "korea", "kospi", "kosdaq", "원화", "won", "반도체", "semiconductor",
    "자동차", "battery", "배터리", "shipbuilding", "조선", "방산", "중국", "china",
    "연준", "fed", "금리", "yield", "tariff", "관세", "수출", "무역",
]
KOREA_MED = ["oil", "원유", "달러", "dollar", "ecb", "boj", "유럽", "japan", "banks", "금융"]


def _text(item: Dict[str, Any]) -> str:
    return f"{item.get('title','')} {item.get('description','')}".strip().lower()


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def _classify_event_type(text: str) -> str:
    for event_type, kws in EVENT_RULES:
        if any(kw.lower() in text for kw in kws):
            return event_type
    return "general_market"


def _extract_entities(text: str) -> List[str]:
    out = []
    for name, kws in ENTITY_RULES.items():
        if any(kw.lower() in text for kw in kws):
            out.append(name)
    return out[:8]


def _korea_relevance(text: str, region: str) -> tuple[float, str]:
    score = 0.0
    if (region or '').upper() == 'KR':
        score += 0.35
    for kw in KOREA_HIGH:
        if kw.lower() in text:
            score += 0.12
    for kw in KOREA_MED:
        if kw.lower() in text:
            score += 0.06
    score = min(score, 1.0)
    if score >= 0.66:
        label = 'high'
    elif score >= 0.36:
        label = 'medium'
    else:
        label = 'low'
    return round(score, 3), label


def _market_links(event_type: str, entities: List[str], text: str) -> List[str]:
    links = []
    if event_type in {'central_bank', 'macro_data'}:
        links.extend(['rates', 'fx', 'equities'])
    if event_type in {'policy', 'regulation', 'geopolitics'}:
        links.extend(['risk_sentiment', 'exporters'])
    if event_type in {'earnings', 'supply_chain'}:
        links.extend(['sector_rotation', 'earnings_revision'])
    if 'Semiconductor' in entities or 'AI' in entities:
        links.extend(['semiconductors', 'growth'])
    if 'Banks' in entities:
        links.extend(['financials'])
    if 'Oil' in entities:
        links.extend(['oil', 'inflation'])
    if 'FX' in entities:
        links.extend(['usdkrw'])
    if 'Rates' in entities:
        links.extend(['bond_yields'])
    if '배당' in text or 'buyback' in text or '자사주' in text:
        links.extend(['shareholder_return'])
    seen = []
    for x in links:
        if x not in seen:
            seen.append(x)
    return seen[:6]


def _impact_scope(event_type: str, korea_label: str, mentions: int, source_count: int) -> str:
    if event_type in {'central_bank', 'macro_data', 'policy', 'geopolitics', 'credit_liquidity'}:
        return 'market_moving'
    if korea_label == 'high' and (mentions >= 2 or source_count >= 2):
        return 'market_moving'
    if event_type in {'earnings', 'supply_chain', 'capital_flow', 'regulation', 'mna_deal'}:
        return 'sector_moving'
    return 'secondary'


def enrich_event_metadata(items: List[Dict[str, Any]], log=None) -> List[Dict[str, Any]]:
    if not items:
        return items

    cluster_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        cluster_groups[str(it.get('cluster_id') or it.get('id') or 'NA')].append(it)

    cluster_meta: Dict[str, Dict[str, Any]] = {}
    sorted_clusters = sorted(cluster_groups.items(), key=lambda kv: (-max(int(x.get('mentions', 1) or 1) for x in kv[1]), kv[0]))

    for idx, (cluster_id, group) in enumerate(sorted_clusters, start=1):
        mentions = sum(int(x.get('mentions', 1) or 1) for x in group)
        sources = sorted({str(s).strip() for x in group for s in (x.get('mention_sources') or [x.get('source')]) if str(s).strip()})
        representative = sorted(group, key=lambda x: (float(x.get('score') or 0.0), int(x.get('mentions', 1) or 1)), reverse=True)[0]
        latest_dt = None
        for x in group:
            t = _parse_dt(x.get('published'))
            if t and (latest_dt is None or t > latest_dt):
                latest_dt = t
        text = ' '.join(_text(x) for x in group)
        event_type = _classify_event_type(text)
        entities = _extract_entities(text)
        kr_score, kr_label = _korea_relevance(text, representative.get('region', ''))
        market_links = _market_links(event_type, entities, text)
        cluster_meta[cluster_id] = {
            'event_id': f'E{idx:02d}',
            'cluster_id': cluster_id,
            'representative_title': representative.get('title', ''),
            'event_type': event_type,
            'entities': entities,
            'market_links': market_links,
            'cluster_mentions': mentions,
            'cluster_source_count': len(sources),
            'cluster_sources': sources,
            'cluster_latest_published': latest_dt.isoformat() if latest_dt else representative.get('published', ''),
            'korea_relevance_score': kr_score,
            'korea_relevance': kr_label,
            'impact_scope': _impact_scope(event_type, kr_label, mentions, len(sources)),
        }

    enriched = []
    for it in items:
        meta = cluster_meta[str(it.get('cluster_id') or it.get('id') or 'NA')]
        x = dict(it)
        x.update(meta)
        enriched.append(x)

    if log:
        log.info(f"Event enrichment done: clusters={len(cluster_meta)}, items={len(enriched)}")
    return enriched

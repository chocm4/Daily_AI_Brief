import re
from collections import defaultdict
from typing import List, Dict, Any

try:
    # rapidfuzz가 있으면 제일 좋음
    from rapidfuzz import fuzz
except Exception:
    fuzz = None


def _clean_text(s: str) -> str:
    s = (s or "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    # 너무 공격적인 정규화는 의미 손실, 최소한만
    return s


def _build_dedupe_text(it: Dict[str, Any], fields: List[str], max_chars: int) -> str:
    parts = []
    for f in fields:
        v = it.get(f)
        if v is None:
            continue
        # description이 nan(float)로 들어오는 경우 방어
        if isinstance(v, float):
            continue
        v = str(v).strip()
        if not v:
            continue
        parts.append(v)
    text = " | ".join(parts)
    text = _clean_text(text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text


def _sim(a: str, b: str, method: str) -> float:
    # rapidfuzz 없으면 매우 간단한 fallback (정확도↓)
    if not a or not b:
        return 0.0
    if fuzz is None:
        # fallback: 공통 토큰 비율
        ta = set(a.split())
        tb = set(b.split())
        inter = len(ta & tb)
        denom = max(1, len(ta | tb))
        return 100.0 * inter / denom

    m = (method or "token_set").lower()
    if m == "ratio":
        return float(fuzz.ratio(a, b))
    if m == "partial":
        return float(fuzz.partial_ratio(a, b))
    if m == "token_sort":
        return float(fuzz.token_sort_ratio(a, b))
    # default: token_set (가장 관대 + 오탐 상대적으로 적음)
    return float(fuzz.token_set_ratio(a, b))


def dedupe_items(items: List[Dict[str, Any]], similarity: int = 92, log=None, cfg: Dict[str, Any] | None = None):
    """
    - title 뿐 아니라 description(요약)까지 포함해 유사도 계산
    - token 기반 유사도(token_set_ratio)로 제목 표현 차이를 관대하게 묶음
    - 대표 아이템에 mentions, mention_sources를 유지(이미 파이프라인에서 사용 중)
    """
    cfg = cfg or {}
    rss = (cfg.get("rss") or {})

    fields = rss.get("dedupe_fields", ["title", "description"])
    method = rss.get("dedupe_method", "token_set")
    max_chars = int(rss.get("dedupe_text_max_chars", 800) or 800)
    min_title_chars = int(rss.get("dedupe_min_title_chars", 12) or 12)

    # 대표(클러스터 중심) 리스트와 그들의 텍스트 캐시
    reps: List[Dict[str, Any]] = []
    rep_texts: List[str] = []

    # 간단한 블로킹: region 별로만 비교하면 속도/오탐 개선
    by_region_idx = defaultdict(list)  # region -> list of rep indices

    for it in items:
        title = str(it.get("title") or "").strip()
        # 제목이 너무 짧으면 오탐 위험이 커서 dedupe 비교 제외(그냥 신규로 둠)
        too_short = len(title) < min_title_chars

        region = it.get("region") or ""
        cand_text = _build_dedupe_text(it, fields, max_chars) if not too_short else ""

        best_i = -1
        best_s = -1.0

        # 비교 대상: 같은 region의 reps만 (없으면 전체 reps fallback)
        candidates = by_region_idx.get(region, [])
        if not candidates:
            candidates = range(len(reps))

        if not too_short and cand_text:
            for i in candidates:
                s = _sim(cand_text, rep_texts[i], method)
                if s > best_s:
                    best_s, best_i = s, i

        if best_s >= float(similarity) and best_i >= 0:
            # merge into rep
            rep = reps[best_i]
            rep["mentions"] = int(rep.get("mentions", 1) or 1) + 1

            ms = rep.get("mention_sources") or []
            if isinstance(ms, str):
                ms = [ms]
            src = it.get("source") or ""
            if src and src not in ms:
                ms.append(src)
            rep["mention_sources"] = ms

            # (선택) velocity 정교화용: mention_published 누적 (추후 활용 가능)
            mp = rep.get("mention_published") or []
            if isinstance(mp, str):
                mp = [mp]
            pub = it.get("published")
            if pub:
                mp.append(pub)
            rep["mention_published"] = mp

        else:
            # new rep
            it = dict(it)  # 방어적으로 복사
            it["mentions"] = int(it.get("mentions", 1) or 1)
            src = it.get("source") or ""
            it["mention_sources"] = [src] if src else []
            pub = it.get("published")
            it["mention_published"] = [pub] if pub else []

            reps.append(it)
            rep_texts.append(cand_text if cand_text else _clean_text(title))

            by_region_idx[region].append(len(reps) - 1)

    if log:
        log.info(f"Dedupe(with mentions, title+desc, {method}): {len(items)} -> {len(reps)}")
    return reps

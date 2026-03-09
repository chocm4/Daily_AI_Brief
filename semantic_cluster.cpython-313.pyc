
from typing import List, Dict, Any, Optional

def select_top_news(
    items: List[Dict[str, Any]],
    top_n: int = 60,
    quotas: Optional[dict] = None,          # alias
    region_quota: Optional[dict] = None,    # canonical
    log=None
) -> List[Dict[str, Any]]:
    """
    점수(score) 기반 Top-N 선택.
    - region_quota={"KR":35,"GLOBAL":25} 같은 quota 지원
    - 과거 코드 호환: quotas 인자도 허용
    """
    if region_quota is None and quotas is not None:
        region_quota = quotas

    items_sorted = sorted(items, key=lambda x: float(x.get("score") or 0.0), reverse=True)

    if not region_quota:
        out = items_sorted[:top_n]
        if log:
            kr = sum(1 for x in out if (x.get("region") == "KR"))
            gl = sum(1 for x in out if (x.get("region") == "GLOBAL"))
            log.info(f"Selected top news: {len(out)} (KR={kr}, GLOBAL={gl})")
        return out

    selected = []
    used_ids = set()

    # 1) quota 먼저 채우기
    for region, n in region_quota.items():
        take = []
        for it in items_sorted:
            if it.get("id") in used_ids:
                continue
            if it.get("region") != region:
                continue
            take.append(it)
            used_ids.add(it.get("id"))
            if len(take) >= int(n):
                break
        selected.extend(take)

    # 2) 남으면 전체에서 높은 점수로 채우기
    if len(selected) < top_n:
        for it in items_sorted:
            if it.get("id") in used_ids:
                continue
            selected.append(it)
            used_ids.add(it.get("id"))
            if len(selected) >= top_n:
                break

    selected = selected[:top_n]
    if log:
        kr = sum(1 for x in selected if (x.get("region") == "KR"))
        gl = sum(1 for x in selected if (x.get("region") == "GLOBAL"))
        log.info(f"Selected top news: {len(selected)} (KR={kr}, GLOBAL={gl})")
    return selected

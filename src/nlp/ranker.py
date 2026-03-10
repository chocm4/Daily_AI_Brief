from typing import List, Dict, Any, Optional


def _event_key(it: Dict[str, Any]) -> str:
    return str(it.get("event_id") or it.get("cluster_id") or it.get("id") or "")


def _take_with_cap(pool: List[Dict[str, Any]], selected: List[Dict[str, Any]], used_ids: set, event_counts: dict, max_items_per_event: int, remaining: int | None = None) -> None:
    for it in pool:
        if remaining is not None and remaining <= 0:
            break
        item_id = it.get("id")
        if item_id in used_ids:
            continue
        event_id = _event_key(it)
        if max_items_per_event > 0 and event_counts.get(event_id, 0) >= max_items_per_event:
            continue
        selected.append(it)
        used_ids.add(item_id)
        event_counts[event_id] = event_counts.get(event_id, 0) + 1
        if remaining is not None:
            remaining -= 1


def select_top_news(
    items: List[Dict[str, Any]],
    top_n: int = 60,
    quotas: Optional[dict] = None,
    region_quota: Optional[dict] = None,
    max_items_per_event: int = 0,
    log=None,
) -> List[Dict[str, Any]]:
    if region_quota is None and quotas is not None:
        region_quota = quotas

    items_sorted = sorted(items, key=lambda x: float(x.get("score") or 0.0), reverse=True)

    if not region_quota:
        selected: List[Dict[str, Any]] = []
        used_ids = set()
        event_counts: Dict[str, int] = {}
        _take_with_cap(items_sorted, selected, used_ids, event_counts, max_items_per_event)
        out = selected[:top_n]
        if log:
            kr = sum(1 for x in out if (x.get("region") == "KR"))
            gl = sum(1 for x in out if (x.get("region") == "GLOBAL"))
            log.info(f"Selected top news: {len(out)} (KR={kr}, GLOBAL={gl})")
        return out

    selected: List[Dict[str, Any]] = []
    used_ids = set()
    event_counts: Dict[str, int] = {}

    for region, n in region_quota.items():
        if len(selected) >= top_n:
            break
        pool = [it for it in items_sorted if it.get("region") == region]
        _take_with_cap(pool, selected, used_ids, event_counts, max_items_per_event, remaining=int(n))

    if len(selected) < top_n:
        _take_with_cap(items_sorted, selected, used_ids, event_counts, max_items_per_event, remaining=top_n - len(selected))

    selected = selected[:top_n]
    if log:
        kr = sum(1 for x in selected if (x.get("region") == "KR"))
        gl = sum(1 for x in selected if (x.get("region") == "GLOBAL"))
        log.info(f"Selected top news: {len(selected)} (KR={kr}, GLOBAL={gl})")
    return selected

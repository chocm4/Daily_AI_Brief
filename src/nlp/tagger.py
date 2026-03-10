import re
import math
import ast
import datetime as dt
from zoneinfo import ZoneInfo


AGGREGATOR_DEFAULTS = {"Google", "Investing.com - All News", "Investing.com - Stock Market News"}


def _contains_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any((k or "").lower() in t for k in keywords if k)


def _parse_iso_dt(s: str, tzname: str):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=ZoneInfo(tzname))
        return d
    except Exception:
        return None


def _parse_mention_sources(v):
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                out = ast.literal_eval(s)
                if isinstance(out, list):
                    return [str(x) for x in out if x]
            except Exception:
                pass
        return [s]
    return [str(v)]


def _source_tier_weight(source: str, sc: dict) -> float:
    source = str(source or "")
    tiers = sc.get("source_tiers") or {}
    for _, block in tiers.items():
        sources = set(block.get("sources") or [])
        if source in sources:
            try:
                return float(block.get("weight", 1.0))
            except Exception:
                return 1.0
    return 1.0


def _time_decay_multiplier(published: str, now: dt.datetime, tzname: str, sc: dict) -> float:
    td_cfg = (sc.get("time_decay") or {})
    if not bool(td_cfg.get("enabled", False)):
        return 1.0
    pub = _parse_iso_dt(published, tzname)
    if pub is None:
        return 1.0
    age_h = max(0.0, (now - pub).total_seconds() / 3600.0)
    half_life = max(float(td_cfg.get("half_life_hours", 10) or 10), 0.1)
    decay = 0.5 ** (age_h / half_life)
    min_mult = float(td_cfg.get("min_multiplier", 0.5) or 0.5)
    max_mult = float(td_cfg.get("max_multiplier", 1.0) or 1.0)
    return max(min_mult, min(max_mult, decay))


def _syndication_penalty(sources: list[str], source_count: int, sc: dict) -> float:
    syn = (sc.get("syndication_penalty") or {})
    if not bool(syn.get("enabled", False)):
        return 0.0
    agg_sources = set(syn.get("aggregator_sources") or list(AGGREGATOR_DEFAULTS))
    penalty_per = float(syn.get("penalty_per_source", 0.08) or 0.08)
    max_pen = float(syn.get("max_penalty", 0.24) or 0.24)
    agg_hits = sum(1 for s in set(sources or []) if s in agg_sources)
    if source_count <= 1:
        return 0.0
    return min(max_pen, agg_hits * penalty_per)


def tag_and_score(items: list[dict], cfg: dict, log=None) -> list[dict]:
    sc = cfg.get("news_scoring", {}) or {}
    kw_weights: dict = sc.get("keywords", {}) or {}
    tag_rules: dict = sc.get("tag_rules", {}) or {}
    event_weights: dict = sc.get("event_type_weights", {}) or {}
    impact_weights: dict = sc.get("impact_scope_weights", {}) or {}

    tzname = (cfg.get("app") or {}).get("timezone", "Asia/Seoul")
    now = dt.datetime.now(tz=ZoneInfo(tzname))

    pen_rules = []
    for p in (sc.get("penalty_patterns", []) or []):
        try:
            pen_rules.append((re.compile(p["pattern"], re.I), float(p["penalty"])))
        except Exception:
            continue
    exceptions = [x.lower() for x in (sc.get("penalty_exceptions", []) or [])]

    cb = (sc.get("cluster_boost") or {})
    cb_enabled = bool(cb.get("enabled", True))
    source_diversity_beta = float(cb.get("source_diversity_beta", 0.25))
    source_diversity_cap = float(cb.get("source_diversity_cap", 1.0))
    velocity_window_hours = float(cb.get("velocity_window_hours", 6))
    velocity_alpha = float(cb.get("velocity_alpha", 0.25))
    velocity_cap = float(cb.get("velocity_cap", 1.0))

    for it in items:
        text = f"{it.get('title', '')} {it.get('description', '')}".strip()
        tlow = text.lower()

        keyword_score = 0.0
        for kw, w in kw_weights.items():
            if kw and (kw.lower() in tlow):
                keyword_score += float(w)

        source_weight = float(it.get("source_weight", 1.0) or 1.0)
        tier_weight = _source_tier_weight(it.get("source", ""), sc)
        pre_penalty_score = keyword_score * source_weight * tier_weight

        penalty_total = 0.0
        has_exception = any(exc in tlow for exc in exceptions)
        if not has_exception:
            for pat, pen in pen_rules:
                if pat.search(text):
                    penalty_total += pen

        cluster_diversity_bonus = 0.0
        cluster_velocity_bonus = 0.0
        ms = _parse_mention_sources(it.get("mention_sources"))
        nsrc = len(set([x.strip() for x in ms if str(x).strip()]))
        k = int(it.get("mentions", 1) or 1)
        pub = _parse_iso_dt(it.get("published", ""), tzname)
        if cb_enabled:
            if nsrc > 1 and source_diversity_beta > 0:
                cluster_diversity_bonus = min(source_diversity_beta * math.log(nsrc), source_diversity_cap)
            if pub and velocity_alpha > 0 and velocity_window_hours > 0 and k >= 2:
                age_h = max(0.0, (now - pub).total_seconds() / 3600.0)
                if age_h <= velocity_window_hours:
                    cluster_velocity_bonus = min(velocity_alpha * math.log(k + 1), velocity_cap)

        event_type = str(it.get("event_type") or "general_market")
        impact_scope = str(it.get("impact_scope") or "secondary")
        kr_rel = float(it.get("korea_relevance_score") or 0.0)
        cluster_mentions = int(it.get("cluster_mentions") or it.get("mentions") or 1)
        cluster_sources = int(it.get("cluster_source_count") or len(ms) or 1)

        event_weight = float(event_weights.get(event_type, 0.0))
        impact_weight = float(impact_weights.get(impact_scope, 0.0))
        korea_bonus = min(0.9, kr_rel * 1.2)
        mentions_bonus = min(0.6, math.log(max(cluster_mentions, 1)) * 0.25)
        sources_bonus = min(0.4, math.log(max(cluster_sources, 1)) * 0.20)
        syndication_penalty = _syndication_penalty(ms, cluster_sources, sc)
        time_decay_mult = _time_decay_multiplier(it.get("published", ""), now, tzname, sc)

        raw_score = (
            pre_penalty_score
            - penalty_total
            + cluster_diversity_bonus
            + cluster_velocity_bonus
            + event_weight
            + impact_weight
            + korea_bonus
            + mentions_bonus
            + sources_bonus
            - syndication_penalty
        )
        final_score = max(raw_score * time_decay_mult, 0.0)

        tags = []
        for tag, kws in tag_rules.items():
            if _contains_any(text, kws):
                tags.append(tag)
        if event_type not in tags:
            tags.append(event_type)
        if impact_scope not in tags:
            tags.append(impact_scope)
        for lbl in (it.get("secondary_event_types") or []):
            if lbl not in tags:
                tags.append(lbl)
        if (it.get("korea_relevance") or "") == "high" and "korea_relevant" not in tags:
            tags.append("korea_relevant")

        it["score"] = round(final_score, 3)
        it["tags"] = tags
        it["score_breakdown"] = {
            "keyword_score": round(keyword_score, 4),
            "source_weight_mult": round(source_weight, 4),
            "source_tier_mult": round(tier_weight, 4),
            "pre_penalty_score": round(pre_penalty_score, 4),
            "penalty_total": round(penalty_total, 4),
            "cluster_diversity_bonus": round(cluster_diversity_bonus, 4),
            "cluster_velocity_bonus": round(cluster_velocity_bonus, 4),
            "event_weight": round(event_weight, 4),
            "impact_weight": round(impact_weight, 4),
            "korea_bonus": round(korea_bonus, 4),
            "cluster_mentions_bonus": round(mentions_bonus, 4),
            "cluster_sources_bonus": round(sources_bonus, 4),
            "syndication_penalty": round(syndication_penalty, 4),
            "time_decay_mult": round(time_decay_mult, 4),
            "final_score": round(final_score, 4),
        }

    if log:
        log.info("Tagging/scoring done (event-aware + time decay + score breakdown).")
    return items

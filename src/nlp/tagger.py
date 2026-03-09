import re, math, ast
import datetime as dt
from zoneinfo import ZoneInfo

def _contains_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any((k or "").lower() in t for k in keywords if k)

def _parse_iso_dt(s: str, tzname: str):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=ZoneInfo(tzname))
        return d
    except Exception:
        return None

def _parse_mention_sources(v):
    # mention_sources는 list이거나, "['a','b']" 같은 문자열일 수 있음
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

def tag_and_score(items: list[dict], cfg: dict, log=None) -> list[dict]:
    sc = cfg.get("news_scoring", {}) or {}
    kw_weights: dict = sc.get("keywords", {}) or {}
    tag_rules: dict = sc.get("tag_rules", {}) or {}

    tzname = (cfg.get("app") or {}).get("timezone", "Asia/Seoul")
    now = dt.datetime.now(tz=ZoneInfo(tzname))

    # penalties
    pen_rules = []
    for p in (sc.get("penalty_patterns", []) or []):
        try:
            pen_rules.append((re.compile(p["pattern"], re.I), float(p["penalty"])))
        except Exception:
            continue
    exceptions = [x.lower() for x in (sc.get("penalty_exceptions", []) or [])]

    # cluster boost (A,B)
    cb = (sc.get("cluster_boost") or {})
    cb_enabled = bool(cb.get("enabled", True))
    # A) source diversity
    source_diversity_beta = float(cb.get("source_diversity_beta", 0.25))
    source_diversity_cap  = float(cb.get("source_diversity_cap", 1.0))
    # B) velocity proxy
    velocity_window_hours = float(cb.get("velocity_window_hours", 6))
    velocity_alpha        = float(cb.get("velocity_alpha", 0.25))
    velocity_cap          = float(cb.get("velocity_cap", 1.0))

    for it in items:
        text = f"{it.get('title','')} {it.get('description','')}".strip()
        tlow = text.lower()

        # 1) keyword score
        score = 0.0
        for kw, w in kw_weights.items():
            if kw and (kw.lower() in tlow):
                score += float(w)

        # 2) source weight
        score *= float(it.get("source_weight", 1.0))

        # 3) penalties
        has_exception = any(exc in tlow for exc in exceptions)
        if not has_exception:
            for pat, pen in pen_rules:
                if pat.search(text):
                    score -= pen

        # ---- A/B boosters ----
        if cb_enabled:
            k = int(it.get("mentions", 1) or 1)

            # A) source diversity: distinct sources in the cluster
            ms = _parse_mention_sources(it.get("mention_sources"))
            nsrc = len(set([x.strip().lower() for x in ms if str(x).strip()]))
            if nsrc > 1 and source_diversity_beta > 0:
                score += min(source_diversity_beta * math.log(nsrc), source_diversity_cap)

            # B) velocity proxy: very recent + clustered
            pub = _parse_iso_dt(it.get("published", ""), tzname)
            if pub and velocity_alpha > 0 and velocity_window_hours > 0 and k >= 2:
                age_h = max(0.0, (now - pub).total_seconds() / 3600.0)
                if age_h <= velocity_window_hours:
                    score += min(velocity_alpha * math.log(k + 1), velocity_cap)

        # 4) tags
        tags = []
        for tag, kws in tag_rules.items():
            if _contains_any(text, kws):
                tags.append(tag)

        it["score"] = round(max(score, 0.0), 3)
        it["tags"] = tags

    if log:
        log.info("Tagging/scoring done (with cluster/source-diversity/velocity).")
    return items

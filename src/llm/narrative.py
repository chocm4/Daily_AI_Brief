import json
import math
import re
from collections import Counter, defaultdict
from typing import Dict, Any, List, Optional, Tuple

from openai import OpenAI


# -----------------------------
# small utils
# -----------------------------
def _safe_float(x, default=None):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return default


def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _truncate(s: str, max_chars: int) -> str:
    s = _clean_text(s)
    if max_chars and len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def _strip_sources_section(md: str) -> str:
    if not md:
        return ""
    lines = md.splitlines()
    cut_idx = None

    for i, line in enumerate(lines):
        if line.strip().lower() in {"## sources", "### sources"}:
            cut_idx = i
            break

    if cut_idx is None:
        return md.strip()

    return "\n".join(lines[:cut_idx]).rstrip()


def _top_scored_sources(news_kr: list, news_gl: list, top_k: int = 5) -> list:
    pool = []
    seen = set()

    for x in (news_kr or []) + (news_gl or []):
        key = (
            str(x.get("title", "")).strip(),
            str(x.get("link", "") or x.get("url", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        pool.append(x)

    def _score(item):
        v = item.get("score")
        try:
            return float(v)
        except Exception:
            return float("-inf")

    pool = sorted(pool, key=_score, reverse=True)
    return pool[:top_k]


def _render_sources_md(news_kr: list, news_gl: list, top_k: int = 5) -> str:
    top_sources = _top_scored_sources(news_kr, news_gl, top_k=top_k)
    lines = ["## Sources"]

    if not top_sources:
        lines.append("- 없음")
        return "\n".join(lines)

    for x in top_sources:
        nid = x.get("id", "")
        title = x.get("title", "")
        source = x.get("source", "")
        link = x.get("link", "") or x.get("url", "")
        score = x.get("score", "")

        try:
            score_txt = f"{float(score):.2f}"
        except Exception:
            score_txt = str(score) if score not in [None, ""] else "N/A"

        lines.append(f"- ({nid}) {title} | {source} | score={score_txt} | {link}")

    return "\n".join(lines)


def _trim_to_chars(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    cut = text[:max_chars]
    candidates = [
        cut.rfind("\n\n"),
        cut.rfind("\n"),
        cut.rfind(". "),
        cut.rfind("다. "),
        cut.rfind("다.\n"),
    ]
    best = max(candidates)
    if best >= int(max_chars * 0.7):
        return cut[:best].rstrip()
    return cut.rstrip()


def _compute_body_budget(header: str, sources_md: str, cfg: Dict[str, Any]) -> int:
    tg_cfg = cfg.get("telegram", {}) or {}
    story_cfg = cfg.get("story", {}) or {}

    single_message_only = bool(tg_cfg.get("single_message_only", True))
    telegram_cap = int(tg_cfg.get("max_message_length", 3500))
    story_target = int(story_cfg.get("target_chars", 4500))
    reserve = int(story_cfg.get("telegram_body_reserve_chars", 120))

    if not single_message_only:
        return max(1200, story_target)

    total_budget = telegram_cap
    used = len(header) + len(sources_md) + 4 + reserve
    body_budget = total_budget - used

    # 너무 빡빡해져서 문서 품질이 무너지는 걸 방지
    return max(900, body_budget)


def _format_market_snapshot(market: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for row in market or []:
        key = row.get("asset") or row.get("name") or ""
        if not key:
            continue
        out[key] = {
            "kind": row.get("kind", "price"),
            "ret1d_pct": row.get("ret1d_pct"),
            "chg1d": row.get("chg1d"),
            "chg1d_bp": row.get("chg1d_bp"),
            "level": row.get("level"),
            "daily_date": row.get("daily_date") or row.get("asof_date"),
            "last_update_kst": row.get("last_update_kst") or row.get("last_ts_kst") or row.get("last_ts"),
        }
    return out


def _mode_guidance(mode: str) -> str:
    mode = (mode or "").upper()

    if mode == "US_AFTERCLOSE_KR_PREOPEN":
        return (
            "현재는 '미국장 마감 후 ~ 한국장 개장 전' 구간(US_AFTERCLOSE_KR_PREOPEN). "
            "미국장 마감 요약과 그 영향이 오늘 한국장에 어떻게 전이될지 중심으로 써라."
        )

    if mode == "KR_INTRADAY":
        return (
            "현재는 한국장 장중(KR_INTRADAY). "
            "오늘 장중 흐름과 마감 전 변수가 될 포인트를 중심으로 써라."
        )

    if mode == "KR_AFTERCLOSE_US_PREOPEN":
        return (
            "현재는 '한국장 마감 후 ~ 미국장 개장 전' 구간(KR_AFTERCLOSE_US_PREOPEN). "
            "오늘 한국장 리뷰와 미국장 개장 전 체크포인트 중심으로 써라."
        )

    if mode == "US_INTRADAY":
        return (
            "현재는 미국장 장중(US_INTRADAY). "
            "미국장 현재 흐름과 아시아/한국장과의 연결을 중심으로 써라."
        )

    if mode == "WEEKEND":
        return (
            "현재는 주말/휴장 구간(WEEKEND). "
            "직전 거래일 핵심 요약과 다음 거래일 체크포인트를 중심으로 써라."
        )

    return "한국/미국 시장의 최근 흐름을 연결해 핵심 이벤트와 다음 세션 체크포인트를 정리해라."


# -----------------------------
# news packing
# -----------------------------
def _clip_news(items: list, n: int, desc_chars: int = 360) -> list:
    out = []
    for x in items or []:
        out.append(
            {
                "id": x.get("id"),
                "title": x.get("title"),
                "link": x.get("link"),
                "source": x.get("source"),
                "published": x.get("published"),
                "score": x.get("score"),
                "tags": x.get("tags") or [],
                "description": _truncate(x.get("description") or x.get("summary") or "", desc_chars),
            }
        )
        if len(out) >= n:
            break
    return out


# -----------------------------
# optional market add-ons
# -----------------------------
def _sector_highlights(sectors: Optional[List[Dict[str, Any]]], topk: int = 3) -> Dict[str, Any]:
    sectors = sectors or []
    norm = []
    for s in sectors:
        name = s.get("sector") or s.get("name") or s.get("industry") or ""
        r = _safe_float(s.get("ret1d_pct"), None)
        if not name or r is None:
            continue
        norm.append(
            {
                "name": name,
                "ret1d_pct": r,
                "net_buy_foreign": s.get("net_buy_foreign"),
                "net_buy_inst": s.get("net_buy_inst"),
                "net_buy_retail": s.get("net_buy_retail"),
            }
        )
    if not norm:
        return {"available": False}

    norm_sorted = sorted(norm, key=lambda x: x["ret1d_pct"], reverse=True)
    leaders = norm_sorted[:topk]
    laggards = list(reversed(norm_sorted[-topk:]))

    rets = [x["ret1d_pct"] for x in norm_sorted]
    dispersion = (max(rets) - min(rets)) if rets else 0.0
    has_feature = dispersion >= 1.5 or abs(leaders[0]["ret1d_pct"]) >= 2.0 or abs(laggards[0]["ret1d_pct"]) >= 2.0

    return {
        "available": True,
        "has_feature": bool(has_feature),
        "dispersion_pctp": round(dispersion, 2),
        "leaders": leaders,
        "laggards": laggards,
    }


def _mover_highlights(movers: Optional[List[Dict[str, Any]]], topk: int = 5) -> Dict[str, Any]:
    movers = movers or []
    norm = []
    for m in movers:
        name = m.get("name") or m.get("ticker") or ""
        r = _safe_float(m.get("ret1d_pct"), None)
        if not name or r is None:
            continue
        norm.append(
            {
                "name": name,
                "ticker": m.get("ticker"),
                "ret1d_pct": r,
                "sector": m.get("sector"),
                "net_buy_foreign": m.get("net_buy_foreign"),
                "net_buy_inst": m.get("net_buy_inst"),
                "net_buy_retail": m.get("net_buy_retail"),
            }
        )
    if not norm:
        return {"available": False}

    gainers = sorted(norm, key=lambda x: x["ret1d_pct"], reverse=True)[:topk]
    losers = sorted(norm, key=lambda x: x["ret1d_pct"])[:topk]
    return {"available": True, "gainers": gainers, "losers": losers}


def _build_theme_map(news: List[Dict[str, Any]], max_themes: int = 6) -> List[Dict[str, Any]]:
    if not news:
        return []

    tag_counter = Counter()
    per_tag = defaultdict(list)

    for it in news:
        tags = it.get("tags") or []
        if tags:
            for t in tags:
                tag_counter[t] += 1
                per_tag[t].append(it.get("id"))
        else:
            title = (it.get("title") or "") + " " + (it.get("description") or "")
            title = title.lower()
            buckets = []
            if any(k in title for k in ["fomc", "fed", "금리", "국채", "파월", "인상", "인하"]):
                buckets.append("금리/통화정책")
            if any(k in title for k in ["유가", "원유", "wti", "brent", "재고", "opec", "물가", "cpi", "ppi"]):
                buckets.append("유가/인플레")
            if any(k in title for k in ["환율", "달러", "dxy", "원/달러", "엔", "위안", "유로"]):
                buckets.append("환율/달러")
            if any(k in title for k in ["실적", "어닝", "가이던스", "매출", "이익", "마진"]):
                buckets.append("실적/가이던스")
            if any(k in title for k in ["정책", "규제", "금융위", "정부", "공매도", "법안"]):
                buckets.append("정책/규제")
            if any(k in title for k in ["반도체", "ai", "엔비디아", "nvidia", "칩", "메모리"]):
                buckets.append("테크/AI")
            if not buckets:
                buckets = ["기타"]

            for b in buckets:
                tag_counter[b] += 1
                per_tag[b].append(it.get("id"))

    top = [t for t, _ in tag_counter.most_common(max_themes)]
    out = []
    for t in top:
        ids = per_tag.get(t, [])
        seen = set()
        ids2 = []
        for _id in ids:
            if _id and _id not in seen:
                ids2.append(_id)
                seen.add(_id)
        out.append({"theme": t, "count": len(ids2), "ids": ids2[:8]})
    return out


# -----------------------------
# LLM call helpers
# -----------------------------
def _responses_call(
    client: OpenAI,
    model: str,
    sys_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> Tuple[str, str, Dict[str, Any]]:
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": sys_prompt.strip()},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    text = getattr(resp, "output_text", "") or ""
    model_used = getattr(resp, "model", model) or model
    usage = getattr(resp, "usage", None)
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else (usage if isinstance(usage, dict) else {})
    return text, model_used, usage_dict


def _compress_to_budget(
    client: OpenAI,
    model: str,
    text: str,
    body_budget: int,
    temperature: float,
    max_tokens: int,
    log=None,
) -> str:
    text = (text or "").strip()
    if len(text) <= body_budget:
        return text

    sys_prompt = f"""
너는 한국 sell-side 리서치센터의 에디터다.
아래 글을 의미 손실을 최소화하면서 압축해라.

규칙:
- 최종 본문 길이는 반드시 {body_budget}자 이하여야 한다.
- 새 사실/새 수치/새 일정 추가 금지.
- 문단 연결과 핵심 인과는 유지.
- 군더더기, 반복, 장황한 수식어 제거.
- bullet 금지, 자연스러운 서술문 유지.
- "## Sources" 섹션 작성 금지.
"""

    user_prompt = "압축 대상:\n```text\n" + text + "\n```"

    try:
        out, model_used, _ = _responses_call(
            client,
            model,
            sys_prompt,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        out = (out or "").strip()
        if log:
            log.info(f"[story-compressor] model_used={model_used} | before={len(text)} | after={len(out)} | budget={body_budget}")
        return out
    except Exception as e:
        if log:
            log.warning(f"compressor pass failed: {e}")
        return text


# -----------------------------
# main
# -----------------------------
def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    story_cfg = cfg.get("story", {}) or {}

    model = llm_cfg.get("story_model", "gpt-5.4")
    fallback_model = llm_cfg.get("story_fallback_model", "gpt-5.4")
    editor_model = llm_cfg.get("story_editor_model", model)

    temperature = float(llm_cfg.get("story_temperature", 0.15))
    editor_temperature = float(llm_cfg.get("story_editor_temperature", 0.10))

    max_tokens = int(llm_cfg.get("story_max_output_tokens", 3200))
    editor_max_tokens = int(llm_cfg.get("story_editor_max_output_tokens", max_tokens))

    max_paragraphs = int(story_cfg.get("max_paragraphs", 6))
    min_paragraphs = int(story_cfg.get("min_paragraphs", 4))
    sentences_per_paragraph = story_cfg.get("sentences_per_paragraph", "2-4")

    enable_editor_pass = bool(story_cfg.get("enable_editor_pass", True))
    enable_expander_pass = bool(story_cfg.get("enable_expander_pass", True))

    mode = fact_pack.get("run_mode") or "AFTERCLOSE"
    guidance = _mode_guidance(mode)

    kr_items_n = int(story_cfg.get("kr_items", 30))
    gl_items_n = int(story_cfg.get("global_items", 26))
    desc_chars = int(story_cfg.get("news_desc_chars", 360))
    max_themes = int(story_cfg.get("max_themes", 7))

    news_kr = _clip_news(fact_pack.get("news_kr", []) or [], kr_items_n, desc_chars=desc_chars)
    news_gl = _clip_news(fact_pack.get("news_overnight", []) or fact_pack.get("news_global", []) or [], gl_items_n, desc_chars=desc_chars)

    brief_kr = _clip_news(fact_pack.get("brief_kr", []) or [], int(story_cfg.get("brief_kr_items", 12)), desc_chars=desc_chars)
    brief_gl = _clip_news(fact_pack.get("brief_global", []) or [], int(story_cfg.get("brief_gl_items", 12)), desc_chars=desc_chars)

    market = _format_market_snapshot(fact_pack.get("market", []) or [])
    drivers = report.get("top_drivers", []) if isinstance(report, dict) else []
    risks = report.get("risk_radar", []) if isinstance(report, dict) else []

    sector_hl = _sector_highlights(fact_pack.get("kr_sectors"), topk=int(story_cfg.get("sector_topk", 3)))
    mover_hl = _mover_highlights(fact_pack.get("kr_movers"), topk=int(story_cfg.get("mover_topk", 5)))

    theme_map_kr = _build_theme_map(news_kr, max_themes=max_themes)
    theme_map_gl = _build_theme_map(news_gl, max_themes=max_themes)

    krx_flows = fact_pack.get("krx_flows") or {}
    krx_flows_compact = {}
    for mkt in ["KOSPI", "KOSDAQ"]:
        p = krx_flows.get(mkt) or {}
        nb = (p.get("net_buy_1e8krw") or {})
        if nb:
            keep = {}
            for k in ["외국인", "기관합계", "개인", "기타법인", "금융투자", "연기금"]:
                if k in nb:
                    keep[k] = nb[k]
            if not keep:
                keep = dict(list(nb.items())[:6])
            krx_flows_compact[mkt] = {
                "date": p.get("date"),
                "net_buy_1e8krw": keep,
            }

    asof = fact_pack.get("asof") or ""
    gen = fact_pack.get("generated_at_kst") or ""
    header = f"# Daily Market Review (as of {asof})\n\n"
    if gen:
        header += f"> generated_at_kst: {gen} | mode: {mode}\n\n"

    sources_md = _render_sources_md(news_kr, news_gl, top_k=5)
    body_budget = _compute_body_budget(header, sources_md, cfg)

    context = {
        "asof": asof,
        "generated_at_kst": gen,
        "run_mode": mode,
        "mode_guidance": guidance,
        "body_budget_chars": body_budget,
        "headline_hint": report.get("headline") if isinstance(report, dict) else "",
        "market": market,
        "krx_flows": krx_flows_compact,
        "krx_flow_tops": fact_pack.get("krx_flow_tops") or {},
        "rally_decomp": fact_pack.get("rally_decomp") or {},
        "drivers": drivers,
        "risks": risks,
        "news_kr": news_kr,
        "news_global": news_gl,
        "brief_kr": brief_kr,
        "brief_global": brief_gl,
        "themes_kr": theme_map_kr,
        "themes_global": theme_map_gl,
        "kr_sector_highlights": sector_hl,
        "kr_mover_highlights": mover_hl,
    }

    sys_prompt = f"""
너는 한국 sell-side 증권사 리서치센터의 Daily Market Review를 작성하는 시황/Quant 애널리스트다.
아래 컨텍스트(JSON)에 있는 정보만 근거로 사용해라.
컨텍스트 밖 사실/수치/일정을 만들지 마라.

중요:
- 최종 본문(body)은 반드시 {body_budget}자 이하여야 한다.
- 이 제한은 매우 중요하다. 길어지면 안 된다.
- 장황한 설명보다 핵심 인과와 연결을 우선한다.
- 글 마지막에 Sources는 작성하지 마라. 출처 목록은 코드가 별도로 붙인다.

작성 목표:
- 결과물은 bullet이 아니라 하나의 자연스러운 시황 글이다.
- 문단 수는 {min_paragraphs}~{max_paragraphs}개.
- 문단당 {sentences_per_paragraph}문장.
- 원인 → 경로 → 결과 구조를 유지.
- 핵심 주제 3~5개 중심으로 압축적으로 작성.
- 한국장/미국장/금리/환율/수급 중 설명력이 높은 것 위주로 선택.
- 반복, 군더더기, 같은 말 바꿔쓰기 금지.
- 수치 나열 금지, 해석 중심.
- 채권/금리 자산이 있으면 금리 문단을 짧게라도 포함.

문체:
- 증권사 시황 톤
- 단정 대신 "~로 해석", "~가능성", "~에 주목"
- 구어체/감탄/과장 금지
"""

    user_prompt = "컨텍스트(JSON):\n```json\n" + json.dumps(context, ensure_ascii=False, indent=2) + "\n```"

    client = OpenAI()

    try:
        draft, model_used, usage = _responses_call(
            client, model, sys_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens
        )
    except Exception as e:
        if log:
            log.warning(f"story model call failed: {model} | {e} -> fallback {fallback_model}")
        draft, model_used, usage = _responses_call(
            client, fallback_model, sys_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens
        )

    if log:
        log.info(f"[story] model_used={model_used} | usage={usage} | body_budget={body_budget}")

    txt = (draft or "").strip()

    if enable_editor_pass and txt:
        editor_sys = f"""
너는 한국 sell-side 리서치센터의 에디터다.
아래 초안을 더 자연스럽고 단단한 시황 문장으로 다듬어라.

절대 규칙:
- 새 사실/새 수치/새 일정 추가 금지
- 최종 본문 길이는 반드시 {body_budget}자 이하여야 한다
- 반복/장황함 제거
- 논리 연결 강화
- Sources 섹션 작성 금지
"""
        editor_user = "초안:\n```text\n" + txt + "\n```"

        try:
            edited, editor_used, _ = _responses_call(
                client, editor_model, editor_sys, editor_user, temperature=editor_temperature, max_tokens=editor_max_tokens
            )
            if edited and edited.strip():
                txt = edited.strip()
                if log:
                    log.info(f"[story-editor] model_used={editor_used} | len={len(txt)}")
        except Exception as e:
            if log:
                log.warning(f"editor pass failed: {e}")

    # 예산 여유가 있을 때만 확장
    if enable_expander_pass and txt and len(txt) < int(body_budget * 0.78):
        exp_target = min(body_budget, int(body_budget * 0.92))
        exp_sys = f"""
너는 한국 sell-side 리서치센터의 확장 편집자다.
아래 글을 조금만 더 풍부하게 만들되, 최종 본문 길이는 반드시 {exp_target}자 이하여야 한다.

규칙:
- 새 사실/새 수치/새 일정 추가 금지
- 핵심 인과와 연결만 보강
- 반복 금지
- Sources 섹션 작성 금지
"""
        exp_user = (
            "확장 대상 글:\n```text\n" + txt + "\n```\n\n"
            "참고 컨텍스트(요약):\n```json\n" + json.dumps(
                {
                    "themes_kr": theme_map_kr,
                    "themes_global": theme_map_gl,
                    "kr_sector_highlights": sector_hl,
                    "kr_mover_highlights": mover_hl,
                    "market": market,
                    "news_kr": news_kr,
                    "news_global": news_gl,
                    "brief_kr": brief_kr,
                    "brief_global": brief_gl,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n```"
        )

        try:
            expanded, exp_used, _ = _responses_call(
                client, editor_model, exp_sys, exp_user, temperature=0.2, max_tokens=editor_max_tokens
            )
            if expanded and expanded.strip():
                txt = expanded.strip()
                if log:
                    log.info(f"[story-expander] model_used={exp_used} | len={len(txt)}")
        except Exception as e:
            if log:
                log.warning(f"expander pass failed: {e}")

    txt = _strip_sources_section((txt or "").strip())

    # 본문이 예산 초과면 "잘라내기" 전에 먼저 압축
    if len(txt) > body_budget:
        txt = _compress_to_budget(
            client=client,
            model=editor_model,
            text=txt,
            body_budget=body_budget,
            temperature=0.05,
            max_tokens=editor_max_tokens,
            log=log,
        ).strip()

    # 마지막 안전장치
    if len(txt) > body_budget:
        txt = _trim_to_chars(txt, body_budget)

    final_md = header + txt + "\n\n" + sources_md + "\n"

    # 정말 드물게 전체 길이가 cap을 넘으면 본문만 추가 압축
    tg_cfg = cfg.get("telegram", {}) or {}
    if bool(tg_cfg.get("single_message_only", True)):
        total_cap = int(tg_cfg.get("max_message_length", 3500))
        if len(final_md) > total_cap:
            extra_over = len(final_md) - total_cap
            tighter_budget = max(700, body_budget - extra_over - 20)

            txt = _compress_to_budget(
                client=client,
                model=editor_model,
                text=txt,
                body_budget=tighter_budget,
                temperature=0.05,
                max_tokens=editor_max_tokens,
                log=log,
            ).strip()

            if len(txt) > tighter_budget:
                txt = _trim_to_chars(txt, tighter_budget)

            final_md = header + txt + "\n\n" + sources_md + "\n"

            if len(final_md) > total_cap:
                # 여기까지 오면 정말 예외적이므로 최소 trim만 적용
                body_only_budget = max(500, tighter_budget - (len(final_md) - total_cap) - 10)
                txt = _trim_to_chars(txt, body_only_budget)
                final_md = header + txt + "\n\n" + sources_md + "\n"

    return final_md

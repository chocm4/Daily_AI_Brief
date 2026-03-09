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
    s = re.sub(r"<[^>]+>", " ", s)           # strip html tags
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _truncate(s: str, max_chars: int) -> str:
    s = _clean_text(s)
    if max_chars and len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


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
            "미국장 마감 요약(지수/섹터/스타일/금리/달러/원자재/변동성)과, "
            "그 영향이 오늘 한국장에 어떻게 전이될지(시나리오/업종/수급) 중심으로 써라. "
            "한국장 전일 내용은 1문단 내로 짧게 연결하고, "
            "'오늘 체크포인트(지표/이벤트/실적/정책)'를 명확히 제시해라."
        )

    if mode == "KR_INTRADAY":
        return (
            "현재는 한국장 장중(KR_INTRADAY). "
            "오늘 장중 흐름(지수 레벨, 업종 강약, 스타일/대형-중소형, 수급/리스크온오프 단서)을 중심으로 쓰고, "
            "미국장/글로벌은 '배경'으로 압축해라. "
            "마감까지 남은 시간에 변곡을 만들 수 있는 요인(뉴스/레벨/크로스에셋)을 제시해라."
        )

    if mode == "KR_AFTERCLOSE_US_PREOPEN":
        return (
            "현재는 '한국장 마감 후 ~ 미국장 개장 전' 구간(KR_AFTERCLOSE_US_PREOPEN). "
            "오늘 한국장 리뷰(무엇이 올랐고/내렸고/왜 그랬는지, 수급/업종/키 이슈)를 가장 비중 있게 쓰고, "
            "미국장 개장 전 주목할 이벤트/리스크(지표, 연준 발언, 실적, 지정학)를 연결해라. "
            "마지막은 '오늘 한국장 흐름이 미국장에 어떤 포지셔닝으로 이어질지' 관점으로 마무리해라."
        )

    if mode == "US_INTRADAY":
        return (
            "현재는 미국장 장중(US_INTRADAY). "
            "미국장 현재 흐름(지수/섹터/빅테크/금리/달러/원자재/변동성)을 중심으로, "
            "아시아/한국 마감 흐름과의 연결(리스크온오프, 달러/금리 경로)을 짚어라. "
            "남은 장중에 주목할 이벤트(지표 발표 시각, 실적, 발언)와 "
            "레벨(금리/달러/주요 지수)을 제시해라."
        )

    if mode == "WEEKEND":
        return (
            "현재는 주말/휴장 구간(WEEKEND). "
            "가장 최근 거래일(한국/미국) 핵심 요약 + 주말 동안 체크해야 할 이벤트/리스크를 중심으로 쓰고, "
            "다음 거래일 시나리오(상방/하방 트리거)를 제시해라."
        )

    # fallback
    return (
        "현재는 일반 구간. "
        "한국/미국 시장의 최근 흐름을 연결해 핵심 이벤트와 다음 세션의 체크포인트를 정리해라."
    )


# -----------------------------
# news packing (IMPORTANT: include description+link for depth)
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
# optional market add-ons: sectors/movers/flows
# -----------------------------
def _sector_highlights(sectors: Optional[List[Dict[str, Any]]], topk: int = 3) -> Dict[str, Any]:
    """
    sectors: list of dicts with keys like:
      - sector/name
      - ret1d_pct
      - (optional) net_buy_foreign, net_buy_inst, net_buy_retail
    """
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

    # "특징"이 있는지 판단(너무 억지로 넣지 않기 위함)
    has_feature = dispersion >= 1.5 or abs(leaders[0]["ret1d_pct"]) >= 2.0 or abs(laggards[0]["ret1d_pct"]) >= 2.0

    return {
        "available": True,
        "has_feature": bool(has_feature),
        "dispersion_pctp": round(dispersion, 2),
        "leaders": leaders,
        "laggards": laggards,
    }


def _mover_highlights(movers: Optional[List[Dict[str, Any]]], topk: int = 5) -> Dict[str, Any]:
    """
    movers: list of dicts with keys like:
      - name/ticker
      - ret1d_pct
      - (optional) net_buy_foreign/net_buy_inst/net_buy_retail
    """
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
    """
    Prefer tags. If tags are empty, fallback to keyword buckets.
    Output: [{theme: str, ids: [..], count: int}]
    """
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
            # fallback keyword bucket (very light)
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
        # 중복 제거 유지
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


# -----------------------------
# main
# -----------------------------
def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    story_cfg = cfg.get("story", {}) or {}

    model = llm_cfg.get("story_model", "gpt-5.2")
    fallback_model = llm_cfg.get("story_fallback_model", "gpt-5.2")
    editor_model = llm_cfg.get("story_editor_model", model)

    temperature = float(llm_cfg.get("story_temperature", 0.25))
    editor_temperature = float(llm_cfg.get("story_editor_temperature", 0.15))

    max_tokens = int(llm_cfg.get("story_max_output_tokens", 2600))
    editor_max_tokens = int(llm_cfg.get("story_editor_max_output_tokens", max_tokens))

    # length knobs (실제로 분량을 늘리는 핵심)
    target_chars = int(story_cfg.get("target_chars", 3000))  # 기본 더 길게
    max_paragraphs = int(story_cfg.get("max_paragraphs", 6))
    min_paragraphs = int(story_cfg.get("min_paragraphs", 4))
    sentences_per_paragraph = story_cfg.get("sentences_per_paragraph", "2-4")  # 문자열로 전달

    enable_editor_pass = bool(story_cfg.get("enable_editor_pass", True))
    enable_expander_pass = bool(story_cfg.get("enable_expander_pass", True))

    mode = fact_pack.get("run_mode") or "AFTERCLOSE"
    guidance = _mode_guidance(mode)

    # news size knobs
    kr_items_n = int(story_cfg.get("kr_items", 28))
    gl_items_n = int(story_cfg.get("global_items", 24))
    desc_chars = int(story_cfg.get("news_desc_chars", 360))
    max_themes = int(story_cfg.get("max_themes", 6))

    # main news
    news_kr = _clip_news(fact_pack.get("news_kr", []) or [], kr_items_n, desc_chars=desc_chars)
    news_gl = _clip_news(fact_pack.get("news_overnight", []) or fact_pack.get("news_global", []) or [], gl_items_n, desc_chars=desc_chars)

    # macro/policy brief
    brief_kr = _clip_news(fact_pack.get("brief_kr", []) or [], int(story_cfg.get("brief_kr_items", 14)), desc_chars=desc_chars)
    brief_gl = _clip_news(fact_pack.get("brief_global", []) or [], int(story_cfg.get("brief_gl_items", 14)), desc_chars=desc_chars)

    market = _format_market_snapshot(fact_pack.get("market", []) or [])
    drivers = report.get("top_drivers", []) if isinstance(report, dict) else []
    risks = report.get("risk_radar", []) if isinstance(report, dict) else []

    # sector / movers / flows (optional)
    sector_hl = _sector_highlights(fact_pack.get("kr_sectors"), topk=int(story_cfg.get("sector_topk", 3)))
    mover_hl = _mover_highlights(fact_pack.get("kr_movers"), topk=int(story_cfg.get("mover_topk", 5)))

    # themes
    theme_map_kr = _build_theme_map(news_kr, max_themes=max_themes)
    theme_map_gl = _build_theme_map(news_gl, max_themes=max_themes)

    # investor flows (optional): keep it compact for context
    krx_flows = fact_pack.get("krx_flows") or {}
    krx_flows_compact = {}
    for mkt in ["KOSPI", "KOSDAQ"]:
        p = krx_flows.get(mkt) or {}
        nb = (p.get("net_buy_1e8krw") or {})
        if nb:
            # 대표 키(있으면)만 우선 포함
            keep = {}
            for k in ["외국인", "기관합계", "개인", "기타법인", "금융투자", "연기금"]:
                if k in nb:
                    keep[k] = nb[k]
            # 혹시 대표 키가 하나도 없으면 그냥 일부만
            if not keep:
                keep = dict(list(nb.items())[:6])
            krx_flows_compact[mkt] = {
                "date": p.get("date"),
                "net_buy_1e8krw": keep,
            }
            
    context = {
        "asof": fact_pack.get("asof"),
        "generated_at_kst": fact_pack.get("generated_at_kst"),
        "run_mode": mode,
        "mode_guidance": guidance,
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
너는 한국 sell-side 증권사 리서치센터의 'Daily Market Review'을 작성하는 시황/Quant 애널리스트다.
아래 컨텍스트(JSON)에 있는 '시장 수치'와 '뉴스(제목+description+링크+태그+ID)'만 근거로 사용해라.
컨텍스트 밖의 사실/일정/수치를 만들어내면 안 된다.

[산출 시각/장상황 모드]
- run_mode={mode}
- mode_guidance: {guidance}

[핵심 문제: 문단 연결성]
- 문단이 따로 놀지 않게, 각 문단 첫 문장은 반드시 "이전 문단의 결론"을 한 번 받아서 이어가라(예: '이런 배경에서', '다만', '한편', '그 결과').
- 주제가 바뀌면 반드시 '왜 지금 이 주제로 넘어가는지' 연결 문장을 1문장 넣어라.
- 같은 말을 반복하지 말고, 원인→경로→결과(Transmission)로 논리를 전개해라.

[작성 목표]
- 결과물은 bullet이 아니라 서론→(전일/오늘 국내장)→원인·촉매(뉴스 연결)→크로스에셋 반응→(필요시)Macro Brief→오버나이트→체크포인트→결론으로 이어지는 '한 편의 글'이다.
- 문단 수는 {min_paragraphs}~{max_paragraphs}개, 문단당 {sentences_per_paragraph}문장.
- 반드시 “원인/촉매(뉴스) → 시장 반응(지수/환율/금리/원자재/변동성)”의 인과를 문장으로 연결해라.
- themes_kr/themes_global을 참고해 '큰 주제 4~6개' 중심으로 전개하되, 억지로 다 넣지 말고 가장 설명력이 높은 축을 우선하라.

[채권/금리(필수 조건부)]
- 컨텍스트.market 안에 kind="yield" 자산이 1개라도 있으면, 채권/금리 시황 문단을 최소 1개 포함해라.
- 금리 자산의 변동은 ret1d_pct가 아니라 chg1d_bp(=bp 변화)와 level(금리 레벨)을 사용해라.
- 전개는 "금리 변화 → (뉴스 기반) 기대/리스크 경로 → 주식/달러/원자재로의 전이" 순서를 우선하라.

[업종/종목/수급(추가)]
- kr_sector_highlights.available=True이고 has_feature=True이면:
  업종 강/약(상위/하위)과 수치(수익률 %)를 자연스럽게 본문에 녹여라.
  단, ‘강제 1문단’처럼 티 나게 넣지 말고, 오늘 흐름을 설명하는 과정에서 자연스럽게 연결해라.
- kr_mover_highlights가 있으면, 종목 레벨은 “테마/뉴스 흐름과 연결이 가능한 경우”에만 간단히 언급해라(과도한 나열 금지).
- 컨텍스트.krx_flows에 값이 있으면, KOSPI/KOSDAQ 투자자별 순매수(억원)를 1~2문장으로 자연스럽게 본문에 녹여라.
- 수급 수치는 net_buy_1e8krw(=억원)만 사용하고, 표기는 "외국인/기관/개인" 3개를 우선으로 하되 컨텍스트에 있는 키만 써라.
- krx_flows가 비어 있으면 수급을 억지로 쓰거나 추정하지 마라.
- 컨텍스트.krx_flow_tops가 있으면, "오늘 수급 주도 종목 TOP5"를 1회만 언급해라.
  단, 종목을 5개 그대로 나열하지 말고 (가능하면) 2~3개만 대표로 언급하고,
  나머지는 "상위권" 정도로 뭉뚱그려라. 과도한 나열 금지.
- 컨텍스트.rally_decomp가 있으면, "외국인 주도 / 기관 주도 / 개인 주도"를 단정하지 말고
  foreign_1e8krw / retail_1e8krw 수치에 근거해 "~가능성", "~성격" 정도로만 표현해라.
  (dominant_actor_hint는 힌트일 뿐이며, 시장 전체 수급으로만 코멘트할 것)

[Macro/Policy Market Brief]
- brief_kr / brief_global을 활용해 ‘주식시장과 1:1로 직접 연결되지 않더라도 시황/퀀트가 알아야 할 이슈’를 1~2개 문단으로 정리해라.
- 영향은 단정하지 말고 시나리오(상방/하방/변동성) 형태로.

[출처 표기(중요)]
- 본문에 [Nxx] 같은 표기는 쓰지 마라.
- 글 마지막에 "## Sources" 섹션을 만들고, 실제로 본문에서 사용한 뉴스만
  "- (ID) 제목 | 매체 | 링크" 형식으로 나열해라.
- 링크는 컨텍스트에 있는 link만 사용해라(새 링크 생성 금지).

[문체]
- 증권사 시황 톤(중립적/단정 피하기: "~로 해석", "~가능성", "~에 주목").
- 학생 에세이 느낌 금지: 구어체/감탄/과장 금지.
"""

    user_prompt = "컨텍스트(JSON):\n```json\n" + json.dumps(context, ensure_ascii=False, indent=2) + "\n```"

    client = OpenAI()

    # 1) draft
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
        log.info(f"[story] model_used={model_used} | usage={usage}")

    txt = (draft or "").strip()

    # 2) editor pass: 연결성/문체 다듬기(새 사실 추가 금지)
    if enable_editor_pass and txt:
        editor_sys = """
너는 한국 sell-side 리서치센터의 '에디터'다.
아래 초안을 '더 자연스럽고, 문단 간 연결이 매끄럽고, 리서치센터 톤'으로 편집해라.

[절대 규칙]
- 새 사실/새 수치/새 이벤트를 추가하지 마라.
- 컨텍스트 밖 정보를 추론해 단정하지 마라.
- 논리 전개를 더 명확히(원인→경로→결과), 문단 연결 문장을 강화해라.
- 반복/나열을 줄이고, 문장 구조를 성숙하게 바꿔라(학생 느낌 제거).
- "## Sources" 섹션은 유지하되, 본문에서 실제로 언급한 항목만 남겨라.
"""
        editor_user = "초안:\n```text\n" + txt + "\n```"

        try:
            edited, editor_used, _ = _responses_call(
                client, editor_model, editor_sys, editor_user, temperature=editor_temperature, max_tokens=editor_max_tokens
            )
            if edited and edited.strip():
                txt = edited.strip()
                if log:
                    log.info(f"[story-editor] model_used={editor_used}")
        except Exception as e:
            if log:
                log.warning(f"editor pass failed: {e}")

    # 3) expander pass: 분량이 부족하면 ‘근거(description) 기반으로’ 확장
    if enable_expander_pass and txt and len(txt) < int(target_chars * 0.9):
        exp_sys = """
너는 한국 sell-side 리서치센터의 '확장 편집자'다.
아래 글을 더 자세하게 확장하되, 반드시 기존 컨텍스트/초안에 들어있는 근거(description)에만 기반해라.

[규칙]
- 새 사실/새 수치/새 일정 추가 금지.
- '왜 그 뉴스가 시장에 영향을 줬는지'를 경로(금리→밸류에이션→업종/스타일, 유가→인플레→금리 기대 등)로 설명을 보강해라.
- 문단 사이 연결을 더 강화해라.
- "## Sources"는 유지.
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

    asof = fact_pack.get("asof") or ""
    gen = fact_pack.get("generated_at_kst") or ""
    header = f"# Daily Market Review (as of {asof})\n\n" + (f"> generated_at_kst: {gen} | mode: {mode}\n\n" if gen else "")
    return header + (txt or "").strip() + "\n"
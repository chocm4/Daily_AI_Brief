import json
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI


def _to_float(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def _fmt_pct(v) -> str:
    v = _to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+.2f}%"


def _fmt_bp(v) -> str:
    v = _to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+.1f}bp"


def _fmt_1e8(v) -> str:
    v = _to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+,.0f}억원"


def _benchmark(fact_pack: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    mc = fact_pack.get("market_context") or {}
    bench = (mc.get("benchmark_summary") or {}).get(name)
    if bench:
        return bench
    for x in fact_pack.get("market", []) or []:
        if (x.get("name") or x.get("asset")) == name:
            kind = (x.get("kind") or "price").lower()
            if kind == "yield":
                return {"name": name, "kind": kind, "chg1d_bp": x.get("chg1d_bp")}
            return {"name": name, "kind": kind, "ret1d_pct": x.get("ret1d_pct")}
    return None


def _render_sources_md(news_kr: list, news_gl: list, top_k: int = 5) -> str:
    pool = []
    seen = set()
    for x in (news_kr or []) + (news_gl or []):
        key = (str(x.get("title", "")).strip(), str(x.get("link", "") or x.get("url", "")).strip())
        if key in seen:
            continue
        seen.add(key)
        pool.append(x)

    def _score(item):
        try:
            return float(item.get("score"))
        except Exception:
            return float("-inf")

    pool = sorted(pool, key=_score, reverse=True)[:top_k]
    lines = ["## Sources"]
    if not pool:
        lines.append("- 없음")
        return "\n".join(lines)

    for x in pool:
        score = x.get("score")
        try:
            score_txt = f"{float(score):.2f}"
        except Exception:
            score_txt = str(score) if score not in [None, ""] else "N/A"
        lines.append(f"- ({x.get('id','')}) {x.get('title','')} | {x.get('source','')} | score={score_txt} | {x.get('link','') or x.get('url','')}")
    return "\n".join(lines)


def _market_lede(fact_pack: Dict[str, Any]) -> str:
    kospi = _benchmark(fact_pack, "KOSPI")
    kosdaq = _benchmark(fact_pack, "KOSDAQ")
    spx = _benchmark(fact_pack, "S&P500")
    ndx = _benchmark(fact_pack, "NASDAQ")
    usdkrw = _benchmark(fact_pack, "USDKRW")
    ust10 = _benchmark(fact_pack, "UST 10Y")

    lines: List[str] = []

    bits1 = []
    if kospi and kospi.get("ret1d_pct") is not None:
        bits1.append(f"KOSPI {_fmt_pct(kospi.get('ret1d_pct'))}")
    if kosdaq and kosdaq.get("ret1d_pct") is not None:
        bits1.append(f"KOSDAQ {_fmt_pct(kosdaq.get('ret1d_pct'))}")
    if bits1:
        lines.append(", ".join(bits1) + ". 지수의 방향성과 상대강도가 한국 시황의 첫 단서였다.")

    bits2 = []
    if spx and spx.get("ret1d_pct") is not None:
        bits2.append(f"S&P500 {_fmt_pct(spx.get('ret1d_pct'))}")
    if ndx and ndx.get("ret1d_pct") is not None:
        bits2.append(f"NASDAQ {_fmt_pct(ndx.get('ret1d_pct'))}")
    if usdkrw and usdkrw.get("ret1d_pct") is not None:
        bits2.append(f"원/달러 {_fmt_pct(usdkrw.get('ret1d_pct'))}")
    if ust10 and ust10.get("chg1d_bp") is not None:
        bits2.append(f"미국 10년물 {_fmt_bp(ust10.get('chg1d_bp'))}")
    if bits2:
        lines.append(", ".join(bits2) + ". 해외 지수와 금리·환율 경로가 개장 전 해석의 핵심이다.")

    mc = fact_pack.get("market_context") or {}
    sectors = ((mc.get("sector_summary") or {}).get("feature_sectors") or [])[:4]
    if sectors:
        leaders = [x for x in sectors if x.get("direction") == "leader"][:2]
        laggards = [x for x in sectors if x.get("direction") == "laggard"][:2]
        parts = []
        if leaders:
            parts.append("강세 업종 " + ", ".join(f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in leaders))
        if laggards:
            parts.append("약세 업종 " + ", ".join(f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in laggards))
        if parts:
            lines.append("; ".join(parts) + ". 업종 확산 여부와 쏠림 강도를 함께 볼 필요가 있다.")

    flows = (mc.get("flow_summary") or {}).get("KOSPI") or (mc.get("flow_summary") or {}).get("KOSDAQ")
    if flows:
        parts = []
        if flows.get("foreign_1e8krw") is not None:
            parts.append(f"외국인 {_fmt_1e8(flows.get('foreign_1e8krw'))}")
        if flows.get("institution_1e8krw") is not None:
            parts.append(f"기관 {_fmt_1e8(flows.get('institution_1e8krw'))}")
        if flows.get("retail_1e8krw") is not None:
            parts.append(f"개인 {_fmt_1e8(flows.get('retail_1e8krw'))}")
        if parts:
            lines.append("수급은 " + ", ".join(parts) + " 수준이었다. 절대금액 기준으로 주도 주체를 확인할 필요가 있다.")

    return "\n\n".join(lines).strip()


def _build_llm_context(fact_pack: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    mc = fact_pack.get("market_context") or {}
    return {
        "headline": report.get("headline"),
        "today_5lines": report.get("today_5lines") or [],
        "kr_bullets": report.get("kr_bullets") or [],
        "overnight_bullets": report.get("overnight_bullets") or [],
        "top_drivers": report.get("top_drivers") or [],
        "market_context": {
            "index_summary": mc.get("index_summary") or {},
            "ficc_summary": mc.get("ficc_summary") or {},
            "sector_summary": mc.get("sector_summary") or {},
            "flow_summary": mc.get("flow_summary") or {},
            "feature_stocks": mc.get("feature_stocks") or [],
            "style_flags": mc.get("style_flags") or [],
        },
        "events_top": fact_pack.get("events_top") or [],
    }


def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("story_model", llm_cfg.get("model", "gpt-5.4"))
    temperature = float(llm_cfg.get("story_temperature", 0.1))
    max_tokens = int(llm_cfg.get("story_max_output_tokens", 2200))

    asof = fact_pack.get("asof") or ""
    mode = fact_pack.get("run_mode") or ""
    gen = fact_pack.get("generated_at_kst") or ""

    header = f"# Daily Market Review (as of {asof})\n\n"
    if gen:
        header += f"> generated_at_kst: {gen} | mode: {mode}\n\n"

    numeric_lede = _market_lede(fact_pack)
    sources_md = _render_sources_md(
        fact_pack.get("news_kr", []) or [],
        fact_pack.get("news_overnight", []) or fact_pack.get("news_global", []) or [],
        top_k=5,
    )

    body = ""
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key)
        context = _build_llm_context(fact_pack, report)
        sys_prompt = """
너는 한국 sell-side 데일리 시황 작성자다.
반드시 제공된 JSON 안의 정보만 사용한다.
새 사실/새 숫자 추가 금지.
이미 앞 문단에 숫자가 들어가 있으므로, 본문에서는 그 숫자가 의미하는 바를 3~4문단으로 정리해라.
숫자를 다시 쓸 수 있는 곳에서는 생략하지 마라.
'## Sources' 섹션은 작성하지 마라.
"""
        user_prompt = "다음 JSON을 바탕으로 시황 본문만 작성해라.\n" + json.dumps(context, ensure_ascii=False)
        try:
            resp = client.responses.create(
                model=model,
                input=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            body = getattr(resp, "output_text", "") or ""
        except Exception as e:
            if log:
                log.warning(f"story generation failed: {e}")

    body = (body or "").strip().replace("## Sources", "").strip()
    parts = []
    if numeric_lede:
        parts.append(numeric_lede)
    if body:
        parts.append(body)
    text = "\n\n".join(parts).strip()

    return header + text + "\n\n" + sources_md + "\n"

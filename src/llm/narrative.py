import json
import os
import re
from typing import Any, Dict
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


def _benchmark(fact_pack: Dict[str, Any], name: str) -> Dict[str, Any] | None:
    mc = fact_pack.get("market_context") or {}
    bench = (mc.get("benchmark_summary") or {}).get(name)
    if bench:
        return bench
    for x in fact_pack.get("market", []) or []:
        if (x.get("name") or x.get("asset")) == name:
            kind = (x.get("kind") or "price").lower()
            if kind == "yield":
                return {"name": name, "kind": kind, "chg1d_bp": x.get("chg1d_bp"), "level": x.get("level")}
            return {"name": name, "kind": kind, "ret1d_pct": x.get("ret1d_pct"), "level": x.get("level")}
    return None


def _market_lede(fact_pack: Dict[str, Any]) -> str:
    kospi = _benchmark(fact_pack, "KOSPI")
    kosdaq = _benchmark(fact_pack, "KOSDAQ")
    spx = _benchmark(fact_pack, "S&P500")
    ndx = _benchmark(fact_pack, "NASDAQ")
    usdkrw = _benchmark(fact_pack, "USDKRW")
    ust10 = _benchmark(fact_pack, "UST 10Y")

    lines = []

    bits1 = []
    if kospi and kospi.get("ret1d_pct") is not None:
        bits1.append(f"KOSPI {_fmt_pct(kospi.get('ret1d_pct'))}")
    if kosdaq and kosdaq.get("ret1d_pct") is not None:
        bits1.append(f"KOSDAQ {_fmt_pct(kosdaq.get('ret1d_pct'))}")
    if bits1:
        if kospi and kosdaq and kospi.get("ret1d_pct") is not None and kosdaq.get("ret1d_pct") is not None and float(kospi.get("ret1d_pct")) > float(kosdaq.get("ret1d_pct")):
            tail = "코스피 우위 반등이 1차 포인트였다."
        else:
            tail = "지수 반등 강도 점검이 필요했다."
        lines.append(", ".join(bits1) + f". {tail}")

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
        lines.append(", ".join(bits2) + ". 해외 지수와 금리·환율 조합이 개장 전 심리를 뒷받침했다.")

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
            "sector_summary": mc.get("sector_summary") or {},
            "flow_summary": mc.get("flow_summary") or {},
            "feature_stocks": mc.get("feature_stocks") or [],
            "style_flags": mc.get("style_flags") or [],
        },
        "events_top": fact_pack.get("events_top") or [],
    }


def _clean_body(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("## Sources", "").strip()
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    cleaned = []
    for p in paras[:3]:
        sents = re.split(r'(?<=[.!?다])\s+', p)
        sents = [s.strip() for s in sents if s.strip()]
        cleaned.append(" ".join(sents[:2]).strip())
    return "\n\n".join(cleaned).strip()


def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("story_model", llm_cfg.get("model", "gpt-5.4"))
    temperature = float(llm_cfg.get("story_temperature", 0.1))
    max_tokens = int(llm_cfg.get("story_max_output_tokens", 1600))

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

    api_key = os.environ.get("OPENAI_API_KEY")
    body = ""
    if api_key:
        client = OpenAI(api_key=api_key)
        context = _build_llm_context(fact_pack, report)
        sys_prompt = """
너는 한국 sell-side 데일리 시황 작성자다.
반드시 제공된 JSON 안의 정보만 사용한다.
새 사실/새 숫자 추가 금지.
첫 2개 문단의 숫자 요약은 이미 위에 제시되어 있으니, 본문에서는 같은 숫자를 반복하지 마라.
본문은 정확히 3개 문단으로 작성하고, 각 문단은 최대 2문장으로 제한한다.
문단 구성은 1) 국내 반등의 성격 2) 정책/해외 변수의 의미 3) 내일 이후 체크포인트 순서로 한다.
같은 표현과 같은 논지를 반복하지 마라.
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

    body = _clean_body(body)
    chunks = []
    if numeric_lede:
        chunks.append(numeric_lede)
    if body:
        chunks.append(body)
    text = "\n\n".join(chunks).strip()

    return header + text + "\n\n" + sources_md + "\n"

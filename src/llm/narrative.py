import json
import os
import re
from typing import Any, Dict, List
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
        key = (str(x.get("event_id", "")).strip(), str(x.get("title", "")).strip(), str(x.get("url", "")).strip())
        if key in seen:
            continue
        seen.add(key)
        pool.append(x)

    def _score(item):
        try:
            return float(item.get("score"))
        except Exception:
            return float("-inf")

    pool = sorted(pool, key=lambda x: (_score(x), int(x.get("cluster_mentions") or 1)), reverse=True)[:top_k]
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
        lines.append(
            f"- ({x.get('event_id','')}/{x.get('id','')}) {x.get('representative_title') or x.get('title','')} | {x.get('source','')} | score={score_txt} | {x.get('url','')}"
        )
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
                return {
                    "name": name,
                    "kind": kind,
                    "chg1d_bp": x.get("chg1d_bp"),
                    "level": x.get("level"),
                }
            return {
                "name": name,
                "kind": kind,
                "ret1d_pct": x.get("ret1d_pct"),
                "level": x.get("level"),
            }
    return None


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


def _build_opening_paragraph(fact_pack: Dict[str, Any]) -> str:
    kospi = _benchmark(fact_pack, "KOSPI")
    kosdaq = _benchmark(fact_pack, "KOSDAQ")
    usdkrw = _benchmark(fact_pack, "USDKRW")
    ust10 = _benchmark(fact_pack, "UST 10Y")
    spx = _benchmark(fact_pack, "S&P500")
    ndx = _benchmark(fact_pack, "NASDAQ")

    seg1 = []
    if kospi and kospi.get("ret1d_pct") is not None:
        seg1.append(f"KOSPI가 {_fmt_pct(kospi.get('ret1d_pct'))}")
    if kosdaq and kosdaq.get("ret1d_pct") is not None:
        seg1.append(f"KOSDAQ이 {_fmt_pct(kosdaq.get('ret1d_pct'))}")

    p1 = ""
    if seg1:
        if len(seg1) == 2:
            p1 = f"오늘 국내 증시는 {seg1[0]}, {seg1[1]}를 기록했다."
        else:
            p1 = f"오늘 국내 증시는 {seg1[0]}를 기록했다."

    seg2 = []
    if usdkrw and usdkrw.get("ret1d_pct") is not None:
        seg2.append(f"원/달러는 {_fmt_pct(usdkrw.get('ret1d_pct'))}")
    if ust10 and ust10.get("chg1d_bp") is not None:
        seg2.append(f"미국 10년물은 {_fmt_bp(ust10.get('chg1d_bp'))}")
    if spx and spx.get("ret1d_pct") is not None:
        seg2.append(f"S&P500은 {_fmt_pct(spx.get('ret1d_pct'))}")
    if ndx and ndx.get("ret1d_pct") is not None:
        seg2.append(f"NASDAQ은 {_fmt_pct(ndx.get('ret1d_pct'))}")

    p2 = ""
    if seg2:
        joined = ", ".join(seg2[:4])
        p2 = f"개장 전 여건으로는 {joined} 수준이 확인됐다."

    return " ".join([x for x in [p1, p2] if x]).strip()


def _build_driver_anchor(fact_pack: Dict[str, Any]) -> str:
    events = (fact_pack.get("events_top") or [])[:3]
    if not events:
        return ""
    chunks = []
    for ev in events:
        theme = str(ev.get("theme") or ev.get("summary") or "").strip()
        if not theme:
            continue
        impact = ev.get("impact_scope") or "secondary"
        chunks.append(f"{theme}({impact})")
    if not chunks:
        return ""
    return "오늘 핵심 이벤트는 " + ", ".join(chunks) + "였다."


def _split_sentences(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    raw = re.split(r'(?<=[.!?])\s+|(?<=다\.)\s+|(?<=다)\s+', text)
    out = []
    for x in raw:
        x = x.strip()
        if x:
            out.append(x)
    return out


def _drop_duplicate_market_sentence(sentences: List[str], opening: str) -> List[str]:
    if not sentences:
        return sentences

    opening_keys = [k for k in ["KOSPI", "KOSDAQ", "원/달러", "USDKRW", "미국 10년물", "S&P500", "NASDAQ"] if k in opening]

    filtered = []
    for i, s in enumerate(sentences):
        if i == 0:
            has_market_key = sum(1 for k in opening_keys if k in s) >= 3
            has_pct = "%" in s or "bp" in s
            if has_market_key and has_pct:
                continue
        filtered.append(s)
    return filtered


def _ensure_complete_sentence(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    if s.endswith(("다.", "요.", ".", "!", "?")):
        return s
    return s + "."


def _chunk_paragraph(sentences: List[str], target_chars: int = 170, max_chars: int = 230) -> List[str]:
    paras = []
    cur = []
    cur_len = 0

    for s in sentences:
        s = _ensure_complete_sentence(s)
        if not s:
            continue

        add_len = len(s) + (1 if cur else 0)
        if cur and (cur_len + add_len > max_chars):
            paras.append(" ".join(cur).strip())
            cur = [s]
            cur_len = len(s)
        else:
            cur.append(s)
            cur_len += add_len
            if cur_len >= target_chars:
                paras.append(" ".join(cur).strip())
                cur = []
                cur_len = 0

    if cur:
        paras.append(" ".join(cur).strip())

    return [p for p in paras if p.strip()]


def _clean_body(text: str, opening: str) -> str:
    text = (text or "").strip()
    text = text.replace("## Sources", "").strip()
    text = re.sub(r'\n{3,}', '\n\n', text)

    raw_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences: List[str] = []
    for p in raw_paras:
        sentences.extend(_split_sentences(p))

    sentences = _drop_duplicate_market_sentence(sentences, opening)
    sentences = [_ensure_complete_sentence(x) for x in sentences if x.strip()]

    if not sentences:
        return ""

    paras = _chunk_paragraph(sentences, target_chars=170, max_chars=230)
    return "\n\n".join(paras[:6]).strip()


def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("story_model", llm_cfg.get("model", "gpt-5.4"))
    temperature = float(llm_cfg.get("story_temperature", 0.1))
    max_tokens = int(llm_cfg.get("story_max_output_tokens", 2400))

    asof = fact_pack.get("asof") or ""
    mode = fact_pack.get("run_mode") or ""
    gen = fact_pack.get("generated_at_kst") or ""

    header = f"# Daily Market Review (as of {asof})\n\n"
    if gen:
        header += f"> generated_at_kst: {gen} | mode: {mode}\n\n"

    opening = _build_opening_paragraph(fact_pack)
    driver_anchor = _build_driver_anchor(fact_pack)
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
첫 도입 문단에 핵심 지수·환율·금리 숫자가 이미 자연스럽게 들어가 있으므로, 이후에는 같은 숫자를 기계적으로 반복하지 마라.
하지만 해석에 꼭 필요하면 숫자를 1회 정도 다시 언급하는 것은 허용한다.
본문은 숫자 문단을 따로 만들지 말고, 내용 속에 자연스럽게 녹여라.
문단은 너무 길게 이어가지 말고, 한 문단이 약 150~200자 안팎이 되도록 적절히 호흡을 나눠라.
문단은 보통 4~6개가 되도록 하고, 문단별 분량은 대체로 비슷하게 맞춰라.
문장이 잘린 채 끝나면 안 된다.
문단 구성은 대체로 1) 오늘 장의 성격 2) 업종/수급 또는 특징주 3) 국내 변수 4) 해외 변수 5) 내일 이후 체크포인트 흐름을 따른다.
같은 표현과 같은 논지를 반복하지 마라.
'## Sources' 섹션은 작성하지 마라.
반드시 events_top 상위 이벤트를 본문 앞단에서 우선 활용하라.
관찰과 해석을 구분하고, 인과가 약하면 '추정:'으로 시작하라.
"""
        user_prompt = "다음 JSON을 바탕으로 시황 본문만 작성해라.\n" + json.dumps(context, ensure_ascii=False)

        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            body = getattr(resp, "output_text", "") or ""
        except Exception as e:
            if log:
                log.warning(f"story generation failed: {e}")

    body = _clean_body(body, opening)

    chunks = []
    if opening:
        chunks.append(opening)
    if driver_anchor:
        chunks.append(driver_anchor)
    if body:
        chunks.append(body)

    text = "\n\n".join(chunks).strip()
    return header + text + "\n\n" + sources_md + "\n"

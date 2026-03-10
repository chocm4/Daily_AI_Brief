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

    opening_keys = [
        k for k in ["KOSPI", "KOSDAQ", "원/달러", "USDKRW", "미국 10년물", "S&P500", "NASDAQ"]
        if k in opening
    ]

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


def _remove_meta_labels(text: str) -> str:
    text = text or ""
    text = re.sub(r'\b관찰:\s*', '', text)
    text = re.sub(r'\b추정:\s*', '', text)
    text = re.sub(r'\b해석:\s*', '', text)
    text = re.sub(r'\b체크포인트:\s*', '', text)
    return text.strip()


def _chunk_paragraph(sentences: List[str], target_chars: int = 180, max_chars: int = 240) -> List[str]:
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
    text = _remove_meta_labels(text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    raw_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences: List[str] = []
    for p in raw_paras:
        sentences.extend(_split_sentences(p))

    sentences = _drop_duplicate_market_sentence(sentences, opening)
    sentences = [_ensure_complete_sentence(x) for x in sentences if x.strip()]

    if not sentences:
        return ""

    paras = _chunk_paragraph(sentences, target_chars=180, max_chars=240)
    return "\n\n".join(paras[:6]).strip()


def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("story_model", llm_cfg.get("model", "gpt-5.4"))
    temperature = float(llm_cfg.get("story_temperature", 0.1))
    max_tokens = int(llm_cfg.get("story_max_output_tokens", 2400))

    # 기본값을 False로 둬서, 별도 설정하지 않으면 Sources 섹션이 나오지 않게 함
    show_sources = bool(llm_cfg.get("show_sources_in_story", False))

    asof = fact_pack.get("asof") or ""
    mode = fact_pack.get("run_mode") or ""
    gen = fact_pack.get("generated_at_kst") or ""

    header = f"# Daily Market Review (as of {asof})\n\n"
    if gen:
        header += f"> generated_at_kst: {gen} | mode: {mode}\n\n"

    opening = _build_opening_paragraph(fact_pack)

    api_key = os.environ.get("OPENAI_API_KEY")
    body = ""
    if api_key:
        client = OpenAI(api_key=api_key)
        context = _build_llm_context(fact_pack, report)

        sys_prompt = """
너는 한국 sell-side 데일리 시황 작성자다.
반드시 제공된 JSON 안의 정보만 사용한다.
새 사실, 새 숫자, 새 해석 축을 임의로 추가하지 마라.

출력 규칙:
- 처음부터 끝까지 자연스럽게 읽히는 서술형 한국어 본문으로 작성하라.
- 기사 제목을 나열하지 마라.
- 이벤트 라벨이나 분류명(예: market_moving, sector_moving, secondary)을 본문에 쓰지 마라.
- 괄호 속 메타정보를 쓰지 마라.
- '관찰:', '추정:', '해석:' 같은 꼬리표를 붙이지 마라.
- 문단은 4~6개 정도로 나누되, 각 문단은 자연스러운 줄글이어야 한다.
- 첫 도입 문단에 핵심 지수·환율·금리 숫자가 이미 들어가 있으므로, 이후에는 같은 숫자를 기계적으로 반복하지 마라.
- 다만 해석상 꼭 필요하면 같은 숫자를 1회 정도 다시 언급하는 것은 허용한다.
- 본문은 숫자 문단을 따로 만들지 말고, 내용 속에 자연스럽게 녹여라.
- 같은 표현과 같은 논지를 반복하지 마라.
- events_top 상위 이벤트를 앞부분에서 우선 반영하되, 기사 제목을 그대로 옮기지 말고 자연스러운 시황 문장으로 재구성하라.
- 인과가 약하면 단정하지 말고, '부담을 키웠다', '배경으로 작용했다', '영향을 준 것으로 보인다' 같은 완곡한 표현을 사용하라.
- 업종/수급 데이터가 비어 있으면 억지로 채우지 말고 넘어가라.
- 마지막까지 보고서 본문처럼 매끄럽게 마무리하라.
- '## Sources' 같은 출처 섹션은 작성하지 마라.
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
    if body:
        chunks.append(body)

    text = "\n\n".join(chunks).strip()

    if show_sources:
        # 필요할 때만 뒤에 붙이기
        news_kr = fact_pack.get("news_kr", []) or []
        news_gl = fact_pack.get("news_overnight", []) or fact_pack.get("news_global", []) or []
        lines = ["## Sources"]
        pool = []
        seen = set()

        for x in news_kr + news_gl:
            key = (
                str(x.get("event_id", "")).strip(),
                str(x.get("title", "")).strip(),
                str(x.get("url", "")).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            pool.append(x)

        def _score(item):
            try:
                return float(item.get("score"))
            except Exception:
                return float("-inf")

        pool = sorted(
            pool,
            key=lambda x: (_score(x), int(x.get("cluster_mentions") or 1)),
            reverse=True
        )[:5]

        if not pool:
            lines.append("- 없음")
        else:
            for x in pool:
                score = x.get("score")
                try:
                    score_txt = f"{float(score):.2f}"
                except Exception:
                    score_txt = str(score) if score not in [None, ""] else "N/A"
                lines.append(
                    f"- ({x.get('event_id','')}/{x.get('id','')}) {x.get('representative_title') or x.get('title','')} | {x.get('source','')} | score={score_txt} | {x.get('url','')}"
                )

        return header + text + "\n\n" + "\n".join(lines) + "\n"

    return header + text + "\n"

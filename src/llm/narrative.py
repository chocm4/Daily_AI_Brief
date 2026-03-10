import json
import os
import re
from typing import Any, Dict, List, Optional
from openai import OpenAI


MARKET_KEYS = [
    "KOSPI", "KOSDAQ", "코스피", "코스닥",
    "원/달러", "USDKRW", "환율",
    "미국 10년물", "UST 10Y",
    "S&P500", "NASDAQ", "나스닥", "VIX", "WTI"
]


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


def _benchmark(fact_pack: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
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
        "news_kr": fact_pack.get("news_kr") or [],
        "news_global": fact_pack.get("news_global") or [],
        "news_overnight": fact_pack.get("news_overnight") or [],
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

    # '다'에서 무조건 자르지 말고, 종결형 '다.' 중심으로만 분리
    parts = re.split(r'(?<=[.!?])\s+|(?<=다\.)\s+|(?<=다!)\s+|(?<=다\?)\s+', text)
    out = []
    for x in parts:
        x = x.strip()
        if x:
            out.append(x)
    return out


def _extract_number_signals(s: str) -> List[str]:
    s = s or ""
    patterns = [
        r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?%',
        r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?bp',
        r'\d+(?:,\d{3})*(?:\.\d+)?원',
        r'\d+(?:,\d{3})*(?:\.\d+)?달러',
        r'\d+(?:,\d{3})*(?:\.\d+)?',
    ]
    found = []
    for p in patterns:
        found.extend(re.findall(p, s))
    return found


def _mentioned_market_keys(s: str) -> List[str]:
    s = s or ""
    found = []
    for k in MARKET_KEYS:
        if k in s:
            found.append(k)
    return found


def _normalize_for_overlap(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'[“”"\'‘’()\[\]{}]', '', s)
    return s.lower()


def _is_opening_duplicate_sentence(s: str, opening: str) -> bool:
    if not s.strip():
        return False

    s_keys = set(_mentioned_market_keys(s))
    o_keys = set(_mentioned_market_keys(opening))
    common_keys = s_keys.intersection(o_keys)

    nums = _extract_number_signals(s)

    # 케이스 1: opening과 같은 시장 숫자 문장을 다시 쓰는 경우
    if len(common_keys) >= 2 and len(nums) >= 2:
        return True

    # 케이스 2: opening과 매우 비슷한 자산군 조합 + 수익률 서술
    market_like = any(k in s for k in ["KOSPI", "KOSDAQ", "코스피", "코스닥", "원/달러", "환율", "S&P500", "NASDAQ"])
    perf_like = "%" in s or "bp" in s
    if market_like and perf_like and len(common_keys) >= 1 and len(nums) >= 3:
        return True

    # 케이스 3: 문장 내용 자체가 opening을 거의 재진술
    ns = _normalize_for_overlap(s)
    no = _normalize_for_overlap(opening)
    if ns and no:
        overlap_hits = 0
        for token in ["kospi", "kosdaq", "원/달러", "s&p500", "nasdaq", "%", "bp", "기록했다", "수준이 확인됐다"]:
            if token in ns and token in no:
                overlap_hits += 1
        if overlap_hits >= 3 and len(nums) >= 2:
            return True

    return False


def _drop_duplicate_market_sentences(sentences: List[str], opening: str) -> List[str]:
    if not sentences:
        return sentences

    filtered = []
    dropped_first_duplicate = False

    for i, s in enumerate(sentences):
        # 본문 초반 2문장까지는 opening 중복을 강하게 제거
        if i <= 1 and _is_opening_duplicate_sentence(s, opening):
            dropped_first_duplicate = True
            continue

        # 첫 중복이 제거된 뒤 바로 이어지는 추가 수익률 나열도 한 번 더 제거
        if dropped_first_duplicate and i <= 2:
            keys = _mentioned_market_keys(s)
            nums = _extract_number_signals(s)
            if len(keys) >= 2 and len(nums) >= 2 and ("%" in s or "bp" in s):
                continue

        filtered.append(s)

    return filtered


def _ensure_complete_sentence(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    if s.endswith(("다.", "요.", ".", "!", "?")):
        return s
    if s.endswith("다"):
        return s + "."
    return s + "."


def _remove_meta_labels(text: str) -> str:
    text = text or ""
    text = re.sub(r'\b관찰:\s*', '', text)
    text = re.sub(r'\b추정:\s*', '', text)
    text = re.sub(r'\b해석:\s*', '', text)
    text = re.sub(r'\b체크포인트:\s*', '', text)
    return text.strip()


def _remove_explicit_sources_block(text: str) -> str:
    if not text:
        return ""
    # LLM이 혹시 출처 섹션을 만들어도 잘라냄
    text = re.split(r'\n##\s*Sources\b|\n##\s*출처\b|\n###\s*Sources\b|\n###\s*출처\b', text)[0]
    return text.strip()


def _chunk_paragraph(sentences: List[str], target_chars: int = 180, max_chars: int = 260) -> List[str]:
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
    text = _remove_explicit_sources_block(text)
    text = _remove_meta_labels(text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    raw_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences: List[str] = []
    for p in raw_paras:
        sentences.extend(_split_sentences(p))

    sentences = _drop_duplicate_market_sentences(sentences, opening)

    clean_sentences = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        # 비정상 분절 최소 보정
        s = re.sub(r'([가-힣A-Za-z0-9])\.\s+([가-힣])', r'\1 \2', s)
        s = _ensure_complete_sentence(s)
        clean_sentences.append(s)

    if not clean_sentences:
        return ""

    paras = _chunk_paragraph(clean_sentences, target_chars=180, max_chars=260)
    return "\n\n".join(paras[:6]).strip()


def _collect_used_source_candidates(fact_pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    pool = []
    seen = set()

    candidates = []
    candidates.extend(fact_pack.get("news_kr", []) or [])
    candidates.extend(fact_pack.get("news_overnight", []) or [])
    candidates.extend(fact_pack.get("news_global", []) or [])

    for x in candidates:
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
            return float(item.get("score") or 0.0)
        except Exception:
            return 0.0

    def _driver_rank(item):
        try:
            return int(item.get("driver_rank") or 999)
        except Exception:
            return 999

    pool = sorted(
        pool,
        key=lambda x: (
            _driver_rank(x),
            -_score(x),
            -(int(x.get("cluster_mentions") or 1)),
            -(int(x.get("cluster_source_count") or x.get("source_count") or 1)),
        )
    )
    return pool[:5]


def _render_reference_articles(fact_pack: Dict[str, Any]) -> str:
    items = _collect_used_source_candidates(fact_pack)
    lines = ["## 참고 기사"]
    if not items:
        lines.append("- 없음")
        return "\n".join(lines)

    for x in items:
        title = x.get("representative_title") or x.get("title") or "제목 없음"
        url = x.get("url") or ""
        source = x.get("source") or ""
        try:
            score_txt = f"{float(x.get('score') or 0.0):.2f}"
        except Exception:
            score_txt = "N/A"

        if url:
            lines.append(f"- {title} | {source} | score={score_txt} | {url}")
        else:
            lines.append(f"- {title} | {source} | score={score_txt}")

    return "\n".join(lines)


def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("story_model", llm_cfg.get("model", "gpt-5.4"))
    temperature = float(llm_cfg.get("story_temperature", 0.1))
    max_tokens = int(llm_cfg.get("story_max_output_tokens", 2400))

    # 이번 요구사항 기준으로 기본 True
    show_reference_articles = bool(llm_cfg.get("show_reference_articles", True))

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
새 사실, 새 숫자, 새 기사, 새 해석 축을 임의로 추가하지 마라.

출력 규칙:
- 처음부터 끝까지 자연스럽게 읽히는 서술형 한국어 본문만 작성하라.
- 기사 제목을 나열하지 마라.
- 이벤트 라벨이나 분류명(예: market_moving, sector_moving, secondary)을 본문에 쓰지 마라.
- 괄호 속 메타정보를 쓰지 마라.
- '관찰:', '추정:', '해석:' 같은 꼬리표를 붙이지 마라.
- 첫 문단의 지수/환율/금리 숫자는 이미 별도로 제공되므로, 본문 첫 두 문장에서는 같은 수익률과 지수 숫자를 반복하지 마라.
- 특히 KOSPI, KOSDAQ, 원/달러, S&P500, NASDAQ의 등락률을 opening과 비슷한 형태로 다시 쓰지 마라.
- 숫자를 다시 쓰더라도 해석상 꼭 필요한 경우로 제한하라.
- 기사 제목을 거의 그대로 옮긴 문장을 만들지 마라.
- 문단은 4~6개로 나누고, 각 문단은 보고서 본문처럼 자연스러운 줄글이어야 한다.
- events_top 상위 이벤트를 앞부분에서 우선 반영하되, 기사 제목이 아니라 시황 해설 문장으로 재구성하라.
- 인과가 약하면 단정하지 말고, '배경으로 작용했다', '부담 요인으로 남았다', '영향을 준 것으로 보인다' 같은 완곡한 표현을 사용하라.
- 업종/수급 데이터가 비어 있으면 억지로 채우지 마라.
- 마지막까지 보고서 본문처럼 매끄럽게 마무리하라.
- 출처 섹션, 참고 기사 섹션, 링크 목록은 절대 본문에 쓰지 마라.
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
    result = header + text + "\n"

    if show_reference_articles:
        result += "\n" + _render_reference_articles(fact_pack) + "\n"

    return result

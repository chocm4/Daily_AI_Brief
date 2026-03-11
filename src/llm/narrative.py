import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI


MARKET_KEYS = [
    "KOSPI", "KOSDAQ", "코스피", "코스닥",
    "원/달러", "USDKRW", "환율",
    "미국 10년물", "UST 10Y",
    "S&P500", "NASDAQ", "나스닥", "VIX", "WTI",
    "MSCI ACWI", "MSCI DM", "MSCI EM", "DXY", "MOVE", "VKOSPI",
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


def _fmt_level(name: str, level: Any, kind: str = "price") -> str:
    v = _to_float(level)
    if v is None:
        return "데이터 없음"

    name = str(name or "")
    kind = (kind or "price").lower()

    if kind == "yield":
        return f"{v:.2f}%"
    if name in {"USDKRW"}:
        return f"{v:,.1f}원"
    if name in {"DXY", "EXY", "VIX", "MOVE", "VKOSPI", "MSCI ACWI", "MSCI DM", "MSCI EM"}:
        return f"{v:.2f}"
    if name in {"WTI", "Gold"}:
        return f"{v:,.2f}"
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v:.2f}"


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _benchmark(fact_pack: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    mc = fact_pack.get("market_context") or {}
    bench = (mc.get("benchmark_summary") or {}).get(name)
    if bench:
        return bench

    for x in fact_pack.get("market", []) or []:
        if (x.get("name") or x.get("asset")) == name:
            kind = (x.get("kind") or "price").lower()
            base = {
                "name": name,
                "kind": kind,
                "level": x.get("level"),
                "date": x.get("date"),
                "ref_ts_kst": x.get("ref_ts_kst"),
            }
            if kind == "yield":
                base["chg1d_bp"] = x.get("chg1d_bp")
            else:
                base["ret1d_pct"] = x.get("ret1d_pct")
            return base
    return None


def _asset_basis_label(run_mode: str, name: str, row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""

    kind = (row.get("kind") or "price").lower()
    if kind == "yield":
        if run_mode == "KR_AFTERCLOSE_US_PREOPEN":
            return "미국 전일 기준"
        if run_mode == "US_AFTERCLOSE_KR_PREOPEN":
            return "미국 당일 마감 기준"
        if run_mode == "US_INTRADAY":
            return "미국 장중 기준"
        return "현재 기준"

    if name in {"KOSPI", "KOSDAQ", "VKOSPI"}:
        if run_mode == "KR_INTRADAY":
            return "국내 장중 기준"
        return "국내 당일 기준"

    if name in {"S&P500", "NASDAQ", "Dow Jones", "Russell 2000", "SOX", "MSCI ACWI", "MSCI DM", "MSCI EM", "VIX", "MOVE"}:
        if run_mode == "KR_AFTERCLOSE_US_PREOPEN":
            return "미국 전일 종가 기준"
        if run_mode == "US_INTRADAY":
            return "미국 장중 기준"
        if run_mode == "US_AFTERCLOSE_KR_PREOPEN":
            return "미국 당일 마감 기준"
        return "글로벌 최근 기준"

    if name in {"Nikkei 225", "Taiwan Weighted", "Hang Seng", "CSI300", "Shanghai Composite", "Euro Stoxx 50", "STOXX Europe 600"}:
        return "해외 최근 종가 기준"

    if name in {"USDKRW", "DXY", "EXY", "WTI", "Gold"}:
        return "현재 확인 기준"

    return "최근 기준"


def _format_asset_line(run_mode: str, name: str, row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not row:
        return None

    kind = (row.get("kind") or "price").lower()
    level_txt = _fmt_level(name, row.get("level"), kind=kind)
    basis = _asset_basis_label(run_mode, name, row)
    move = _fmt_bp(row.get("chg1d_bp")) if kind == "yield" else _fmt_pct(row.get("ret1d_pct"))

    if level_txt == "데이터 없음" and move == "데이터 없음":
        return None
    if level_txt != "데이터 없음" and move != "데이터 없음":
        return f"{name} {level_txt}({move}, {basis})"
    if move != "데이터 없음":
        return f"{name} {move}({basis})"
    return f"{name} {level_txt}({basis})"


def _pick_lines(fact_pack: Dict[str, Any], names: List[str]) -> List[str]:
    run_mode = str(fact_pack.get("run_mode") or "")
    lines: List[str] = []
    for name in names:
        text = _format_asset_line(run_mode, name, _benchmark(fact_pack, name))
        if text:
            lines.append(text)
    return lines


def _build_llm_context(fact_pack: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    mc = fact_pack.get("market_context") or {}
    return {
        "run_mode": fact_pack.get("run_mode") or "",
        "generated_at_kst": fact_pack.get("generated_at_kst") or "",
        "session_clock": fact_pack.get("session_clock") or {},
        "headline": report.get("headline"),
        "today_5lines": report.get("today_5lines") or [],
        "kr_bullets": report.get("kr_bullets") or [],
        "overnight_bullets": report.get("overnight_bullets") or [],
        "top_drivers": report.get("top_drivers") or [],
        "market_context": {
            "benchmark_summary": mc.get("benchmark_summary") or {},
            "index_summary": mc.get("index_summary") or {},
            "ficc_summary": mc.get("ficc_summary") or {},
            "global_summary": mc.get("global_summary") or {},
            "sector_summary": mc.get("sector_summary") or {},
            "flow_summary": mc.get("flow_summary") or {},
            "feature_stocks": mc.get("feature_stocks") or [],
            "style_flags": mc.get("style_flags") or [],
        },
        "timing_note": {
            "KR_AFTERCLOSE_US_PREOPEN": "국내는 오늘 마감 데이터, 미국 주식과 미국 변동성 지표는 전일 종가 데이터, 환율·원자재는 현재 확인 시점 데이터다.",
            "US_AFTERCLOSE_KR_PREOPEN": "미국은 당일 마감 데이터, 국내는 전일 마감 데이터, 환율·원자재는 현재 확인 시점 데이터다.",
            "KR_INTRADAY": "국내는 장중 데이터이며 미국 주식은 전일 종가 기준일 가능성이 높다.",
            "US_INTRADAY": "미국은 장중 데이터이며 국내는 전일 마감 기준일 가능성이 높다.",
        },
        "opening_reference": {
            "domestic": _pick_lines(fact_pack, ["KOSPI", "KOSDAQ"]),
            "global_equity": _pick_lines(fact_pack, ["S&P500", "NASDAQ", "MSCI ACWI", "MSCI DM", "MSCI EM"]),
            "macro": _pick_lines(fact_pack, ["USDKRW", "DXY", "EXY", "UST 10Y", "VIX", "MOVE", "VKOSPI", "WTI"]),
        },
        "events_top": fact_pack.get("events_top") or [],
        "news_kr": fact_pack.get("news_kr") or [],
        "news_global": fact_pack.get("news_global") or [],
        "news_overnight": fact_pack.get("news_overnight") or [],
    }


def _pct(row: Optional[Dict[str, Any]]) -> Optional[float]:
    return _to_float((row or {}).get("ret1d_pct"))


def _bp(row: Optional[Dict[str, Any]]) -> Optional[float]:
    return _to_float((row or {}).get("chg1d_bp"))


def _lvl(row: Optional[Dict[str, Any]]) -> Optional[float]:
    return _to_float((row or {}).get("level"))


def _fmt_pct_only(v: Optional[float]) -> Optional[str]:
    return None if v is None else f"{v:+.2f}%"


def _fmt_bp_only(v: Optional[float]) -> Optional[str]:
    return None if v is None else f"{v:+.1f}bp"


def _build_opening_paragraph(fact_pack: Dict[str, Any]) -> str:
    run_mode = str(fact_pack.get("run_mode") or "")

    kospi = _benchmark(fact_pack, "KOSPI")
    kosdaq = _benchmark(fact_pack, "KOSDAQ")
    spx = _benchmark(fact_pack, "S&P500")
    ndx = _benchmark(fact_pack, "NASDAQ")
    sox = _benchmark(fact_pack, "SOX")
    usdkrw = _benchmark(fact_pack, "USDKRW")
    dxy = _benchmark(fact_pack, "DXY")
    ust10 = _benchmark(fact_pack, "UST 10Y")
    vix = _benchmark(fact_pack, "VIX")
    move = _benchmark(fact_pack, "MOVE")
    wti = _benchmark(fact_pack, "WTI")

    lines: List[str] = []

    k1 = _pct(kospi)
    k2 = _pct(kosdaq)
    if k1 is not None and k2 is not None:
        if run_mode == "KR_INTRADAY":
            lines.append(f"오늘 국내 증시는 KOSPI가 {_fmt_pct_only(k1)} 움직이는 반면 KOSDAQ은 {_fmt_pct_only(k2)}로 상대적으로 약한 흐름을 보이고 있다.")
        else:
            if k1 > 0 and k2 <= 0:
                lines.append(f"오늘 국내 증시는 KOSPI가 {_fmt_pct_only(k1)} 오른 반면 KOSDAQ은 {_fmt_pct_only(k2)}로 약보합에 머물렀다.")
            elif k1 < 0 and k2 >= 0:
                lines.append(f"오늘 국내 증시는 KOSPI가 {_fmt_pct_only(k1)} 내린 반면 KOSDAQ은 {_fmt_pct_only(k2)}로 상대적으로 선방했다.")
            elif abs(k1 - k2) >= 0.7:
                winner = "KOSPI" if k1 > k2 else "KOSDAQ"
                lines.append(f"오늘 국내 증시는 {winner} 우위가 뚜렷했다. KOSPI는 {_fmt_pct_only(k1)}, KOSDAQ은 {_fmt_pct_only(k2)}로 마감했다.")
            else:
                lines.append(f"오늘 국내 증시는 KOSPI {_fmt_pct_only(k1)}, KOSDAQ {_fmt_pct_only(k2)}로 마감했다.")
    elif k1 is not None:
        lines.append(f"오늘 국내 증시는 KOSPI가 {_fmt_pct_only(k1)} 움직였다.")

    us_bits: List[str] = []
    spx_ret = _pct(spx)
    ndx_ret = _pct(ndx)
    sox_ret = _pct(sox)
    if spx_ret is not None:
        us_bits.append(f"S&P500 {_fmt_pct_only(spx_ret)}")
    if ndx_ret is not None:
        us_bits.append(f"NASDAQ {_fmt_pct_only(ndx_ret)}")
    if sox_ret is not None and abs(sox_ret) >= 0.5:
        us_bits.append(f"SOX {_fmt_pct_only(sox_ret)}")

    if us_bits and run_mode in {"KR_AFTERCLOSE_US_PREOPEN", "KR_INTRADAY"}:
        lines.append("전일 미국 증시는 " + ", ".join(us_bits) + "로 혼조였다.")
    elif us_bits and run_mode in {"US_AFTERCLOSE_KR_PREOPEN", "US_INTRADAY"}:
        if run_mode == "US_INTRADAY":
            lines.append("현재 미국 증시는 " + ", ".join(us_bits[:2]) + " 흐름을 보이고 있다.")
        else:
            lines.append("미국 증시는 " + ", ".join(us_bits) + "로 마감했다.")

    macro_bits: List[str] = []
    fx_ret = _pct(usdkrw)
    fx_lvl = _lvl(usdkrw)
    if fx_ret is not None and fx_lvl is not None and abs(fx_ret) >= 0.2:
        macro_bits.append(f"원/달러 환율은 {_fmt_level('USDKRW', fx_lvl)}로 {_fmt_pct_only(fx_ret)}")

    dxy_ret = _pct(dxy)
    dxy_lvl = _lvl(dxy)
    if dxy_ret is not None and dxy_lvl is not None and abs(dxy_ret) >= 0.15:
        macro_bits.append(f"달러인덱스는 {_fmt_level('DXY', dxy_lvl)}로 {_fmt_pct_only(dxy_ret)}")

    ust10_bp = _bp(ust10)
    ust10_lvl = _lvl(ust10)
    if ust10_bp is not None and ust10_lvl is not None and abs(ust10_bp) >= 1.5:
        macro_bits.append(f"미국 10년물은 {_fmt_level('UST 10Y', ust10_lvl, kind='yield')}로 {_fmt_bp_only(ust10_bp)}")

    vix_ret = _pct(vix)
    vix_lvl = _lvl(vix)
    if vix_ret is not None and vix_lvl is not None and (abs(vix_ret) >= 3 or vix_lvl >= 25):
        macro_bits.append(f"VIX는 {_fmt_level('VIX', vix_lvl)}로 {_fmt_pct_only(vix_ret)}")

    move_ret = _pct(move)
    move_lvl = _lvl(move)
    if move_ret is not None and move_lvl is not None and abs(move_ret) >= 5:
        macro_bits.append(f"MOVE는 {_fmt_level('MOVE', move_lvl)}로 {_fmt_pct_only(move_ret)}")

    wti_ret = _pct(wti)
    wti_lvl = _lvl(wti)
    if wti_ret is not None and wti_lvl is not None and abs(wti_ret) >= 1.0:
        macro_bits.append(f"WTI는 {_fmt_level('WTI', wti_lvl)}달러로 {_fmt_pct_only(wti_ret)}")

    if macro_bits:
        lines.append("대외 변수로는 " + ", ".join(macro_bits[:3]) + "가 확인됐다.")

    return " ".join(lines).strip()


def _split_sentences(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r'(?<=[.!?])\s+|(?<=다\.)\s+|(?<=다!)\s+|(?<=다\?)\s+', text)
    return [x.strip() for x in parts if x.strip()]


def _extract_number_signals(s: str) -> List[str]:
    s = s or ""
    patterns = [
        r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?%',
        r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?bp',
        r'\d+(?:,\d{3})*(?:\.\d+)?원',
        r'\d+(?:,\d{3})*(?:\.\d+)?달러',
        r'\d+(?:,\d{3})*(?:\.\d+)?',
    ]
    found: List[str] = []
    for p in patterns:
        found.extend(re.findall(p, s))
    return found


def _mentioned_market_keys(s: str) -> List[str]:
    s = s or ""
    return [k for k in MARKET_KEYS if k in s]


def _normalize_for_overlap(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'["“”‘’()\[\]{}]', '', s)
    return s.lower()


def _is_opening_duplicate_sentence(s: str, opening: str) -> bool:
    if not s.strip():
        return False

    s_keys = set(_mentioned_market_keys(s))
    o_keys = set(_mentioned_market_keys(opening))
    common_keys = s_keys.intersection(o_keys)
    nums = _extract_number_signals(s)

    if len(common_keys) >= 2 and len(nums) >= 2:
        return True

    market_like = any(k in s for k in ["KOSPI", "KOSDAQ", "코스피", "코스닥", "원/달러", "환율", "S&P500", "NASDAQ", "MSCI ACWI", "MSCI DM", "MSCI EM"])
    perf_like = "%" in s or "bp" in s
    if market_like and perf_like and len(common_keys) >= 1 and len(nums) >= 3:
        return True

    ns = _normalize_for_overlap(s)
    no = _normalize_for_overlap(opening)
    if ns and no:
        overlap_hits = 0
        for token in ["kospi", "kosdaq", "원/달러", "s&p500", "nasdaq", "msci acwi", "msci dm", "msci em", "%", "bp", "기준"]:
            if token in ns and token in no:
                overlap_hits += 1
        if overlap_hits >= 3 and len(nums) >= 2:
            return True

    return False


def _drop_duplicate_market_sentences(sentences: List[str], opening: str) -> List[str]:
    if not sentences:
        return sentences

    filtered: List[str] = []
    dropped_first_duplicate = False
    for i, s in enumerate(sentences):
        if i <= 1 and _is_opening_duplicate_sentence(s, opening):
            dropped_first_duplicate = True
            continue
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
    text = re.split(r'\n##\s*Sources\b|\n##\s*출처\b|\n###\s*Sources\b|\n###\s*출처\b', text)[0]
    return text.strip()


def _chunk_paragraph(sentences: List[str], target_chars: int = 180, max_chars: int = 260) -> List[str]:
    paras: List[str] = []
    cur: List[str] = []
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

    clean_sentences: List[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        s = re.sub(r'([가-힣A-Za-z0-9])\.\s+([가-힣])', r'\1 \2', s)
        clean_sentences.append(_ensure_complete_sentence(s))

    if not clean_sentences:
        return ""
    paras = _chunk_paragraph(clean_sentences, target_chars=180, max_chars=260)
    return "\n\n".join(paras[:6]).strip()


def _collect_used_source_candidates(fact_pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    pool: List[Dict[str, Any]] = []
    seen = set()
    candidates: List[Dict[str, Any]] = []
    candidates.extend(fact_pack.get("news_kr", []) or [])
    candidates.extend(fact_pack.get("news_overnight", []) or [])
    candidates.extend(fact_pack.get("news_global", []) or [])

    for x in candidates:
        key = (str(x.get("event_id", "")).strip(), str(x.get("title", "")).strip(), str(x.get("url", "")).strip())
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
        ),
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
        lines.append(f"- {title} | {source} | score={score_txt}" + (f" | {url}" if url else ""))
    return "\n".join(lines)


def generate_narrative_md(fact_pack: Dict[str, Any], report: Dict[str, Any], cfg: Dict[str, Any], log=None) -> str:
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("story_model", llm_cfg.get("model", "gpt-5.4"))
    temperature = float(llm_cfg.get("story_temperature", 0.1))
    max_tokens = int(llm_cfg.get("story_max_output_tokens", 2400))
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
- opening 문단은 이미 별도로 들어가므로, 본문 첫 문단에서 지수·환율·금리·ETF를 한꺼번에 다시 나열하지 마라.
- opening 문단에 나온 숫자를 그대로 반복해 첫 문단을 시작하지 마라. 본문에서는 필요한 숫자만 논리 전개에 맞게 다시 불러와라.
- benchmark를 소개할 때는 모든 자산을 다 덤프하지 말고, 해당 문단의 논지에 필요한 것만 선택해서 써라.
- run_mode가 KR_AFTERCLOSE_US_PREOPEN이면 국내 지수는 오늘 마감 기준, 미국 지수는 전일 종가 기준임을 전제로 서술하라.
- run_mode가 US_AFTERCLOSE_KR_PREOPEN이면 미국 지수는 당일 마감 기준, 국내 지수는 전일 국내 마감 기준으로 다뤄라.
- run_mode가 KR_INTRADAY 또는 US_INTRADAY이면 아직 진행 중인 시장이라는 점을 분명히 반영하라.
- KOSDAQ을 설명할 때는 반드시 'KOSDAQ'이라고 명시하라. '중소형 성장주가 많은 시장' 같은 우회 표현은 금지한다.
- 시장 비교가 필요하면 KOSPI 대 KOSDAQ, 또는 미국 대 글로벌 ETF처럼 명시적으로 적어라.
- 지수/금리/환율/변동성 수치가 있으면 해석상 필요한 범위에서 level과 등락률을 함께 활용하라.
- 기사 제목을 거의 그대로 옮긴 문장을 만들지 마라.
- 문단은 4~6개로 나누고, 각 문단은 보고서 본문처럼 자연스러운 줄글이어야 한다.
- events_top 상위 이벤트를 앞부분에서 우선 반영하되, 기사 제목이 아니라 시황 해설 문장으로 재구성하라.
- JSON의 run_mode가 KR_INTRADAY 또는 US_INTRADAY이면 장중 코멘트로 작성하라. 이 경우 '했다', '마감했다'보다 '보이고 있다', '진행 중이다', '이어지고 있다' 같은 현재 시제를 우선 사용하라.
- JSON의 run_mode가 KR_AFTERCLOSE_US_PREOPEN 또는 US_AFTERCLOSE_KR_PREOPEN이면 이미 끝난 세션은 과거형, 아직 진행 전이거나 진행 중인 세션은 현재형/예정 표현으로 구분하라.
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

    chunks: List[str] = []
    if opening:
        chunks.append(opening)
    if body:
        chunks.append(body)

    result = header + "\n\n".join(chunks).strip() + "\n"
    if show_reference_articles:
        result += "\n" + _render_reference_articles(fact_pack) + "\n"
    return result

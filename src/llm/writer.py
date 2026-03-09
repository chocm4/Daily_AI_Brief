import json
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

from src.llm.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.llm.schema import DailyBriefing


def _extract_text(resp) -> str:
    if hasattr(resp, "output_text") and resp.output_text:
        return resp.output_text
    try:
        parts = []
        for out in getattr(resp, "output", []) or []:
            for c in getattr(out, "content", []) or []:
                if getattr(c, "type", "") in ("output_text", "text") and getattr(c, "text", None):
                    parts.append(c.text)
        return "".join(parts)
    except Exception:
        return str(resp)


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        s = "\n".join(lines).strip()
    return s


def _safe_json_load(s: str) -> dict:
    s = _strip_code_fences(s)
    try:
        return json.loads(s)
    except Exception:
        a = s.find("{")
        b = s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a : b + 1])
        raise


def _ensure_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _to_float(x) -> Optional[float]:
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


def _fmt_move(row: Dict[str, Any]) -> str:
    kind = (row.get("kind") or "price").lower()
    if kind == "yield":
        return _fmt_bp(row.get("chg1d_bp"))
    return _fmt_pct(row.get("ret1d_pct"))


def _fmt_1e8(v) -> str:
    v = _to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+,.0f}억원"


def _with_evidence(text: str, evidence: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    if text.endswith("]") and "[근거]" in text:
        return text
    return f"{text} {evidence}".strip()


def _append_unique(lines: List[str], line: Optional[str], limit: int) -> List[str]:
    line = (line or "").strip()
    if not line:
        return lines
    if line not in lines and len(lines) < limit:
        lines.append(line)
    return lines


def _build_numeric_headline(fp: Dict[str, Any]) -> str:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    kospi = idx.get("KOSPI")
    kosdaq = idx.get("KOSDAQ")
    ust10 = ficc.get("UST 10Y")
    usdkrw = ficc.get("USDKRW")

    bits = []
    if kospi:
        bits.append(f"KOSPI {_fmt_pct(kospi.get('ret1d_pct'))}")
    if kosdaq:
        bits.append(f"KOSDAQ {_fmt_pct(kosdaq.get('ret1d_pct'))}")
    if ust10:
        bits.append(f"미 10년물 {_fmt_bp(ust10.get('chg1d_bp'))}")
    if usdkrw:
        bits.append(f"원/달러 {_fmt_pct(usdkrw.get('ret1d_pct'))}")

    if bits:
        return " · ".join(bits[:3]) + " 기준 시황 점검"
    return "오늘의 시황"


def _fallback_today_5lines(fp: Dict[str, Any]) -> List[str]:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    sec = (mc.get("sector_summary") or {})
    flow = mc.get("flow_summary") or {}
    events = fp.get("events_top") or []

    lines: List[str] = []

    kospi = idx.get("KOSPI")
    kosdaq = idx.get("KOSDAQ")
    if kospi or kosdaq:
        parts = []
        if kospi:
            parts.append(f"KOSPI는 {_fmt_pct(kospi.get('ret1d_pct'))}")
        if kosdaq:
            parts.append(f"KOSDAQ은 {_fmt_pct(kosdaq.get('ret1d_pct'))}")
        tail = ""
        if kospi and kosdaq:
            k1 = _to_float(kospi.get("ret1d_pct"))
            k2 = _to_float(kosdaq.get("ret1d_pct"))
            if k1 is not None and k2 is not None:
                if k1 > k2:
                    tail = " 지수 반등의 폭보다 KOSPI 우위 여부가 먼저 확인된다."
                elif k2 > k1:
                    tail = " 지수 반등의 폭보다 KOSDAQ 상대강도가 먼저 확인된다."
        _append_unique(lines, " / ".join(parts) + "." + tail, 5)

    ust10 = ficc.get("UST 10Y")
    usdkrw = ficc.get("USDKRW")
    dxy = ficc.get("DXY")
    if ust10 or usdkrw or dxy:
        parts = []
        if ust10:
            parts.append(f"미국 10년물 금리는 {_fmt_bp(ust10.get('chg1d_bp'))}")
        if usdkrw:
            parts.append(f"원/달러는 {_fmt_pct(usdkrw.get('ret1d_pct'))}")
        elif dxy:
            parts.append(f"달러인덱스는 {_fmt_pct(dxy.get('ret1d_pct'))}")
        _append_unique(lines, " / ".join(parts) + ". 금리/환율 경로가 국내 주식의 스타일 해석에 선행한다.", 5)

    leaders = (sec.get("leaders") or [])[:2]
    laggards = (sec.get("laggards") or [])[:2]
    if leaders or laggards:
        leader_txt = ", ".join([f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in leaders])
        laggard_txt = ", ".join([f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in laggards])
        txt = ""
        if leader_txt:
            txt += f"강세 업종은 {leader_txt}"
        if laggard_txt:
            txt += f" / 약세 업종은 {laggard_txt}"
        _append_unique(lines, txt + ". 업종 확산 여부와 차별화 강도를 함께 볼 필요가 있다.", 5)

    flow_line_parts = []
    for mkt in ["KOSPI", "KOSDAQ"]:
        panel = flow.get(mkt) or {}
        if not panel:
            continue
        ftxt = _fmt_1e8(panel.get("foreign_1e8krw")) if panel.get("foreign_1e8krw") is not None else None
        itxt = _fmt_1e8(panel.get("institution_1e8krw")) if panel.get("institution_1e8krw") is not None else None
        if ftxt or itxt:
            flow_line_parts.append(f"{mkt}에서 외국인 {ftxt if ftxt else '데이터 없음'}, 기관 {itxt if itxt else '데이터 없음'}")
    if flow_line_parts:
        _append_unique(lines, " / ".join(flow_line_parts) + ". 수급 주체의 방향이 특징주와 업종 상대강도를 설명하는지 확인이 필요하다.", 5)

    if events:
        e = events[0]
        theme = e.get("theme") or e.get("summary") or "핵심 이벤트"
        _append_unique(lines, f"오늘의 핵심 이벤트는 {theme}이다. 한국 증시 기준으로 가격 반응과 이벤트 해석의 정합성을 점검할 필요가 있다.", 5)

    filler = "데이터가 제한적인 구간에서는 지수, 금리, 환율, 업종, 수급의 우선순위로 해석하는 편이 안정적이다."
    while len(lines) < 5:
        _append_unique(lines, filler, 5)
    return lines[:5]


def _fallback_price_action(fp: Dict[str, Any]) -> List[Dict[str, str]]:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    sec = (mc.get("sector_summary") or {})
    stocks = mc.get("feature_stocks") or []

    out: List[Dict[str, str]] = []
    seen = set()

    def add(asset: str, move: str, comment: str, evidence: str):
        if not asset or asset in seen:
            return
        seen.add(asset)
        out.append({"asset": asset, "move": move, "comment": comment, "evidence": evidence})

    for name in ["KOSPI", "KOSDAQ", "S&P500", "NASDAQ"]:
        row = idx.get(name)
        if row:
            add(name, _fmt_pct(row.get("ret1d_pct")), "지수 방향성과 상대강도를 파악하는 기준선이다.", f"M:{name}")

    for name in ["USDKRW", "UST 10Y", "DXY", "WTI", "VIX"]:
        row = ficc.get(name)
        if row:
            move = _fmt_bp(row.get("chg1d_bp")) if (row.get("kind") == "yield") else _fmt_pct(row.get("ret1d_pct"))
            add(name, move, "주식시장 밸류에이션과 리스크 선호를 설명하는 핵심 FICC 변수다.", f"M:{name}")

    for x in (sec.get("feature_sectors") or [])[:4]:
        name = x.get("name")
        if name:
            add(name, _fmt_pct(x.get("ret1d_pct")), x.get("comment_hint") or "업종 상대강도 확인용 지표다.", f"SECTOR:{name}")

    for x in stocks[:4]:
        name = x.get("name")
        if name:
            flow_label = x.get("flow_label") or "수급"
            flow_amt = _fmt_1e8(x.get("net_buy_1e8krw")) if x.get("net_buy_1e8krw") is not None else "데이터 없음"
            add(name, _fmt_pct(x.get("ret1d_pct")), f"{flow_label} {flow_amt}과 함께 본 특징주다.", f"STOCK:{name}")

    return out[:14]


def _ensure_numeric_bullets(d: Dict[str, Any], fp: Dict[str, Any]) -> Dict[str, Any]:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    sec = (mc.get("sector_summary") or {})
    flow = mc.get("flow_summary") or {}
    stocks = mc.get("feature_stocks") or []

    kr = _ensure_list(d.get("kr_bullets"))
    ov = _ensure_list(d.get("overnight_bullets"))

    if idx.get("KOSPI") and idx.get("KOSDAQ"):
        k = idx["KOSPI"]
        q = idx["KOSDAQ"]
        line = (
            f"KOSPI {_fmt_pct(k.get('ret1d_pct'))}, KOSDAQ {_fmt_pct(q.get('ret1d_pct'))}로 마감했다. "
            f"지수 반등의 폭보다 두 시장의 상대강도 차이를 먼저 확인할 필요가 있다. [근거: M:KOSPI, M:KOSDAQ]"
        )
        if not any("KOSPI" in x and "KOSDAQ" in x for x in kr):
            kr = [line] + kr

    leaders = (sec.get("leaders") or [])[:2]
    if leaders and not any((leaders[0].get("name") or "") in x for x in kr):
        names = ", ".join([f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in leaders])
        kr.insert(min(1, len(kr)), f"국내 업종에서는 {names} 등이 상대강도를 보였다. 업종 확산 여부가 추세 해석의 관건이다. [근거: market_context.sector_summary]")

    if flow and not any("외국인" in x or "기관" in x for x in kr):
        parts = []
        for mkt in ["KOSPI", "KOSDAQ"]:
            panel = flow.get(mkt) or {}
            if panel:
                parts.append(f"{mkt} 외국인 {_fmt_1e8(panel.get('foreign_1e8krw'))}, 기관 {_fmt_1e8(panel.get('institution_1e8krw'))}")
        if parts:
            kr.insert(min(2, len(kr)), " / ".join(parts) + ". 수급 주체의 방향이 업종 차별화와 일치하는지 확인이 필요하다. [근거: market_context.flow_summary]")

    if stocks and not any((stocks[0].get("name") or "") in x for x in kr):
        s = stocks[0]
        kr.insert(min(3, len(kr)), f"특징주로는 {s.get('name')} {_fmt_pct(s.get('ret1d_pct'))}가 눈에 띄었다. {s.get('flow_label') or '수급'} 흐름과 가격 반응의 정합성이 중요하다. [근거: market_context.feature_stocks]")

    if idx.get("NASDAQ") and ficc.get("UST 10Y"):
        n = idx["NASDAQ"]
        y = ficc["UST 10Y"]
        line = (
            f"NASDAQ {_fmt_pct(n.get('ret1d_pct'))}, 미국 10년물 {_fmt_bp(y.get('chg1d_bp'))} 조합이 확인됐다. "
            f"한국 증시 기준으로는 성장주와 대형 기술주의 밸류에이션 경로를 점검할 필요가 있다. [근거: M:NASDAQ, M:UST 10Y]"
        )
        if not any("NASDAQ" in x and "10년물" in x for x in ov):
            ov = [line] + ov

    if ficc.get("USDKRW") and not any("원/달러" in x or "USDKRW" in x for x in ov):
        fx = ficc["USDKRW"]
        ov.insert(min(1, len(ov)), f"원/달러 {_fmt_pct(fx.get('ret1d_pct'))} 흐름도 함께 봐야 한다. 한국 증시 기준으로 외국인 수급과 수출주 해석에 직접 연결된다. [근거: M:USDKRW]")

    d["kr_bullets"] = kr[:10]
    d["overnight_bullets"] = ov[:10]
    return d


def _normalize(d: dict, fact_pack: dict) -> dict:
    d.setdefault("asof", fact_pack.get("asof", ""))
    if not d.get("headline"):
        d["headline"] = _build_numeric_headline(fact_pack)
    d.setdefault("disclaimer", "RSS 헤드라인 및 공개 데이터 기반의 자동 작성 초안")

    d["today_5lines"] = _ensure_list(d.get("today_5lines"))[:5]
    d["kr_bullets"] = _ensure_list(d.get("kr_bullets"))[:10]
    d["overnight_bullets"] = _ensure_list(d.get("overnight_bullets"))[:10]
    d["tomorrow_watch"] = _ensure_list(d.get("tomorrow_watch"))[:8]

    if len(d["today_5lines"]) < 5:
        for line in _fallback_today_5lines(fact_pack):
            _append_unique(d["today_5lines"], line, 5)

    pa = _ensure_list(d.get("price_action"))
    fixed_pa = []
    for m in pa:
        if not isinstance(m, dict):
            continue
        m.setdefault("asset", "")
        if "move" in m and isinstance(m["move"], (int, float)):
            m["move"] = str(m["move"])
        if "evidence" in m and isinstance(m["evidence"], (int, float)):
            m["evidence"] = str(m["evidence"])
        m.setdefault("comment", "")
        fixed_pa.append(m)

    existing_assets = {str(x.get("asset") or "") for x in fixed_pa}
    for extra in _fallback_price_action(fact_pack):
        if extra["asset"] not in existing_assets and len(fixed_pa) < 14:
            fixed_pa.append(extra)
            existing_assets.add(extra["asset"])

    d["price_action"] = fixed_pa[:14]

    td = _ensure_list(d.get("top_drivers"))
    fixed_td = []
    for x in td:
        if not isinstance(x, dict):
            continue
        x.setdefault("title", "")
        x.setdefault("why_it_matters", "")
        if "sources" not in x:
            if "id" in x and isinstance(x["id"], str):
                x["sources"] = [x["id"]]
            else:
                x["sources"] = []
        fixed_td.append(x)
    d["top_drivers"] = fixed_td[:12]

    rr = _ensure_list(d.get("risk_radar"))
    fixed_rr = []
    for x in rr:
        if not isinstance(x, dict):
            continue
        x.setdefault("name", "")
        x.setdefault("level", "Yellow")
        x.setdefault("trigger", "")
        x.setdefault("sources", [])
        fixed_rr.append(x)
    d["risk_radar"] = fixed_rr

    d = _ensure_numeric_bullets(d, fact_pack)
    return d


def generate_report(fact_pack: dict, cfg: dict, log=None) -> DailyBriefing:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("model", "gpt-5.4")
    temperature = float(llm_cfg.get("temperature", 0.15))
    max_out = int(llm_cfg.get("max_output_tokens", 3200))

    fact_pack_json = json.dumps(fact_pack, ensure_ascii=False)
    sys = SYSTEM_PROMPT + """
추가 규칙:
- top_drivers는 fact_pack.events_top를 우선 활용해 event 단위로 묶어라.
- 한국장 관련 포인트에서는 market_context.index_summary -> market_context.ficc_summary -> market_context.sector_summary -> market_context.flow_summary -> events_top 순으로 우선 확인해라.
- 특징 업종은 market_context.sector_summary.feature_sectors를 우선 사용해라.
- 특징주는 market_context.feature_stocks를 우선 사용해라.
- 사실과 해석을 섞을 때는 문장 내에서 분리해라. 예: '...로 확인된다. 추정: ...'.
- 뉴스가 많아도 중요도 낮은 기사 나열은 금지.
- 숫자 데이터가 있으면 추상 표현보다 숫자를 먼저 쓴다.
- 출력은 반드시 JSON 1개만.
"""

    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(fact_pack_json=fact_pack_json)},
    ]

    resp = client.responses.create(
        model=model,
        input=messages,
        temperature=temperature,
        max_output_tokens=max_out,
    )
    text = _extract_text(resp)
    try:
        d = _safe_json_load(text)
        d = _normalize(d, fact_pack)
        return DailyBriefing.model_validate(d)
    except Exception as e1:
        if log:
            log.warning(f"LLM JSON parse failed; attempting repair. err={e1}")

        repair_messages = [
            {"role": "system", "content": "너는 JSON 리페어 도구다. JSON만 출력한다."},
            {"role": "user", "content": f"""아래 출력이 스키마를 어겼거나 JSON이 깨졌다.
필수 키를 모두 채우고, move/evidence는 문자열로, top_drivers에는 sources(list[str])를 넣어라.
kr_bullets/overnight_bullets 끝에는 근거 태그를 유지하라.
숫자 데이터가 있으면 today_5lines와 price_action에는 우선 반영하라.

[Broken Output]
{text}
"""},
        ]
        resp2 = client.responses.create(
            model=model,
            input=repair_messages,
            temperature=0,
            max_output_tokens=max_out,
        )
        text2 = _extract_text(resp2)
        d2 = _safe_json_load(text2)
        d2 = _normalize(d2, fact_pack)
        return DailyBriefing.model_validate(d2)

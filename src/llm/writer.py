import json
import os
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from src.llm.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.llm.schema import DailyBriefing


def _supports_temperature(model: str) -> bool:
    """GPT-5 family and o-series reasoning models do not accept temperature on the Responses API."""
    m = (model or "").lower()
    if m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return False
    return True


def _build_responses_kwargs(model: str, messages, temperature: float, max_out: int) -> dict:
    kwargs = {"model": model, "input": messages, "max_output_tokens": max_out}
    if _supports_temperature(model):
        kwargs["temperature"] = temperature
    return kwargs


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


def _fmt_1e8(v) -> str:
    v = _to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+,.0f}억원"


def _investor_display(name: Optional[str]) -> str:
    mapping = {
        "기관합계": "기관",
        "외국인합계": "외국인",
    }
    return mapping.get(name or "", name or "")


def _contains_number(text: str) -> bool:
    text = str(text or "")
    return bool(re.search(r"[-+]?\d+[\d,]*(?:\.\d+)?(?:%|bp|억원|원|달러)?", text))


def _find_market_row(market: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for x in market or []:
        if (x.get("name") or x.get("asset")) == name:
            return x
    return None


def _metric_keys(fp: Dict[str, Any], kind: str) -> tuple[str, str]:
    weekly = str((fp or {}).get("run_mode") or "") == "WEEKLY_RECAP"
    if kind == "yield":
        return ("chg1w_bp", "chg1d_bp") if weekly else ("chg1d_bp", "chg1d_bp")
    return ("ret1w_pct", "ret1d_pct") if weekly else ("ret1d_pct", "ret1d_pct")


def _get_summary_row(fp: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    row = idx.get(name) or ficc.get(name)
    if row:
        return row
    raw = _find_market_row(fp.get("market") or [], name)
    if not raw:
        return None
    kind = (raw.get("kind") or "price").lower()
    if kind == "yield":
        return {"name": name, "kind": kind, "chg1d_bp": raw.get("chg1d_bp"), "chg1w_bp": raw.get("chg1w_bp")}
    return {"name": name, "kind": kind, "ret1d_pct": raw.get("ret1d_pct"), "ret1w_pct": raw.get("ret1w_pct")}


def _build_numeric_headline(fp: Dict[str, Any]) -> str:
    weekly = str((fp or {}).get("run_mode") or "") == "WEEKLY_RECAP"
    kospi = _get_summary_row(fp, "KOSPI")
    kosdaq = _get_summary_row(fp, "KOSDAQ")
    ust10 = _get_summary_row(fp, "UST 10Y")
    usdkrw = _get_summary_row(fp, "USDKRW")

    bits = []
    if kospi and kospi.get("ret1w_pct" if weekly else "ret1d_pct") is not None:
        bits.append(f"KOSPI {_fmt_pct(kospi.get('ret1w_pct' if weekly else 'ret1d_pct'))}")
    if kosdaq and kosdaq.get("ret1w_pct" if weekly else "ret1d_pct") is not None:
        bits.append(f"KOSDAQ {_fmt_pct(kosdaq.get('ret1w_pct' if weekly else 'ret1d_pct'))}")
    if ust10 and ust10.get("chg1w_bp" if weekly else "chg1d_bp") is not None:
        bits.append(f"미 10년물 {_fmt_bp(ust10.get('chg1w_bp' if weekly else 'chg1d_bp'))}")
    if usdkrw and usdkrw.get("ret1w_pct" if weekly else "ret1d_pct") is not None:
        bits.append(f"원/달러 {_fmt_pct(usdkrw.get('ret1w_pct' if weekly else 'ret1d_pct'))}")

    if bits:
        suffix = "지난주 시황 점검" if weekly else "기준 시황 점검"
        return " · ".join(bits[:3]) + f" {suffix}"
    return "지난주 시황" if weekly else "오늘의 시황"


def _fallback_today_5lines(fp: Dict[str, Any]) -> List[str]:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    sec = mc.get("sector_summary") or {}
    flow = mc.get("flow_summary") or {}
    events = fp.get("events_top") or []

    lines: List[str] = []

    kospi = idx.get("KOSPI")
    kosdaq = idx.get("KOSDAQ")
    if kospi or kosdaq:
        parts = []
        if kospi and kospi.get("ret1d_pct") is not None:
            parts.append(f"KOSPI는 {_fmt_pct(kospi.get('ret1d_pct'))}")
        if kosdaq and kosdaq.get("ret1d_pct") is not None:
            parts.append(f"KOSDAQ은 {_fmt_pct(kosdaq.get('ret1d_pct'))}")
        tail = ""
        if kospi and kosdaq and kospi.get("ret1d_pct") is not None and kosdaq.get("ret1d_pct") is not None:
            k1 = _to_float(kospi.get("ret1d_pct"))
            k2 = _to_float(kosdaq.get("ret1d_pct"))
            if k1 is not None and k2 is not None:
                if k1 > k2:
                    tail = " 상대강도 측면에선 KOSPI 우위였다."
                elif k2 > k1:
                    tail = " 상대강도 측면에선 KOSDAQ 우위였다."
                else:
                    tail = " 양 지수 강도 차이는 크지 않았다."
        lines.append(" / ".join(parts) + "." + tail)

    ust10 = ficc.get("UST 10Y")
    usdkrw = ficc.get("USDKRW")
    dxy = ficc.get("DXY")
    if ust10 or usdkrw or dxy:
        parts = []
        if ust10 and ust10.get("chg1d_bp") is not None:
            parts.append(f"미국 10년물 금리는 {_fmt_bp(ust10.get('chg1d_bp'))}")
        if usdkrw and usdkrw.get("ret1d_pct") is not None:
            parts.append(f"원/달러는 {_fmt_pct(usdkrw.get('ret1d_pct'))}")
        elif dxy and dxy.get("ret1d_pct") is not None:
            parts.append(f"달러인덱스는 {_fmt_pct(dxy.get('ret1d_pct'))}")
        lines.append(" / ".join(parts) + ". 금리와 환율 경로가 밸류에이션과 외국인 수급 해석의 핵심 변수였다.")

    leaders = (sec.get("leaders") or [])[:2]
    laggards = (sec.get("laggards") or [])[:2]
    if leaders or laggards:
        leader_txt = ", ".join([f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in leaders])
        laggard_txt = ", ".join([f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in laggards])
        if leader_txt or laggard_txt:
            txt = []
            if leader_txt:
                txt.append(f"강세 업종은 {leader_txt}")
            if laggard_txt:
                txt.append(f"약세 업종은 {laggard_txt}")
            lines.append(" / ".join(txt) + ". 업종 확산 여부와 차별화 강도를 함께 볼 필요가 있다.")

    flow_parts = []
    for mkt in ["KOSPI", "KOSDAQ"]:
        panel = flow.get(mkt) or {}
        if not panel:
            continue
        sub = []
        if panel.get("foreign_1e8krw") is not None:
            sub.append(f"외국인 {_fmt_1e8(panel.get('foreign_1e8krw'))}")
        if panel.get("institution_1e8krw") is not None:
            sub.append(f"기관 {_fmt_1e8(panel.get('institution_1e8krw'))}")
        if panel.get("retail_1e8krw") is not None:
            sub.append(f"개인 {_fmt_1e8(panel.get('retail_1e8krw'))}")
        if sub:
            flow_parts.append(f"{mkt} 수급은 " + ", ".join(sub))
    if flow_parts:
        lines.append(" / ".join(flow_parts) + ". 수급 주체의 방향이 특징주와 업종 상대강도를 설명하는지 확인이 필요하다.")

    if events:
        e = events[0]
        theme = e.get("theme") or e.get("summary") or "핵심 이벤트"
        lines.append(f"뉴스 측면에서는 '{theme}' 이슈가 상위에 놓였다. 한국 증시 기준으로 가격 반응과 이벤트 해석의 정합성을 점검할 필요가 있다.")

    filler = "데이터가 제한적인 구간에서는 지수, 금리, 환율, 업종, 수급의 우선순위로 해석하는 편이 안정적이다."
    while len(lines) < 5:
        lines.append(filler)
    return lines[:5]


def _fallback_price_action(fp: Dict[str, Any]) -> List[Dict[str, str]]:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    sec = mc.get("sector_summary") or {}
    stocks = mc.get("feature_stocks") or []

    out: List[Dict[str, str]] = []
    seen = set()

    def add(asset: str, move: str, comment: str, evidence: str) -> None:
        if not asset or asset in seen:
            return
        seen.add(asset)
        out.append({"asset": asset, "move": move, "comment": comment, "evidence": evidence})

    for name in ["KOSPI", "KOSDAQ", "S&P500", "NASDAQ"]:
        row = idx.get(name)
        if row and row.get("ret1d_pct") is not None:
            add(name, _fmt_pct(row.get("ret1d_pct")), "지수 방향성과 상대강도를 파악하는 기준선이다.", f"M:{name}")

    for name in ["USDKRW", "UST 10Y", "DXY", "WTI", "VIX"]:
        row = ficc.get(name)
        if not row:
            continue
        move = _fmt_bp(row.get("chg1d_bp")) if (row.get("kind") == "yield") else _fmt_pct(row.get("ret1d_pct"))
        add(name, move, "주식시장 밸류에이션과 리스크 선호를 설명하는 핵심 FICC 변수다.", f"M:{name}")

    for x in (sec.get("feature_sectors") or [])[:4]:
        name = x.get("name")
        if name and x.get("ret1d_pct") is not None:
            add(f"업종:{name}", _fmt_pct(x.get("ret1d_pct")), x.get("comment_hint") or "업종 상대강도 확인용 지표다.", f"SECTOR:{name}")

    for x in stocks[:4]:
        name = x.get("name")
        if not name:
            continue
        if x.get("ret1d_pct") is not None:
            move = _fmt_pct(x.get("ret1d_pct"))
        elif x.get("net_buy_1e8krw") is not None:
            move = _fmt_1e8(x.get("net_buy_1e8krw"))
        else:
            move = "데이터 없음"
        flow_label = _investor_display((x.get("flow_label") or "").replace(" 순매수", ""))
        add(f"종목:{name}", move, f"{flow_label or '수급'}가 집중된 특징주다.", f"STOCK:{name}")

    return out[:14]


def _need_numeric_override(lines: List[str], keywords: List[str]) -> bool:
    text = " ".join([str(x or "") for x in lines])
    has_keyword = any(k in text for k in keywords)
    has_number = _contains_number(text)
    return (not has_keyword) or (not has_number)


def _ensure_numeric_bullets(d: Dict[str, Any], fp: Dict[str, Any]) -> None:
    mc = fp.get("market_context") or {}
    idx = mc.get("index_summary") or {}
    ficc = mc.get("ficc_summary") or {}
    sec = mc.get("sector_summary") or {}
    flow = mc.get("flow_summary") or {}
    stocks = mc.get("feature_stocks") or []

    kr = [str(x).strip() for x in _ensure_list(d.get("kr_bullets")) if str(x).strip()]
    ov = [str(x).strip() for x in _ensure_list(d.get("overnight_bullets")) if str(x).strip()]

    prefix_kr: List[str] = []
    prefix_ov: List[str] = []

    kospi = idx.get("KOSPI")
    kosdaq = idx.get("KOSDAQ")
    if _need_numeric_override(kr, ["KOSPI", "KOSDAQ"]):
        parts = []
        if kospi and kospi.get("ret1d_pct") is not None:
            parts.append(f"KOSPI {_fmt_pct(kospi.get('ret1d_pct'))}")
        if kosdaq and kosdaq.get("ret1d_pct") is not None:
            parts.append(f"KOSDAQ {_fmt_pct(kosdaq.get('ret1d_pct'))}")
        if parts:
            prefix_kr.append(", ".join(parts) + ". 지수 방향성과 상대강도가 전일 국내장 해석의 출발점이었다. [근거: M:KOSPI, M:KOSDAQ]")

    if _need_numeric_override(kr, ["외국인", "기관", "개인", "수급"]):
        for mkt in ["KOSPI", "KOSDAQ"]:
            panel = flow.get(mkt) or {}
            if not panel:
                continue
            parts = []
            if panel.get("foreign_1e8krw") is not None:
                parts.append(f"외국인 {_fmt_1e8(panel.get('foreign_1e8krw'))}")
            if panel.get("institution_1e8krw") is not None:
                parts.append(f"기관 {_fmt_1e8(panel.get('institution_1e8krw'))}")
            if panel.get("retail_1e8krw") is not None:
                parts.append(f"개인 {_fmt_1e8(panel.get('retail_1e8krw'))}")
            if parts:
                dominant = _investor_display(panel.get("dominant_actor")) or "수급"
                prefix_kr.append(f"{mkt} 수급은 " + ", ".join(parts) + f" 수준이었다. 절대금액 기준으론 {dominant} 주도가 확인된다. [근거: KRX flows]")
                break

    if _need_numeric_override(kr, ["업종", "반도체", "증권", "자동차", "은행", "정유"]):
        leaders = (sec.get("leaders") or [])[:2]
        laggards = (sec.get("laggards") or [])[:2]
        chunks = []
        if leaders:
            chunks.append("강세 업종 " + ", ".join(f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in leaders))
        if laggards:
            chunks.append("약세 업종 " + ", ".join(f"{x.get('name')} {_fmt_pct(x.get('ret1d_pct'))}" for x in laggards))
        if chunks:
            prefix_kr.append("; ".join(chunks) + ". 업종 확산 여부와 쏠림 강도를 함께 볼 필요가 있다. [근거: KRX sectors]")

    if _need_numeric_override(kr, ["특징주", "종목:", "순매수"]):
        for x in stocks[:2]:
            name = x.get("name")
            if not name:
                continue
            flow_label = x.get("flow_label") or "수급"
            if x.get("ret1d_pct") is not None:
                prefix_kr.append(f"{name}는 {_fmt_pct(x.get('ret1d_pct'))} 움직였고 {flow_label}가 동반됐다. [근거: STOCK:{name}]")
            elif x.get("net_buy_1e8krw") is not None:
                prefix_kr.append(f"{name}에는 {flow_label} {_fmt_1e8(x.get('net_buy_1e8krw'))}가 집중됐다. [근거: STOCK:{name}]")
            break

    if _need_numeric_override(ov, ["S&P500", "NASDAQ", "원/달러", "미국 10년물", "UST 10Y"]):
        parts = []
        spx = idx.get("S&P500")
        ndx = idx.get("NASDAQ")
        ust10 = ficc.get("UST 10Y")
        fx = ficc.get("USDKRW")
        if spx and spx.get("ret1d_pct") is not None:
            parts.append(f"S&P500 {_fmt_pct(spx.get('ret1d_pct'))}")
        if ndx and ndx.get("ret1d_pct") is not None:
            parts.append(f"NASDAQ {_fmt_pct(ndx.get('ret1d_pct'))}")
        if ust10 and ust10.get("chg1d_bp") is not None:
            parts.append(f"미국 10년물 {_fmt_bp(ust10.get('chg1d_bp'))}")
        if fx and fx.get("ret1d_pct") is not None:
            parts.append(f"원/달러 {_fmt_pct(fx.get('ret1d_pct'))}")
        if parts:
            prefix_ov.append(", ".join(parts) + ". 한국 증시 기준으로는 금리와 환율 경로가 대형주와 성장주 선호를 좌우할 변수다. [근거: M:S&P500, M:NASDAQ, M:UST 10Y, M:USDKRW]")

    merged_kr: List[str] = []
    for x in prefix_kr + kr:
        if x not in merged_kr:
            merged_kr.append(x)
    merged_ov: List[str] = []
    for x in prefix_ov + ov:
        if x not in merged_ov:
            merged_ov.append(x)

    d["kr_bullets"] = merged_kr[:10]
    d["overnight_bullets"] = merged_ov[:10]


def _normalize(d: dict, fact_pack: dict) -> dict:
    d.setdefault("asof", fact_pack.get("asof", ""))
    if not d.get("headline") or not _contains_number(d.get("headline")):
        d["headline"] = _build_numeric_headline(fact_pack)
    d.setdefault("disclaimer", "RSS 헤드라인 및 공개 데이터 기반의 자동 작성 초안")

    t5 = [str(x).strip() for x in _ensure_list(d.get("today_5lines")) if str(x).strip()]
    fallback_t5 = _fallback_today_5lines(fact_pack)
    if len(t5) < 5:
        t5.extend(fallback_t5)
    if sum(1 for x in t5[:5] if _contains_number(x)) < 3:
        t5 = fallback_t5
    d["today_5lines"] = t5[:5]

    d["kr_bullets"] = [str(x).strip() for x in _ensure_list(d.get("kr_bullets")) if str(x).strip()][:10]
    d["overnight_bullets"] = [str(x).strip() for x in _ensure_list(d.get("overnight_bullets")) if str(x).strip()][:10]
    d["tomorrow_watch"] = [str(x).strip() for x in _ensure_list(d.get("tomorrow_watch")) if str(x).strip()][:8]

    pa = _ensure_list(d.get("price_action"))
    fixed_pa = []
    for m in pa:
        if not isinstance(m, dict):
            continue
        asset = str(m.get("asset") or "").strip()
        move = str(m.get("move") or "").strip()
        evidence = str(m.get("evidence") or "").strip() if m.get("evidence") is not None else None
        comment = str(m.get("comment") or "").strip()
        if not asset:
            continue
        fixed_pa.append({"asset": asset, "move": move, "evidence": evidence, "comment": comment})

    numeric_pa = _fallback_price_action(fact_pack)
    if len(fixed_pa) < 8 or sum(1 for x in fixed_pa if _contains_number(x.get("move", ""))) < 6:
        fixed_pa = numeric_pa
    else:
        existing = {x.get("asset") for x in fixed_pa}
        for item in numeric_pa:
            if item.get("asset") not in existing:
                fixed_pa.append(item)
    d["price_action"] = fixed_pa[:14]

    td = _ensure_list(d.get("top_drivers"))
    fixed_td = []
    for x in td:
        if not isinstance(x, dict):
            continue
        fixed_td.append({
            "title": str(x.get("title") or "").strip(),
            "why_it_matters": str(x.get("why_it_matters") or "").strip(),
            "sources": x.get("sources") or [],
        })
    d["top_drivers"] = fixed_td[:12]

    rr = _ensure_list(d.get("risk_radar"))
    fixed_rr = []
    for x in rr:
        if not isinstance(x, dict):
            continue
        fixed_rr.append({
            "name": str(x.get("name") or "").strip(),
            "level": str(x.get("level") or "Yellow").strip(),
            "trigger": str(x.get("trigger") or "").strip(),
            "sources": x.get("sources") or [],
        })
    d["risk_radar"] = fixed_rr

    _ensure_numeric_bullets(d, fact_pack)
    return d


def generate_report(fact_pack: dict, cfg: dict, log=None) -> DailyBriefing:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    llm_cfg = cfg.get("llm", {}) or {}
    model = llm_cfg.get("model", "gpt-5-mini")
    temperature = float(llm_cfg.get("temperature", 0.15))
    max_out = int(llm_cfg.get("max_output_tokens", 3200))

    fact_pack_json = json.dumps(fact_pack, ensure_ascii=False)
    sys = SYSTEM_PROMPT + """
추가 규칙:
- top_drivers는 fact_pack.events_top를 우선 활용해 event 단위로 묶어라.
- 한국장 관련 포인트에서는 market_context.index_summary -> market_context.ficc_summary -> market_context.sector_summary -> market_context.flow_summary -> events_top 순으로 우선 확인해라.
- 특징 업종은 market_context.sector_summary.feature_sectors를 우선 사용해라.
- 특징주는 market_context.feature_stocks를 우선 사용해라.
- 숫자를 쓸 수 있는 곳에서 숫자를 생략하지 마라.
- 출력은 반드시 JSON 1개만.
"""

    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(fact_pack_json=fact_pack_json)},
    ]

    resp = client.responses.create(**_build_responses_kwargs(model, messages, temperature, max_out))
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
필수 키를 모두 채우고, 숫자 정보가 있으면 today_5lines, price_action, kr_bullets, overnight_bullets에 최대한 반영하라.

[Broken Output]
{text}
"""},
        ]
        resp2 = client.responses.create(**_build_responses_kwargs(model, repair_messages, 0, max_out))
        text2 = _extract_text(resp2)
        d2 = _safe_json_load(text2)
        d2 = _normalize(d2, fact_pack)
        return DailyBriefing.model_validate(d2)

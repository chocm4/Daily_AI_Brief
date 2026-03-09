import os
import json
from openai import OpenAI
from src.llm.schema import DailyBriefing
from src.llm.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


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
            return json.loads(s[a:b + 1])
        raise


def _ensure_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _normalize(d: dict, fact_pack: dict) -> dict:
    d.setdefault("asof", fact_pack.get("asof", ""))
    d.setdefault("headline", "오늘의 시황")
    d.setdefault("disclaimer", "RSS 헤드라인 및 공개 데이터 기반의 자동 작성 초안")

    d["today_5lines"] = _ensure_list(d.get("today_5lines"))[:5]
    d["kr_bullets"] = _ensure_list(d.get("kr_bullets"))[:9]
    d["overnight_bullets"] = _ensure_list(d.get("overnight_bullets"))[:9]
    d["tomorrow_watch"] = _ensure_list(d.get("tomorrow_watch"))[:8]

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
    d["price_action"] = fixed_pa[:12]

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
- 한국장 관련 포인트에서는 가능하면 market_context.sector_summary, market_context.flow_summary를 먼저 확인해라.
- 사실과 해석을 섞을 때는 문장 내에서 분리해라. 예: '...로 확인된다. 추정: ...'.
- 뉴스가 많아도 중요도 낮은 기사 나열은 금지.
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

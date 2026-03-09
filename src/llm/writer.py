
import os, json
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
        # 첫 줄 제거
        lines = s.splitlines()
        # 마지막 ``` 제거
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
        # JSON 본문만 잘라서 재시도
        a = s.find("{")
        b = s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a:b+1])
        raise

def _ensure_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]

def _normalize(d: dict, fact_pack: dict) -> dict:
    # 필수 키 채우기
    d.setdefault("asof", fact_pack.get("asof", ""))
    d.setdefault("headline", "오늘의 시황")
    d.setdefault("disclaimer", "RSS 헤드라인 및 공개 데이터 기반의 자동 작성 초안")

    d["today_5lines"] = _ensure_list(d.get("today_5lines"))
    d["kr_bullets"] = _ensure_list(d.get("kr_bullets"))
    d["overnight_bullets"] = _ensure_list(d.get("overnight_bullets"))
    d["tomorrow_watch"] = _ensure_list(d.get("tomorrow_watch"))

    # price_action 보정: move/evidence가 숫자면 문자열화, comment 없으면 채움
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
    d["price_action"] = fixed_pa

    # top_drivers 보정: sources/why_it_matters 누락 방어, id만 있으면 sources로 승격
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
    d["top_drivers"] = fixed_td

    # risk_radar 보정
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
    model = llm_cfg.get("model", "gpt-4o-mini")
    temperature = float(llm_cfg.get("temperature", 0.2))
    max_out = int(llm_cfg.get("max_output_tokens", 2600))

    fact_pack_json = json.dumps(fact_pack, ensure_ascii=False)

    sys = SYSTEM_PROMPT + """
추가 규칙:
추가 규칙(시황 품질 강화):
- 'top_drivers'는 반드시 "시장에 영향을 줄 만한 이벤트/사건" 중심 Top 3~5개를 먼저 뽑아라.
- 후보 선택 시 우선순위: (1) mentions가 큰 이슈, (2) mention_sources(고유 소스) 수가 많은 이슈, (3) published가 최근인 이슈.
- 각 top_driver는 반드시 다음을 포함:
  1) 한 줄 요약(무슨 일이 있었는지)
  2) why_it_matters: 왜 금융시장(주식/금리/FX/원자재/섹터)에 영향인지
  3) sources: 관련 뉴스 id 목록(가능하면 서로 다른 소스 포함)
- 단순 "주가 상승/하락" 기사만으로 채우지 말고, '정책/딜/규제/실적 가이던스/신용 이벤트/지정학' 등 촉발 요인을 우선하라.


- 출력은 반드시 '유효한 JSON 1개'만.
- 절대 ``` 코드펜스 쓰지 마라.
- move/evidence는 문자열로 써라(예: "-1.57%", "z20=-1.68").
"""

    messages = [
        {"role":"system","content":sys},
        {"role":"user","content":USER_PROMPT_TEMPLATE.format(fact_pack_json=fact_pack_json)},
    ]

    # 1차 생성
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

        # 2차 리페어
        repair_messages = [
            {"role":"system","content":"너는 JSON 리페어 도구다. 반드시 JSON만 출력한다. ``` 금지."},
            {"role":"user","content":f"""아래 출력이 스키마를 어겼거나 JSON이 깨졌다.
필수 키를 모두 채우고, move/evidence는 문자열로, top_drivers에는 sources(list[str])를 넣어라.
JSON 외 텍스트 금지.

[Broken Output]
{text}
"""}
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

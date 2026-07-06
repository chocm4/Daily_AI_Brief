"""LLM 레이어 공용 헬퍼.

writer.py와 narrative.py에 중복돼 있던 OpenAI Responses API 플러밍과
숫자 포맷 함수를 한곳으로 모은 모듈. 동작은 기존과 동일하다.
"""

from typing import Optional


# --- OpenAI Responses API plumbing ---

def is_reasoning_model(model: str) -> bool:
    """GPT-5 family and o-series are reasoning models on the Responses API."""
    m = (model or "").lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


def supports_temperature(model: str) -> bool:
    """Reasoning models do not accept temperature on the Responses API."""
    return not is_reasoning_model(model)


def build_responses_kwargs(
    model: str,
    messages,
    temperature: float,
    max_out: int,
    reasoning_effort: Optional[str] = None,
) -> dict:
    kwargs = {"model": model, "input": messages, "max_output_tokens": max_out}
    if supports_temperature(model):
        kwargs["temperature"] = temperature
    if reasoning_effort and is_reasoning_model(model):
        kwargs["reasoning"] = {"effort": reasoning_effort}
    return kwargs


def extract_text(resp) -> str:
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


# --- number formatting ---

def to_float(x) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def fmt_pct(v) -> str:
    v = to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+.2f}%"


def fmt_bp(v) -> str:
    v = to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+.1f}bp"


def fmt_1e8(v) -> str:
    v = to_float(v)
    if v is None:
        return "데이터 없음"
    return f"{v:+,.0f}억원"

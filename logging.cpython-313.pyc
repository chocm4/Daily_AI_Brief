
from typing import Any, Dict
from ..llm.narrative import generate_narrative_md

def render_story(report: Any, fact_pack: Dict, cfg: dict, log=None) -> str:
    rep_dict = report.model_dump() if hasattr(report, "model_dump") else (report if isinstance(report, dict) else {})
    return generate_narrative_md(fact_pack, rep_dict, cfg, log=log)

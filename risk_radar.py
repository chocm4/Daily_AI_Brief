
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any

class MarketMove(BaseModel):
    asset: str
    move: str
    comment: str
    evidence: Optional[str] = None

    @field_validator("move", "evidence", mode="before")
    @classmethod
    def coerce_to_str(cls, v: Any):
        if v is None:
            return None
        # 숫자로 오면 문자열로 변환 (예: -1.57 -> "-1.57")
        if isinstance(v, (int, float)):
            return str(v)
        return str(v)

class NewsDriver(BaseModel):
    title: str
    why_it_matters: str = ""                       # 누락 방어
    sources: List[str] = Field(default_factory=list)  # 누락 방어

class RiskItem(BaseModel):
    name: str
    level: str
    trigger: str
    sources: List[str] = Field(default_factory=list)

class DailyBriefing(BaseModel):
    asof: str
    headline: str
    today_5lines: List[str] = Field(default_factory=list)   # ✅ (너 로그에 required로 떠서 포함)
    kr_bullets: List[str] = Field(default_factory=list)
    overnight_bullets: List[str] = Field(default_factory=list)
    price_action: List[MarketMove] = Field(default_factory=list)
    top_drivers: List[NewsDriver] = Field(default_factory=list)
    risk_radar: List[RiskItem] = Field(default_factory=list)
    tomorrow_watch: List[str] = Field(default_factory=list)
    disclaimer: str

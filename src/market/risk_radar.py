
def build_risk_radar(market: list[dict]) -> list[dict]:
    """
    Rule-based risk radar.
    - 시장 데이터가 없으면 '확실하지 않음'으로 표시
    """
    if not market:
        return [{
            "name": "데이터 상태",
            "level": "Yellow",
            "trigger": "market 데이터 없음 → 레이더 산출 불가",
            "sources": []
        }]

    by_name = {m["name"]: m for m in market}

    def get(name: str):
        return by_name.get(name)

    out = []

    # 1) 변동성(VIX)
    vix = get("VIX")
    if vix:
        z = vix.get("z20_ret", 0.0)
        lvl = "Green"
        if z >= 1.5: lvl = "Red"
        elif z >= 0.8: lvl = "Yellow"
        out.append({"name":"변동성(VIX)","level":lvl,"trigger":f"VIX 1일수익률 z20={z} (>=1.5 Red, >=0.8 Yellow)","sources":[]})

    # 2) 환율(USDKRW)
    fx = get("USDKRW")
    if fx:
        z = fx.get("z20_ret", 0.0)
        r = fx.get("ret1d_pct", 0.0)
        lvl = "Green"
        if z >= 1.5 or r >= 1.0: lvl = "Red"
        elif z >= 0.8 or r >= 0.6: lvl = "Yellow"
        out.append({"name":"환율(원/달러)","level":lvl,"trigger":f"USDKRW ret1d={r:.2f}%, z20={z}","sources":[]})

    # 3) 글로벌 주식(S&P500)
    spx = get("S&P500")
    if spx:
        r = spx.get("ret1d_pct", 0.0)
        z = spx.get("z20_ret", 0.0)
        lvl = "Green"
        if r <= -2.0 or z <= -1.5: lvl = "Red"
        elif r <= -1.0 or z <= -0.8: lvl = "Yellow"
        out.append({"name":"글로벌 주식(S&P500)","level":lvl,"trigger":f"S&P500 ret1d={r:.2f}%, z20={z}","sources":[]})

    # 4) 국내 주식(KOSPI proxy)
    ks = get("KOSPI proxy")
    if ks:
        r = ks.get("ret1d_pct", 0.0)
        z = ks.get("z20_ret", 0.0)
        lvl = "Green"
        if r <= -2.0 or z <= -1.5: lvl = "Red"
        elif r <= -1.0 or z <= -0.8: lvl = "Yellow"
        out.append({"name":"국내 주식(KOSPI)","level":lvl,"trigger":f"KOSPI ret1d={r:.2f}%, z20={z}","sources":[]})

    return out[:6]

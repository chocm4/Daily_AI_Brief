from typing import Any, Dict, List, Optional


INDEX_ALIASES = {
    "iShares MSCI ACWI": "MSCI ACWI",
    "iShares MSCI World": "MSCI DM",
    "iShares MSCI EM": "MSCI EM",
    "Euro Currency Index": "EXY",
}


GLOBAL_INDEX_ORDER = [
    "KOSPI",
    "KOSDAQ",
    "Dow Jones",
    "S&P500",
    "NASDAQ",
    "Russell 2000",
    "SOX",
    "Nikkei 225",
    "Taiwan Weighted",
    "Hang Seng",
    "CSI300",
    "Shanghai Composite",
    "Euro Stoxx 50",
    "STOXX Europe 600",
    "MSCI ACWI",
    "MSCI DM",
    "MSCI EM",
]

FICC_ORDER = [
    "USDKRW",
    "DXY",
    "EXY",
    "WTI",
    "Gold",
    "VIX",
    "MOVE",
    "VKOSPI",
    "UST 3M",
    "UST 5Y",
    "UST 10Y",
    "UST 30Y",
]


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


def _canonical_name(name: Optional[str]) -> str:
    name = (name or "").strip()
    return INDEX_ALIASES.get(name, name)


def _find_asset(market: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    targets = {name}
    for raw_name, alias in INDEX_ALIASES.items():
        if name == alias:
            targets.add(raw_name)
        if name == raw_name:
            targets.add(alias)

    for x in market or []:
        nm = _canonical_name(x.get("name") or x.get("asset"))
        if nm in targets:
            return x
    return None


def _asset_brief(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None

    kind = (row.get("kind") or "price").lower()
    name = _canonical_name(row.get("name") or row.get("asset") or "")
    out = {
        "name": name,
        "kind": kind,
        "level": row.get("level"),
        "date": row.get("date") or row.get("daily_date") or row.get("asof_date"),
        "ref_ts_kst": row.get("ref_ts_kst") or row.get("last_update_kst") or row.get("last_ts_kst"),
    }

    if kind == "yield":
        out["chg1d_bp"] = _to_float(row.get("chg1d_bp"))
        out["move_text"] = _fmt_bp(row.get("chg1d_bp"))
    else:
        out["ret1d_pct"] = _to_float(row.get("ret1d_pct"))
        out["move_text"] = _fmt_pct(row.get("ret1d_pct"))

    return out


def _top_moves(market: List[Dict[str, Any]], top_k: int = 8) -> List[Dict[str, Any]]:
    rows = []
    for x in market or []:
        kind = (x.get("kind") or "price").lower()
        if kind == "yield":
            mag = abs(_to_float(x.get("chg1d_bp")) or 0.0)
        else:
            mag = abs(_to_float(x.get("ret1d_pct")) or 0.0)

        rows.append(
            {
                "name": _canonical_name(x.get("name") or x.get("asset") or ""),
                "kind": kind,
                "level": x.get("level"),
                "ret1d_pct": _to_float(x.get("ret1d_pct")),
                "chg1d_bp": _to_float(x.get("chg1d_bp")),
                "abs_move": mag,
            }
        )

    rows = [x for x in rows if x["name"]]
    rows.sort(key=lambda z: z["abs_move"], reverse=True)
    return rows[:top_k]


def _build_benchmark_summary(market: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = GLOBAL_INDEX_ORDER + FICC_ORDER
    out = {}
    for name in names:
        row = _find_asset(market, name)
        if row:
            out[name] = _asset_brief(row)
    return out


def _pick_rows(benchmarks: Dict[str, Any], keep: List[str]) -> Dict[str, Any]:
    return {k: benchmarks[k] for k in keep if k in benchmarks}


def _build_index_summary(benchmarks: Dict[str, Any]) -> Dict[str, Any]:
    return _pick_rows(
        benchmarks,
        [
            "KOSPI",
            "KOSDAQ",
            "Dow Jones",
            "S&P500",
            "NASDAQ",
            "Russell 2000",
            "SOX",
            "Nikkei 225",
            "Taiwan Weighted",
            "Hang Seng",
            "CSI300",
            "Shanghai Composite",
            "Euro Stoxx 50",
            "STOXX Europe 600",
            "MSCI ACWI",
            "MSCI DM",
            "MSCI EM",
        ],
    )


def _build_ficc_summary(benchmarks: Dict[str, Any]) -> Dict[str, Any]:
    return _pick_rows(
        benchmarks,
        [
            "USDKRW",
            "DXY",
            "EXY",
            "WTI",
            "Gold",
            "VIX",
            "MOVE",
            "VKOSPI",
            "UST 3M",
            "UST 5Y",
            "UST 10Y",
            "UST 30Y",
        ],
    )


def _build_global_summary(benchmarks: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "domestic": _pick_rows(benchmarks, ["KOSPI", "KOSDAQ"]),
        "us": _pick_rows(benchmarks, ["Dow Jones", "S&P500", "NASDAQ", "Russell 2000", "SOX"]),
        "asia": _pick_rows(benchmarks, ["Nikkei 225", "Taiwan Weighted", "Hang Seng", "CSI300", "Shanghai Composite"]),
        "europe": _pick_rows(benchmarks, ["Euro Stoxx 50", "STOXX Europe 600"]),
        "world_etf": _pick_rows(benchmarks, ["MSCI ACWI", "MSCI DM", "MSCI EM"]),
        "macro": _pick_rows(benchmarks, ["USDKRW", "DXY", "EXY", "WTI", "Gold", "VIX", "MOVE", "VKOSPI", "UST 10Y"]),
    }


def _infer_market_flags(benchmarks: Dict[str, Any], sector_summary: Dict[str, Any]) -> List[str]:
    flags: List[str] = []

    kospi = benchmarks.get("KOSPI")
    kosdaq = benchmarks.get("KOSDAQ")
    usdkrw = benchmarks.get("USDKRW")
    ust10 = benchmarks.get("UST 10Y")
    nasdaq = benchmarks.get("NASDAQ")
    vix = benchmarks.get("VIX")
    move = benchmarks.get("MOVE")
    vkospi = benchmarks.get("VKOSPI")
    dxy = benchmarks.get("DXY")

    if kospi and kosdaq:
        k1 = _to_float(kospi.get("ret1d_pct"))
        k2 = _to_float(kosdaq.get("ret1d_pct"))
        if k1 is not None and k2 is not None:
            if k1 > k2 + 0.7:
                flags.append("KOSPI 우위 장세")
            elif k2 > k1 + 0.7:
                flags.append("KOSDAQ 우위 장세")

    if usdkrw:
        fx = _to_float(usdkrw.get("ret1d_pct"))
        if fx is not None:
            if fx >= 0.5:
                flags.append("원화 약세 부담")
            elif fx <= -0.5:
                flags.append("원화 강세 환경")

    if dxy:
        dx = _to_float(dxy.get("ret1d_pct"))
        if dx is not None:
            if dx >= 0.4:
                flags.append("달러 강세 압력")
            elif dx <= -0.4:
                flags.append("달러 약세 완화")

    if ust10:
        y = _to_float(ust10.get("chg1d_bp"))
        if y is not None:
            if y >= 5:
                flags.append("미국 장기금리 상승 압력")
            elif y <= -5:
                flags.append("미국 장기금리 안정")

    if nasdaq and ust10:
        n = _to_float(nasdaq.get("ret1d_pct"))
        y = _to_float(ust10.get("chg1d_bp"))
        if n is not None and y is not None:
            if n > 0 and y < 0:
                flags.append("성장주 친화 조합")
            elif n < 0 and y > 0:
                flags.append("밸류에이션 부담 조합")

    if vix:
        vv = _to_float(vix.get("ret1d_pct"))
        if vv is not None and vv >= 8:
            flags.append("미국 변동성 확대")

    if move:
        mv = _to_float(move.get("ret1d_pct"))
        if mv is not None and mv >= 8:
            flags.append("채권 변동성 확대")

    if vkospi:
        kv = _to_float(vkospi.get("ret1d_pct"))
        if kv is not None and kv >= 8:
            flags.append("국내 변동성 확대")

    dispersion = _to_float(sector_summary.get("dispersion_pctp"))
    if dispersion is not None and dispersion >= 2.5:
        flags.append("업종 차별화가 큰 장세")

    return flags


def _build_sector_summary(sectors_kr: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    sectors_kr = sectors_kr or []
    ordered = sorted(
        [x for x in sectors_kr if x.get("ret1d_pct") is not None],
        key=lambda z: float(z["ret1d_pct"]),
        reverse=True,
    )

    if not ordered:
        return {"leaders": [], "laggards": [], "dispersion_pctp": None, "feature_sectors": []}

    leaders = ordered[:3]
    laggards = list(reversed(ordered[-3:])) if len(ordered) >= 3 else ordered[-3:]
    dispersion = round(float(ordered[0]["ret1d_pct"]) - float(ordered[-1]["ret1d_pct"]), 2)

    feature_sectors = []
    for x in leaders[:2]:
        feature_sectors.append(
            {
                "name": x.get("name"),
                "ret1d_pct": float(x.get("ret1d_pct")),
                "direction": "leader",
                "comment_hint": "상대강도 상위 업종",
            }
        )
    for x in laggards[:2]:
        feature_sectors.append(
            {
                "name": x.get("name"),
                "ret1d_pct": float(x.get("ret1d_pct")),
                "direction": "laggard",
                "comment_hint": "상대약세 업종",
            }
        )

    return {
        "leaders": [{"name": x.get("name"), "ret1d_pct": float(x.get("ret1d_pct"))} for x in leaders],
        "laggards": [{"name": x.get("name"), "ret1d_pct": float(x.get("ret1d_pct"))} for x in laggards],
        "dispersion_pctp": dispersion,
        "feature_sectors": feature_sectors,
    }


def _normalize_top_list(items: Optional[List[Dict[str, Any]]], investor: str) -> List[Dict[str, Any]]:
    out = []
    for x in items or []:
        out.append(
            {
                "investor": investor,
                "ticker": x.get("ticker"),
                "name": x.get("name"),
                "ret1d_pct": _to_float(x.get("ret1d_pct")),
                "close": x.get("close"),
                "unit": x.get("unit"),
                "net_buy_1e8krw": _to_float(x.get("net_buy_1e8krw")),
                "net_buy_shares": _to_float(x.get("net_buy_shares")),
            }
        )
    return out


def _build_flow_summary(
    krx_flows: Optional[Dict[str, Any]],
    krx_flow_tops: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    krx_flows = krx_flows or {}
    krx_flow_tops = krx_flow_tops or {}

    out: Dict[str, Any] = {}

    for market_name, panel in krx_flows.items():
        panel = panel or {}
        net = panel.get("net_buy_1e8krw") or {}
        if not net:
            continue

        foreign = _to_float(net.get("외국인"))
        inst = _to_float(net.get("기관합계"))
        retail = _to_float(net.get("개인"))

        abs_rank = sorted(
            [
                ("외국인", abs(foreign or 0)),
                ("기관합계", abs(inst or 0)),
                ("개인", abs(retail or 0)),
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        dominant = abs_rank[0][0] if abs_rank else None

        out[market_name] = {
            "foreign_1e8krw": foreign,
            "institution_1e8krw": inst,
            "retail_1e8krw": retail,
            "dominant_actor": dominant,
            "top_foreign": _normalize_top_list(((krx_flow_tops.get(market_name) or {}).get("외국인") or [])[:5], "외국인"),
            "top_institution": _normalize_top_list(((krx_flow_tops.get(market_name) or {}).get("기관합계") or [])[:5], "기관합계"),
            "top_retail": _normalize_top_list(((krx_flow_tops.get(market_name) or {}).get("개인") or [])[:5], "개인"),
        }

    return out


def _build_feature_stocks(flow_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    picked: List[Dict[str, Any]] = []
    seen = set()

    for market_name in ["KOSPI", "KOSDAQ"]:
        panel = flow_summary.get(market_name) or {}
        for bucket_name, label in [
            ("top_foreign", "외국인 순매수"),
            ("top_institution", "기관 순매수"),
            ("top_retail", "개인 순매수"),
        ]:
            for x in (panel.get(bucket_name) or [])[:2]:
                name = x.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                picked.append(
                    {
                        "market": market_name,
                        "name": name,
                        "ticker": x.get("ticker"),
                        "ret1d_pct": x.get("ret1d_pct"),
                        "flow_label": label,
                        "net_buy_1e8krw": x.get("net_buy_1e8krw"),
                        "net_buy_shares": x.get("net_buy_shares"),
                    }
                )

    picked.sort(
        key=lambda z: (
            abs(_to_float(z.get("ret1d_pct")) or 0.0),
            abs(_to_float(z.get("net_buy_1e8krw")) or 0.0),
        ),
        reverse=True,
    )
    return picked[:8]


def build_market_context(
    market: Optional[List[Dict[str, Any]]],
    sectors_kr: Optional[List[Dict[str, Any]]] = None,
    krx_flows: Optional[Dict[str, Any]] = None,
    krx_flow_tops: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    market = market or []

    benchmarks = _build_benchmark_summary(market)
    sector_summary = _build_sector_summary(sectors_kr)
    flow_summary = _build_flow_summary(krx_flows, krx_flow_tops)
    feature_stocks = _build_feature_stocks(flow_summary)

    context: Dict[str, Any] = {
        "benchmark_summary": benchmarks,
        "index_summary": _build_index_summary(benchmarks),
        "ficc_summary": _build_ficc_summary(benchmarks),
        "global_summary": _build_global_summary(benchmarks),
        "market_top_moves": _top_moves(market, top_k=12),
        "sector_summary": sector_summary,
        "flow_summary": flow_summary,
        "feature_stocks": feature_stocks,
        "style_flags": _infer_market_flags(benchmarks, sector_summary),
    }

    for market_name, panel in flow_summary.items():
        dominant = panel.get("dominant_actor")
        if dominant:
            context["style_flags"].append(f"{market_name} 수급 주도: {dominant}")

    return context

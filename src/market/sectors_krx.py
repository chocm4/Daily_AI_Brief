import datetime as dt
from typing import List, Dict, Any

import pandas as pd


def _normalize_sec_cfg(cfg: dict) -> dict:
    """
    run_daily에서는 보통 cfg['market']['sectors_krx'] (딕셔너리 자체)를 넘김.
    기존 코드처럼 cfg.get('sectors')를 찾으면 항상 비활성화되는 문제가 있었음.
    """
    if not isinstance(cfg, dict):
        return {}
    # 1) 이미 sectors_krx 딕셔너리 형태라면 그대로
    if "enabled" in cfg or "market" in cfg or "top_k" in cfg:
        return cfg
    # 2) 레거시: cfg['sectors'] 형태를 지원
    if "sectors" in cfg and isinstance(cfg["sectors"], dict):
        return cfg["sectors"]
    return {}


def fetch_krx_sector_snapshot(asof: dt.date, cfg: dict, log=None) -> List[Dict[str, Any]]:
    sec_cfg = _normalize_sec_cfg(cfg)
    if not sec_cfg.get("enabled", False):
        return []

    market = str(sec_cfg.get("market", "KOSPI")).upper()
    lookback_days = int(sec_cfg.get("lookback_days", 14))
    top_k = int(sec_cfg.get("top_k", 4))
    bottom_k = int(sec_cfg.get("bottom_k", 4))

    try:
        from pykrx import stock
    except Exception as e:
        if log:
            log.warning(f"sectors_krx: pykrx import failed: {e}")
        return []

    # 마지막 2개 거래일을 잡기 위한 앵커 지수
    anchor = "1001" if market == "KOSPI" else "2001"  # 관행: 1001=코스피, 2001=코스닥
    end = asof.strftime("%Y%m%d")
    start = (asof - dt.timedelta(days=lookback_days)).strftime("%Y%m%d")

    # pykrx 버전별 함수명 fallback
    ohlcv = None
    try:
        # 일부 환경에서 get_index_ohlcv(start,end,ticker) 형태가 동작
        ohlcv = stock.get_index_ohlcv(start, end, anchor)
    except Exception:
        pass
    if ohlcv is None or getattr(ohlcv, "empty", True):
        try:
            ohlcv = stock.get_index_ohlcv_by_date(start, end, anchor)
        except Exception as e:
            if log:
                log.warning(f"sectors_krx: index ohlcv fetch failed: {e}")
            return []

    if ohlcv is None or ohlcv.empty or len(ohlcv.index) < 2:
        return []

    dates = list(pd.to_datetime(ohlcv.index).sort_values())
    d0 = dates[-2].strftime("%Y%m%d")
    d1 = dates[-1].strftime("%Y%m%d")

    # 업종/지수 등락률(기간 등락률)
    chg = None
    try:
        chg = stock.get_index_price_change(d0, d1, market)
    except Exception:
        # 혹시 인자 순서/타입이 다른 경우를 대비해 2차 시도
        try:
            chg = stock.get_index_price_change(d0, d1, market=market)
        except Exception as e:
            if log:
                log.warning(f"sectors_krx: get_index_price_change failed: {e}")
            return []

    if chg is None or chg.empty:
        return []

    df = chg.reset_index()

    # 이름 컬럼 정규화
    if "지수명" in df.columns:
        df = df.rename(columns={"지수명": "name"})
    elif "INDEX_NM" in df.columns:
        df = df.rename(columns={"INDEX_NM": "name"})
    else:
        df = df.rename(columns={df.columns[0]: "name"})

    # 등락률 컬럼 정규화
    ret_col = None
    for c in ["등락률", "등락률(%)", "CHG_RT", "변동률"]:
        if c in df.columns:
            ret_col = c
            break
    if ret_col is None:
        # 숫자 컬럼 중 첫번째를 사용(최후 fallback)
        num_cols = [c for c in df.columns if c != "name"]
        if not num_cols:
            return []
        ret_col = num_cols[0]

    df[ret_col] = pd.to_numeric(df[ret_col], errors="coerce")
    df = df.dropna(subset=[ret_col]).copy()

    # 광의지수 제거(너무 많이 걸러지면 원본 유지)
    drop_pat = r"(?:코스피|코스닥|KRX|TOP|200|100|50|선물|옵션|리츠|채권)"
    df2 = df[~df["name"].astype(str).str.contains(drop_pat, regex=True, na=False)].copy()
    if len(df2) < 8:
        df2 = df.copy()

    df2 = df2.sort_values(ret_col, ascending=False)

    top = df2.head(top_k)
    bot = df2.tail(bottom_k).sort_values(ret_col, ascending=True)

    out: List[Dict[str, Any]] = []
    for _, r in pd.concat([top, bot], axis=0).iterrows():
        out.append(
            {
                "name": str(r["name"]),
                "ret1d_pct": float(r[ret_col]),
                "from_date": dt.datetime.strptime(d0, "%Y%m%d").date().isoformat(),
                "to_date": dt.datetime.strptime(d1, "%Y%m%d").date().isoformat(),
                "market": market,
            }
        )
    return out
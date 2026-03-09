# src/market/flows_krx.py
import datetime as dt
from typing import Optional, Any


def prev_business_day(d: dt.date) -> dt.date:
    x = d - dt.timedelta(days=1)
    while x.weekday() >= 5:  # Sat/Sun
        x -= dt.timedelta(days=1)
    return x


def _pick_netbuy_col(df) -> Optional[str]:
    """
    pykrx 버전/함수에 따라 컬럼명이 다를 수 있음.
    - 시장 전체 투자자별: 보통 '순매수'
    - 종목별: '순매수거래대금', '순매수금액', '순매수', 'NETBUY' 등 다양
    """
    if df is None or getattr(df, "empty", True):
        return None
    cols = list(df.columns)

    # 1) 가장 명시적인 금액 계열 우선
    for key in ["순매수거래대금", "순매수금액", "순매수대금", "NETBUY_VALUE", "NETBUY_AMT"]:
        if key in cols:
            return key

    # 2) 일반 순매수
    for key in ["순매수", "NETBUY", "net_buy"]:
        if key in cols:
            return key

    # 3) 숫자 컬럼 중 하나 선택(최후)
    for c in cols:
        if c is None:
            continue
        if c in ("종목명", "티커", "종목코드", "종목명(약칭)"):
            continue
        return c
    return None


def fetch_krx_investor_flow(asof: dt.date, market: str = "KOSPI", log=None) -> Optional[dict]:
    """
    KRX 투자자별 거래대금/순매수(개인/외국인/기관 등) 조회 (전영업일 기준).

    반환:
    {
      "date": "YYYY-MM-DD",
      "market": "KOSPI",
      "net_buy_krw": {...},
      "net_buy_1e8krw": {...}  # 억원 단위
    }
    """
    try:
        from pykrx import stock
    except Exception as e:
        if log:
            log.warning(f"pykrx import failed: {e}")
        return None

    qd = prev_business_day(asof)
    ymd = qd.strftime("%Y%m%d")

    try:
        df = stock.get_market_trading_value_by_investor(ymd, ymd, market)
        if df is None or df.empty:
            return None

        # index=투자자구분, columns=[매도, 매수, 순매수] 형태가 일반적
        if "순매수" not in df.columns:
            # 혹시 다르면 가장 그럴듯한 컬럼 선택
            col = _pick_netbuy_col(df)
        else:
            col = "순매수"

        if col is None:
            return None

        net = df[col].to_dict()  # 원 단위(대개 거래대금)
        net_1e8 = {k: int(round(float(v) / 1e8)) for k, v in net.items()}

        return {
            "date": qd.isoformat(),
            "market": market,
            "net_buy_krw": {k: int(float(v)) for k, v in net.items()},
            "net_buy_1e8krw": net_1e8,
            "unit_note": "net_buy_krw is raw from KRX/pykrx (usually trading value). net_buy_1e8krw is approx in 억원(1e8 KRW).",
        }

    except Exception as e:
        if log:
            log.warning(f"KRX investor flow fetch failed ({market}, {ymd}): {e}")
        return None


def fetch_krx_top_netbuy_tickers(
    asof: dt.date,
    market: str = "KOSPI",
    investor: str = "외국인",
    top_k: int = 5,
    include_price: bool = True,
    log=None,
) -> list[dict]:
    """
    '수급 특징주 TOP'용: 전영업일 기준, 특정 투자자(investor)의 종목별 순매수 TOP_k

    NOTE:
    - pykrx 함수/버전에 따라 종목별 순매수가 '금액' 또는 '수량'일 수 있음.
    - 가능한 경우 컬럼명으로 '금액' 계열을 우선 선택하고, unit을 같이 표기함.
    """
    try:
        from pykrx import stock
    except Exception as e:
        if log:
            log.warning(f"pykrx import failed: {e}")
        return []

    qd = prev_business_day(asof)
    ymd = qd.strftime("%Y%m%d")

    df = None
    # pykrx 버전별 시그니처 fallback
    try:
        df = stock.get_market_net_purchases_of_equities_by_ticker(ymd, ymd, market, investor)
    except Exception:
        try:
            df = stock.get_market_net_purchases_of_equities_by_ticker(ymd, ymd, investor, market)
        except Exception:
            try:
                df = stock.get_market_net_purchases_of_equities_by_ticker(ymd, ymd, market=market, investor=investor)
            except Exception as e:
                if log:
                    log.warning(f"KRX top netbuy tickers fetch failed ({market},{investor},{ymd}): {e}")
                return []

    if df is None or df.empty:
        return []

    # 종목코드가 index인 형태가 많음
    work = df.copy()

    # netbuy 컬럼 선정
    net_col = _pick_netbuy_col(work)
    if net_col is None:
        return []

    # 단위 추정(컬럼명 기반)
    colname = str(net_col)
    is_value_like = any(k in colname for k in ["금액", "거래대금", "대금", "VALUE", "AMT"])

    # 정렬
    work[net_col] = work[net_col].apply(lambda x: float(x) if x is not None else 0.0)
    work = work.sort_values(net_col, ascending=False).head(int(top_k))

    out: list[dict] = []
    for idx, row in work.iterrows():
        ticker = str(idx)
        # 종목명
        try:
            name = stock.get_market_ticker_name(ticker)
        except Exception:
            name = ""

        item: dict[str, Any] = {
            "date": qd.isoformat(),
            "market": market,
            "investor": investor,
            "ticker": ticker,
            "name": name,
        }

        net_raw = float(row[net_col])

        if is_value_like:
            item["net_buy_krw"] = int(net_raw)
            item["net_buy_1e8krw"] = int(round(net_raw / 1e8))
            item["unit"] = "krw"
        else:
            item["net_buy_shares"] = int(round(net_raw))
            item["unit"] = "shares"

        # 가격/등락률(가능할 때만)
        if include_price:
            try:
                # 최근 2영업일만 요청 (대개 가능)
                d1 = qd.strftime("%Y%m%d")
                d0 = prev_business_day(qd).strftime("%Y%m%d")
                ohl = stock.get_market_ohlcv_by_date(d0, d1, ticker)
                if ohl is not None and not ohl.empty:
                    ohl2 = ohl.sort_index()
                    last = ohl2.iloc[-1]
                    item["close"] = int(last.get("종가", last.iloc[0]))
                    if "등락률" in ohl2.columns:
                        item["ret1d_pct"] = float(last["등락률"])
                    elif len(ohl2) >= 2 and "종가" in ohl2.columns:
                        prev_close = float(ohl2.iloc[-2]["종가"])
                        cur_close = float(last["종가"])
                        item["ret1d_pct"] = (cur_close / prev_close - 1.0) * 100.0
            except Exception:
                pass

        out.append(item)

    return out
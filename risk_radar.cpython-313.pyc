
import yfinance as yf
import pandas as pd


def fetch_history(ticker: str, history_days: int = 90) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period=f"{history_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=False,
    )
    if df is None or df.empty:
        raise ValueError("empty history")

    # MultiIndex 방어 (가끔 ("Close", ticker) 형태로 옴)
    if isinstance(df.columns, pd.MultiIndex):
        if ("Close", ticker) in df.columns:
            close = df[("Close", ticker)]
        else:
            close_cols = [c for c in df.columns if c[0] == "Close"]
            if not close_cols:
                raise ValueError("Close column not found in MultiIndex")
            close = df[close_cols[0]]
        out = pd.DataFrame({"date": df.index, "close": close})
    else:
        if "Close" in df.columns:
            close = df["Close"]
        elif "Adj Close" in df.columns:
            close = df["Adj Close"]
        else:
            raise ValueError("Close column not found")
        out = pd.DataFrame({"date": df.index, "close": close})

    out["date"] = pd.to_datetime(out["date"])
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna().sort_values("date")
    if out.empty:
        raise ValueError("no valid close after cleaning")
    return out[["date", "close"]]


def fetch_last_update(ticker: str, interval: str = "5m") -> dict:
    """
    가능한 경우 intraday(예: 5분봉)로 '마지막 갱신 시각'을 구해준다.
    실패하면 빈 dict 반환.
    """
    try:
        df = yf.download(
            ticker,
            period="2d",
            interval=interval,
            auto_adjust=True,
            progress=False,
            group_by="column",
            threads=False,
        )
    except Exception:
        return {}

    if df is None or df.empty:
        return {}

    ts = pd.Timestamp(df.index[-1])
    # tz 없는 경우가 있어 UTC로 가정(보수적)
    if ts.tzinfo is None:
        ts_utc = ts.tz_localize("UTC")
    else:
        ts_utc = ts.tz_convert("UTC")

    ts_kst = ts_utc.tz_convert("Asia/Seoul")

    return {
        "ref_interval": interval,
        "ref_ts_utc": ts_utc.isoformat(),
        "ref_ts_kst": ts_kst.isoformat(),
    }

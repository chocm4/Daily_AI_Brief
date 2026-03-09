import pandas as pd


def compute_snapshot(
    name: str,
    ticker: str,
    hist: pd.DataFrame,
    z_window: int = 20,
    kind: str = "price",
) -> dict:
    """Compute a 1D move snapshot.

    kind:
      - "price" (default): treat close as a price/index level and compute 1D % return.
      - "yield": treat close as a yield level and compute 1D change in bp.
    """
    if hist is None or getattr(hist, "empty", True):
        raise ValueError("empty history")

    h = hist.copy()

    if "date" not in h.columns:
        h = h.reset_index().rename(columns={"index": "date"})
    if "close" not in h.columns:
        raise ValueError("missing close column")

    h["date"] = pd.to_datetime(h["date"], errors="coerce")
    h["close"] = pd.to_numeric(h["close"], errors="coerce")
    h = h.dropna(subset=["date", "close"]).sort_values("date")
    if h.empty:
        raise ValueError("no valid rows after cleaning")

    close = h["close"].reset_index(drop=True)

    kind = (kind or "price").lower()
    if kind not in {"price", "yield"}:
        kind = "price"

    level = float(close.iloc[-1])

    if kind == "yield":
        dclose = close.diff()
        chg1d = float(dclose.iloc[-1]) if pd.notna(dclose.iloc[-1]) else 0.0
        chg1d_bp = chg1d * 100.0

        d = dclose.dropna()
        z = 0.0
        if len(d) >= z_window:
            mu = float(d.tail(z_window).mean())
            sd = float(d.tail(z_window).std(ddof=0))
            z = float((chg1d - mu) / sd) if sd > 0 else 0.0

    else:
        ret = close.pct_change()
        ret1d = float(ret.iloc[-1]) if pd.notna(ret.iloc[-1]) else 0.0

        r = ret.dropna()
        z = 0.0
        if len(r) >= z_window:
            mu = float(r.tail(z_window).mean())
            sd = float(r.tail(z_window).std(ddof=0))
            z = float((ret1d - mu) / sd) if sd > 0 else 0.0

    last_date = str(pd.to_datetime(h["date"].iloc[-1]).date())

    base = {
        "name": name,
        "ticker": ticker,
        "kind": kind,
        "date": last_date,
        "level": round(level, 4),
    }

    if kind == "yield":
        base.update(
            {
                "chg1d": round(chg1d, 4),
                "chg1d_bp": round(chg1d_bp, 1),
                "z20_chg": round(z, 3),
            }
        )
    else:
        base.update(
            {
                "ret1d_pct": round(ret1d * 100, 3),
                "z20_ret": round(z, 3),
            }
        )

    return base
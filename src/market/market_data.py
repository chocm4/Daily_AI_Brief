
from .yfinance_source import fetch_history, fetch_last_update
from .indicators import compute_snapshot
from datetime import datetime
from zoneinfo import ZoneInfo


def fetch_market_snapshot(cfg_market: dict, log=None) -> list[dict] | None:
    source = cfg_market.get("source", "yfinance")
    assets = cfg_market.get("assets", []) or []
    if not assets:
        return None

    history_days = int(cfg_market.get("history_days", 90))
    intraday_interval = cfg_market.get("intraday_interval", "5m")

    if source != "yfinance":
        if log:
            log.warning(f"Unknown market source '{source}'. Set market.enabled=false or implement your source.")
        return None

    run_ts_kst = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")

    snapshots = []
    for a in assets:
        name = a["name"]
        ticker = a["ticker"]
        try:
            hist = fetch_history(ticker, history_days=history_days)
            kind = a.get("kind", "price")
            snap = compute_snapshot(name=name, ticker=ticker, hist=hist, kind=kind)

            # 실행 시각(리포트 생성 시각)
            snap["asof_run_kst"] = run_ts_kst

            # 가능하면 intraday로 최종 갱신 시각
            try:
                snap.update(fetch_last_update(ticker, interval=intraday_interval))
            except Exception:
                pass

            snapshots.append(snap)

        except Exception as e:
            if log:
                log.warning(f"Market fetch failed: {name} ({ticker}): {e}")
            continue

    if log:
        log.info(f"Market snapshot assets: {len(snapshots)}")
    return snapshots

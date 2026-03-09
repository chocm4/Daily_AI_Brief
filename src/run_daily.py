import argparse
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from .utils.config import load_config
from .utils.logging import get_logger
from .utils.io import save_json, save_csv

from .ingest.rss import fetch_all_rss
from .ingest.normalize import normalize_entries
from .nlp.dedupe import dedupe_items
from .nlp.semantic_cluster import semantic_cluster
from .nlp.tagger import tag_and_score
from .nlp.filtering import filter_market_news, filter_brief_news
from .nlp.ranker import select_top_news

from .market.market_data import fetch_market_snapshot
from .market.sectors_krx import fetch_krx_sector_snapshot
from .market.flows_krx import fetch_krx_investor_flow, fetch_krx_top_netbuy_tickers

from .fact_pack import build_fact_pack
from .llm.writer import generate_report
from .render.md import render_markdown
from .render.excel import render_excel
from .render.story import render_story
from .notify.telegram import send_daily_briefing

KST = ZoneInfo("Asia/Seoul")


def parse_args():
    p = argparse.ArgumentParser(description="Daily Briefing AI")
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--date", default=None, help="As-of date (YYYY-MM-DD). Default: KST today.")
    p.add_argument("--skip-telegram", action="store_true", help="Skip telegram notification.")
    return p.parse_args()


def _resolve_asof(date_str: str | None) -> dt.date:
    if date_str:
        return dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    return dt.datetime.now(tz=KST).date()


def _hhmm(t: dt.datetime) -> str:
    return f"{t.hour:02d}{t.minute:02d}"


def _in_window(now_kst: dt.datetime, start_hhmm: str, end_hhmm: str) -> bool:
    cur = _hhmm(now_kst)
    if start_hhmm <= end_hhmm:
        return (start_hhmm <= cur) and (cur < end_hhmm)
    return (cur >= start_hhmm) or (cur < end_hhmm)


def _build_rally_decomp(krx_flows: dict) -> dict:
    out = {}
    for mkt in ["KOSPI", "KOSDAQ"]:
        panel = (krx_flows or {}).get(mkt) or {}
        net_buy = panel.get("net_buy_1e8krw") or {}
        if not net_buy:
            continue

        foreign = net_buy.get("외국인")
        retail = net_buy.get("개인")
        institution = net_buy.get("기관합계")

        dominant = None
        if foreign is not None and retail is not None:
            dominant = "외국인" if abs(foreign) >= abs(retail) else "개인"

        out[mkt] = {
            "date": panel.get("date"),
            "foreign_1e8krw": foreign,
            "retail_1e8krw": retail,
            "institution_1e8krw": institution,
            "dominant_actor_hint": dominant,
            "note": "dominant_actor_hint는 외국인 vs 개인 순매수 절대값 비교 기반의 얇은 힌트",
        }
    return out


def _detect_run_mode(now_kst: dt.datetime, cfg: dict) -> tuple[str, dict]:
    story_cfg = cfg.get("story", {}) or {}

    open_hhmm = story_cfg.get("kst_open_hhmm", "09:00")
    close_hhmm = story_cfg.get("kst_close_hhmm", "15:30")
    oh, om = [int(x) for x in open_hhmm.split(":")]
    ch, cm = [int(x) for x in close_hhmm.split(":")]

    ny_tz = ZoneInfo(story_cfg.get("us_tz", "America/New_York"))
    us_open_hhmm = story_cfg.get("us_open_hhmm", "09:30")
    us_close_hhmm = story_cfg.get("us_close_hhmm", "16:00")
    uoh, uom = [int(x) for x in us_open_hhmm.split(":")]
    uch, ucm = [int(x) for x in us_close_hhmm.split(":")]

    now_ny = now_kst.astimezone(ny_tz)
    ny_date = now_ny.date()

    kr_open = dt.datetime(now_kst.year, now_kst.month, now_kst.day, oh, om, tzinfo=KST)
    kr_close = dt.datetime(now_kst.year, now_kst.month, now_kst.day, ch, cm, tzinfo=KST)

    us_open_ny = dt.datetime(ny_date.year, ny_date.month, ny_date.day, uoh, uom, tzinfo=ny_tz)
    us_close_ny = dt.datetime(ny_date.year, ny_date.month, ny_date.day, uch, ucm, tzinfo=ny_tz)
    us_open_kst = us_open_ny.astimezone(KST)
    us_close_kst = us_close_ny.astimezone(KST)

    session_clock = {
        "now_kst": now_kst.isoformat(timespec="minutes"),
        "now_ny": now_ny.isoformat(timespec="minutes"),
        "kr_open_kst": kr_open.isoformat(timespec="minutes"),
        "kr_close_kst": kr_close.isoformat(timespec="minutes"),
        "us_open_kst": us_open_kst.isoformat(timespec="minutes"),
        "us_close_kst": us_close_kst.isoformat(timespec="minutes"),
    }

    wd = now_kst.weekday()
    if wd >= 5:
        return "WEEKEND", session_clock

    if us_open_kst <= now_kst < us_close_kst:
        return "US_INTRADAY", session_clock
    if kr_open <= now_kst < kr_close:
        return "KR_INTRADAY", session_clock
    if kr_close <= now_kst < us_open_kst:
        return "KR_AFTERCLOSE_US_PREOPEN", session_clock
    return "US_AFTERCLOSE_KR_PREOPEN", session_clock


def main():
    args = parse_args()
    cfg = load_config(args.config)
    log = get_logger("daily_briefing_ai", cfg["app"]["log_level"])

    asof = _resolve_asof(args.date)
    out_root = Path(cfg["app"]["output_dir"]) / asof.isoformat()
    out_root.mkdir(parents=True, exist_ok=True)

    log.info(f"Run date: {asof} | output: {out_root}")

    # 1) RSS fetch
    raw_entries = fetch_all_rss(cfg["rss"]["feeds"], cfg["rss"]["max_items_per_feed"], log)
    items = normalize_entries(raw_entries, cfg, log)

    # 1b) archive raw normalized items
    save_json(out_root / "items_raw.json", items, log)

    # 1c) semantic clustering
    items = semantic_cluster(items, cfg, log)
    save_csv(out_root / "items_clustered.csv", items, log)

    # 2) dedupe
    items = dedupe_items(items, similarity=cfg["rss"]["dedupe_similarity"], log=log)

    # 3) tag + score
    items = tag_and_score(items, cfg, log)

    # 3a) keep copy before market-only filter
    items_all = list(items)

    # 3b) build brief candidates
    brief_candidates = filter_brief_news(items_all, cfg, log)
    brief_top_n = int((cfg.get("rss", {}) or {}).get("brief_top_n", 24))
    brief_quota = (cfg.get("rss", {}) or {}).get("brief_region_quota")

    try:
        brief_news = select_top_news(brief_candidates, top_n=brief_top_n, quotas=brief_quota, log=log)
    except TypeError:
        brief_news = select_top_news(brief_candidates, top_n=brief_top_n, region_quota=brief_quota, log=log)

    save_csv(out_root / "brief.csv", brief_news, log)

    # 4) market filter
    mf_enabled = bool((cfg.get("nlp", {}) or {}).get("market_filter", {}).get("enabled", True))
    if mf_enabled:
        items = filter_market_news(items, cfg, log)

    # 5) select top news
    region_quota = (cfg.get("rss", {}) or {}).get("region_quota")
    top_n = int(cfg["rss"]["top_n"])

    try:
        top_news = select_top_news(items, top_n=top_n, quotas=region_quota, log=log)
    except TypeError:
        top_news = select_top_news(items, top_n=top_n, region_quota=region_quota, log=log)

    save_csv(out_root / "news.csv", top_news, log)

    # 6) market data
    market = None
    if cfg.get("market", {}).get("enabled", False):
        market = fetch_market_snapshot(cfg["market"], log)
        if market:
            save_csv(out_root / "market.csv", market, log)

    # 6-1) KRX sectors
    sectors_kr = None
    sectors_cfg = (cfg.get("market", {}) or {}).get("sectors_krx", {}) or {}
    if bool(sectors_cfg.get("enabled", False)):
        try:
            sectors_kr = fetch_krx_sector_snapshot(asof, sectors_cfg, log=log)
        except Exception as e:
            log.warning(f"KRX sector snapshot failed: {e}")
            sectors_kr = None

        if sectors_kr:
            save_csv(out_root / "sectors_kr.csv", sectors_kr, log)

    # 7) fact pack
    fact_pack = build_fact_pack(asof, top_news, market, cfg)
    now_kst = dt.datetime.now(tz=KST)
    fact_pack["generated_at_kst"] = now_kst.isoformat(timespec="minutes")

    run_mode, session_clock = _detect_run_mode(now_kst, cfg)
    fact_pack["run_mode"] = run_mode
    fact_pack["session_clock"] = session_clock

    # 7-1) KRX flows
    flows_cfg = (cfg.get("market", {}) or {}).get("flows_krx", {}) or {}
    flows_enabled = bool(flows_cfg.get("enabled", True))
    flow_window = (flows_cfg.get("window", {}) or {})
    start_hhmm = str(flow_window.get("start_hhmm", "1830"))
    end_hhmm = str(flow_window.get("end_hhmm", "0900"))

    flows_krx = None
    if flows_enabled and _in_window(now_kst, start_hhmm, end_hhmm):
        try:
            flows_krx = {
                "KOSPI": fetch_krx_investor_flow(asof, market="KOSPI", log=log),
                "KOSDAQ": fetch_krx_investor_flow(asof, market="KOSDAQ", log=log),
            }
            fact_pack["krx_flows"] = flows_krx

            tops_cfg = (flows_cfg.get("tops") or {})
            top_k = int(tops_cfg.get("top_k", 5))
            include_price = bool(tops_cfg.get("include_price", True))
            investors = tops_cfg.get("investors") or ["외국인", "개인"]

            krx_flow_tops = {}
            for mkt in ["KOSPI", "KOSDAQ"]:
                krx_flow_tops[mkt] = {}
                for inv in investors:
                    krx_flow_tops[mkt][inv] = fetch_krx_top_netbuy_tickers(
                        asof=asof,
                        market=mkt,
                        investor=str(inv),
                        top_k=top_k,
                        include_price=include_price,
                        log=log,
                    )

            fact_pack["krx_flow_tops"] = krx_flow_tops
            fact_pack["rally_decomp"] = _build_rally_decomp(flows_krx)

        except Exception as e:
            log.warning(f"KRX flows fetch failed: {e}")
    else:
        if flows_enabled:
            log.info(
                f"Skip KRX flows: outside window ({start_hhmm}~{end_hhmm}), "
                f"now={now_kst.isoformat(timespec='minutes')}"
            )

    # 7-2) attach brief
    fact_pack["brief_kr"] = [x for x in (brief_news or []) if x.get("region") == "KR"]
    fact_pack["brief_global"] = [x for x in (brief_news or []) if x.get("region") == "GLOBAL"]

    if sectors_kr:
        fact_pack["kr_sectors"] = sectors_kr

    save_json(out_root / "fact_pack.json", fact_pack, log)

    # 8) LLM report
    report = generate_report(fact_pack, cfg, log)
    rep = report.model_dump() if hasattr(report, "model_dump") else report
    save_json(out_root / "report.json", rep, log)

    # 9) render markdown / excel
    if cfg.get("render", {}).get("markdown", True):
        md = render_markdown(report, fact_pack, cfg)
        (out_root / "report.md").write_text(md, encoding="utf-8")
        log.info("Saved report.md")

    if cfg.get("render", {}).get("excel", False):
        render_excel(out_root / "report.xlsx", report, fact_pack, cfg)
        log.info("Saved report.xlsx")

    # 10) narrative story markdown
    if cfg.get("render", {}).get("story", True):
        try:
            story_md = render_story(rep, fact_pack, cfg)
            (out_root / "report_story.md").write_text(story_md, encoding="utf-8")
            log.info("Saved report_story.md")
        except Exception as e:
            log.warning(f"Story render failed: {e}")

    # 11) telegram
    if not args.skip_telegram:
        try:
            send_daily_briefing(
                out_root=out_root,
                asof=asof,
                run_mode=run_mode,
                generated_at_kst=fact_pack.get("generated_at_kst", ""),
                cfg=cfg,
                log=log,
            )
        except Exception as e:
            fail_on_error = bool((cfg.get("telegram") or {}).get("fail_on_error", True))
            log.error(f"Telegram send failed: {e}")
            if fail_on_error:
                raise

    log.info("Daily briefing pipeline finished.")


if __name__ == "__main__":
    main()

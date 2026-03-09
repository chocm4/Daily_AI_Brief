
def _fmt_kst(ts_iso: str) -> str:
    try:
        s = ts_iso.replace("T", " ")
        # "YYYY-mm-dd HH:MM"
        return s[:16] + " KST"
    except Exception:
        return ts_iso


def render_markdown(report, fact_pack: dict, cfg: dict) -> str:
    lines = []
    lines.append(f"# Daily Briefing (as of {report.asof})\n")
    lines.append(f"**Headline:** {report.headline}\n")

    market = fact_pack.get("market", []) or []
    if market:
        lines.append("## 시장 데이터(기준일/시각)")
        lines.append("- ret1d_pct는 **일봉 종가 기준 1일 변화율**")
        lines.append("- 마지막 갱신은 yfinance **intraday(예: 5분봉) 마지막 시각**")
        for m in market:
            daily_date = m.get("date", "N/A")
            last_upd = _fmt_kst(m["ref_ts_kst"]) + f" ({m.get('ref_interval','')})" if m.get("ref_ts_kst") else "N/A"
            lines.append(
                f"- **{m.get('name','')}** {m.get('ret1d_pct',0):+.2f}% | "
                f"level {m.get('level','')} | Daily: {daily_date} 종가 | 마지막 갱신: {last_upd}"
            )
        run_ts = market[0].get("asof_run_kst", "")
        if run_ts:
            lines.append(f"- 리포트 생성시각: {_fmt_kst(run_ts)}")
        lines.append("")

    lines.append("## 전일 국내장")
    for b in report.kr_bullets:
        lines.append(f"- {b}")
    lines.append("")

    lines.append("## 야간 해외장")
    for b in report.overnight_bullets:
        lines.append(f"- {b}")
    lines.append("")

    if report.price_action:
        lines.append("## 가격이 말해준 것")
        for m in report.price_action:
            ev = f" _(evidence: {m.evidence})_" if getattr(m, "evidence", None) else ""
            lines.append(f"- **{m.asset}** {m.move}: {m.comment}{ev}")
        lines.append("")

    if report.top_drivers:
        lines.append("## 뉴스 드라이버(근거 포함)")
        for d in report.top_drivers:
            src = ", ".join(d.sources)
            lines.append(f"- {d.title} — {d.why_it_matters}  [{src}]")
        lines.append("")

    if report.risk_radar:
        lines.append("## 리스크 레이더(규칙 기반)")
        for r in report.risk_radar:
            lines.append(f"- **{r.level}** {r.name}: {r.trigger}")
        lines.append("")

    if report.tomorrow_watch:
        lines.append("## 오늘/내일 체크리스트")
        for x in report.tomorrow_watch:
            lines.append(f"- {x}")
        lines.append("")

    lines.append("---")
    lines.append(report.disclaimer)
    lines.append("")
    lines.append("### Sources (RSS)")
    for n in fact_pack.get("news_kr", []):
        lines.append(f"- {n['id']}: {n['title']} ({n.get('source','')}) — {n.get('url','')}")
    for n in fact_pack.get("news_global", []):
        lines.append(f"- {n['id']}: {n['title']} ({n.get('source','')}) — {n.get('url','')}")
    return "\n".join(lines)

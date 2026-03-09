from pathlib import Path
from openpyxl import Workbook
from openpyxl.utils import get_column_letter


def _auto_width(ws):
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        max_len = 0
        for cell in ws[letter]:
            v = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(v))
        ws.column_dimensions[letter].width = min(max_len + 2, 60)


def render_excel(path: Path, report, fact_pack: dict, cfg: dict):
    wb = Workbook()

    ws = wb.active
    ws.title = "Briefing"

    r = 1
    ws.cell(r, 1, f"Daily Briefing (as of {report.asof})"); r += 2
    ws.cell(r, 1, "Headline"); ws.cell(r, 2, report.headline); r += 2

    ws.cell(r, 1, "오늘의 5줄"); r += 1
    for b in report.today_5lines:
        ws.cell(r, 2, b); r += 1
    r += 1

    ws.cell(r, 1, "가격이 말해준 것"); r += 1
    ws.cell(r, 1, "Asset"); ws.cell(r, 2, "Move"); ws.cell(r, 3, "Comment"); ws.cell(r, 4, "Evidence"); r += 1
    for m in report.price_action:
        ws.cell(r, 1, m.asset)
        ws.cell(r, 2, m.move)
        ws.cell(r, 3, m.comment)
        ws.cell(r, 4, getattr(m, "evidence", "") or "")
        r += 1
    r += 1

    ws.cell(r, 1, "뉴스 드라이버"); r += 1
    ws.cell(r, 1, "Title"); ws.cell(r, 2, "Why"); ws.cell(r, 3, "Sources"); r += 1
    for d in report.top_drivers:
        ws.cell(r, 1, d.title)
        ws.cell(r, 2, d.why_it_matters)
        ws.cell(r, 3, ", ".join(d.sources))
        r += 1
    r += 1

    ws.cell(r, 1, "리스크 레이더"); r += 1
    ws.cell(r, 1, "Level"); ws.cell(r, 2, "Name"); ws.cell(r, 3, "Trigger"); ws.cell(r, 4, "Sources"); r += 1
    for rr in report.risk_radar:
        ws.cell(r, 1, rr.level)
        ws.cell(r, 2, rr.name)
        ws.cell(r, 3, rr.trigger)
        ws.cell(r, 4, ", ".join(rr.sources) if rr.sources else "")
        r += 1
    r += 1

    ws.cell(r, 1, "내일 체크리스트"); r += 1
    for x in report.tomorrow_watch:
        ws.cell(r, 2, x); r += 1
    r += 1

    ws.cell(r, 1, "Disclaimer"); ws.cell(r, 2, report.disclaimer)

    _auto_width(ws)

    ws2 = wb.create_sheet("News")
    headers = ["id", "published", "source", "score", "tags", "title", "url"]
    ws2.append(headers)
    for n in fact_pack.get("news", []):
        ws2.append([
            n.get("id",""),
            n.get("published",""),
            n.get("source",""),
            n.get("score",0),
            ", ".join(n.get("tags",[])),
            n.get("title",""),
            n.get("url",""),
        ])
    _auto_width(ws2)

    if fact_pack.get("market"):
        ws3 = wb.create_sheet("Market")
        mheaders = list(fact_pack["market"][0].keys())
        ws3.append(mheaders)
        for row in fact_pack["market"]:
            ws3.append([row.get(k,"") for k in mheaders])
        _auto_width(ws3)

    wb.save(path)

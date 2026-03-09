# Daily Briefing AI (RSS-only MVP)

**Goal:** Generate a daily market briefing draft using **RSS headlines** + your own market data, with a clean-room workflow.

- No web scraping of full articles by default
- Stores only RSS metadata (title/time/link) unless you explicitly enable snippets
- Uses the OpenAI Responses API + Structured Outputs (Pydantic) for reliable JSON output

## 1) Quick start

### (A) Create venv & install
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### (B) Set API key (OpenAI)
```bash
# Windows PowerShell
setx OPENAI_API_KEY "sk-..."
# macOS/Linux
export OPENAI_API_KEY="sk-..."
```

### (C) Edit config
Open `config.yaml` and replace RSS feed URLs and market tickers.

### (D) Run
```bash
python -m src.run_daily
# or a specific date:
python -m src.run_daily --date 2026-01-23
```

Outputs go to `./outputs/YYYY-MM-DD/`.

## 2) Outputs
- `report.json`  (structured output)
- `report.md`    (rendered draft)
- `news.csv`     (normalized headlines, tags, scores)
- `market.csv`   (optional market snapshot)
- `fact_pack.json`
- `report.xlsx`  (optional; minimal clean template)

## 3) Clean-room / copyright notes
- Default mode does **not** fetch full article text.
- The model is instructed to **paraphrase**, avoid direct quotes, and attach source IDs.

## 4) Next steps
- Swap `market` module to your vendor (Quantiwise/FnGuide/FactSet/etc.)
- Add a calendar module (macro/earnings)
- Add a rules-based "risk radar" thresholds

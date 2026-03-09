name: AI Daily Briefing

on:
  schedule:
    # KST 07:00 = UTC 전날 22:00
    - cron: "0 22 * * 0-4"
    # KST 11:00 / 13:00 / 16:00
    - cron: "0 2 * * 1-5"
    - cron: "0 4 * * 1-5"
    - cron: "0 7 * * 1-5"
  workflow_dispatch:

concurrency:
  group: ai-daily-briefing
  cancel-in-progress: false

jobs:
  run-briefing:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    env:
      TZ: Asia/Seoul
      PYTHONUNBUFFERED: "1"
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Upgrade pip
        run: python -m pip install --upgrade pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run daily briefing
        run: python -m src.run_daily --config config.yaml

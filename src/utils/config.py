import yaml
from pathlib import Path


def load_config(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p.resolve()}")

    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("app", {})
    cfg.setdefault("rss", {})
    cfg.setdefault("news_scoring", {})
    cfg.setdefault("market", {})
    cfg.setdefault("llm", {})
    cfg.setdefault("render", {})
    cfg.setdefault("nlp", {})
    cfg.setdefault("story", {})
    cfg.setdefault("telegram", {})

    cfg["app"].setdefault("timezone", "Asia/Seoul")
    cfg["app"].setdefault("locale", "ko-KR")
    cfg["app"].setdefault("output_dir", "outputs")
    cfg["app"].setdefault("cache_dir", "data/cache")
    cfg["app"].setdefault("log_level", "INFO")

    cfg["market"].setdefault("enabled", False)

    cfg["render"].setdefault("markdown", True)
    cfg["render"].setdefault("excel", False)
    cfg["render"].setdefault("story", True)

    cfg["telegram"].setdefault("enabled", False)
    cfg["telegram"].setdefault("bot_token_env", "TELEGRAM_BOT_TOKEN")
    cfg["telegram"].setdefault("chat_id_env", "TELEGRAM_CHAT_ID")
    cfg["telegram"].setdefault("fail_on_error", True)
    cfg["telegram"].setdefault("send_message", True)
    cfg["telegram"].setdefault("message_file_priority", ["report_story.md", "report.md"])
    cfg["telegram"].setdefault("prepend_title", True)
    cfg["telegram"].setdefault("title_template", "AI Daily Briefing | {asof} | {run_mode}")
    cfg["telegram"].setdefault("max_message_length", 3500)
    cfg["telegram"].setdefault("disable_web_page_preview", True)
    cfg["telegram"].setdefault("send_story_file", False)
    cfg["telegram"].setdefault("send_markdown_file", False)
    cfg["telegram"].setdefault("send_excel_file", False)
    cfg["telegram"].setdefault("send_fact_pack_file", False)

    return cfg

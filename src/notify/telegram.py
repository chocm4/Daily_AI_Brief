import os
from pathlib import Path
import requests

TELEGRAM_API_BASE = "https://api.telegram.org"


def _get_credentials(cfg: dict) -> tuple[str, str]:
    tg_cfg = cfg.get("telegram", {}) or {}
    token_env = tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_id_env = tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID")

    token = os.environ.get(token_env, "").strip()
    chat_id = os.environ.get(chat_id_env, "").strip()

    if not token:
        raise EnvironmentError(f"{token_env} is not set.")
    if not chat_id:
        raise EnvironmentError(f"{chat_id_env} is not set.")

    return token, chat_id


def _read_first_existing(base_dir: Path, filenames: list[str]) -> tuple[str | None, Path | None]:
    for name in filenames:
        p = base_dir / name
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8"), p
    return None, None


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    resp = requests.post(url, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text}")
    return resp.json()


def _post_document(url: str, file_path: Path, data: dict, timeout: int = 60) -> dict:
    with open(file_path, "rb") as f:
        resp = requests.post(url, data=data, files={"document": f}, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"Telegram document API error {resp.status_code}: {resp.text}")
    return resp.json()


def _truncate_one_message(text: str, max_len: int, suffix: str = "\n\n...(truncated)") -> str:
    text = (text or "").strip()
    if not text:
        return ""

    if len(text) <= max_len:
        return text

    suffix = suffix or ""
    allowed = max_len - len(suffix)
    if allowed <= 0:
        return text[:max_len]

    cut = text[:allowed]

    # 문단/줄 경계에서 최대한 자연스럽게 자르기
    candidates = [
        cut.rfind("\n## "),
        cut.rfind("\n### "),
        cut.rfind("\n\n"),
        cut.rfind("\n"),
        cut.rfind(". "),
        cut.rfind("."),
    ]
    best = max(candidates)

    if best >= int(max_len * 0.6):
        cut = cut[:best].rstrip()

    return cut.rstrip() + suffix


def send_message(text: str, cfg: dict, log=None):
    token, chat_id = _get_credentials(cfg)
    tg_cfg = cfg.get("telegram", {}) or {}
    max_len = int(tg_cfg.get("max_message_length", 3500))
    disable_preview = bool(tg_cfg.get("disable_web_page_preview", True))
    single_message_only = bool(tg_cfg.get("single_message_only", True))
    truncate_suffix = str(tg_cfg.get("truncate_suffix", "\n\n...(truncated)"))

    final_text = (text or "").strip()
    if not final_text:
        if log:
            log.info("Telegram message skipped: empty text")
        return

    if single_message_only:
        final_text = _truncate_one_message(final_text, max_len=max_len, suffix=truncate_suffix)

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": final_text,
        "disable_web_page_preview": disable_preview,
    }
    _post_json(url, payload)

    if log:
        msg = f"Telegram message sent (single message, len={len(final_text)})"
        if len((text or "").strip()) > len(final_text):
            msg += " [truncated]"
        log.info(msg)


def send_document(file_path: Path, caption: str, cfg: dict, log=None):
    if not file_path.exists():
        if log:
            log.info(f"Telegram document skipped (not found): {file_path.name}")
        return

    token, chat_id = _get_credentials(cfg)
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendDocument"

    data = {
        "chat_id": chat_id,
        "caption": caption[:1024],
    }
    _post_document(url, file_path, data)

    if log:
        log.info(f"Telegram document sent: {file_path.name}")


def _build_title(asof: str, run_mode: str, generated_at_kst: str, cfg: dict) -> str:
    tg_cfg = cfg.get("telegram", {}) or {}
    title_template = tg_cfg.get("title_template", "AI Daily Briefing | {asof} | {run_mode}")
    title = title_template.format(asof=asof, run_mode=run_mode)
    if generated_at_kst:
        return f"{title}\n생성시각: {generated_at_kst}"
    return title


def send_daily_briefing(
    out_root: Path,
    asof,
    run_mode: str,
    generated_at_kst: str,
    cfg: dict,
    log=None,
):
    tg_cfg = cfg.get("telegram", {}) or {}
    if not bool(tg_cfg.get("enabled", False)):
        if log:
            log.info("Telegram disabled in config")
        return

    message_priority = tg_cfg.get("message_file_priority", ["report_story.md", "report.md"])
    message_text, used_file = _read_first_existing(out_root, message_priority)

    title = _build_title(str(asof), run_mode, generated_at_kst, cfg)

    if bool(tg_cfg.get("send_message", True)):
        if message_text is None:
            raise FileNotFoundError(
                f"No telegram message file found under {out_root} "
                f"(tried: {', '.join(message_priority)})"
            )

        full_text = message_text.strip()
        if bool(tg_cfg.get("prepend_title", True)):
            full_text = f"{title}\n\n{full_text}"

        send_message(full_text, cfg, log=log)

        if log and used_file is not None:
            log.info(f"Telegram message source: {used_file.name}")

    if bool(tg_cfg.get("send_story_file", False)):
        send_document(out_root / "report_story.md", title, cfg, log=log)

    if bool(tg_cfg.get("send_markdown_file", False)):
        send_document(out_root / "report.md", title, cfg, log=log)

    if bool(tg_cfg.get("send_excel_file", False)):
        send_document(out_root / "report.xlsx", title, cfg, log=log)

    if bool(tg_cfg.get("send_fact_pack_file", False)):
        send_document(out_root / "fact_pack.json", title, cfg, log=log)

import yaml
from pathlib import Path


def load_config(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p.resolve()}")
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("app", {})
    cfg.setdefault("rss", {})
    cfg.setdefault("news_scoring", {})
    cfg.setdefault("market", {"enabled": False})
    cfg.setdefault("llm", {"enabled": False})
    cfg.setdefault("render", {"markdown": True, "excel": False})
    return cfg

import json
from pathlib import Path
import pandas as pd


def save_json(path: Path, obj: dict, log=None):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    if log:
        log.info(f"Saved {path.name}")


def save_csv(path: Path, rows, log=None):
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    if log:
        log.info(f"Saved {path.name} ({len(df)} rows)")

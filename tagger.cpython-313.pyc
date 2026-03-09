import json
import os
import urllib.request
import urllib.error
from typing import List

import numpy as np
from sklearn.cluster import DBSCAN


def _openai_embed(texts: List[str], model: str) -> np.ndarray:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    url = "https://api.openai.com/v1/embeddings"
    payload = {
        "model": model,
        "input": texts,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Embeddings HTTPError {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Embeddings request failed: {e}")

    if "data" not in data:
        raise RuntimeError(f"Embeddings response missing 'data': {data}")

    arr = [x["embedding"] for x in data["data"]]
    return np.array(arr, dtype=np.float32)


def _build_cluster_text(item: dict, max_chars: int = 1200) -> str:
    parts = []

    for key in ["title", "description", "summary"]:
        v = item.get(key)
        if v:
            parts.append(str(v).strip())

    text = " | ".join(parts).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]

    return text


def _assign_single_clusters(items: list[dict]) -> list[dict]:
    for i, item in enumerate(items):
        item["cluster_id"] = f"SINGLE_{i}"
        item["cluster_size"] = 1
    return items


def semantic_cluster(items: list[dict], cfg: dict, log=None) -> list[dict]:
    scfg = ((cfg.get("nlp") or {}).get("semantic_cluster") or {})

    if not scfg.get("enabled", False):
        if log:
            log.info("Semantic cluster disabled.")
        return items

    if not items:
        if log:
            log.info("Semantic cluster skipped: no items.")
        return items

    model = scfg.get("embedding_model", "text-embedding-3-small")
    distance_threshold = float(scfg.get("distance_threshold", 0.18))
    min_text_chars = int(scfg.get("min_text_chars", 30))
    text_max_chars = int(scfg.get("text_max_chars", 1200))
    batch_size = int(scfg.get("batch_size", 64))

    texts: list[str] = []
    valid_idx: list[int] = []

    for i, item in enumerate(items):
        text = _build_cluster_text(item, max_chars=text_max_chars)
        if len(text) >= min_text_chars:
            texts.append(text)
            valid_idx.append(i)

    if not texts:
        if log:
            log.info("Semantic cluster skipped: no valid texts.")
        return _assign_single_clusters(items)

    try:
        embs = []
        for j in range(0, len(texts), batch_size):
            batch = texts[j:j + batch_size]
            embs.append(_openai_embed(batch, model=model))

        X = np.vstack(embs)

        clustering = DBSCAN(
            eps=distance_threshold,
            min_samples=1,
            metric="cosine",
        ).fit(X)

        labels = clustering.labels_.tolist()

        cluster_sizes = {}
        for label in labels:
            cluster_sizes[label] = cluster_sizes.get(label, 0) + 1

        for idx, label in zip(valid_idx, labels):
            items[idx]["cluster_id"] = f"C{label}"
            items[idx]["cluster_size"] = cluster_sizes[label]

        for i, item in enumerate(items):
            if "cluster_id" not in item:
                item["cluster_id"] = f"SINGLE_{i}"
                item["cluster_size"] = 1

        if log:
            log.info(
                f"Semantic cluster done: valid_texts={len(texts)}, "
                f"batch_size={batch_size}, clusters={len(set(labels))}"
            )

        return items

    except Exception as e:
        if log:
            log.warning(f"Semantic clustering skipped due to API/quota error: {e}")
        return _assign_single_clusters(items)

import os, json, re
import numpy as np
import datetime as dt
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

# -----------------------------
# Canonicalization for "exact duplicates"
# -----------------------------
_RE_PREFIX_BRACKET = re.compile(r"^\s*\[[^\]]+\]\s*")          # [1보], [속보] ...
_RE_PAREN_ANY      = re.compile(r"\([^)]*\)")                  # (종합) 등
_RE_MULTI_SPACE    = re.compile(r"\s+")
_RE_WIRE_TOKENS    = re.compile(
    r"(속보|단독|종합|해설|기획|인터뷰|포토|사진|영상|라이브|전문|재송|수정|추가|업데이트|특보|1보|2보|3보|4보|5보)",
    re.IGNORECASE
)

def _canon_text(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # remove leading [..] blocks repeatedly
    while True:
        ns = _RE_PREFIX_BRACKET.sub("", s)
        if ns == s:
            break
        s = ns.strip()

    # remove (...) blocks (often "(종합)" etc.)
    s = _RE_PAREN_ANY.sub(" ", s)

    # remove common wire tokens anywhere (keep meaning but reduce duplicates)
    s = _RE_WIRE_TOKENS.sub(" ", s)

    # normalize spacing/case
    s = s.lower()
    s = _RE_MULTI_SPACE.sub(" ", s).strip()
    return s

def _build_text(it: Dict[str, Any], max_chars: int = 1200) -> str:
    title = (it.get("title") or "").strip()
    desc = it.get("description")
    if isinstance(desc, float):  # NaN 방어
        desc = ""
    desc = (desc or "").strip()
    s = f"{title}\n{desc}".strip()
    if len(s) > max_chars:
        s = s[:max_chars]
    return s

def _cosine_dist_matrix(X: np.ndarray) -> np.ndarray:
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)
    S = Xn @ Xn.T
    return 1.0 - S

def _openai_embed(texts: List[str], model: str) -> np.ndarray:
    import urllib.request
    import urllib.error

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/embeddings"
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Embeddings HTTPError: {e.read().decode('utf-8')}")
    vecs = [d["embedding"] for d in data["data"]]
    return np.array(vecs, dtype=np.float32)

def semantic_cluster(items: List[Dict[str, Any]], cfg: dict, log=None) -> List[Dict[str, Any]]:
    scfg = (((cfg.get("nlp") or {}).get("semantic_cluster") or {}))
    if not bool(scfg.get("enabled", True)):
        return items

    model = scfg.get("embedding_model", "text-embedding-3-small")
    dist_th = float(scfg.get("distance_threshold", 0.22))
    min_chars = int(scfg.get("min_text_chars", 30))
    max_chars = int(scfg.get("text_max_chars", 1200))

    texts = []
    idxs = []
    for i, it in enumerate(items):
        t = _build_text(it, max_chars=max_chars)
        if len(t) >= min_chars:
            texts.append(t)
            idxs.append(i)

    if len(texts) < 2:
        for it in items:
            it["cluster_id"] = it.get("id") or None
            it["mentions_raw"] = 1
            it["mentions"] = 1
            it["dup_raw"] = 0
            it["dup_share"] = 0.0
        return items

    # embeddings
    embs = []
    bs = 128
    for j in range(0, len(texts), bs):
        embs.append(_openai_embed(texts[j:j+bs], model=model))
    X = np.vstack(embs)

    # clustering
    from sklearn.cluster import AgglomerativeClustering
    D = _cosine_dist_matrix(X)

    cl = AgglomerativeClustering(
        n_clusters=None,
        metric="precomputed",
        linkage="average",
        distance_threshold=dist_th
    )
    labels = cl.fit_predict(D)

    members = {}
    for k, lab in enumerate(labels):
        members.setdefault(int(lab), []).append(idxs[k])

    tzname = (cfg.get("app") or {}).get("timezone", "Asia/Seoul")
    _ = dt.datetime.now(tz=ZoneInfo(tzname))

    for lab, mids in members.items():
        # cluster id (stable-ish)
        tops = sorted(mids, key=lambda i: (items[i].get("published") or ""), reverse=True)[:3]
        key = "||".join([(items[i].get("title") or "")[:80] for i in tops])
        cid = "C" + str(abs(hash(key)) % (10**10))

        # ----- compute "unique mentions" after canonicalization -----
        sig_to_sources = {}
        sig_to_first_pub = {}
        pubs_all = []
        sources_all = []

        for i in mids:
            items[i]["cluster_id"] = cid

            title = _canon_text(str(items[i].get("title") or ""))
            desc_raw = items[i].get("description")
            if isinstance(desc_raw, float):  # NaN
                desc_raw = ""
            desc = _canon_text(str(desc_raw or ""))

            # signature: canonical title + first 240 chars of desc
            sig = (title + "|" + desc[:240]).strip("|")

            src = items[i].get("source")
            if src:
                sources_all.append(src)
            pub = items[i].get("published")
            if pub:
                pubs_all.append(pub)

            sig_to_sources.setdefault(sig, set())
            if src:
                sig_to_sources[sig].add(src)
            if pub and sig not in sig_to_first_pub:
                sig_to_first_pub[sig] = pub

        mentions_raw = len(mids)
        mentions_unique = len(sig_to_sources)
        dup_raw = max(0, mentions_raw - mentions_unique)
        dup_share = (dup_raw / mentions_raw) if mentions_raw else 0.0

        # unique sources across unique signatures
        uniq_sources = sorted(list({s for ss in sig_to_sources.values() for s in ss if s}))
        uniq_pubs = sorted(list(sig_to_first_pub.values()))

        # attach same aggregates to each member
        for i in mids:
            # raw + unique
            items[i]["mentions_raw"] = mentions_raw
            items[i]["mentions"] = mentions_unique          # ✅ 핵심: 완전중복은 mentions에 반영 안됨
            items[i]["dup_raw"] = dup_raw
            items[i]["dup_share"] = dup_share

            items[i]["mention_sources"] = uniq_sources
            items[i]["mention_published"] = uniq_pubs

    if log:
        log.info(f"Semantic clustering: items={len(items)} clustered={len(members)} dist_th={dist_th}")
    return items


def tag_clusters_llm(items: List[Dict[str, Any]], cfg: dict, log=None):
    # 기존 유지(필요하면 별도 개선 가능)
    tcfg = (((cfg.get("nlp") or {}).get("cluster_tagging") or {}))
    if not bool(tcfg.get("enabled", True)):
        return {}

    model = tcfg.get("model", "gpt-4o-mini")
    temperature = float(tcfg.get("temperature", 0.1))
    max_tokens = int(tcfg.get("max_output_tokens", 800))
    top_k = int(tcfg.get("top_k_titles", 10))

    clusters = {}
    for it in items:
        cid = it.get("cluster_id")
        if not cid:
            continue
        clusters.setdefault(cid, []).append(it)

    import urllib.request, urllib.error
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/chat/completions"
    out = {}

    for cid, its in clusters.items():
        its_sorted = sorted(its, key=lambda x: x.get("score", 0), reverse=True)[:top_k]
        titles = [x.get("title","") for x in its_sorted if x.get("title")]
        sources = sorted(list({x.get("source","") for x in its_sorted if x.get("source")}))
        mentions = its_sorted[0].get("mentions", len(its))

        prompt = f"""
너는 금융시장 모닝노트를 쓰는 애널리스트다.
아래는 같은 이슈로 묶인 뉴스 클러스터다.

- cluster_id: {cid}
- mentions(유니크 기사 수): {mentions}
- sources(고유 출처): {sources}

[제목 목록]
{chr(10).join("- " + t for t in titles)}

요구사항:
1) 이 클러스터를 설명하는 'theme'(한 문장, 한국어)
2) 자유 태그 2~5개(tags): 사건/자산/섹터/정책 키워드 중심. 미리 정해진 목록을 따르지 말 것.
3) market_moving: 금융시장 영향 가능성(0~3점, 정수). 3은 매우 큼.
4) why_it_matters: 왜 시장에 중요할 수 있는지 2~3문장.

출력은 JSON만:
{{"theme":"...","tags":["..",".."],"market_moving":0,"why_it_matters":"..."}}
""".strip()

        payload = json.dumps({
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role":"system","content":"Return ONLY valid JSON. No extra text."},
                {"role":"user","content": prompt}
            ]
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            obj = json.loads(content)
            out[cid] = obj
        except Exception as e:
            if log:
                log.warning(f"cluster tagging failed cid={cid}: {e}")
            continue

    return out

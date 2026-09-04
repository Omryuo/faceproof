"""On-disk cache of raw provider results.

Free search endpoints rate-limit hard, so a demo that re-runs the same query a
few times will get throttled. Cached entries record a real search that actually
happened, with its timestamp and query -- they are never a substitute for one.
Reuse is opt-in (`--use-cache`) and every cached record is stamped in the
evidence as such, so a cached run can never be passed off as a fresh one.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from .base import Candidate

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "out" / "search-cache"


def _key(provider: str, probe_sha: str, hint: str | None, probe_url: str | None) -> str:
    raw = f"{provider}|{probe_sha}|{hint or ''}|{probe_url or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def path_for(provider: str, probe_sha: str, hint, probe_url) -> Path:
    return CACHE_DIR / f"{provider}.{_key(provider, probe_sha, hint, probe_url)}.json"


def save(provider: str, probe_sha: str, hint, probe_url, candidates: list[Candidate]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = path_for(provider, probe_sha, hint, probe_url)
    p.write_text(
        json.dumps(
            {
                "provider": provider,
                "probe_sha256": probe_sha,
                "hint": hint,
                "probe_url": probe_url,
                "fetched_at": int(time.time()),
                "candidates": [asdict(c) for c in candidates],
            },
            indent=2,
        )
        + "\n"
    )
    return p


def load(provider: str, probe_sha: str, hint, probe_url, max_age_s: int = 7 * 86400):
    p = path_for(provider, probe_sha, hint, probe_url)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    if time.time() - data.get("fetched_at", 0) > max_age_s:
        return None
    cands = [Candidate(**c) for c in data["candidates"]]
    return cands, data["fetched_at"]

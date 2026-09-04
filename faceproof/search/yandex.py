"""Key-free reverse image search by scripting Yandex's image-view endpoint.

Best-effort: Yandex serves a captcha to datacentre IPs fairly aggressively, so
this provider is allowed to return nothing. It exists so the pipeline has a
no-API-key path.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlparse, parse_qs, unquote

import requests

from .base import Candidate, Probe, SearchError, TIMEOUT, USER_AGENT

VIEW = "https://yandex.com/images/search?rpt=imageview&format=json&request={req}&url={url}"
SIMPLE = "https://yandex.com/images/search?rpt=imageview&url={url}&cbir_page=similar"


class YandexReverse:
    name = "yandex"
    needs_public_url = True

    def __init__(self, max_results: int = 30):
        self.max_results = max_results

    def available(self) -> bool:
        return True

    def search(self, probe: Probe) -> list[Candidate]:
        if not probe.public_url:
            raise SearchError(f"{self.name} needs a publicly reachable probe URL")
        url = SIMPLE.format(url=quote(probe.public_url, safe=""))
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise SearchError(f"yandex request failed: {exc}") from exc
        if "showcaptcha" in r.url or "captcha" in r.text[:2000].lower():
            raise SearchError("yandex served a captcha (expected from cloud IPs)")
        if r.status_code != 200:
            raise SearchError(f"yandex HTTP {r.status_code}")
        return self._parse(r.text)

    def _parse(self, html: str) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        # Results are embedded as JSON in data-bem attributes.
        for blob in re.findall(r'data-bem=\'(\{.*?\})\'', html, re.S)[:400]:
            try:
                obj = json.loads(blob.replace("&quot;", '"'))
            except Exception:
                continue
            for item in _walk_items(obj):
                page = item.get("url") or item.get("page_url")
                if not page or page in seen:
                    continue
                seen.add(page)
                out.append(
                    Candidate(
                        page_url=page,
                        image_url=item.get("img_href") or item.get("origin", {}).get("url"),
                        title=_strip(item.get("title", "")),
                        snippet=_strip(item.get("description", "")),
                        provider=self.name,
                    )
                )
                if len(out) >= self.max_results:
                    return out
        return out


def _walk_items(obj, depth: int = 0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        if "img_href" in obj or ("url" in obj and "title" in obj):
            yield obj
        for v in obj.values():
            yield from _walk_items(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_items(v, depth + 1)


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()

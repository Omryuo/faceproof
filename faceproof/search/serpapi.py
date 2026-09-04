"""Reverse image search via SerpAPI.

SerpAPI fronts Google Lens / Yandex Images and returns structured 'visual
matches'. This is the highest-recall provider and the recommended one; it needs
SERPAPI_KEY and a publicly reachable probe URL.
"""

from __future__ import annotations

import os

import requests

from .base import Candidate, Probe, SearchError, TIMEOUT

ENDPOINT = "https://serpapi.com/search.json"


class SerpApiLens:
    name = "serpapi_lens"
    needs_public_url = True
    engine = "google_lens"

    def __init__(self, api_key: str | None = None, max_results: int = 40):
        self.api_key = api_key or os.getenv("SERPAPI_KEY") or os.getenv("SERPAPI_API_KEY")
        self.max_results = max_results

    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, probe: Probe) -> list[Candidate]:
        if not self.api_key:
            raise SearchError("SERPAPI_KEY is not set")
        if not probe.public_url:
            raise SearchError(
                f"{self.name} needs a publicly reachable probe URL "
                "(pass --probe-url, or --upload-probe to opt in to uploading it)"
            )
        params = {
            "engine": self.engine,
            "url": probe.public_url,
            "api_key": self.api_key,
        }
        try:
            r = requests.get(ENDPOINT, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise SearchError(f"serpapi request failed: {exc}") from exc
        if r.status_code != 200:
            raise SearchError(f"serpapi HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("error"):
            raise SearchError(f"serpapi error: {data['error']}")
        return self._parse(data)

    def _parse(self, data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        buckets = (
            data.get("visual_matches")
            or data.get("image_results")
            or data.get("inline_images")
            or []
        )
        for item in buckets[: self.max_results]:
            page = item.get("link") or item.get("source_url")
            if not page:
                continue
            out.append(
                Candidate(
                    page_url=page,
                    image_url=item.get("image") or item.get("thumbnail"),
                    title=item.get("title", "") or "",
                    snippet=item.get("source", "") or "",
                    provider=self.name,
                    extra={"position": item.get("position")},
                )
            )
        return out


class SerpApiYandex(SerpApiLens):
    """Yandex tends to surface different (often more social) results than Lens."""

    name = "serpapi_yandex"
    engine = "yandex_images"

    def search(self, probe: Probe) -> list[Candidate]:
        if not self.api_key:
            raise SearchError("SERPAPI_KEY is not set")
        if not probe.public_url:
            raise SearchError(f"{self.name} needs a publicly reachable probe URL")
        params = {
            "engine": "yandex_images",
            "url": probe.public_url,
            "api_key": self.api_key,
        }
        r = requests.get(ENDPOINT, params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            raise SearchError(f"serpapi HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("error"):
            raise SearchError(f"serpapi error: {data['error']}")
        out: list[Candidate] = []
        for item in (data.get("image_results") or [])[: self.max_results]:
            page = item.get("link")
            if not page:
                continue
            out.append(
                Candidate(
                    page_url=page,
                    image_url=(item.get("original_image") or {}).get("link")
                    or item.get("thumbnail"),
                    title=item.get("title", "") or "",
                    snippet=item.get("source", "") or "",
                    provider=self.name,
                )
            )
        return out

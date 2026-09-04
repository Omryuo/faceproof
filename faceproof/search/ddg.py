"""Key-free search via DuckDuckGo.

Two modes, both hint-assisted (they need a name/handle to query with):

  ddg_images -- DuckDuckGo image search; every hit is a real image URL plus the
                page it sits on, so face verification does the discriminating.
  ddg_web    -- DuckDuckGo web search restricted to social platforms.

Neither is reverse-image search. They are here so the pipeline is runnable with
zero API keys: the *search* is genuine and the identity decision is still made
by the face recogniser, never by the search engine's ranking.
"""

from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .base import CORE_SOCIAL, Candidate, Probe, SearchError, TIMEOUT, USER_AGENT


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/",
        }
    )
    return s


def _vqd(sess: requests.Session, query: str) -> str:
    r = sess.get("https://duckduckgo.com/", params={"q": query}, timeout=TIMEOUT)
    for pat in (r"vqd=\"([^\"]+)\"", r"vqd='([^']+)'", r"vqd=([0-9-]+)&"):
        m = re.search(pat, r.text)
        if m:
            return m.group(1)
    raise SearchError("could not obtain DuckDuckGo vqd token")


class DdgImages:
    name = "ddg_images"
    needs_public_url = False

    def __init__(self, max_results: int = 40):
        self.max_results = max_results

    def available(self) -> bool:
        return True

    def search(self, probe: Probe) -> list[Candidate]:
        if not probe.hint:
            raise SearchError(
                f"{self.name} is hint-assisted: pass --hint 'name or handle'. "
                "For true reverse-image search set SERPAPI_KEY."
            )
        sess = _session()
        vqd = _vqd(sess, probe.hint)
        r = sess.get(
            "https://duckduckgo.com/i.js",
            params={"l": "us-en", "o": "json", "q": probe.hint, "vqd": vqd, "f": "", "p": "1"},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise SearchError(f"ddg images HTTP {r.status_code}")
        try:
            results = r.json().get("results", [])
        except json.JSONDecodeError as exc:
            raise SearchError(f"ddg images returned non-JSON: {exc}") from exc

        out: list[Candidate] = []
        for item in results[: self.max_results]:
            page = item.get("url")
            img = item.get("image")
            if not page or not img:
                continue
            out.append(
                Candidate(
                    page_url=page,
                    image_url=img,
                    title=item.get("title", "") or "",
                    snippet=item.get("source", "") or "",
                    provider=self.name,
                )
            )
        return out


class DdgWeb:
    name = "ddg_web"
    needs_public_url = False

    def __init__(self, max_results: int = 30, social_only: bool = True):
        self.max_results = max_results
        self.social_only = social_only

    def available(self) -> bool:
        return True

    def search(self, probe: Probe) -> list[Candidate]:
        if not probe.hint:
            raise SearchError(f"{self.name} is hint-assisted: pass --hint 'name or handle'")
        query = probe.hint
        if self.social_only:
            sites = " OR ".join(f"site:{d}" for d in CORE_SOCIAL)
            query = f"{probe.hint} ({sites})"
        sess = _session()
        html = _fetch_with_retry(sess, query)
        return self._parse(html)

    def _parse(self, html: str) -> list[Candidate]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        out: list[Candidate] = []
        if not soup.select(".result"):
            return self._parse_lite(soup)
        for res in soup.select(".result")[: self.max_results * 2]:
            a = res.select_one("a.result__a")
            if not a or not a.get("href"):
                continue
            url = _unwrap(a["href"])
            if not url:
                continue
            snip = res.select_one(".result__snippet")
            out.append(
                Candidate(
                    page_url=url,
                    image_url=None,
                    title=a.get_text(" ", strip=True),
                    snippet=snip.get_text(" ", strip=True) if snip else "",
                    provider=self.name,
                )
            )
            if len(out) >= self.max_results:
                break
        return out

    def _parse_lite(self, soup) -> list[Candidate]:
        """The lite endpoint is a bare table of links."""
        out: list[Candidate] = []
        for a in soup.select("a.result-link, a[href]"):
            url = _unwrap(a.get("href", ""))
            if not url or "duckduckgo.com" in url:
                continue
            out.append(
                Candidate(
                    page_url=url,
                    image_url=None,
                    title=a.get_text(" ", strip=True),
                    provider=self.name,
                )
            )
            if len(out) >= self.max_results:
                break
        return out


# DuckDuckGo answers automated traffic with HTTP 202 + a challenge page. Backing
# off and falling through to the lite endpoint clears it most of the time.
ENDPOINTS = (
    ("https://html.duckduckgo.com/html/", ".result"),
    ("https://lite.duckduckgo.com/lite/", "tr"),
)


def _fetch_with_retry(sess: requests.Session, query: str, attempts: int = 3) -> str:
    last = ""
    for attempt in range(attempts):
        for url, _ in ENDPOINTS:
            try:
                r = sess.post(url, data={"q": query}, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last = f"request failed: {exc}"
                continue
            if r.status_code == 200 and "result" in r.text:
                return r.text
            last = f"HTTP {r.status_code} from {url}"
        time.sleep(1.5 * (attempt + 1) + random.random())
    raise SearchError(f"ddg web: {last} (rate limited; retry shortly or set SERPAPI_KEY)")


def _unwrap(href: str) -> str | None:
    """DuckDuckGo wraps outbound links as //duckduckgo.com/l/?uddg=<encoded>."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        return unquote(target) if target else None
    return href if href.startswith("http") else None

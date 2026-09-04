"""Key-free web search via Yahoo.

Yahoo is the most automation-tolerant of the free engines tested (Bing, Brave,
Ecosia, Mojeek and SearXNG instances all block or serve decoy results from a
plain client), which makes it the practical default when no SERPAPI_KEY is set.
Like the DuckDuckGo provider it is hint-assisted: it finds *pages*, and the face
recogniser -- not Yahoo's ranking -- decides whether they show the probe.
"""

from __future__ import annotations

import re
import time
from urllib.parse import unquote

import requests

from .base import CORE_SOCIAL, Candidate, Probe, SearchError, TIMEOUT, USER_AGENT

SEARCH = "https://search.yahoo.com/search"
# Yahoo wraps outbound links as .../RU=<urlencoded target>/RK=... or /RO=...
REDIRECT = re.compile(r"/RU=([^/]+)/R[KO]=")


class YahooWeb:
    name = "yahoo_web"
    needs_public_url = False

    def __init__(self, max_results: int = 30, social_only: bool = True):
        self.max_results = max_results
        self.social_only = social_only

    def available(self) -> bool:
        return True

    def search(self, probe: Probe) -> list[Candidate]:
        if not probe.hint:
            raise SearchError(
                f"{self.name} is hint-assisted: pass --hint 'name or handle'. "
                "For true reverse-image search set SERPAPI_KEY."
            )
        query = probe.hint
        if self.social_only:
            sites = " OR ".join(f"site:{d}" for d in CORE_SOCIAL)
            query = f"{probe.hint} ({sites})"

        sess = requests.Session()
        sess.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        try:
            sess.get("https://search.yahoo.com/", timeout=TIMEOUT)  # warm cookies
            time.sleep(1.0)
            r = sess.get(SEARCH, params={"p": query, "n": "30"}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise SearchError(f"yahoo request failed: {exc}") from exc
        if r.status_code != 200:
            raise SearchError(f"yahoo HTTP {r.status_code}")
        results = self._parse(r.text)
        if not results:
            raise SearchError("yahoo returned no parseable results")
        return results

    def _parse(self, html: str) -> list[Candidate]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        out: list[Candidate] = []
        seen: set[str] = set()
        for a in soup.select("h3 a, ol li a"):
            url = _unwrap(a.get("href", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            block = a.find_parent("li")
            snippet = ""
            if block:
                p = block.find("p")
                if p:
                    snippet = p.get_text(" ", strip=True)[:400]
            out.append(
                Candidate(
                    page_url=url,
                    image_url=None,
                    title=a.get_text(" ", strip=True),
                    snippet=snippet,
                    provider=self.name,
                )
            )
            if len(out) >= self.max_results:
                break
        return out


def _unwrap(href: str) -> str | None:
    if not href:
        return None
    m = REDIRECT.search(href)
    if m:
        target = unquote(m.group(1))
        return target if target.startswith("http") else None
    if href.startswith("http") and "yahoo.com" not in href:
        return href
    return None

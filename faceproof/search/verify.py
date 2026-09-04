"""Promote candidates to matches by re-running face recognition on their imagery.

A search engine ranking is not evidence. For every candidate page we download
the actual images, detect faces, encode them with the same SFace model used on
the probe, and keep only pages whose best face clears the identity threshold.
That decision -- not the search engine's opinion -- is what gets anchored.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

from ..face import FaceEngine, SAME_IDENTITY_COSINE, cosine
from ..evidence import sha256
from .base import Candidate, TIMEOUT, USER_AGENT

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGES_PER_PAGE = 6


@dataclass
class VerifiedMatch:
    candidate: Candidate
    similarity: float
    matched_image_url: str
    matched_image_sha256: str
    faces_in_image: int
    page_title: str = ""
    page_fetched: bool = False
    all_scores: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_url": self.candidate.page_url,
            "platform": self.candidate.platform,
            "is_individual_post": self.candidate.is_post,
            "title": self.page_title or self.candidate.title,
            "snippet": self.candidate.snippet,
            "discovered_by": self.candidate.provider,
            "matched_image_url": self.matched_image_url,
            "matched_image_sha256": self.matched_image_sha256,
            "faces_in_matched_image": self.faces_in_image,
            "face_similarity": round(self.similarity, 6),
            "similarity_metric": "cosine(sface_2021dec)",
            "identity_threshold": SAME_IDENTITY_COSINE,
            "page_html_fetched": self.page_fetched,
        }


def _is_public_http(url: str) -> bool:
    """Refuse non-HTTP schemes and private/loopback hosts."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        infos = socket.getaddrinfo(p.hostname, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


class Verifier:
    def __init__(
        self,
        engine: FaceEngine,
        probe_vector,
        threshold: float = SAME_IDENTITY_COSINE,
        workers: int = 8,
        fetch_pages: bool = True,
    ):
        self.engine = engine
        self.probe = probe_vector
        self.threshold = threshold
        self.workers = workers
        self.fetch_pages = fetch_pages
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": USER_AGENT})

    # -- network helpers -------------------------------------------------

    def _get_image(self, url: str) -> bytes | None:
        if not _is_public_http(url):
            return None
        try:
            r = self.sess.get(url, timeout=TIMEOUT, stream=True)
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype and not url.lower().endswith(
                (".jpg", ".jpeg", ".png", ".webp", ".bmp")
            ):
                return None
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf.extend(chunk)
                if len(buf) > MAX_IMAGE_BYTES:
                    return None
            return bytes(buf)
        except requests.RequestException:
            return None

    def _page_images(self, page_url: str) -> tuple[list[str], str, bool]:
        """Scrape a page for its most identity-relevant images (og:image first)."""
        if not self.fetch_pages or not _is_public_http(page_url):
            return [], "", False
        try:
            r = self.sess.get(page_url, timeout=TIMEOUT)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return [], "", False
        except requests.RequestException:
            return [], "", False

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        urls: list[str] = []
        for prop in ("og:image", "twitter:image", "og:image:secure_url"):
            for tag in soup.find_all("meta", attrs={"property": prop}) + soup.find_all(
                "meta", attrs={"name": prop}
            ):
                if tag.get("content"):
                    urls.append(urljoin(page_url, tag["content"]))
        for img in soup.find_all("img")[:40]:
            src = img.get("src") or img.get("data-src")
            if src:
                urls.append(urljoin(page_url, src))
        # de-dupe, keep order
        seen, ordered = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        return ordered[:MAX_IMAGES_PER_PAGE], title, True

    # -- verification ----------------------------------------------------

    def _score_image(self, data: bytes) -> tuple[float, int]:
        try:
            encs = self.engine.encode_bytes(data)
        except Exception:
            return 0.0, 0
        if not encs:
            return 0.0, 0
        return max(cosine(self.probe, e.vector) for e in encs), len(encs)

    def verify_one(self, cand: Candidate) -> VerifiedMatch | None:
        image_urls: list[str] = []
        if cand.image_url:
            image_urls.append(cand.image_url)
        page_title, fetched = "", False
        page_urls, page_title, fetched = self._page_images(cand.page_url)
        image_urls.extend(u for u in page_urls if u not in image_urls)

        best: tuple[float, str, int] | None = None
        scores: list[float] = []
        for url in image_urls[: MAX_IMAGES_PER_PAGE + 1]:
            data = self._get_image(url)
            if not data:
                continue
            score, nfaces = self._score_image(data)
            scores.append(round(score, 4))
            if best is None or score > best[0]:
                best = (score, url, nfaces)
                best_bytes = data
            if score >= self.threshold:
                break

        if best is None or best[0] < self.threshold:
            return None
        return VerifiedMatch(
            candidate=cand,
            similarity=best[0],
            matched_image_url=best[1],
            matched_image_sha256=sha256(best_bytes),
            faces_in_image=best[2],
            page_title=page_title,
            page_fetched=fetched,
            all_scores=scores,
        )

    def verify(self, candidates: list[Candidate], stop_after: int = 0, on_result=None):
        """Verify candidates in parallel. `stop_after` > 0 returns early once
        that many matches are confirmed."""
        matches: list[VerifiedMatch] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.verify_one, c): c for c in candidates}
            for fut in as_completed(futures):
                cand = futures[fut]
                try:
                    m = fut.result()
                except Exception:
                    m = None
                if on_result:
                    on_result(cand, m)
                if m:
                    matches.append(m)
                    if stop_after and len(matches) >= stop_after:
                        for f in futures:
                            f.cancel()
                        break
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches

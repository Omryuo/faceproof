from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 faceproof/0.1"
)
TIMEOUT = 20

SOCIAL_DOMAINS = {
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "linkedin.com": "linkedin",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "reddit.com": "reddit",
    "threads.net": "threads",
    "threads.com": "threads",
    "mastodon.social": "mastodon",
    "bsky.app": "bluesky",
    "github.com": "github",
    "medium.com": "medium",
    "flickr.com": "flickr",
    "pinterest.com": "pinterest",
    "weibo.com": "weibo",
    "vk.com": "vk",
    "tumblr.com": "tumblr",
}


# URL shapes that indicate an individual post/permalink rather than a profile.
POST_MARKERS = (
    "/status/", "/statuses/", "/p/", "/reel/", "/posts/", "/post/", "/pulse/",
    "/videos/", "/video/", "/watch", "/permalink/", "/photo/", "/comments/",
    "/@", "/shorts/",
)


def is_post_url(url: str) -> bool:
    """True if the URL looks like a single post rather than a profile/channel."""
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        return False
    if path in ("", "/"):
        return False
    return any(m in path for m in POST_MARKERS)


# Search engines' own domains -- never useful as candidates.
ENGINE_DOMAINS = (
    "yahoo.com", "duckduckgo.com", "bing.com", "google.com", "google.co",
    "yandex.com", "yandex.ru", "search.brave.com", "ecosia.org",
    "startpage.com", "mojeek.com",
)


def is_engine_url(url: str) -> bool:
    host = ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in ENGINE_DOMAINS)


# The platforms worth spending `site:` operators on -- long queries with 20+
# operators get truncated or rejected by the free engines.
CORE_SOCIAL = (
    "x.com", "instagram.com", "facebook.com", "linkedin.com",
    "youtube.com", "tiktok.com", "reddit.com", "threads.net",
)


def platform_of(url: str) -> str | None:
    """Map a URL to a social platform name, or None if it isn't one."""
    try:
        host = (urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return None
    for domain, name in SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


class SearchError(RuntimeError):
    pass


@dataclass
class Probe:
    """What we search *with*."""

    image_bytes: bytes
    image_sha256: str
    # A publicly reachable URL for the probe image. Reverse-image APIs need one;
    # we never upload the user's image implicitly to obtain it.
    public_url: str | None = None
    # Optional operator-supplied hint (a name/handle) for text-assisted search.
    hint: str | None = None


@dataclass
class Candidate:
    """An unverified lead: a page that *might* contain the probe's face."""

    page_url: str
    image_url: str | None = None
    title: str = ""
    snippet: str = ""
    provider: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def platform(self) -> str | None:
        return platform_of(self.page_url)

    @property
    def is_post(self) -> bool:
        return is_post_url(self.page_url)

    @property
    def rank(self) -> int:
        """Lower sorts first: social post, social profile, then plain web."""
        if self.platform and self.is_post:
            return 0
        if self.platform:
            return 1
        return 2

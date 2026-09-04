"""Offline tests for URL classification and result parsing."""

import pytest

from faceproof.search.base import Candidate, is_post_url, platform_of
from faceproof.search.ddg import _unwrap


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/sundarpichai", "x"),
        ("https://twitter.com/a/status/1", "x"),
        ("https://www.instagram.com/p/ABC/", "instagram"),
        ("https://m.facebook.com/someone", "facebook"),
        ("https://www.linkedin.com/in/someone", "linkedin"),
        ("https://example.com/blog", None),
        ("not a url", None),
    ],
)
def test_platform_detection(url, expected):
    assert platform_of(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/user/status/123", True),
        ("https://www.instagram.com/p/ABC/", True),
        ("https://www.linkedin.com/pulse/some-article", True),
        ("https://x.com/user", False),
        ("https://www.youtube.com/channel/UC123", False),
        ("https://example.com/", False),
    ],
)
def test_post_vs_profile(url, expected):
    assert is_post_url(url) is expected


def test_candidate_ranking_prefers_posts_then_profiles():
    post = Candidate(page_url="https://x.com/u/status/1")
    profile = Candidate(page_url="https://x.com/u")
    web = Candidate(page_url="https://example.com/x")
    assert [c.page_url for c in sorted([web, profile, post], key=lambda c: c.rank)] == [
        post.page_url, profile.page_url, web.page_url
    ]


def test_duckduckgo_redirect_unwrapping():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.com%2Fuser%2Fstatus%2F9&rut=abc"
    assert _unwrap(wrapped) == "https://x.com/user/status/9"


def test_unwrap_passes_through_direct_links():
    assert _unwrap("https://x.com/user") == "https://x.com/user"


def test_unwrap_rejects_non_http():
    assert _unwrap("javascript:alert(1)") is None

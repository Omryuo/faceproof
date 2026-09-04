"""Web / social-media search providers.

Every provider does the same job: turn a probe image into a list of *candidate*
web pages. None of them are trusted to be correct -- candidates are only
promoted to matches by `verify.py`, which re-downloads the imagery and runs the
same face recogniser used on the probe.
"""

from .base import Candidate, Probe, SearchError, is_post_url, platform_of
from .registry import PROVIDERS, available_providers, get_provider

__all__ = [
    "Candidate",
    "Probe",
    "SearchError",
    "platform_of",
    "is_post_url",
    "PROVIDERS",
    "get_provider",
    "available_providers",
]

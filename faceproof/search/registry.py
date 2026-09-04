from __future__ import annotations

from .ddg import DdgImages, DdgWeb
from .serpapi import SerpApiLens, SerpApiYandex
from .yahoo import YahooWeb
from .yandex import YandexReverse

# Ordered by strength: true reverse-image search first, then key-free
# hint-assisted engines as fallbacks.
PROVIDERS = {
    "serpapi_lens": SerpApiLens,
    "serpapi_yandex": SerpApiYandex,
    "yandex": YandexReverse,
    "ddg_images": DdgImages,
    "yahoo_web": YahooWeb,
    "ddg_web": DdgWeb,
}

DEFAULT_CHAIN = [
    "serpapi_lens",
    "serpapi_yandex",
    "yandex",
    "ddg_images",
    "yahoo_web",
    "ddg_web",
]


def get_provider(name: str):
    if name not in PROVIDERS:
        raise KeyError(f"unknown provider {name!r}; choose from {sorted(PROVIDERS)}")
    return PROVIDERS[name]()


def available_providers() -> list[str]:
    return [n for n in DEFAULT_CHAIN if get_provider(n).available()]

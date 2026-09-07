"""Process-wide ChainClient cache.

The in-process `tester` backend builds a *new, empty* chain every time a client
is constructed. Anything that anchors in one call and verifies in another --
the `run` command, and every web request -- must therefore reuse one client, or
the verify step queries a chain that has never seen the anchor and reports a
false FAILED.

Keyed by (network, rpc_url); safe to call from multiple threads.
"""

from __future__ import annotations

import threading

from .client import ChainClient

_lock = threading.Lock()
_clients: dict[tuple[str, str | None], ChainClient] = {}


def get_client(network: str = "tester", rpc_url: str | None = None) -> ChainClient:
    """Return the shared client for this network, creating it on first use."""
    key = (network, rpc_url)
    with _lock:
        client = _clients.get(key)
        if client is None:
            client = ChainClient(network, rpc_url=rpc_url)
            _clients[key] = client
        return client


def reset() -> None:
    """Drop every cached client (tests)."""
    with _lock:
        _clients.clear()

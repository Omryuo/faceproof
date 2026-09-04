"""Canonical evidence records and their hashes.

The record is what gets anchored on-chain. Canonicalisation matters: the same
logical record must always produce the same bytes, on any machine, in any
Python version, or the on-chain hash is meaningless.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from . import PIPELINE_VERSION


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_bytes(obj: Any) -> bytes:
    """RFC-8785-ish canonical JSON: sorted keys, no whitespace, UTF-8, no NaN."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def keccak256(data: bytes) -> str:
    """Keccak-256 -- the hash the EVM speaks. Returned as 0x-prefixed hex."""
    from eth_utils import keccak

    return "0x" + keccak(data).hex()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_record(
    *,
    probe: dict,
    match: dict,
    search: dict,
) -> dict:
    """Assemble the evidence record.

    `payload` is the part that is hashed and anchored. Everything outside it
    (the hash itself, chain receipts) is derived, so it stays out of the digest.
    """
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "created_at": utcnow(),
        "probe": probe,
        "search": search,
        "match": match,
    }
    return {
        "payload": payload,
        "record_hash": keccak256(canonical_bytes(payload)),
        "hash_algorithm": "keccak256(canonical-json(payload))",
    }


def recompute_hash(record: dict) -> str:
    return keccak256(canonical_bytes(record["payload"]))


def check_integrity(record: dict) -> tuple[bool, str, str]:
    """(matches, stored_hash, recomputed_hash)"""
    stored = record.get("record_hash", "")
    fresh = recompute_hash(record)
    return stored.lower() == fresh.lower(), stored, fresh

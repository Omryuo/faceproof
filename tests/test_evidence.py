"""Canonicalisation and tamper detection."""

import pytest

from faceproof.evidence import (
    build_record, canonical_bytes, check_integrity, keccak256, recompute_hash,
)


def test_canonical_json_is_key_order_independent():
    a = {"b": 1, "a": {"y": 2, "x": [3, 4]}}
    b = {"a": {"x": [3, 4], "y": 2}, "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b)
    assert keccak256(canonical_bytes(a)) == keccak256(canonical_bytes(b))


def test_canonical_json_is_order_sensitive_for_lists():
    assert canonical_bytes({"a": [1, 2]}) != canonical_bytes({"a": [2, 1]})


def test_keccak_is_32_bytes_hex():
    h = keccak256(b"hello")
    assert h.startswith("0x") and len(h) == 66


def test_nan_is_rejected():
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("nan")})


def _record():
    return build_record(
        probe={"embedding_sha256": "a" * 64, "image_sha256": "b" * 64},
        match={"page_url": "https://x.com/someone/status/1", "face_similarity": 0.71},
        search={"providers_used": ["ddg_web"]},
    )


def test_fresh_record_is_internally_consistent():
    ok, stored, fresh = check_integrity(_record())
    assert ok and stored == fresh


@pytest.mark.parametrize(
    "field,value",
    [
        ("page_url", "https://example.com/forged"),
        ("face_similarity", 0.99),
    ],
)
def test_any_payload_edit_breaks_the_hash(field, value):
    rec = _record()
    original = rec["record_hash"]
    rec["payload"]["match"][field] = value
    ok, stored, fresh = check_integrity(rec)
    assert not ok
    assert stored == original          # forger leaves the old hash in place
    assert fresh != original           # but the payload no longer produces it


def test_adding_a_key_breaks_the_hash():
    rec = _record()
    rec["payload"]["match"]["injected"] = True
    assert recompute_hash(rec) != rec["record_hash"]

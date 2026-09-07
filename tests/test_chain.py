"""Anchoring and re-verification against a real (in-process) EVM."""

import pytest

from faceproof.chain import AlreadyAnchored, ChainClient
from faceproof.evidence import build_record, canonical_bytes, keccak256
from faceproof.pipeline import anchor_record, reverify


@pytest.fixture(scope="module")
def client():
    c = ChainClient("tester")
    c.deploy()
    return c


def _record(url="https://x.com/u/status/1"):
    return build_record(
        probe={"embedding_sha256": "a" * 64},
        match={"page_url": url, "face_similarity": 0.7},
        search={"providers_used": ["ddg_web"]},
    )


def test_deploy_creates_a_live_contract(client):
    assert client.contract is not None
    assert client.w3.eth.get_code(client.contract.address) not in (b"", b"0x")


def test_anchor_then_verify_round_trip(client):
    rec = _record("https://x.com/u/status/round-trip")
    res = anchor_record(rec, client)
    assert res["status"] == "anchored"
    assert res["block_number"] > 0

    out = reverify(rec, client)
    assert out["verdict"] == "VERIFIED"
    assert out["internal_hash_ok"] and out["onchain_found"]


def test_unanchored_record_is_not_found(client):
    out = reverify(_record("https://x.com/u/status/never-anchored"), client)
    assert out["internal_hash_ok"] is True     # internally consistent...
    assert out["onchain_found"] is False       # ...but never anchored
    assert out["verdict"] == "FAILED"


def test_tampering_after_anchoring_is_detected(client):
    rec = _record("https://x.com/u/status/tamper")
    anchor_record(rec, client)
    assert reverify(rec, client)["verdict"] == "VERIFIED"

    rec["payload"]["match"]["page_url"] = "https://evil.example/forged"
    out = reverify(rec, client)
    assert out["internal_hash_ok"] is False
    assert out["onchain_found"] is False       # the new hash was never anchored
    assert out["verdict"] == "FAILED"


def test_re_anchoring_the_same_digest_is_rejected(client):
    rec = _record("https://x.com/u/status/dup")
    anchor_record(rec, client)
    with pytest.raises(AlreadyAnchored):
        client.anchor(rec["record_hash"], "")


def test_anchor_refuses_a_record_whose_hash_does_not_match(client):
    from faceproof.pipeline import PipelineError

    rec = _record("https://x.com/u/status/bad-hash")
    rec["payload"]["match"]["page_url"] = "https://changed.example/"
    with pytest.raises(PipelineError):
        anchor_record(rec, client)


def test_count_increases_with_anchors(client):
    before = client.count()
    anchor_record(_record("https://x.com/u/status/counting"), client)
    assert client.count() == before + 1


# -- process-wide client sharing --------------------------------------------


def test_pool_returns_one_client_per_network():
    """The in-process `tester` backend builds a new empty chain per client, so
    anchoring and verifying through separate clients silently fails."""
    from faceproof.chain import get_client, reset

    reset()
    try:
        a = get_client("tester")
        b = get_client("tester")
        assert a is b
    finally:
        reset()


def test_anchor_then_verify_through_the_pool_agrees():
    """Regression: the web server anchored with one client and re-verified with
    another, so an untampered record reported FAILED."""
    from faceproof.chain import get_client, reset

    reset()
    try:
        client = get_client("tester")
        client.ensure()
        rec = _record("https://x.com/u/status/pooled")
        anchor_record(rec, client)

        # a later, independent lookup -- as the tamper endpoint does
        assert reverify(rec, get_client("tester"))["verdict"] == "VERIFIED"
    finally:
        reset()


def test_already_anchored_receipt_has_the_same_keys_as_a_fresh_one(client):
    """The UI reads tx_hash/gas_used/block_timestamp off the receipt; the
    already-anchored path used to omit them and render `undefined`."""
    rec = _record("https://x.com/u/status/shape")
    fresh = anchor_record(rec, client)
    again = anchor_record(rec, client)

    assert fresh["status"] == "anchored"
    assert again["status"] == "already_anchored"
    assert set(fresh) == set(again)
    assert again["tx_hash"] is None
    assert again["block_number"] == fresh["block_number"]
    assert again["block_timestamp"] == fresh["block_timestamp"]

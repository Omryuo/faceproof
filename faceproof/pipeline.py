"""End-to-end orchestration: face scan -> search -> verify -> anchor -> re-verify."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .chain import AlreadyAnchored, ChainClient
from .evidence import build_record, check_integrity, sha256, utcnow
from .face import FaceEngine, SAME_IDENTITY_COSINE, FaceEncoding
from .search import Probe, get_provider
from .search.base import SearchError
from .search.registry import DEFAULT_CHAIN
from .search.verify import Verifier, VerifiedMatch


@dataclass
class StageLog:
    events: list[tuple[str, str]] = field(default_factory=list)

    def add(self, stage: str, msg: str):
        self.events.append((stage, msg))


class PipelineError(RuntimeError):
    pass


def scan_face(image_path: Path, engine: FaceEngine) -> FaceEncoding:
    """Stage 1 -- detect and encode the probe face."""
    data = image_path.read_bytes()
    enc = engine.encode_primary(data)
    if enc is None:
        raise PipelineError(f"no face detected in {image_path}")
    return enc


def run_search(
    probe: Probe,
    providers: list[str] | None = None,
    log: StageLog | None = None,
    use_cache: bool = False,
    write_cache: bool = True,
) -> tuple[list, list[str]]:
    """Stage 2a -- collect candidates, trying providers in order of strength."""
    from .search import cache as search_cache

    names = providers or DEFAULT_CHAIN
    candidates, used = [], []
    for name in names:
        try:
            prov = get_provider(name)
        except KeyError as exc:
            raise PipelineError(str(exc)) from exc
        if not prov.available():
            if log:
                log.add("search", f"{name}: skipped (not configured)")
            continue

        found = None
        try:
            found = prov.search(probe)
            if write_cache and found:
                search_cache.save(name, probe.image_sha256, probe.hint, probe.public_url, found)
        except SearchError as exc:
            if log:
                log.add("search", f"{name}: {exc}")
            if use_cache:
                hit = search_cache.load(name, probe.image_sha256, probe.hint, probe.public_url)
                if hit:
                    found, fetched_at = hit
                    used.append(f"{name}(cached)")
                    if log:
                        log.add("search", f"{name}: {len(found)} candidates from cache "
                                          f"of {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(fetched_at))}")
                    candidates.extend(found)
            continue
        used.append(name)
        if log:
            log.add("search", f"{name}: {len(found)} candidates")
        candidates.extend(found)

    # de-duplicate by page URL, keeping the strongest provider's version, and
    # drop the search engines' own pages
    from .search.base import is_engine_url

    seen, unique = set(), []
    for c in candidates:
        if c.page_url in seen or is_engine_url(c.page_url):
            continue
        seen.add(c.page_url)
        unique.append(c)
    return unique, used


def verify_candidates(
    engine: FaceEngine,
    probe_enc: FaceEncoding,
    candidates: list,
    threshold: float = SAME_IDENTITY_COSINE,
    social_only: bool = False,
    stop_after: int = 0,
    on_result=None,
) -> list[VerifiedMatch]:
    """Stage 2b -- the identity decision, made locally by the face model."""
    pool = [c for c in candidates if c.platform] if social_only else list(candidates)
    # Individual social posts first, then profiles, then plain web pages.
    pool.sort(key=lambda c: (c.rank, c.page_url))
    verifier = Verifier(engine, probe_enc.vector, threshold=threshold)
    return verifier.verify(pool, stop_after=stop_after, on_result=on_result)


def best_match(matches: list[VerifiedMatch]) -> VerifiedMatch:
    """Pick the headline match: prefer an individual social post, then a social
    profile, then any page -- breaking ties on face similarity."""
    return sorted(matches, key=lambda m: (m.candidate.rank, -m.similarity))[0]


def make_record(
    probe_enc: FaceEncoding,
    match: VerifiedMatch,
    *,
    providers_used: list[str],
    candidates_seen: int,
    candidates_verified: int,
    probe_url: str | None,
    source_image: str,
) -> dict:
    """Stage 3a -- freeze the finding into a canonical, hashable record."""
    return build_record(
        probe=dict(probe_enc.to_dict(), source_image=source_image, public_probe_url=probe_url),
        search={
            "providers_used": providers_used,
            "candidates_seen": candidates_seen,
            "candidates_verified": candidates_verified,
            "searched_at": utcnow(),
        },
        match=match.to_dict(),
    )


def anchor_record(record: dict, client: ChainClient, uri: str = "") -> dict:
    """Stage 3b -- write the digest on chain."""
    ok, stored, fresh = check_integrity(record)
    if not ok:
        raise PipelineError(
            f"record hash does not match its payload (stored {stored}, recomputed {fresh})"
        )
    try:
        receipt = client.anchor(stored, uri)
        return dict(receipt.to_dict(), status="anchored")
    except AlreadyAnchored as exc:
        # Re-anchoring is refused by the contract, which is the point: the first
        # submission stands. Report it with the same keys a fresh anchor uses so
        # callers never have to special-case the shape.
        e = exc.existing
        return {
            "status": "already_anchored",
            "network": e["network"],
            "chain_id": e["chain_id"],
            "contract_address": e["contract_address"],
            "tx_hash": None,
            "block_number": e["block_number"],
            "block_timestamp": e["timestamp"],
            "submitter": e["submitter"],
            "gas_used": 0,
            "uri": e.get("uri", ""),
            "record_hash": stored,
        }


def reverify(record: dict, client: ChainClient) -> dict:
    """Stage 4 -- prove the record is unchanged since it was anchored.

    Two independent checks:
      1. internal   -- payload still hashes to the stored digest
      2. on-chain   -- that digest is present in the registry
    Both must pass. Editing any byte of the payload breaks (1); fabricating a
    record that was never anchored breaks (2).
    """
    ok_internal, stored, fresh = check_integrity(record)
    chain = client.lookup(fresh)
    return {
        "internal_hash_ok": ok_internal,
        "stored_hash": stored,
        "recomputed_hash": fresh,
        "onchain_found": chain["exists"],
        "onchain": chain,
        "verdict": "VERIFIED" if (ok_internal and chain["exists"]) else "FAILED",
    }

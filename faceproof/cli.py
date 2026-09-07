"""FaceProof command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .evidence import sha256

console = Console()
OUT = Path(__file__).resolve().parent.parent / "out"


def _engine():
    from .face import FaceEngine, ModelsMissing

    try:
        return FaceEngine()
    except ModelsMissing as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)


def _load_env():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _rule(title: str):
    console.rule(f"[bold cyan]{title}[/bold cyan]")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_fetch_models(args):
    from .models_fetch import fetch_all

    fetch_all(console)


def cmd_ui(args):
    from .server import start_server

    start_server(port=args.port, open_browser=not args.no_browser)



def cmd_scan(args):
    _rule("Stage 1 - face scan")
    engine = _engine()
    data = Path(args.image).read_bytes()
    encs = engine.encode_bytes(data)
    if not encs:
        console.print("[red]no face detected[/red]")
        sys.exit(1)
    table = Table("#", "score", "bbox (x,y,w,h)", "embedding sha256")
    for i, e in enumerate(encs):
        table.add_row(
            str(i), f"{e.face.score:.3f}", str(list(e.face.bbox)), e.embedding_sha256[:24] + "..."
        )
    console.print(table)
    console.print(f"image sha256: [dim]{sha256(data)}[/dim]")
    if args.annotate:
        n = engine.annotate(data, Path(args.annotate))
        console.print(f"annotated {n} face(s) -> {args.annotate}")


def cmd_search(args):
    _load_env()
    from .pipeline import StageLog, run_search, scan_face, verify_candidates
    from .search import Probe

    engine = _engine()
    log = StageLog()

    _rule("Stage 1 - face scan")
    image_path = Path(args.image)
    enc = scan_face(image_path, engine)
    console.print(
        f"face detected (score {enc.face.score:.3f}), "
        f"embedding [bold]{enc.embedding_sha256[:16]}...[/bold]"
    )

    _rule("Stage 2a - web / social search")
    probe = Probe(
        image_bytes=image_path.read_bytes(),
        image_sha256=enc.image_sha256,
        public_url=args.probe_url,
        hint=args.hint,
    )
    providers = args.provider or None
    candidates, used = run_search(probe, providers, log, use_cache=args.use_cache)
    for stage, msg in log.events:
        console.print(f"  [dim]{stage}[/dim] {msg}")
    if not candidates:
        console.print("[red]no candidates returned by any provider[/red]")
        sys.exit(1)
    social = sum(1 for c in candidates if c.platform)
    console.print(
        f"[bold]{len(candidates)}[/bold] unique candidates "
        f"([bold]{social}[/bold] on social platforms) via {', '.join(used)}"
    )

    _rule("Stage 2b - face verification of candidates")
    checked = {"n": 0}

    def on_result(cand, match):
        checked["n"] += 1
        mark = "[green]MATCH[/green]" if match else "[dim]no[/dim]"
        sim = f" {match.similarity:.3f}" if match else ""
        console.print(f"  {mark}{sim} [dim]{cand.page_url[:90]}[/dim]")

    matches = verify_candidates(
        engine, enc, candidates,
        threshold=args.threshold,
        social_only=args.social_only,
        stop_after=args.stop_after,
        on_result=on_result,
    )
    console.print(
        f"verified [bold]{len(matches)}[/bold] / {checked['n']} checked "
        f"at cosine >= {args.threshold}"
    )
    if not matches:
        console.print("[red]no face-verified match found[/red]")
        sys.exit(1)

    from .pipeline import best_match

    best = best_match(matches)
    kind = "post" if best.candidate.is_post else "profile/page"
    console.print(
        f"\n[bold green]best match[/bold green] "
        f"({best.candidate.platform or 'web'} {kind}, cosine {best.similarity:.4f})\n"
        f"  {best.candidate.page_url}"
    )

    from .pipeline import make_record

    record = make_record(
        enc, best,
        providers_used=used,
        candidates_seen=len(candidates),
        candidates_verified=len(matches),
        probe_url=args.probe_url,
        source_image=image_path.name,
    )
    record["other_matches"] = [m.to_dict() for m in matches[1:11]]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    console.print(f"\nevidence record -> [bold]{out}[/bold]")
    console.print(f"record hash: [bold yellow]{record['record_hash']}[/bold yellow]")


# One client per (network, rpc) for the life of the process. This matters for
# the in-process `tester` chain: building a second client would spin up a brand
# new empty chain, so `run`'s verify step would never find what its anchor step
# just wrote.
_CLIENTS: dict[tuple, object] = {}


def _client(args):
    from .chain import ChainClient, ChainError

    key = (args.network, args.rpc_url)
    if key in _CLIENTS:
        return _CLIENTS[key]
    try:
        c = ChainClient(args.network, rpc_url=args.rpc_url)
    except ChainError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(3)
    _CLIENTS[key] = c
    return c


def cmd_anchor(args):
    _load_env()
    from .pipeline import anchor_record

    record = json.loads(Path(args.record).read_text())
    _rule("Stage 3 - blockchain anchoring")
    client = _client(args)
    addr = client.ensure(args.contract)
    console.print(f"network [bold]{client.network}[/bold] chain_id {client.w3.eth.chain_id}")
    console.print(f"registry [bold]{addr}[/bold]")
    console.print(f"submitter {client.account}")

    if client.network == "tester":
        console.print(
            "[yellow]note[/yellow] the 'tester' chain lives inside this process only -- "
            "a later `verify` run will not find this anchor.\n"
            "      use --network local (see `make chain`) or the one-shot `run` command."
        )

    result = anchor_record(record, client, uri=args.uri)
    record["blockchain"] = result
    Path(args.record).write_text(json.dumps(record, indent=2) + "\n")

    if result["status"] == "already_anchored":
        console.print(
            f"[yellow]already anchored[/yellow] in block {result['block_number']}"
        )
    else:
        console.print(f"[green]anchored[/green] tx {result['tx_hash']}")
        console.print(f"  block {result['block_number']}  gas {result['gas_used']}")
        if result.get("explorer_url"):
            console.print(f"  {result['explorer_url']}")
    console.print(f"record updated -> {args.record}")


def cmd_verify(args):
    _load_env()
    from .pipeline import reverify

    record = json.loads(Path(args.record).read_text())
    _rule("Stage 4 - re-verification against the chain")
    client = _client(args)
    addr = client.ensure(args.contract)
    console.print(f"registry [bold]{addr}[/bold] on {client.network}")

    res = reverify(record, client)
    table = Table("check", "result")
    table.add_row("payload -> hash matches stored hash", _ok(res["internal_hash_ok"]))
    table.add_row("hash present in on-chain registry", _ok(res["onchain_found"]))
    console.print(table)
    console.print(f"stored     {res['stored_hash']}")
    console.print(f"recomputed {res['recomputed_hash']}")
    if res["onchain_found"]:
        c = res["onchain"]
        console.print(
            f"anchored in block [bold]{c['block_number']}[/bold] "
            f"at unix {c['timestamp']} by {c['submitter']}"
        )
    colour = "green" if res["verdict"] == "VERIFIED" else "red"
    console.print(f"\n[bold {colour}]{res['verdict']}[/bold {colour}]")
    sys.exit(0 if res["verdict"] == "VERIFIED" else 1)


def _ok(b: bool) -> str:
    return "[green]PASS[/green]" if b else "[red]FAIL[/red]"


def cmd_tamper(args):
    """Write a modified copy of a record to show verification catching it."""
    record = json.loads(Path(args.record).read_text())
    target = record["payload"]["match"]
    old = target.get(args.field)
    target[args.field] = args.value
    out = Path(args.output)
    out.write_text(json.dumps(record, indent=2) + "\n")
    console.print(f"tampered [bold]{args.field}[/bold]")
    console.print(f"  was: [dim]{old}[/dim]")
    console.print(f"  now: [yellow]{args.value}[/yellow]")
    console.print(f"wrote {out} (record_hash left untouched, as a forger would)")


def cmd_chain_info(args):
    _load_env()
    client = _client(args)
    console.print(f"network      {client.network}")
    console.print(f"chain_id     {client.w3.eth.chain_id}")
    console.print(f"block        {client.w3.eth.block_number}")
    console.print(f"account      {client.account}")
    try:
        addr = client.load(args.contract)
        console.print(f"registry     {addr}")
        console.print(f"anchors      {client.count()}")
    except Exception as exc:
        console.print(f"registry     [yellow]not deployed ({exc})[/yellow]")


def cmd_run(args):
    """Full pipeline in one process."""
    cmd_search(args)
    args.record = args.output
    cmd_anchor(args)
    cmd_verify(args)


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="faceproof",
        description="Face scan -> web/social search -> blockchain verification.",
    )
    p.add_argument("--version", action="version", version=f"faceproof {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def chain_args(sp):
        sp.add_argument("--network", default="tester",
                        help="tester | local | sepolia | amoy (default: tester)")
        sp.add_argument("--rpc-url", default=None, help="override RPC endpoint")
        sp.add_argument("--contract", default=None, help="existing registry address")

    def search_args(sp):
        sp.add_argument("image", help="path to the face image to scan")
        sp.add_argument("--probe-url", default=None,
                        help="public URL of the probe image (needed by reverse-image providers)")
        sp.add_argument("--hint", default=None,
                        help="name/handle for hint-assisted key-free providers")
        sp.add_argument("--provider", action="append", default=None,
                        help="restrict to a provider (repeatable)")
        sp.add_argument("--threshold", type=float, default=None,
                        help="cosine identity threshold (default 0.363)")
        sp.add_argument("--social-only", action="store_true",
                        help="only verify candidates on known social platforms")
        sp.add_argument("--stop-after", type=int, default=0,
                        help="stop once N matches are verified (0 = check all)")
        sp.add_argument("--use-cache", action="store_true",
                        help="fall back to a cached earlier search if a provider is rate limited")
        sp.add_argument("-o", "--output", default=str(OUT / "evidence.json"))

    sp = sub.add_parser("fetch-models", help="download the ONNX face models")
    sp.set_defaults(func=cmd_fetch_models)

    sp = sub.add_parser("ui", help="launch the beach-themed interactive web dashboard")
    sp.add_argument("--port", type=int, default=8080, help="port to listen on (default 8080)")
    sp.add_argument("--no-browser", action="store_true", help="do not auto-open browser")
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("scan", help="stage 1 only: detect + encode faces")
    sp.add_argument("image")
    sp.add_argument("--annotate", default=None, help="write a boxed copy here")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("search", help="stages 1-2: scan, search, face-verify")
    search_args(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("anchor", help="stage 3: anchor an evidence record on chain")
    sp.add_argument("record")
    sp.add_argument("--uri", default="", help="optional off-chain pointer to store")
    chain_args(sp)
    sp.set_defaults(func=cmd_anchor)

    sp = sub.add_parser("verify", help="stage 4: re-verify a record against the chain")
    sp.add_argument("record")
    chain_args(sp)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("tamper", help="produce a doctored record for the demo")
    sp.add_argument("record")
    sp.add_argument("--field", default="page_url")
    sp.add_argument("--value", default="https://example.com/not-the-real-post")
    sp.add_argument("-o", "--output", default=str(OUT / "evidence.tampered.json"))
    sp.set_defaults(func=cmd_tamper)

    sp = sub.add_parser("chain-info", help="show chain / registry status")
    chain_args(sp)
    sp.set_defaults(func=cmd_chain_info)

    sp = sub.add_parser("run", help="full pipeline: scan -> search -> anchor -> verify")
    search_args(sp)
    chain_args(sp)
    sp.add_argument("--uri", default="")
    sp.set_defaults(func=cmd_run)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "threshold", None) is None and hasattr(args, "threshold"):
        from .face import SAME_IDENTITY_COSINE

        args.threshold = SAME_IDENTITY_COSINE
    args.func(args)


if __name__ == "__main__":
    main()

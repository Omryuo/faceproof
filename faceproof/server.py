"""Beach-themed Web Dashboard Server for FaceProof.

Provides a lightweight, zero-dependency REST API and HTTP server to run,
visualise, and interactively audit the FaceProof pipeline.
"""

from __future__ import annotations

import base64
import json
import io
import os
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .chain import get_client
from .evidence import check_integrity
from .face import FaceEngine, SAME_IDENTITY_COSINE
from .pipeline import (
    StageLog,
    anchor_record,
    best_match,
    make_record,
    reverify,
    run_search,
    scan_face,
    verify_candidates,
)
from .search import Probe

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"


class FaceProofRequestHandler(BaseHTTPRequestHandler):
    engine: FaceEngine | None = None

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress verbose standard HTTP logging for cleaner terminal output
        sys.stderr.write(f"[WebUI] {self.address_string()} - {format % args}\n")

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path, content_type: str) -> None:
        if not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            rel = path[len("/static/") :]
            sub_path = WEB_DIR / rel
            if sub_path.suffix == ".css":
                self._send_file(sub_path, "text/css")
            elif sub_path.suffix == ".js":
                self._send_file(sub_path, "application/javascript")
            elif sub_path.suffix in (".jpg", ".jpeg"):
                self._send_file(sub_path, "image/jpeg")
            elif sub_path.suffix == ".png":
                self._send_file(sub_path, "image/png")
            else:
                self._send_file(sub_path, "application/octet-stream")
        elif path == "/api/health":
            self._send_json({"status": "ok", "models_loaded": self.engine is not None})
        elif path.startswith("/examples/"):
            file_name = path[len("/examples/") :]
            file_path = ROOT_DIR / "examples" / file_name
            self._send_file(file_path, "image/jpeg")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        payload = {}
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                pass

        if path == "/api/scan":
            self._handle_scan(payload)
        elif path == "/api/pipeline":
            self._handle_pipeline(payload)
        elif path == "/api/tamper":
            self._handle_tamper(payload)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _get_engine(self) -> FaceEngine:
        if FaceProofRequestHandler.engine is None:
            FaceProofRequestHandler.engine = FaceEngine()
        return FaceProofRequestHandler.engine

    def _handle_scan(self, payload: dict) -> None:
        try:
            engine = self._get_engine()
            image_b64 = payload.get("image_b64", "")
            image_path_str = payload.get("image_path", "examples/probe.jpg")

            if image_b64:
                if "," in image_b64:
                    image_b64 = image_b64.split(",", 1)[1]
                data = base64.b64decode(image_b64)
            else:
                p = ROOT_DIR / image_path_str
                if not p.exists():
                    self._send_json({"error": f"Image file not found: {image_path_str}"}, 400)
                    return
                data = p.read_bytes()

            encs = engine.encode_bytes(data)
            if not encs:
                self._send_json({"error": "No faces detected in probe image"}, 400)
                return

            probe_enc = encs[0]

            # Annotate in memory: a shared temp file would let concurrent
            # requests hand each other the wrong image.
            annotated, _ = engine.annotate_bytes(data)
            ann_b64 = "data:image/jpeg;base64," + base64.b64encode(annotated).decode("utf-8")

            self._send_json({
                "status": "success",
                "faces_detected": len(encs),
                "primary_face": probe_enc.to_dict(),
                "annotated_image_b64": ann_b64,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _handle_pipeline(self, payload: dict) -> None:
        try:
            engine = self._get_engine()

            hint = payload.get("hint", "Sundar Pichai")
            network = payload.get("network", "tester")
            probe_url = payload.get("probe_url") or None
            image_b64 = payload.get("image_b64", "")
            image_path_str = payload.get("image_path", "examples/probe.jpg")
            use_cache = payload.get("use_cache", True)

            if image_b64:
                if "," in image_b64:
                    image_b64 = image_b64.split(",", 1)[1]
                data = base64.b64decode(image_b64)
                # Content-addressed, so two uploads in flight cannot clobber
                # each other's file.
                import hashlib

                digest = hashlib.sha256(data).hexdigest()[:16]
                probe_path = ROOT_DIR / "out" / "uploads" / f"{digest}.jpg"
                probe_path.parent.mkdir(parents=True, exist_ok=True)
                probe_path.write_bytes(data)
            else:
                probe_path = ROOT_DIR / image_path_str
                data = probe_path.read_bytes()

            log = StageLog()

            # Stage 1: Face Scan
            probe_enc = engine.encode_primary(data)
            if probe_enc is None:
                self._send_json({"error": "No face detected in probe image"}, 400)
                return

            annotated, _ = engine.annotate_bytes(data)
            ann_b64 = "data:image/jpeg;base64," + base64.b64encode(annotated).decode("utf-8")

            stage1_data = {
                "face": probe_enc.to_dict(),
                "faces_count": 1,
                "annotated_b64": ann_b64,
            }

            # Stage 2a: Web Search
            probe = Probe(
                image_bytes=data,
                image_sha256=probe_enc.image_sha256,
                hint=hint,
                public_url=probe_url,
            )

            candidates, providers_used = run_search(probe, log=log, use_cache=use_cache)

            stage2a_data = {
                "candidates_count": len(candidates),
                "providers_used": providers_used,
                "social_candidates_count": sum(1 for c in candidates if c.platform),
                "log": log.events,
            }

            # Stage 2b: Face Verification
            verified_matches = verify_candidates(
                engine, probe_enc, candidates, threshold=SAME_IDENTITY_COSINE
            )

            if not verified_matches:
                self._send_json({
                    "error": "No candidates cleared the face similarity threshold (cosine >= 0.363)",
                    "stage1": stage1_data,
                    "stage2a": stage2a_data,
                }, 400)
                return

            matched = best_match(verified_matches)

            stage2b_data = {
                "total_verified": len(verified_matches),
                "candidates_checked": len(candidates),
                "best_match": matched.to_dict(),
                "all_matches": [m.to_dict() for m in verified_matches],
            }

            # Stage 3: Make Record & Anchor
            record = make_record(
                probe_enc,
                matched,
                providers_used=providers_used,
                candidates_seen=len(candidates),
                candidates_verified=len(verified_matches),
                probe_url=probe_url,
                source_image=str(probe_path.name),
            )

            # One shared client per network for the life of the server: a fresh
            # `tester` client would be a brand new empty chain, so stage 4 (and
            # the tamper endpoint) would never find what stage 3 just anchored.
            client = get_client(network)
            client.ensure()
            receipt = anchor_record(record, client, uri="faceproof-web-ui")

            stage3_data = {
                "record_hash": record["record_hash"],
                "payload": record["payload"],
                "receipt": receipt,
            }

            # Stage 4: Re-Verification
            verification = reverify(record, client)
            stage4_data = verification

            self._send_json({
                "status": "success",
                "stage1": stage1_data,
                "stage2a": stage2a_data,
                "stage2b": stage2b_data,
                "stage3": stage3_data,
                "stage4": stage4_data,
                "record": record,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _handle_tamper(self, payload: dict) -> None:
        try:
            record = payload.get("record", {})
            field_name = payload.get("field", "page_url")
            new_value = payload.get("value", "https://example.com/forged")
            network = payload.get("network", "tester")

            if not record or "payload" not in record:
                self._send_json({"error": "Invalid evidence record provided"}, 400)
                return

            tampered = json.loads(json.dumps(record))
            p = tampered["payload"]
            found = False
            for section in ("match", "probe", "search"):
                if section in p and field_name in p[section]:
                    p[section][field_name] = new_value
                    found = True
                    break

            if not found:
                p["match"][field_name] = new_value

            client = get_client(network)
            client.ensure()

            verdict = reverify(tampered, client)
            ok, stored, fresh = check_integrity(tampered)

            self._send_json({
                "status": "success",
                "original_record_hash": record.get("record_hash"),
                "recomputed_record_hash": fresh,
                "hashes_match": ok,
                "onchain_verdict": verdict,
                "tampered_record": tampered,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)


def start_server(port: int = 8080, open_browser: bool = True) -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, FaceProofRequestHandler)

    url = f"http://localhost:{port}"
    print(f"\n=======================================================")
    print(f"  🌊 FaceProof Beach Dashboard Web Server Running! ")
    print(f"  URL: {url}")
    print(f"=======================================================\n")

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down FaceProof Web Server...")
        httpd.server_close()


if __name__ == "__main__":
    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    start_server(port=port)

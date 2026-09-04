#!/usr/bin/env bash
# End-to-end demo: face scan -> web/social search -> anchor -> re-verify -> tamper.
#
#   ./scripts/demo.sh              # local ganache (auto-started if needed)
#   ./scripts/demo.sh sepolia      # public testnet; needs RPC_URL + PRIVATE_KEY
#
# Note: the `tester` backend is in-process, so its state does not survive
# between commands. This script re-verifies from a *separate* process on
# purpose, which needs a persistent chain. For a single-process run on
# `tester`, use `python -m faceproof.cli run ...` instead.
set -euo pipefail

NETWORK="${1:-local}"
HINT="${HINT:-Sundar Pichai}"
PROBE="${PROBE:-examples/probe.jpg}"
PY="${PY:-.venv/bin/python}"
GANACHE_PID=""

banner() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$1"; }

cleanup() {
  if [[ -n "$GANACHE_PID" ]]; then
    printf '\nstopping ganache (pid %s)\n' "$GANACHE_PID"
    kill "$GANACHE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ "$NETWORK" == "tester" ]]; then
  echo "refusing to run the cross-process demo on the in-process 'tester' chain;" >&2
  echo "use 'local' (default) or run: $PY -m faceproof.cli run $PROBE --hint \"$HINT\"" >&2
  exit 2
fi

if [[ ! -f "$PROBE" ]]; then
  echo "probe image missing; running scripts/fetch_example.py"
  "$PY" scripts/fetch_example.py
fi

if [[ "$NETWORK" == "local" ]] && ! nc -z 127.0.0.1 8545 2>/dev/null; then
  banner "0. starting a local chain"
  npx --yes ganache@7 --wallet.deterministic --database.dbPath .chaindata \
      --chain.chainId 1337 --server.port 8545 > out/ganache.log 2>&1 &
  GANACHE_PID=$!
  for _ in $(seq 1 40); do
    nc -z 127.0.0.1 8545 2>/dev/null && break
    sleep 0.5
  done
  nc -z 127.0.0.1 8545 2>/dev/null || { echo "ganache failed to start; see out/ganache.log" >&2; exit 1; }
  echo "ganache up on :8545 (pid $GANACHE_PID)"
fi

mkdir -p out

banner "1-2. scan, search and face-verify"
"$PY" -m faceproof.cli search "$PROBE" --hint "$HINT" --use-cache -o out/evidence.json

banner "3. anchor on chain ($NETWORK)"
"$PY" -m faceproof.cli anchor out/evidence.json --network "$NETWORK" --uri "faceproof-demo"

banner "4. re-verify (fresh process, reads the chain back)"
"$PY" -m faceproof.cli verify out/evidence.json --network "$NETWORK"

banner "5. tamper with the record"
"$PY" -m faceproof.cli tamper out/evidence.json \
  --field page_url --value "https://example.com/forged-post" \
  -o out/evidence.tampered.json

banner "6. re-verify the tampered record (must fail)"
if "$PY" -m faceproof.cli verify out/evidence.tampered.json --network "$NETWORK"; then
  echo "UNEXPECTED: tampered record verified" >&2
  exit 1
else
  printf '\n\033[1;32mtampering correctly rejected\033[0m\n'
fi

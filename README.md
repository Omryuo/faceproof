# FaceProof

**Face scan → web/social search → blockchain-anchored verification.**

A command-line pipeline that takes a photograph of a face, searches the web for
pages showing the same person, confirms each hit by re-running face recognition
on the imagery it finds, and then writes a tamper-evident digest of the finding
to an EVM blockchain — so the discovery can be re-verified later and any edit to
it can be detected.

```
   probe image
        │
        ▼
 ┌──────────────────┐   YuNet detection + SFace 128-d embedding, all local
 │ 1. face scan     │   no image or biometric data leaves the machine here
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   SerpAPI (Google Lens / Yandex reverse image), or
 │ 2a. web search   │   key-free hint-assisted engines (Yahoo, DuckDuckGo)
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   download each candidate's images, detect + encode,
 │ 2b. face verify  │   keep only cosine ≥ 0.363 against the probe
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   keccak256(canonical-JSON(payload)) → FaceProofRegistry
 │ 3. anchor        │   only the 32-byte digest goes on chain
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   recompute the hash, look it up on chain,
 │ 4. re-verify     │   both must agree or the verdict is FAILED
 └──────────────────┘
```

The identity decision is made **locally by the face model, not by the search
engine's ranking**. A search engine returns leads; only a face that clears the
cosine threshold against the probe becomes a match, and only a match gets
anchored.

---

## What it actually produced

A real run against `examples/probe.jpg` (a Wikimedia portrait of Sundar Pichai,
a public figure — see [Ethics](#ethics-and-scope)):

```
Stage 2a  yahoo_web: 8 candidates (7 on social platforms)
Stage 2b  no     https://www.facebook.com/sundar.pichai/          (login wall, no image)
          no     https://www.instagram.com/sundarpichai/          (login wall, no image)
          no     https://www.linkedin.com/in/sundarpichai         (login wall, no image)
          MATCH  0.800  https://www.youtube.com/channel/UCvbxHc24sXU5fv8ynVD811Q
          MATCH  0.765  https://www.youtube.com/watch?v=duHhImuaZGU
          MATCH  0.768  https://x.com/sundarpichai
          MATCH  0.418  https://x.com/sundarpichai/status/2095181765082292334
          verified 4 / 8 checked at cosine >= 0.363

record hash  0xaac12ee80ad54f4fa06cf997b097e2ab15f2f62f0be992219258270390017f74
anchored     block 2, tx 0xaf4f4411…8d9ba78c, gas 137566
re-verify    VERIFIED
tampered     FAILED  (recomputed 0x93b74a43…5d66b542 ≠ stored)
```

Both `x.com/sundarpichai/status/…` and the YouTube video are individual social
media posts, found by search and confirmed by face match — not hardcoded.

---

## Quick Start (Single Command)

To set up dependencies, models, example images, and run the complete end-to-end pipeline in one single step:

```bash
./start.sh
# or
make run
```

You can also pass CLI subcommands directly to `./start.sh`, e.g.:

```bash
./start.sh scan examples/probe.jpg --annotate out/boxed.jpg
```

### Web dashboard

```bash
make ui           # or: ./start.sh ui   /   python -m faceproof.cli ui
```

Serves an interactive dashboard on <http://localhost:8080> (`--port` to change,
`--no-browser` to skip auto-opening). Upload a face or use the bundled probe,
watch all four stages run, and use the tamper sandbox to edit a field of the
signed record and see re-verification reject it.

The server binds `127.0.0.1` only. It shares one face engine and one chain
client across requests, so on the in-process `tester` network the whole
dashboard session shares a single chain — anchor in one request, re-verify in
the next. Restarting the server starts that chain over; use `--network local`
(with `make chain`) if you need anchors to outlive the process.

---

## Install & Manual Setup

Requires Python 3.10+. Node is only needed for the optional persistent local
chain.

```bash
git clone https://github.com/Omryuo/faceproof.git
cd faceproof
make setup        # venv + dependencies
make models       # ~38 MB of ONNX weights (OpenCV Model Zoo)
make examples     # demo photographs (not redistributed in this repo)
make test         # 40 tests
```

## Run the whole thing manually

```bash
./scripts/demo.sh
```

That runs scan → search → anchor → re-verify → tamper → re-verify-fails,
starting a local ganache chain on `:8545` if one isn't already up and stopping
it again afterwards.

The demo deliberately re-verifies from a **separate process**, so it needs a
chain whose state outlives one command. The zero-setup `tester` backend is
in-process and cannot do that — for a single-process run with no chain at all,
use:

```bash
python -m faceproof.cli run examples/probe.jpg --hint "Sundar Pichai" --network tester
```

## Run the stages individually

```bash
# 1. detect and encode a face
python -m faceproof.cli scan examples/probe.jpg --annotate out/boxed.jpg

# 2. search the web and face-verify the hits
python -m faceproof.cli search examples/probe.jpg --hint "Sundar Pichai" \
    -o out/evidence.json

#    with a SerpAPI key this becomes true reverse image search, no hint needed:
python -m faceproof.cli search examples/probe.jpg \
    --probe-url https://upload.wikimedia.org/.../portrait.jpg \
    --provider serpapi_lens -o out/evidence.json

# 3. anchor the evidence digest
python -m faceproof.cli anchor out/evidence.json --network local

# 4. re-verify it against the chain (exit 0 = VERIFIED, 1 = FAILED)
python -m faceproof.cli verify out/evidence.json --network local

# 5. show tamper detection
python -m faceproof.cli tamper out/evidence.json --field page_url \
    --value https://example.com/forged -o out/evidence.tampered.json
python -m faceproof.cli verify out/evidence.tampered.json --network local
```

---

## How each stage works

### 1. Face identification

OpenCV's **YuNet** detector (232 KB ONNX) finds faces and five landmarks;
**SFace** (37 MB ONNX) aligns each crop and produces a 128-dimensional
L2-normalised embedding. Identity comparison is cosine similarity, with the
Model Zoo's published operating point of **0.363** as the threshold. Everything
runs locally through `cv2.dnn`.

Measured separation on the bundled examples (`tests/test_face.py` asserts it):

| pair | cosine |
|---|---|
| probe vs. a different photo of the same person | **0.74 – 0.76** |
| probe vs. two other people | **0.08 – 0.16** |

### 2. Web / social media search

Providers all return *unverified candidates*, tried strongest-first:

| provider | key | kind |
|---|---|---|
| `serpapi_lens` | `SERPAPI_KEY` | true reverse image search (Google Lens) |
| `serpapi_yandex` | `SERPAPI_KEY` | true reverse image search (Yandex) |
| `yandex` | none | scripted reverse image search, best-effort |
| `ddg_images` | none | hint-assisted image search |
| `yahoo_web` | none | hint-assisted web search, social-restricted |
| `ddg_web` | none | hint-assisted web search, social-restricted |

Then `search/verify.py` does the work that matters: for every candidate it
fetches the page, pulls `og:image` / `twitter:image` / `<img>` sources,
downloads them, runs detection and encoding again, and keeps the page only if
some face on it clears the threshold. Results are ranked individual post →
profile → plain web page, so the pipeline surfaces an actual post where one
exists.

### 3. Blockchain anchoring

**Chain used: any EVM chain.** Three interchangeable backends:

| `--network` | what it is | setup |
|---|---|---|
| `tester` *(CLI default)* | in-process py-evm chain via `eth-tester` | none |
| `local` | JSON-RPC at `127.0.0.1:8545` (ganache/anvil) | `make chain` |
| `sepolia`, `amoy` | public testnets | `RPC_URL` + `PRIVATE_KEY` |

The demo output above is from **ganache, chain ID 1337**, which persists to
`.chaindata/` — so step 4 re-verifies in a *different process* by reading the
chain back, not from anything held in memory.

[`contracts/FaceProofRegistry.sol`](contracts/FaceProofRegistry.sol) (Solidity
0.8.24) stores `keccak256(canonical-JSON(payload)) → {submitter, timestamp,
blockNumber, uri}`. Re-anchoring an existing digest reverts, so the first
submission's timestamp cannot be rewritten.

**Only the 32-byte digest goes on chain.** No image, no embedding, no name, no
URL. That is deliberate: a public ledger is permanent and world-readable, and
writing biometric data to one would be far worse than the problem it solves. The
chain proves *when a record existed and that it has not changed since*; the
record itself stays local.

### 4. Re-verification

Two independent checks, both required:

1. **Internal** — the payload still hashes to the digest stored in the file.
   Editing any byte breaks this.
2. **On-chain** — that digest is present in the registry. Fabricating a fresh,
   internally-consistent record breaks this, because it was never anchored.

Canonicalisation is sorted-key, whitespace-free, UTF-8 JSON with `NaN` rejected,
so the same logical record hashes identically on any machine.

---

## Ethics and scope

Face search against social media is the same capability used for stalking,
doxxing and harassment. This is a hackathon pipeline, and it is built to be
demonstrated on faces where that risk does not apply:

- the bundled demo uses a **public figure**, with a portrait fetched from
  Wikimedia Commons rather than redistributed here;
- run it on **your own face**, or on someone who has agreed to it;
- the probe image is **never uploaded anywhere implicitly** — reverse-image
  providers need a public URL, and you must pass `--probe-url` yourself;
- **no biometric data is written to the blockchain**, only a digest.

Don't point it at strangers. In several jurisdictions (GDPR Art. 9, Illinois
BIPA) processing someone's face without consent is unlawful regardless of intent.

## Known limitations

**Search**
- Free search engines are the weak link. Bing, Brave, Ecosia, Mojeek and public
  SearXNG instances all block automated clients or serve decoy results; Yahoo
  was the most tolerant and is the key-free default. DuckDuckGo works but
  rate-limits to HTTP 202 after a handful of queries — `--use-cache` reuses a
  previous *real* search rather than re-hitting the engine, and cached runs are
  labelled as such in the evidence.
- The key-free providers are **hint-assisted**: they need `--hint "name"` and do
  a text search. True face-to-web reverse image search needs `SERPAPI_KEY`. The
  face match is genuine either way; only the candidate-generation step differs.
- Login-walled platforms (Instagram, Facebook, LinkedIn) serve no usable
  `og:image` to an anonymous client, so their pages are almost always rejected
  at verification even when the person really is there. This is visible in the
  demo output above. Recall is bounded by what is publicly fetchable.
- No robots.txt enforcement and no crawl-rate limiting beyond timeouts.

**Face recognition**
- SFace at 0.363 is a good general operating point, not a forensic one. Expect
  false negatives on profile, low-light and heavily-compressed images, and
  known demographic accuracy differences across face-recognition models. A
  0.42 match is a lead, not proof of identity.
- Only the largest face in the probe is used.

**Blockchain**
- The chain proves a digest existed at a block time. It says nothing about
  whether the *finding* was correct — garbage in, notarised garbage out.
- `tester` state lives and dies with a single process, so `anchor` and `verify`
  as two separate commands will not see each other's writes on it — use
  `--network local`, or the one-shot `run` command. `local` persists to
  `.chaindata/`. Neither is publicly auditable: for a third party to verify
  independently you need a public testnet or mainnet, which needs a funded key.
  Nothing in this project has been anchored to a public chain.
- Anchoring is not private: anyone watching the chain learns that *some* record
  was anchored, and when.

**General**
- Not audited. No rate limiting, no retry budget on page fetches, no
  provenance for the probe image itself (nothing proves the probe wasn't
  synthetic or edited before scanning).

## Layout

```
faceproof/
  face.py              detection + embedding (YuNet, SFace)
  evidence.py          canonical JSON, keccak256, integrity checks
  pipeline.py          stage orchestration
  cli.py               command line interface
  search/
    base.py            candidate model, platform + post classification
    serpapi.py         reverse image search (Google Lens, Yandex)
    yandex.py          scripted reverse image search, no key
    yahoo.py  ddg.py   key-free hint-assisted engines
    verify.py          the face-match gate that promotes candidates
    cache.py           on-disk cache of real prior searches
  chain/
    compile.py         solc build, committed artifact as fast path
    client.py          deploy / anchor / lookup across three backends
contracts/FaceProofRegistry.sol
tests/                 40 tests: hashing, identity separation, on-chain flow
```

## Licence

MIT. The ONNX models are from the [OpenCV Model Zoo](https://github.com/opencv/opencv_zoo)
under their own licences; demo photographs are from Wikimedia Commons under
their respective licences and are downloaded at runtime, not redistributed here.

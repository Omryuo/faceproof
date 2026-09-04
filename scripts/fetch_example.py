#!/usr/bin/env python3
"""Download the demo probe images from Wikimedia Commons.

Not committed to the repo: they are third-party photographs, so the repo links
to them rather than redistributing them. All are of a public figure, which is
the deliberate choice for a demo of face search (see README, 'Ethics').
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

DEST = Path(__file__).resolve().parent.parent / "examples"
UA = "faceproof/0.1 (https://github.com/Omryuo/faceproof; hackathon demo)"

IMAGES = {
    # probe: the face we scan
    "probe.jpg": "https://upload.wikimedia.org/wikipedia/commons/c/c3/Sundar_Pichai_-_2023_%28cropped%29.jpg",
    # same identity, different photograph -- used by the recogniser tests
    "same_person.jpg": "https://upload.wikimedia.org/wikipedia/commons/b/b3/Sundar_Pichai_%28cropped%29.jpg",
    # different identities -- negative controls
    "other_person_1.jpg": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/78/MS-Exec-Nadella-Satya-2017-08-31-22_%28cropped%29.jpg/960px-MS-Exec-Nadella-Satya-2017-08-31-22_%28cropped%29.jpg",
    "other_person_2.jpg": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/SXSW-2024-alih-OB7A0861-Lisa_Su_%28cropped_2%29.jpg/960px-SXSW-2024-alih-OB7A0861-Lisa_Su_%28cropped_2%29.jpg",
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url in IMAGES.items():
        target = DEST / name
        if target.exists() and target.stat().st_size > 10_000:
            print(f"have {name}")
            continue
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        if r.status_code != 200:
            print(f"FAILED {name}: HTTP {r.status_code}", file=sys.stderr)
            return 1
        target.write_bytes(r.content)
        print(f"ok   {name} ({len(r.content):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

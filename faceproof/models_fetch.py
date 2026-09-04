"""Download the OpenCV Model Zoo ONNX weights (kept out of git)."""

from __future__ import annotations

from pathlib import Path

import requests

BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
DEST = Path(__file__).resolve().parent.parent / "models"


def fetch_all(console=None):
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        target = DEST / name
        if target.exists() and target.stat().st_size > 100_000:
            if console:
                console.print(f"[dim]have[/dim] {name}")
            continue
        if console:
            console.print(f"downloading {name} ...")
        r = requests.get(url, timeout=120, stream=True)
        r.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
        if console:
            console.print(f"[green]ok[/green] {name} ({target.stat().st_size:,} bytes)")
    return DEST

"""Face detection and embedding.

Backend: OpenCV YuNet (detection, 5-point landmarks) + SFace (128-d embedding).
Both are ONNX models from the OpenCV Model Zoo, run locally through cv2.dnn --
no face data leaves the machine during this stage.
"""

from __future__ import annotations

import hashlib
import io
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
YUNET = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE = MODELS_DIR / "face_recognition_sface_2021dec.onnx"

# OpenCV Model Zoo's published operating point for SFace cosine similarity.
SAME_IDENTITY_COSINE = 0.363

MAX_SIDE = 1600  # downscale huge images before detection


class ModelsMissing(RuntimeError):
    pass


@dataclass
class Face:
    """A single detected face within one image."""

    bbox: tuple[int, int, int, int]  # x, y, w, h
    score: float
    landmarks: list[tuple[int, int]] = field(default_factory=list)
    _row: np.ndarray | None = None

    @property
    def area(self) -> int:
        return self.bbox[2] * self.bbox[3]


@dataclass
class FaceEncoding:
    """An L2-normalised 128-d identity vector plus provenance."""

    vector: np.ndarray
    face: Face
    image_sha256: str
    image_size: tuple[int, int]

    @property
    def embedding_sha256(self) -> str:
        """Stable digest of the embedding, quantised so tiny float noise
        does not change the hash. Goes into the on-chain record."""
        q = np.round(self.vector.astype(np.float64), 6)
        return hashlib.sha256(q.tobytes()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "embedding_sha256": self.embedding_sha256,
            "embedding_dim": int(self.vector.shape[0]),
            "detector": "yunet_2023mar",
            "recogniser": "sface_2021dec",
            "detection_score": round(float(self.face.score), 4),
            "bbox": [int(v) for v in self.face.bbox],
            "image_sha256": self.image_sha256,
            "image_size": list(self.image_size),
        }


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two embeddings (both are already L2-normalised)."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def load_image(data: bytes) -> np.ndarray:
    """Decode bytes to a BGR array, honouring EXIF orientation."""
    try:
        from PIL import Image, ImageOps

        pil = Image.open(io.BytesIO(data))
        pil = ImageOps.exif_transpose(pil).convert("RGB")
        rgb = np.asarray(pil)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("could not decode image")
        return img


def _fit(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= MAX_SIDE:
        return img
    s = MAX_SIDE / longest
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


class FaceEngine:
    """Lazily-loaded detector + recogniser pair.

    cv2's FaceDetectorYN carries per-instance state: `setInputSize` and
    `detect` are two calls that must not be interleaved with another thread's
    pair, or OpenCV aborts with a shape assertion. The verifier runs candidate
    checks on a thread pool and the web server is threaded, so both share one
    engine -- hence the lock around every native call. It is held only for the
    cv2 work, so image decoding and network I/O still overlap freely.
    """

    def __init__(self, det_threshold: float = 0.7):
        if not YUNET.exists() or not SFACE.exists():
            raise ModelsMissing(
                f"ONNX models not found in {MODELS_DIR}. "
                "Run: python -m faceproof.cli fetch-models"
            )
        self.det_threshold = det_threshold
        self._lock = threading.Lock()
        self._detector = cv2.FaceDetectorYN.create(
            str(YUNET), "", (320, 320), det_threshold, 0.3, 5000
        )
        self._recogniser = cv2.FaceRecognizerSF.create(str(SFACE), "")

    def detect(self, img: np.ndarray) -> tuple[np.ndarray, list[Face]]:
        img = _fit(img)
        h, w = img.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, raw = self._detector.detect(img)
        faces: list[Face] = []
        if raw is None:
            return img, faces
        for row in raw:
            x, y, bw, bh = (int(v) for v in row[:4])
            lms = [(int(row[4 + 2 * i]), int(row[5 + 2 * i])) for i in range(5)]
            faces.append(Face((x, y, bw, bh), float(row[-1]), lms, row))
        faces.sort(key=lambda f: f.area, reverse=True)
        return img, faces

    def encode_face(self, img: np.ndarray, face: Face) -> np.ndarray:
        with self._lock:
            aligned = self._recogniser.alignCrop(img, face._row)
            vec = self._recogniser.feature(aligned).flatten().astype(np.float32)
        n = np.linalg.norm(vec)
        return vec / n if n else vec

    def encode_bytes(self, data: bytes) -> list[FaceEncoding]:
        """Detect and encode every face in an image."""
        img = load_image(data)
        sha = hashlib.sha256(data).hexdigest()
        img, faces = self.detect(img)
        h, w = img.shape[:2]
        return [
            FaceEncoding(self.encode_face(img, f), f, sha, (w, h)) for f in faces
        ]

    def encode_primary(self, data: bytes) -> FaceEncoding | None:
        """Encode the single largest face -- the 'probe' for a scan."""
        encs = self.encode_bytes(data)
        return encs[0] if encs else None

    def _draw(self, data: bytes) -> tuple[np.ndarray, int]:
        img = load_image(data)
        img, faces = self.detect(img)
        for i, f in enumerate(faces):
            x, y, w, h = f.bbox
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 220, 0), 2)
            cv2.putText(
                img, f"#{i} {f.score:.2f}", (x, max(16, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 0), 2,
            )
            for px, py in f.landmarks:
                cv2.circle(img, (px, py), 2, (0, 128, 255), -1)
        return img, len(faces)

    def annotate_bytes(self, data: bytes) -> tuple[bytes, int]:
        """Return a JPEG of the image with detection boxes drawn, plus the face
        count. Nothing touches disk, so concurrent callers cannot overwrite each
        other's output -- which a shared temp file would allow."""
        img, n = self._draw(data)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            raise ValueError("failed to encode annotated image")
        return buf.tobytes(), n

    def annotate(self, data: bytes, out_path: Path) -> int:
        """Write a copy of the image with detection boxes drawn. Returns face count."""
        encoded, n = self.annotate_bytes(data)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(encoded)
        return n

import pytest
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MODELS = Path(__file__).resolve().parent.parent / "models"


def _have_models() -> bool:
    return all(
        (MODELS / n).exists()
        for n in ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx")
    )


@pytest.fixture(scope="session")
def engine():
    if not _have_models():
        pytest.skip("ONNX models missing -- run `python -m faceproof.cli fetch-models`")
    from faceproof.face import FaceEngine

    return FaceEngine()


@pytest.fixture(scope="session")
def images():
    needed = ["probe.jpg", "same_person.jpg", "other_person_1.jpg", "other_person_2.jpg"]
    if not all((EXAMPLES / n).exists() for n in needed):
        pytest.skip("example images missing -- run `python scripts/fetch_example.py`")
    return {n.split(".")[0]: (EXAMPLES / n).read_bytes() for n in needed}

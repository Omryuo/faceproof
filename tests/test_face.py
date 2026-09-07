"""The recogniser must actually discriminate identities, not just return vectors."""

import numpy as np
import pytest

from faceproof.face import SAME_IDENTITY_COSINE, cosine


def test_detects_exactly_one_face_in_the_probe(engine, images):
    encs = engine.encode_bytes(images["probe"])
    assert len(encs) == 1
    assert encs[0].face.score > 0.7


def test_embeddings_are_unit_length_128d(engine, images):
    enc = engine.encode_primary(images["probe"])
    assert enc.vector.shape == (128,)
    assert np.isclose(np.linalg.norm(enc.vector), 1.0, atol=1e-4)


def test_encoding_is_deterministic(engine, images):
    a = engine.encode_primary(images["probe"])
    b = engine.encode_primary(images["probe"])
    assert a.embedding_sha256 == b.embedding_sha256


def test_same_person_different_photo_clears_threshold(engine, images):
    probe = engine.encode_primary(images["probe"]).vector
    same = engine.encode_primary(images["same_person"]).vector
    assert cosine(probe, same) > SAME_IDENTITY_COSINE


@pytest.mark.parametrize("other", ["other_person_1", "other_person_2"])
def test_different_people_fall_below_threshold(engine, images, other):
    probe = engine.encode_primary(images["probe"]).vector
    neg = engine.encode_primary(images[other]).vector
    assert cosine(probe, neg) < SAME_IDENTITY_COSINE


def test_separation_margin_is_wide(engine, images):
    """Positives and negatives should be well separated, not marginal."""
    probe = engine.encode_primary(images["probe"]).vector
    pos = cosine(probe, engine.encode_primary(images["same_person"]).vector)
    negs = [
        cosine(probe, engine.encode_primary(images[o]).vector)
        for o in ("other_person_1", "other_person_2")
    ]
    assert pos - max(negs) > 0.3


def test_no_face_returns_none(engine):
    import cv2

    blank = np.full((240, 240, 3), 200, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", blank)
    assert ok
    assert engine.encode_primary(buf.tobytes()) is None


def test_concurrent_encoding_is_safe_and_stable(engine, images):
    """One FaceEngine is shared by the verifier's thread pool and by the
    threaded web server. cv2's detector is stateful (setInputSize + detect),
    so without a lock this aborts with an OpenCV shape assertion or returns
    embeddings computed at another thread's input size."""
    from concurrent.futures import ThreadPoolExecutor

    baseline = {
        name: engine.encode_primary(images[name]).embedding_sha256
        for name in ("probe", "same_person")
    }

    def work(i):
        name = "probe" if i % 2 == 0 else "same_person"
        enc = engine.encode_primary(images[name])
        assert enc is not None, "face vanished under concurrency"
        return enc.embedding_sha256 == baseline[name]

    for _ in range(3):
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert all(pool.map(work, range(24)))


def test_annotate_bytes_returns_the_callers_own_image(engine, images):
    """The server annotated through a fixed temp file, so two concurrent
    requests could hand each other the wrong picture."""
    from concurrent.futures import ThreadPoolExecutor

    expected = {
        name: engine.annotate_bytes(images[name])[0]
        for name in ("probe", "other_person_1")
    }

    def work(i):
        name = "probe" if i % 2 == 0 else "other_person_1"
        got, count = engine.annotate_bytes(images[name])
        return got == expected[name] and count == 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(work, range(16)))


def test_annotate_writes_the_same_bytes_it_returns(engine, images, tmp_path):
    out = tmp_path / "boxed.jpg"
    n = engine.annotate(images["probe"], out)
    encoded, n2 = engine.annotate_bytes(images["probe"])
    assert n == n2 == 1
    assert out.read_bytes() == encoded

"""Unit tests for vision.storage — local image persistence + path-traversal guard."""
from __future__ import annotations

import os

import pytest

from vision import storage


@pytest.fixture
def tmp_vision_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_VISION_DIR", str(tmp_path))
    return tmp_path


def test_save_image_writes_file_and_returns_public_url(tmp_vision_dir):
    abs_path, url = storage.save_image(10, "morning", b"\xff\xd8\xff data", "image/jpeg")
    assert os.path.exists(abs_path)
    assert open(abs_path, "rb").read() == b"\xff\xd8\xff data"
    assert url.startswith("/kirana/vision/image/")
    assert url.endswith(".jpg")
    # store id partitions the path
    assert "/10/" in url.replace(os.sep, "/")


def test_resolve_url_round_trips_a_saved_image(tmp_vision_dir):
    abs_path, url = storage.save_image(10, "evening", b"data", "image/png")
    resolved = storage.resolve_url(url)
    assert resolved is not None
    assert os.path.samefile(resolved, abs_path)
    assert url.endswith(".png")


def test_resolve_url_rejects_path_traversal(tmp_vision_dir):
    assert storage.resolve_url("/kirana/vision/image/../../../etc/passwd") is None


def test_resolve_url_rejects_foreign_prefix(tmp_vision_dir):
    assert storage.resolve_url("/some/other/path.jpg") is None


def test_resolve_url_returns_none_for_missing_file(tmp_vision_dir):
    assert storage.resolve_url("/kirana/vision/image/10/2026-01-01/nope.jpg") is None


def test_ext_for_content_type():
    assert storage._ext_for("image/png").endswith("png")
    assert storage._ext_for("image/webp").endswith("webp")
    assert storage._ext_for("image/jpeg") == ".jpg"
    assert storage._ext_for(None) == ".jpg"
    assert storage._ext_for("application/octet-stream") == ".jpg"

"""Phase 10 web /chat 多模态 payload 校验单测。"""

from __future__ import annotations

import base64

import pytest

from sanshiliu.channels.web.handlers import (
    MultimodalValidationError,
    _build_multimodal_content,
    _validate_data_uri,
)


def _make_data_uri(mime: str = "image/jpeg", payload: bytes = b"x" * 1024) -> str:
    b64 = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ────────── _validate_data_uri ──────────

def test_valid_jpeg() -> None:
    uri = _make_data_uri("image/jpeg", b"hello")
    norm, size = _validate_data_uri(uri, max_decoded_bytes=1024)
    assert norm.startswith("data:image/jpeg;base64,")
    assert size == len(b"hello")


def test_jpg_normalized_to_jpeg() -> None:
    uri = _make_data_uri("image/jpg", b"hi")
    norm, _ = _validate_data_uri(uri, max_decoded_bytes=1024)
    assert "image/jpeg" in norm
    assert "image/jpg" not in norm


def test_png_ok() -> None:
    uri = _make_data_uri("image/png", b"png")
    _, _ = _validate_data_uri(uri, max_decoded_bytes=1024)


def test_webp_ok() -> None:
    uri = _make_data_uri("image/webp", b"webp")
    _, _ = _validate_data_uri(uri, max_decoded_bytes=1024)


def test_rejects_non_data_uri() -> None:
    with pytest.raises(MultimodalValidationError):
        _validate_data_uri("https://example.com/x.png", max_decoded_bytes=1024)


def test_rejects_unsupported_mime() -> None:
    uri = _make_data_uri("image/gif", b"gif")
    with pytest.raises(MultimodalValidationError):
        _validate_data_uri(uri, max_decoded_bytes=1024)


def test_rejects_oversized_decoded() -> None:
    uri = _make_data_uri("image/jpeg", b"x" * 200)
    with pytest.raises(MultimodalValidationError, match="too large"):
        _validate_data_uri(uri, max_decoded_bytes=100)


def test_rejects_invalid_base64() -> None:
    with pytest.raises(MultimodalValidationError):
        _validate_data_uri("data:image/jpeg;base64,!!not-base64@@", max_decoded_bytes=1024)


# ────────── _build_multimodal_content ──────────

def test_text_only_returns_str() -> None:
    content = _build_multimodal_content("你好", [], max_images=4, max_image_bytes=1024)
    assert content == "你好"


def test_text_with_images_returns_list() -> None:
    uri = _make_data_uri()
    content = _build_multimodal_content("看图", [uri], max_images=4, max_image_bytes=4096)
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看图"}
    assert content[1]["type"] == "image_url"


def test_image_only_no_text_part() -> None:
    """q 为空白 + 有图：只产 image_url part，不放空 text part。"""
    uri = _make_data_uri()
    content = _build_multimodal_content("   ", [uri], max_images=4, max_image_bytes=4096)
    assert isinstance(content, list)
    assert all(p["type"] == "image_url" for p in content)


def test_too_many_images() -> None:
    uri = _make_data_uri()
    with pytest.raises(MultimodalValidationError, match="too many images"):
        _build_multimodal_content(
            "q", [uri] * 5, max_images=4, max_image_bytes=4096,
        )

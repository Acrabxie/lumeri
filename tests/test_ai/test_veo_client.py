from __future__ import annotations

import pytest

from gemia.ai.veo_client import _decode_json_response


def test_decode_json_response_rejects_empty_body() -> None:
    with pytest.raises(RuntimeError, match="empty response"):
        _decode_json_response(b"", url="https://openrouter.ai/api/v1/video/generations", method="POST")


def test_decode_json_response_rejects_non_json_body() -> None:
    with pytest.raises(RuntimeError, match="non-JSON response"):
        _decode_json_response(b"<html>not json</html>", url="https://openrouter.ai/api/v1/video/generations", method="POST")


def test_decode_json_response_requires_object() -> None:
    with pytest.raises(RuntimeError, match="expected object"):
        _decode_json_response(b"[]", url="https://openrouter.ai/api/v1/video/generations", method="POST")


def test_decode_json_response_returns_object() -> None:
    assert _decode_json_response(b'{"id":"job_1"}', url="url", method="POST") == {"id": "job_1"}

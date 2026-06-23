import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from api_util.call_nametag import process_data  # noqa: E402


def test_process_data_smoke():
    result = process_data({"text": "Hello"})
    assert result == {"text": "Hello"}

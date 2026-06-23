import pytest

pytest.importorskip("transformers")

from api_util.call_udpipe import run_udpipe_like_workflow  # noqa: E402


def test_udpipe_smoke():
    # Keep this lightweight and patch the heavy model call if needed.
    assert callable(run_udpipe_like_workflow)

"""
tests/test_udpipe.py – Unit tests for api_util/call_udpipe.py.

NOTE: this file replaces the stray ``tests/tets_udpipe.py`` (a typo — pytest's
``python_files = test_*.py`` never collected it, so its smoke check never ran).
Delete ``tets_udpipe.py`` when adding this file.

Covers the HTTP layer (robust session, single-chunk POST) with a mocked session
and the CoNLL-U merge/renumber logic. Network is never touched.
"""

from unittest.mock import MagicMock

import pytest

requests = pytest.importorskip("requests")

from requests.adapters import HTTPAdapter  # noqa: E402

from api_util import call_udpipe  # noqa: E402
from api_util.call_udpipe import (  # noqa: E402
    UDPIPE_URL,
    get_robust_session,
    merge_conllu_chunks,
    process_chunk,
)


def test_get_robust_session_mounts_retry_adapters():
    session = get_robust_session(retries=4)
    assert isinstance(session, requests.Session)
    adapter = session.get_adapter("https://lindat.mff.cuni.cz")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total == 4
    assert 429 in adapter.max_retries.status_forcelist


def test_process_chunk_posts_expected_payload_and_returns_result():
    session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"result": "# text\n1\tword\n"}
    session.post.return_value = resp

    out = process_chunk(session, "hello world", "czech-pdt", timeout=30)

    assert out == "# text\n1\tword\n"
    resp.raise_for_status.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0] == UDPIPE_URL
    assert kwargs["data"]["model"] == "czech-pdt"
    assert kwargs["data"]["data"] == "hello world"
    assert kwargs["timeout"] == 30


def test_process_chunk_empty_result_returns_empty_string():
    session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"result": ""}
    session.post.return_value = resp
    assert process_chunk(session, "x", "m", timeout=10) == ""


def test_merge_conllu_chunks_renumbers_and_marks_page_break():
    chunk1 = "# sent_id = 1\n1\tA\n\n# sent_id = 2\n1\tB\n"
    chunk2 = "# sent_id = 1\n1\tC\n"
    merged = merge_conllu_chunks([chunk1, chunk2])
    assert "# sent_id = 1" in merged  # first chunk keeps its numbering
    assert "# sent_id = 3" in merged  # second chunk offset by chunk1's max (2)
    assert "# page_break = true" in merged  # inserted when a chunk restarts at 1


def test_run_udpipe_like_workflow_is_callable():
    # Preserves the intent of the original (uncollected) smoke check.
    assert callable(call_udpipe.run_udpipe_like_workflow)

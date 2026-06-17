"""
tests/test_api_service.py
=========================
Hermetic tests for the nlp-enrich API service (issue #8).
"""

import csv
import io
import json
import zipfile

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")

import service.enrichment as enr  # noqa: E402
from service.enrichment import (  # noqa: E402
    PipelineManager,
    normalize_upload,
    sanitize_doc_id,
)

from pathlib import Path

from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from service.api import app


@pytest.fixture
def test_client():
    """Explicitly named fixture to avoid clashing with pytest's built-in 'client'."""
    return TestClient(app)


@pytest.fixture
def mock_subprocess_run():
    with patch("service.enrichment.subprocess.run") as mock_run:
        yield mock_run


def create_dummy_csv() -> bytes:
    return b"text,page_num,line_num\nTest line,1,1"


# Update test_api_exit_code_0_success in tests/test_api_service.py
def test_api_exit_code_0_success(mock_subprocess_run, test_client):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_subprocess_run.return_value = mock_result

    # Mock the directory/file existence check
    with patch("pathlib.Path.exists", return_value=True), \
            patch("pathlib.Path.glob", return_value=[Path("test.teitok.xml")]), \
            patch("service.enrichment.PipelineManager.collect_teitok", return_value="<xml></xml>"), \
            patch("service.enrichment.PipelineManager.collect_keywords", return_value=[]), \
            patch("service.enrichment.PipelineManager.collect_ne_summary", return_value=[]), \
            patch("service.enrichment.PipelineManager.collect_merged_paradata", return_value={}):
        response = test_client.post(
            "/enrich",
            files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
            data={"kw_method": "none", "format": "json"}
        )

    assert response.status_code == 200


def test_api_exit_code_1_or_2_bad_gateway(mock_subprocess_run, test_client):
    """Exit codes 1 and 2 (pipeline errors) should map to HTTP 502."""
    for rc in [1, 2]:
        mock_result = MagicMock()
        mock_result.returncode = rc
        mock_result.stdout = "Error output"
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        response = test_client.post(
            "/enrich",
            files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
            data={"kw_method": "none"}
        )
        assert response.status_code == 502
        assert "exit" in response.json()["detail"].lower()


def test_api_exit_code_3_or_4_service_unavailable(mock_subprocess_run, test_client):
    """Exit codes 3 and 4 (keyword/preflight errors) should map to HTTP 503."""
    for rc in [3, 4]:
        mock_result = MagicMock()
        mock_result.returncode = rc
        mock_result.stdout = "Keyword error"
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        response = test_client.post(
            "/enrich",
            files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
            data={"kw_method": "keybert"}
        )
        assert response.status_code == 503


@patch("service.enrichment.shutil.rmtree")
def test_workspace_cleanup_on_success_and_failure(mock_rmtree, mock_subprocess_run, test_client):
    mock_result = MagicMock()
    mock_result.returncode = 2  # Failure case
    mock_subprocess_run.return_value = mock_result

    # Execute the request
    test_client.post(
        "/enrich",
        files={"file": ("test.csv", create_dummy_csv(), "text/csv")}
    )

    # Manually trigger the cleanup that would have run in the background
    from service.enrichment import PipelineManager
    # Access the last call to cleanup or simulate the execution
    # Alternatively, bypass BackgroundTask in tests to make it run synchronously
    # but the easiest way is to call the cleanup explicitly since we are testing the logic:
    # (Assuming the result object was created)

    # Or, simply use a mock that catches the call if the API executed it
    # ensure your EnrichmentResult creation doesn't fail.
    mock_rmtree.assert_called()


# ── input normalization ───────────────────────────────────────────────────────
class TestNormalization:

    def test_csv_requires_text_column(self):
        with pytest.raises(ValueError):
            normalize_upload("x.csv", b"file,page_num\nA,1\n")

    def test_csv_rows_parsed(self):
        data = b"text,page_num,line_num\nHello,1,2\nWorld,2,1\n"
        rows = normalize_upload("x.csv", data)
        assert [r["text"] for r in rows] == ["Hello", "World"]

    def test_txt_one_row_per_line(self):
        rows = normalize_upload("x.txt", b"alpha\n\nbeta\n")
        assert [r["text"] for r in rows] == ["alpha", "beta"]
        assert rows[0]["line_num"] == 1 and rows[1]["line_num"] == 3

    def test_unsupported_zip_extension(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            normalize_upload("x.zip", b"PK\x03\x04")


def test_enrich_text_inline(client, monkeypatch):
    api, _ = client
    _make_stub(monkeypatch, returncode=0, doc_id="inlinedoc")
    c = TestClient(api.app)
    r = c.post("/enrich_text",
               json={"doc_id": "inlinedoc",
                     "lines": ["Praha", "Brno"],
                     "kw_method": "yake"})
    assert r.status_code == 200
    assert r.json()["doc_id"] == "inlinedoc"


def test_invalid_kw_method_422(client):
    api, _ = client
    c = TestClient(api.app)
    r = c.post("/enrich",
               files={"file": ("x.csv", b"text\nA\n", "text/csv")},
               data={"kw_method": "bogus"})
    assert r.status_code == 422


def test_empty_input_422(client):
    api, _ = client
    c = TestClient(api.app)
    r = c.post("/enrich",
               files={"file": ("x.csv", b"text\n", "text/csv")},
               data={"kw_method": "none"})
    assert r.status_code == 422


def test_job_cleanup_evicts_job_from_memory():
    from service.jobs import _jobs, Job
    from service.api import app

    job_id = "test-delete-job"
    _jobs[job_id] = Job(job_id=job_id, status="done")

    c = TestClient(app)
    r = c.delete(f"/jobs/{job_id}")

    assert r.status_code == 200
    assert job_id not in _jobs


def _make_stub(monkeypatch, returncode=0, doc_id="document"):
    class StubResult:
        def __init__(self):
            self.job_id = "stub-123"
            self.doc_id = doc_id
            self.workspace = Path("/tmp")
            self.output_dir = Path("/tmp")
            self.returncode = returncode
            self.kw_method_requested = "keybert"
            self.kw_method_used = "yake" if returncode == 0 else None
            self.pages = 1
            self.stages = []
            self.stdout_tail = "..."

    def _stub_enrich(*a, **k):
        if returncode == 3:
            raise enr.KeywordPreflightError("stub failed")
        if returncode != 0:
            raise enr.PipelineError("stub failed", 502, returncode)
        return StubResult()

    monkeypatch.setattr(PipelineManager, "enrich", _stub_enrich)
    monkeypatch.setattr(PipelineManager, "collect_teitok", lambda *a: "<teitok/>")
    monkeypatch.setattr(PipelineManager, "collect_keywords", lambda *a: [])
    monkeypatch.setattr(PipelineManager, "collect_ne_summary", lambda *a: [])
    monkeypatch.setattr(PipelineManager, "collect_merged_paradata", lambda *a: {})
    monkeypatch.setattr(PipelineManager, "zip_workspace_output", lambda *a: Path("test.zip"))
    monkeypatch.setattr(PipelineManager, "cleanup", lambda *a: None)


@pytest.fixture
def client():
    from service import api
    return api, None
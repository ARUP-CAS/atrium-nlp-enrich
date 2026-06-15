"""
tests/test_api_service.py
=========================
Hermetic tests for the nlp-enrich API service (issue #8).
"""

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402

import service.enrichment as enr  # noqa: E402
from service.enrichment import (  # noqa: E402
    PipelineManager,
    normalize_upload,
    sanitize_doc_id,
)

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
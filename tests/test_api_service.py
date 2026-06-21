"""
tests/test_api_service.py
=========================
Hermetic tests for the nlp-enrich API service (issue #8).
"""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")

from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import service.enrichment as enr  # noqa: E402
from service.api import app  # noqa: E402
from service.enrichment import (  # noqa: E402
    PipelineManager,
    _detect_kw_method_used,
    normalize_upload,
)


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


# ── exit-code → HTTP mapping ──────────────────────────────────────────────────


def test_api_exit_code_0_success(mock_subprocess_run, test_client):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    mock_subprocess_run.return_value = mock_result

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.glob", return_value=[Path("test.teitok.xml")]),
        patch("service.enrichment.PipelineManager.collect_teitok", return_value="<xml></xml>"),
        patch("service.enrichment.PipelineManager.collect_keywords", return_value=[]),
        patch("service.enrichment.PipelineManager.collect_ne_summary", return_value=[]),
        patch("service.enrichment.PipelineManager.collect_merged_paradata", return_value={}),
    ):
        response = test_client.post(
            "/enrich",
            files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
            data={"kw_method": "none", "format": "json"},
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
            data={"kw_method": "none"},
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
            data={"kw_method": "keybert"},
        )
        assert response.status_code == 503


# ── F-S2: workspace cleanup ───────────────────────────────────────────────────


@patch("service.enrichment.shutil.rmtree")
def test_workspace_cleanup_on_failure(mock_rmtree, mock_subprocess_run, test_client):
    """F-S2: workspace must be deleted when the pipeline returns a non-zero exit code.

    The bug this pins: enrich() raised PipelineError before building an
    EnrichmentResult, so cleanup() was never called and the workspace leaked.
    The fix wraps the whole post-mkdir body in try/except and deletes on any
    exception (unless _KEEP_WORKSPACES is set).
    """
    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = ""
    mock_subprocess_run.return_value = mock_result

    test_client.post(
        "/enrich",
        files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
        data={"kw_method": "none"},
    )

    mock_rmtree.assert_called()


@patch("service.enrichment.shutil.rmtree")
def test_workspace_cleanup_on_success(mock_rmtree, mock_subprocess_run, test_client):
    """F-S2: workspace must also be deleted on a successful run via cleanup()."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    mock_subprocess_run.return_value = mock_result

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.glob", return_value=[Path("test.teitok.xml")]),
        patch("service.enrichment.PipelineManager.collect_teitok", return_value="<xml/>"),
        patch("service.enrichment.PipelineManager.collect_keywords", return_value=[]),
        patch("service.enrichment.PipelineManager.collect_ne_summary", return_value=[]),
        patch("service.enrichment.PipelineManager.collect_merged_paradata", return_value={}),
    ):
        test_client.post(
            "/enrich",
            files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
            data={"kw_method": "none", "format": "json"},
        )

    mock_rmtree.assert_called()


# ── concurrency guard ─────────────────────────────────────────────────────────


def test_concurrency_limit_returns_429(test_client):
    """Posting to /enrich while the semaphore is locked must return HTTP 429.

    Pins api.py:133-134: the semaphore.locked() early-exit path.
    """
    import service.api as api_module

    with patch.object(api_module._semaphore, "locked", return_value=True):
        response = test_client.post(
            "/enrich",
            files={"file": ("test.csv", create_dummy_csv(), "text/csv")},
            data={"kw_method": "none"},
        )

    assert response.status_code == 429


# ── _detect_kw_method_used ────────────────────────────────────────────────────


def test_detect_kw_method_used_degradation(tmp_path):
    """_detect_kw_method_used reports the correct backend from output dir layout.

    Tests the keybert → yake → legacy → none precedence and the
    keybert-takes-priority-when-both-present case.
    """
    # Case 1: yake only — should return "yake"
    yake_dir = tmp_path / "yake_only"
    yake_dir.mkdir()
    (yake_dir / "KW_PER_DOC_Y").mkdir()
    (yake_dir / "KW_PER_DOC_Y" / "doc_keywords.csv").write_text("keyword,score\n")
    assert _detect_kw_method_used(yake_dir) == "yake"

    # Case 2: keybert and yake present — keybert wins
    kb_dir = tmp_path / "both"
    kb_dir.mkdir()
    (kb_dir / "KW_PER_DOC_KB").mkdir()
    (kb_dir / "KW_PER_DOC_KB" / "doc_keywords.csv").write_text("keyword,score\n")
    (kb_dir / "KW_PER_DOC_Y").mkdir()
    (kb_dir / "KW_PER_DOC_Y" / "doc_keywords.csv").write_text("keyword,score\n")
    assert _detect_kw_method_used(kb_dir) == "keybert"

    # Case 3: legacy only
    leg_dir = tmp_path / "legacy_only"
    leg_dir.mkdir()
    (leg_dir / "KW_PER_DOC_L").mkdir()
    (leg_dir / "KW_PER_DOC_L" / "doc_keywords.csv").write_text("keyword,score\n")
    assert _detect_kw_method_used(leg_dir) == "legacy"

    # Case 4: no keyword output at all
    empty_dir = tmp_path / "empty_out"
    empty_dir.mkdir()
    assert _detect_kw_method_used(empty_dir) == "none"

    # Case 5: subdirectory exists but contains no *_keywords.csv files
    ghost_dir = tmp_path / "ghost"
    ghost_dir.mkdir()
    (ghost_dir / "KW_PER_DOC_Y").mkdir()
    # no CSV files inside
    assert _detect_kw_method_used(ghost_dir) == "none"


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
    r = c.post(
        "/enrich_text",
        json={"doc_id": "inlinedoc", "lines": ["Praha", "Brno"], "kw_method": "yake"},
    )
    assert r.status_code == 200
    assert r.json()["doc_id"] == "inlinedoc"


def test_invalid_kw_method_422(client):
    api, _ = client
    c = TestClient(api.app)
    r = c.post(
        "/enrich", files={"file": ("x.csv", b"text\nA\n", "text/csv")}, data={"kw_method": "bogus"}
    )
    assert r.status_code == 422


def test_empty_input_422(client):
    api, _ = client
    c = TestClient(api.app)
    r = c.post(
        "/enrich", files={"file": ("x.csv", b"text\n", "text/csv")}, data={"kw_method": "none"}
    )
    assert r.status_code == 422


def test_job_cleanup_evicts_job_from_memory():
    from service.api import app
    from service.jobs import Job, _jobs

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

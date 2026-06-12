"""
tests/test_api_service.py
=========================
Hermetic tests for the nlp-enrich API service (issue #8).

No network, no LINDAT, no ML models. The pipeline subprocess is monkeypatched
to copy fixture outputs into the per-request workspace, so the full HTTP
contract (POST /enrich, /enrich_text, /info, /health, json + zip) is exercised
without running UDPipe/NameTag.

Covered units:
  • input normalization (CSV/XLSX/TXT/inline JSON, page_num/line_num ordering)
  • doc_id sanitization
  • exit-code → HTTP mapping
  • the FastAPI surface via TestClient
"""

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

# Skip the whole module cleanly if the service deps aren't installed.
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

    def test_unsupported_extension(self):
        with pytest.raises(ValueError):
            normalize_upload("x.pdf", b"...")


class TestCanonicalCsvOrdering:

    def test_max_page_and_skips_empty(self, tmp_path):
        rows = [
            {"text": "b", "page_num": 2, "line_num": 1},
            {"text": "", "page_num": 9, "line_num": 9},
            {"text": "a", "page_num": 1, "line_num": 5},
        ]
        dest = tmp_path / "in.csv"
        pages = enr._rows_to_canonical_csv(rows, dest)
        assert pages == 2  # empty row's page 9 must not count
        out = list(csv.DictReader(dest.open(encoding="utf-8")))
        assert [r["text"] for r in out] == ["b", "a"]


# ── doc_id sanitization ───────────────────────────────────────────────────────
class TestSanitizeDocId:

    @pytest.mark.parametrize("raw,expected", [
        ("CTX000000001.csv", "CTX000000001"),
        ("../../etc/passwd", "passwd"),
        ("a b;rm -rf/", "a_b_rm_-rf"),
        ("", "document"),
        ("...", "document"),
        ("good.name_1", "good.name_1"),
    ])
    def test_cases(self, raw, expected):
        assert sanitize_doc_id(raw) == expected


# ── stubbed-runner fixture ────────────────────────────────────────────────────

def _make_stub(monkeypatch, *, returncode=0, doc_id="document",
               produce_outputs=True, kw_method_dir="KB"):
    """Patch subprocess.run inside enrichment to fabricate workspace outputs."""
    import subprocess as _sp

    def fake_run(cmd, cwd=None, env=None, capture_output=True, text=True):
        # Locate the derived --config to find the workspace.
        cfg_path = Path(cmd[cmd.index("--config") + 1])
        ws = cfg_path.parent
        out = ws / "out"
        if "--dry-run" in cmd:
            return _sp.CompletedProcess(cmd, returncode, "dry-run ok\n", "")

        if produce_outputs and returncode == 0:
            (out / "TEITOK").mkdir(parents=True, exist_ok=True)
            (out / "TEITOK" / f"{doc_id}.teitok.xml").write_text(
                '<?xml version="1.0"?><TEI/>', encoding="utf-8")
            (out / "summary_ne_counts.csv").write_text(
                '"file","page","ne1","type1","cnt-1"\n'
                f'"{doc_id}","1","Praha","LOC","1"\n', encoding="utf-8")
            kw_dir = out / f"KW_PER_DOC_{kw_method_dir}"
            kw_dir.mkdir(parents=True, exist_ok=True)
            (kw_dir / f"{doc_id}_keywords.csv").write_text(
                "keyword,score\nvýzkum,0.9\nkeramika,0.7\n", encoding="utf-8")
            pd = out / "paradata"
            pd.mkdir(parents=True, exist_ok=True)
            (pd / "260101-000001_nlp-enrich.json").write_text(json.dumps({
                "config": {"script": "api_4_stats"},
                "statistics": {"successfully_processed": 1, "skipped_files": 0,
                               "output_counts_by_type": {"xml": 1}},
            }), encoding="utf-8")
            (pd / "260101-000002_nlp-enrich_pipeline-run.json").write_text(
                json.dumps({"record_type": "pipeline-run-merged",
                            "license": "CC BY-NC-SA 4.0"}), encoding="utf-8")
        return _sp.CompletedProcess(cmd, returncode, "stage log\n", "")

    monkeypatch.setattr(enr.subprocess, "run", fake_run)


# ── exit-code → HTTP mapping (via PipelineManager directly) ───────────────────
class TestExitCodeMapping:

    def _rows(self):
        return [{"text": "Praha", "page_num": 1, "line_num": 1}]

    def test_zero_succeeds(self, monkeypatch, tmp_path):
        monkeypatch.setattr(enr, "_API_JOBS_ROOT", tmp_path)
        _make_stub(monkeypatch, returncode=0, doc_id="d")
        res = PipelineManager().enrich(self._rows(), "d", kw_method="keybert")
        assert res.returncode == 0
        assert PipelineManager.collect_teitok(res) is not None

    def test_exit_3_raises_keyword_preflight(self, monkeypatch, tmp_path):
        monkeypatch.setattr(enr, "_API_JOBS_ROOT", tmp_path)
        _make_stub(monkeypatch, returncode=3, produce_outputs=False)
        with pytest.raises(enr.KeywordPreflightError) as ei:
            PipelineManager().enrich(self._rows(), "d", kw_method="keybert")
        assert ei.value.http_status == 503

    @pytest.mark.parametrize("rc,status", [(1, 502), (2, 502), (7, 502)])
    def test_failures_map_to_502(self, monkeypatch, tmp_path, rc, status):
        monkeypatch.setattr(enr, "_API_JOBS_ROOT", tmp_path)
        _make_stub(monkeypatch, returncode=rc, produce_outputs=False)
        with pytest.raises(enr.PipelineError) as ei:
            PipelineManager().enrich(self._rows(), "d", kw_method="yake")
        assert ei.value.http_status == status


# ── full HTTP contract via TestClient ─────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(enr, "_API_JOBS_ROOT", tmp_path)
    # Disable workspace cleanup so zip responses can be inspected if needed.
    import service.api as api
    # Fresh manager bound to the patched jobs root.
    monkeypatch.setattr(api, "_manager", PipelineManager())
    return api, monkeypatch


def test_info_endpoint(client):
    api, _ = client
    c = TestClient(api.app)
    r = c.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["core_stages_mandatory"] is True
    assert body["stage_plan"] == ["manifest", "udp", "nt", "stats"]
    assert body["llm"] == "excluded from API entry points"


def test_health_endpoint(client, monkeypatch):
    api, _ = client
    _make_stub(monkeypatch, returncode=0)
    c = TestClient(api.app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_enrich_json(client, monkeypatch):
    api, _ = client
    _make_stub(monkeypatch, returncode=0, doc_id="CTX1")
    c = TestClient(api.app)
    data = b"text,page_num,line_num\nPraha,1,1\nBrno,1,2\n"
    r = c.post("/enrich",
               files={"file": ("CTX1.csv", data, "text/csv")},
               data={"kw_method": "keybert", "num_keywords": "20"})
    assert r.status_code == 200
    body = r.json()
    assert body["doc_id"] == "CTX1"
    assert body["llm"] is None
    assert body["method_requested"] == "keybert"
    assert len(body["keywords"]) == 2
    assert body["teitok_xml"].startswith("<?xml")


def test_enrich_keybert_degrades_to_yake(client, monkeypatch):
    api, _ = client
    # First call (keybert) fails preflight; manager retries with yake (rc 0).
    import subprocess as _sp
    state = {"n": 0}

    def fake_run(cmd, cwd=None, env=None, capture_output=True, text=True):
        cfg_path = Path(cmd[cmd.index("--config") + 1])
        out = cfg_path.parent / "out"
        if "--dry-run" in cmd:
            return _sp.CompletedProcess(cmd, 0, "ok", "")
        state["n"] += 1
        if "--kw-method" in cmd and cmd[cmd.index("--kw-method") + 1] == "keybert":
            return _sp.CompletedProcess(cmd, 3, "", "keybert preflight failed")
        # yake path: produce outputs
        (out / "TEITOK").mkdir(parents=True, exist_ok=True)
        (out / "TEITOK" / "CTX1.teitok.xml").write_text("<?xml?><TEI/>", "utf-8")
        kw = out / "KW_PER_DOC_Y"
        kw.mkdir(parents=True, exist_ok=True)
        (kw / "CTX1_keywords.csv").write_text("keyword,score\na,0.5\n", "utf-8")
        (out / "paradata").mkdir(parents=True, exist_ok=True)
        return _sp.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(enr.subprocess, "run", fake_run)
    c = TestClient(api.app)
    r = c.post("/enrich",
               files={"file": ("CTX1.csv", b"text\nPraha\n", "text/csv")},
               data={"kw_method": "keybert"})
    assert r.status_code == 200
    body = r.json()
    assert body["method_requested"] == "keybert"
    assert body["method_used"] == "yake"


def test_enrich_zip_format(client, monkeypatch):
    api, _ = client
    _make_stub(monkeypatch, returncode=0, doc_id="CTX1")
    c = TestClient(api.app)
    r = c.post("/enrich",
               files={"file": ("CTX1.csv", b"text\nPraha\n", "text/csv")},
               data={"format": "zip"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert any("TEITOK" in n for n in names)


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


def test_oversize_words_413(client, monkeypatch):
    api, _ = client
    monkeypatch.setattr(api, "MAX_WORDS", 3)
    _make_stub(monkeypatch, returncode=0)
    c = TestClient(api.app)
    big = b"text\n" + b"a b c d e f\n"
    r = c.post("/enrich",
               files={"file": ("x.csv", big, "text/csv")},
               data={"kw_method": "none"})
    assert r.status_code == 413

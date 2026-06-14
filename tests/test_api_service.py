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

    def test_unsupported_extension(self):
        with pytest.raises(ValueError, match="Allowed: .csv, .xlsx, .txt"):
            normalize_upload("x.pdf", b"...")


class TestCanonicalCsvOrdering:

    def test_max_page_and_skips_empty(self, tmp_path):
        rows = [
            {"text": "b", "page_num": 2, "line_num": 1},
            {"text": "", "page_num": 9, "line_num": 9},
            {"text": "a", "page_num": 1, "line_num": 5},
        ]
        dest = tmp_path / "in"
        pages = enr._write_canonical_csvs(rows, dest, "document")
        assert pages == 2
        out = list(csv.DictReader((dest / "document.csv").open(encoding="utf-8")))
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
    import subprocess as _sp

    def fake_run(cmd, cwd=None, env=None, capture_output=True, text=True):
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
    import service.api as api
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
    import subprocess as _sp

    def fake_run(cmd, cwd=None, env=None, capture_output=True, text=True):
        cfg_path = Path(cmd[cmd.index("--config") + 1])
        out = cfg_path.parent / "out"

        if "--dry-run" in cmd:
            return _sp.CompletedProcess(cmd, 0, "ok", "")

        # Simulate preflight failure for keybert
        if "--kw-method" in cmd and cmd[cmd.index("--kw-method") + 1] == "keybert":
            return _sp.CompletedProcess(cmd, 3, "preflight failed", "")

        # Simulate successful yake fallback execution
        (out / "TEITOK").mkdir(parents=True, exist_ok=True)
        (out / "TEITOK" / "CTX1.teitok.xml").write_text("<?xml?><TEI/>", "utf-8")
        (out / "UDP").mkdir(parents=True, exist_ok=True)
        (out / "UDP" / "CTX1.conllu").write_text("1\ttext", "utf-8")
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
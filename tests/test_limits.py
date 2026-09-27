"""tests/test_limits.py — every limit is a setting, and none cuts an input quietly.

atrium-project#53 (factor III), for this repo: the limits are declared in tool_limits.py, an
input over one is refused with the harmonised error, a full service answers 429 ``busy``, a
run over API_JOB_TIMEOUT is really stopped, and every limit that shapes a result without
refusing it is recorded (``limits_applied``). tests/test_limits_contract.py (canonical)
checks the declaration against .env.example and service/README.md.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import keywords as kw
import service.enrichment as enr
import tool_limits
from api_util import summarize_nt_udp as summ
from atrium_limits import LimitExceeded
from service import bounded_run

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import service.api as api  # noqa: E402
from service.jobs import Job, _jobs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

TEITOK = """<?xml version="1.0" encoding="utf-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="cs">
  <facsimile><surface id="d.surface1" lrx="1000" lry="2000"><graphic url="d-1.png"/></surface></facsimile>
  <text><body><pb n="1" id="d.pb1" facs="d-1.png"/>
    <div type="Zone" id="d.b1" bbox="100 200 300 400"><s id="d.s1"><tok id="d.s1.w1" bbox="100 200 150 240">Praha</tok></s></div>
  </body></text>
</TEI>
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(enr, "_API_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(api, "_semaphore", api._Slots())
    return TestClient(app=api.app)


def _csv(text="Výzkum proběhl v Praze."):
    return {"file": ("CTX1.csv", f"text,page_num,line_num\n{text},1,1\n".encode(), "text/csv")}


# ── bounded_run: the job timeout stops every process the run started ───────────────────


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except FileNotFoundError:
        return False
    except OSError:  # no /proc: fall back to a signal-0 probe
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_bounded_run_stops_the_command_and_its_children(tmp_path):
    pidfile = tmp_path / "child.pid"
    cmd = ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"]
    started = time.monotonic()
    assert bounded_run.run(0.5, cmd, grace_s=0.5) == bounded_run.TIMED_OUT
    assert time.monotonic() - started < 10
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child), "the grandchild outlived the deadline"


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_bounded_run_passes_the_status_of_a_command_that_ends_in_time():
    assert bounded_run.run(10, [sys.executable, "-c", "raise SystemExit(3)"]) == 3
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "service" / "bounded_run.py"), "10", "--", "true"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_a_run_over_api_job_timeout_is_limit_exceeded_and_leaves_no_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(enr, "_API_JOBS_ROOT", tmp_path)
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"], seen["timeout"] = cmd, kwargs.get("timeout")
        return SimpleNamespace(returncode=bounded_run.TIMED_OUT, stdout="", stderr="")

    monkeypatch.setattr(enr.subprocess, "run", run)
    with pytest.raises(LimitExceeded) as info:
        enr.PipelineManager().enrich(
            [{"text": "Praha", "page_num": 1, "line_num": 1}], "d.csv", kw_method="none", timeout=7
        )
    assert (
        info.value.http_status == 504
        and info.value.key == "api_job_timeout"
        and info.value.value == 7
    )
    assert seen["cmd"][1].endswith("bounded_run.py") and seen["cmd"][2:4] == ["7", "--"]
    assert seen["timeout"] > 7
    assert list(tmp_path.iterdir()) == []


def test_the_endpoint_answers_504_limit_exceeded_and_frees_the_slot(client, monkeypatch):
    monkeypatch.setattr(
        enr.subprocess,
        "run",
        lambda cmd, **k: SimpleNamespace(returncode=124, stdout="", stderr=""),
    )
    monkeypatch.setenv("API_JOB_TIMEOUT", "9")
    response = client.post("/enrich", files=_csv(), data={"kw_method": "none"})
    assert response.status_code == 504
    body = response.json()
    assert body["reason"] == "limit_exceeded" and body["limit"]["env"] == "API_JOB_TIMEOUT"
    assert body["limit"]["value"] == 9
    assert api._semaphore.running == 0


# ── the request limits ───────────────────────────────────────────────────────────────


def test_every_slot_taken_is_429_busy_with_retry_after(client):
    api._semaphore.running = tool_limits.MAX_CONCURRENT_JOBS.get()
    response = client.post("/enrich", files=_csv(), data={"kw_method": "none"})
    assert response.status_code == 429
    assert response.json()["reason"] == "busy"
    assert response.headers["Retry-After"] == "30"


def test_max_words_is_413_limit_exceeded_on_jobs_too(client, monkeypatch):
    monkeypatch.setenv("MAX_WORDS", "2")
    for path in ("/enrich", "/jobs"):
        response = client.post(path, files=_csv("jedna dva tři"), data={"kw_method": "none"})
        assert response.status_code == 413, path
        body = response.json()
        assert body["reason"] == "limit_exceeded" and body["limit"]["key"] == "max_words"
        assert body["detail"] == "Input too large: 3 words > 2 (MAX_WORDS)."


def test_an_empty_input_is_refused_by_jobs_at_once(client):
    files = {"file": ("empty.txt", b"\n\n", "text/plain")}
    assert client.post("/jobs", files=files, data={"kw_method": "none"}).status_code == 422


def test_a_full_queue_is_429_busy(client, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
    monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    api._semaphore.running = 1
    waiting = Job(job_id="waiting-" + os.urandom(4).hex(), status="queued")
    _jobs[waiting.job_id] = waiting
    try:
        response = client.post("/jobs", files=_csv(), data={"kw_method": "none"})
        assert response.status_code == 429
        assert (
            response.json()["reason"] == "busy" and "MAX_QUEUED_JOBS=1" in response.json()["detail"]
        )
    finally:
        del _jobs[waiting.job_id]


def test_a_job_is_queued_until_it_holds_a_slot(monkeypatch):
    async def scenario():
        slots = api._Slots()
        monkeypatch.setattr(api, "_semaphore", slots)
        monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
        slots.running = 1
        job = Job(job_id="j", status="queued")

        async def fake_run(*_a, **_k):
            return {"doc_id": "d"}, "json", SimpleNamespace(workspace=Path("/nonexistent"))

        monkeypatch.setattr(api, "_run_enrichment", fake_run)
        monkeypatch.setattr(api.PipelineManager, "cleanup", staticmethod(lambda _r: None))
        task = asyncio.ensure_future(api._run_job_background(job, [], "d", "none", 20, "cs"))
        await asyncio.sleep(0.05)
        assert job.status == "queued"
        await slots.release()
        await task
        return job, slots

    job, slots = asyncio.run(scenario())
    assert job.status == "done" and slots.running == 0


def test_a_timed_out_job_fails_with_the_reason(monkeypatch):
    async def scenario():
        monkeypatch.setattr(api, "_semaphore", api._Slots())
        job = Job(job_id="t", status="queued")

        async def over(*_a, **_k):
            raise tool_limits.API_JOB_TIMEOUT.exceeded(None, detail="Pipeline execution timed out.")

        monkeypatch.setattr(api, "_run_enrichment", over)
        await api._run_job_background(job, [], "d", "none", 20, "cs")
        return job

    job = asyncio.run(scenario())
    assert (job.status, job.reason, job.error) == (
        "failed",
        "limit_exceeded",
        "Pipeline execution timed out.",
    )


def test_finished_jobs_are_forgotten_after_job_ttl_s(monkeypatch):
    monkeypatch.setenv("JOB_TTL_S", "5")
    old = Job(job_id="old-" + os.urandom(4).hex(), status="done", finished_at=time.time() - 10)
    new = Job(job_id="new-" + os.urandom(4).hex(), status="done", finished_at=time.time())
    _jobs[old.job_id], _jobs[new.job_id] = old, new
    try:
        api._evict_finished_jobs()
        assert old.job_id not in _jobs and new.job_id in _jobs
    finally:
        _jobs.pop(new.job_id, None)


def test_the_enrich_text_body_is_bounded(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.0001")
    response = client.post("/enrich_text", json={"lines": ["slovo " * 100]})
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large: over 0.0001 MB (MAX_UPLOAD_MB)."


def test_an_oversized_document_json_part_is_413(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")
    files = {**_csv(), "document_json": ("d.json", b"{" + b" " * 4096 + b"}", "application/json")}
    response = client.post("/enrich", files=files, data={"kw_method": "none"})
    assert response.status_code == 413
    assert response.json()["detail"] == "document_json too large: over 0.001 MB (MAX_UPLOAD_MB)."


def test_rescale_is_bounded_by_max_rescale_dim_through_scale_too(client, monkeypatch):
    monkeypatch.setenv("MAX_RESCALE_DIM", "3000")
    files = {"file": ("d.teitok.xml", TEITOK.encode(), "application/xml")}
    over = client.post("/rescale", files=files, data={"width": "4000", "height": "10"})
    assert over.status_code == 422 and over.json()["reason"] == "limit_exceeded"
    scaled = client.post("/rescale", files=files, data={"scale": "2"})  # 2000 x 4000
    assert scaled.status_code == 422
    body = scaled.json()
    assert body["reason"] == "limit_exceeded" and body["limit"]["observed"] == 4000
    assert client.post("/rescale", files=files, data={"scale": "1.5"}).status_code == 200


# ── the stage limits: config_api.txt keys written into the job's config ─────────────


def test_the_stage_limits_reach_the_jobs_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LINDAT_TIMEOUT_S", "7")
    monkeypatch.setenv("WORD_CHUNK_LIMIT", "300")
    cfg = enr._derive_config(tmp_path).read_text(encoding="utf-8").splitlines()
    assert "TIMEOUT=7" in cfg and "WORD_CHUNK_LIMIT=300" in cfg and "MAX_RETRIES=5" in cfg
    assert tool_limits.LIMITS.meta()["lindat_max_retries"]["source"] == "config"


def test_a_callers_source_path_cannot_leave_the_workspace(tmp_path):
    rows = [{"text": "Praha", "page_num": 1, "line_num": 1, "_source_path": "../../escaped.csv"}]
    enr._write_canonical_csvs(rows, tmp_path / "in", "doc")
    assert [p.name for p in (tmp_path / "in").iterdir()] == ["doc.csv"]
    assert not (tmp_path.parent / "escaped.csv").exists()


# ── notes: KeyBERT chunking and the entity summary ──────────────────────────────────


class _Encoder:
    max_seq_length = 8

    @staticmethod
    def tokenizer(text):
        return {"input_ids": ["[CLS]", *text.split(), "[SEP]"]}


class _KeyBERT:
    model = SimpleNamespace(embedding_model=_Encoder())

    def extract_keywords(self, chunks, **_kw):
        return [[(chunk.split()[0], 0.5)] for chunk in chunks]


def test_keybert_chunking_is_a_setting_and_is_counted(monkeypatch):
    monkeypatch.setenv("KEYBERT_CHUNK_WORDS", "10")
    monkeypatch.setenv("KEYBERT_CHUNK_OVERLAP", "2")
    monkeypatch.setattr(kw, "_get_keybert_model", lambda _name: _KeyBERT())
    monkeypatch.setattr(
        kw,
        "_extract_surface_text",
        lambda p: " ".join(f"w{i}" for i in range(25)) if p == "long" else "a b",
    )
    counts: dict = {}
    result = kw._extract_keybert(["long", "short"], 5, limit_counts=counts)
    assert len(result) == 2 and result[1] == [("a", 0.5)]
    # 25 words, chunks of 10 starting every 8 words: w0-w9, w8-w17, w16-w24 (over the 8-token
    # window) and w24 (not over it) -- the chunking the literal 400/50 always did.
    assert counts == {"split": 1, "trimmed": 3, "window": 8}
    assert [c.split()[0] for c in kw._chunk_words([f"w{i}" for i in range(25)], 10, 2)] == [
        "w0",
        "w8",
        "w16",
        "w24",
    ]


def test_keybert_notes_reach_the_paradata(tmp_path):
    from atrium_paradata import ParadataLogger

    logger = ParadataLogger(program="nlp-enrich", config={}, paradata_dir=str(tmp_path))
    kw._note_keybert_limits(logger, {"split": 2, "trimmed": 5, "window": 128})
    notes = {n["limit"]: n for n in logger.limits_applied}
    assert (notes["keybert_chunk_words"]["effect"], notes["keybert_chunk_words"]["count"]) == (
        "split",
        2,
    )
    assert (notes["keybert_max_seq_tokens"]["value"], notes["keybert_max_seq_tokens"]["count"]) == (
        128,
        5,
    )


def test_the_entity_summary_top_n_is_a_setting_and_is_noted(tmp_path, monkeypatch):
    monkeypatch.setenv("NE_SUMMARY_TOP_N", "2")
    summary = tmp_path / "summary_ne_counts.csv"
    ents = {1: [("Praha", "gu"), ("Brno", "gu"), ("Ostrava", "gu")], 2: [("Praha", "gu")]}
    assert summ._write_summary_rows_from_data("doc", ents, str(summary)) == 1
    with open(summary, encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh))
    assert header == ["file", "page", "ne1", "type1", "cnt-1", "ne2", "type2", "cnt-2"]

    out = tmp_path / "out"
    out.mkdir()
    summary.rename(out / "summary_ne_counts.csv")
    got = enr.PipelineManager.collect_ne_summary(SimpleNamespace(output_dir=out))
    assert [len(page["entities"]) for page in got] == [2, 1]

    state = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "atrium_paradata.py"),
            "start",
            "--program",
            "nlp-enrich",
            "--paradata-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    summ.note_summary_trimmed(state, 1)
    [note] = json.loads(Path(state).read_text(encoding="utf-8"))["limits_applied"]
    assert (note["limit"], note["value"], note["effect"], note["count"]) == (
        "ne_summary_top_n",
        2,
        "trimmed",
        1,
    )


# ── /info and the envelope ───────────────────────────────────────────────────────────


def test_info_reports_every_limit(client, monkeypatch):
    monkeypatch.setenv("MAX_QUEUED_JOBS", "3")
    data = client.get("/info").json()
    assert data["limits"] == tool_limits.LIMITS.values()
    assert data["limits"]["max_queued_jobs"] == 3
    assert data["limits_meta"]["lindat_timeout_s"]["env"] == "LINDAT_TIMEOUT_S"
    assert data["limits_meta"]["keybert_max_seq_tokens"]["source"] == "derived"


def test_the_envelope_carries_the_runs_limits_applied(monkeypatch):
    note = {
        "limit": "ne_summary_top_n",
        "value": 20,
        "effect": "trimmed",
        "count": 1,
        "detail": "",
        "program": "nlp-enrich",
    }
    for name, value in (
        ("collect_teitok", None),
        ("collect_keywords", []),
        ("collect_ne_summary", []),
        ("collect_merged_paradata", {"limits_applied": [note]}),
    ):
        monkeypatch.setattr(enr.PipelineManager, name, staticmethod(lambda _r, v=value: v))
    result = SimpleNamespace(
        doc_id="d",
        pages=1,
        stages=[],
        kw_method_used="none",
        layout_source="rows",
        document_json_out=None,
    )
    assert api._build_envelope(result, "none")["limits_applied"] == [note]
    monkeypatch.setattr(
        enr.PipelineManager, "collect_merged_paradata", staticmethod(lambda _r: None)
    )
    assert api._build_envelope(result, "none")["limits_applied"] == []

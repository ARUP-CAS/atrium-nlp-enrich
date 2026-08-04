"""
service/api.py — FastAPI surface for the nlp-enrich pipeline.
"""

from __future__ import annotations

import asyncio
import configparser
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .atrium_service import list_endpoints
from .enrichment import (
    KeywordPreflightError,
    PipelineError,
    PipelineManager,
    count_words,
    normalize_upload,
    sanitize_doc_id,
)
from .jobs import Job, _jobs, create_job
from .rescale import RescaleError, rescale_teitok

# ── operator-tunable limits ───────────────────────────────────────────────────
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "5"))
MAX_WORDS = int(os.environ.get("MAX_WORDS", "30000"))
API_JOB_TIMEOUT = int(os.environ.get("API_JOB_TIMEOUT", "600"))
MAX_RESCALE_DIM = int(os.environ.get("MAX_RESCALE_DIM", "100000"))
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()
]
DEFAULT_KW_METHOD = os.environ.get("DEFAULT_KW_METHOD", "keybert")

_ALLOWED_KW = ("keybert", "yake", "legacy", "none")
_ALLOWED_LANG = ("cs",)

_manager = PipelineManager()
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_SERVICE_DIR = Path(__file__).resolve().parent


def _read_tool_version() -> str:
    """Read the tool version from para_config.txt [tool] section.

    Single source of truth — security.reusable.yml already validates this value
    against CITATION.cff and the release tag, so the API version can never drift
    from the released version again.
    """
    config = configparser.ConfigParser()
    config.read(_SERVICE_DIR.parent / "para_config.txt", encoding="utf-8")
    version = config.get("tool", "version", fallback="unknown")
    return version[1:] if version.lower().startswith("v") else version


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _manager.warmup, DEFAULT_KW_METHOD)
    yield


# DEFINITION OF THE APP
app = FastAPI(
    title="ATRIUM nlp-enrich API",
    version=_read_tool_version(),
    description="Text lines → NLP-enriched TEITOK XML + keywords.",
    lifespan=lifespan,
)

# Safely mount static directories if they exist
if (_SERVICE_DIR / "frontend").exists():
    app.mount(
        "/frontend",
        StaticFiles(directory=str(_SERVICE_DIR / "frontend"), html=True),
        name="frontend",
    )
if (_SERVICE_DIR / "frontend-lindat").exists():
    app.mount(
        "/frontend-lindat",
        StaticFiles(directory=str(_SERVICE_DIR / "frontend-lindat"), html=True),
        name="frontend-lindat",
    )

try:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
except Exception:  # pragma: no cover
    pass

# ── helpers ────────────────────────────────────────────────────────────────────


def _validate_params(kw_method: str, lang: str, num_keywords: int) -> None:
    if kw_method not in _ALLOWED_KW:
        raise HTTPException(422, f"kw_method must be one of {_ALLOWED_KW}") from None
    if lang not in _ALLOWED_LANG:
        raise HTTPException(422, f"lang must be one of {_ALLOWED_LANG} in v1") from None
    if not (1 <= num_keywords <= 100):
        raise HTTPException(422, "num_keywords must be between 1 and 100") from None


def _schema_verdict(xml_text: str) -> tuple[bool | None, List[str]]:
    """TEITOK XSD conformance verdict for a document this service produced
    (issue #28), as ``(valid, diagnostics)``.

    ``valid`` is ``None`` when no verdict could be reached — the validator or
    lxml is unavailable — so callers can tell "not conformant" apart from
    "not checked". Never raises: a reporting extra must not be able to fail a
    transform that already succeeded.
    """
    try:
        from api_util.validate_teitok_xml import validate_xml_text

        errors = validate_xml_text(xml_text)
    except Exception as exc:  # noqa: BLE001 - advisory field, never fatal
        return None, [f"schema check unavailable: {exc}"]
    return not errors, errors


def _run_pipeline_sync(rows, doc_id, kw_method, num_keywords, lang):
    """Blocking pipeline call with graceful backend degradation configured."""
    return _manager.enrich(
        rows, doc_id, kw_method=kw_method, num_keywords=num_keywords, lang=lang
    ), kw_method


def _build_envelope(result, requested_method) -> Dict[str, Any]:
    return {
        "doc_id": result.doc_id,
        "pages": result.pages,
        "stages": result.stages,
        "teitok_xml": PipelineManager.collect_teitok(result),
        "keywords": PipelineManager.collect_keywords(result),
        "ne_summary": PipelineManager.collect_ne_summary(result),
        "paradata": PipelineManager.collect_merged_paradata(result),
        "method_requested": requested_method,
        "method_used": result.kw_method_used,
        "llm": None,
    }


async def _run_enrichment(rows, doc_id, kw_method, num_keywords, lang, fmt) -> tuple[Any, str, Any]:
    loop = asyncio.get_event_loop()
    try:
        result, requested = await loop.run_in_executor(
            None, _run_pipeline_sync, rows, doc_id, kw_method, num_keywords, lang
        )
    except KeywordPreflightError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PipelineError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc

    try:
        if fmt == "zip":
            zip_path = PipelineManager.zip_workspace_output(result)
            return zip_path, "zip", result
        envelope = _build_envelope(result, requested)
        return envelope, "json", result
    except Exception:
        PipelineManager.cleanup(result)
        raise


async def _enrich_common(rows, doc_id, kw_method, num_keywords, lang, fmt):
    _validate_params(kw_method, lang, num_keywords)
    if not rows:
        raise HTTPException(422, "No usable text rows found in input.") from None
    words = count_words(rows)
    if words > MAX_WORDS:
        raise HTTPException(413, f"Input too large: {words} words > {MAX_WORDS}.") from None

    if _semaphore.locked():
        raise HTTPException(429, "Server busy; max concurrent jobs reached.") from None

    async with _semaphore:
        try:
            data, out_fmt, result = await asyncio.wait_for(
                _run_enrichment(rows, doc_id, kw_method, num_keywords, lang, fmt),
                timeout=API_JOB_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Pipeline execution timed out.") from exc

        if out_fmt == "zip":
            return FileResponse(
                str(data),
                media_type="application/zip",
                filename=f"{doc_id}_enriched.zip",
                background=BackgroundTask(PipelineManager.cleanup, result),
            )
        else:
            PipelineManager.cleanup(result)
            return JSONResponse(data)


async def _run_job_background(job: Job, rows, doc_id, kw_method, num_keywords, lang):
    try:
        job.status = "running"
        async with _semaphore:
            data, out_fmt, result = await asyncio.wait_for(
                _run_enrichment(rows, doc_id, kw_method, num_keywords, lang, fmt="json"),
                timeout=API_JOB_TIMEOUT,
            )
            PipelineManager.cleanup(result)
            job.result = data
            job.status = "done"
    except asyncio.TimeoutError:
        job.error = "Pipeline execution timed out."
        job.status = "failed"
    except HTTPException as e:
        job.error = str(e.detail)
        job.status = "failed"
    except Exception as e:
        job.error = str(e)
        job.status = "failed"
    finally:
        job.finished_at = time.time()


# ── endpoints ──────────────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/frontend")


@app.get("/info")
async def info() -> Dict[str, Any]:
    facts = _manager.config_facts()
    return {
        "service": "atrium-nlp-enrich",
        "version": app.version,
        "endpoints": list_endpoints(app),
        "stage_plan": ["manifest", "udp", "nt", "stats"],
        "core_stages_mandatory": True,
        "models": {
            "udpipe": facts.get("udpipe_model"),
            "nametag": facts.get("nametag_model"),
        },
        "keyword_methods": {
            "default": DEFAULT_KW_METHOD,
            "available": {
                "keybert": "best quality; GPU-capable embedding model",
                "yake": "fast CPU statistical extraction",
                "legacy": "stdlib KER lemma-frequency baseline",
                "none": "skip keyword extraction",
            },
        },
        "limits": {
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_words": MAX_WORDS,
            "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        },
    }


@app.get("/health")
async def health(deep: bool = False) -> JSONResponse:
    rc, tail = _manager.dry_run(kw_method="none")
    ok = rc == 0
    if deep and ok:
        facts = _manager.config_facts()
        import urllib.request

        for url in (facts.get("udpipe_url"), facts.get("nametag_url")):
            if url:
                try:
                    urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)
                except Exception as e:
                    ok = False
                    tail += f"\nDeep health check failed for {url}: {e}"
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "dry_run_returncode": rc, "detail": tail[-1500:]},
        status_code=200 if ok else 503,
    )


@app.post("/enrich")
async def enrich(
    file: UploadFile = File(...),  # noqa: B008
    kw_method: str = Form(DEFAULT_KW_METHOD),
    num_keywords: int = Form(20),
    lang: str = Form("cs"),
    format: str = Form("json"),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_MB} MB.") from None
    try:
        rows = normalize_upload(file.filename or "upload.csv", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    doc_id = file.filename or "document"
    fmt = format if format in ("json", "zip") else "json"
    return await _enrich_common(rows, doc_id, kw_method, num_keywords, lang, fmt)


@app.post("/enrich_text")
async def enrich_text(payload: Dict[str, Any]):
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise HTTPException(422, "'lines' must be a non-empty list.") from None
    rows: List[Dict[str, Any]] = []
    for i, item in enumerate(lines, start=1):
        if isinstance(item, str):
            rows.append({"text": item, "page_num": 1, "line_num": i})
        elif isinstance(item, dict) and item.get("text"):
            rows.append(item)
    doc_id = payload.get("doc_id", "document")
    kw_method = payload.get("kw_method", DEFAULT_KW_METHOD)
    num_keywords = int(payload.get("num_keywords", 20))
    lang = payload.get("lang", "cs")
    fmt = payload.get("format", "json")
    fmt = fmt if fmt in ("json", "zip") else "json"
    return await _enrich_common(rows, doc_id, kw_method, num_keywords, lang, fmt)


@app.post("/rescale")
async def rescale(
    file: UploadFile = File(...),  # noqa: B008
    width: int = Form(...),
    height: int = Form(...),
    format: str = Form("json"),
    fix_names: bool = Form(True),
):
    """Rescale a single-page TEITOK to a target page-image size.

    Pure XML coordinate transform (no pipeline): scales every ``bbox`` and the
    ``<surface>`` ``lrx``/``lry`` extents from the document's own coordinate
    space to ``width`` × ``height`` so annotations sit correctly on top of an
    image of that size. By default it also repairs the malformed
    ``<name>…</n>`` named-entity closings to ``</name>`` (set ``fix_names=false``
    to disable). ``format=json`` (default) returns the rewritten XML plus scale
    metadata; ``format=xml`` streams the rescaled ``.teitok.xml`` file.
    """
    if not (1 <= width <= MAX_RESCALE_DIM and 1 <= height <= MAX_RESCALE_DIM):
        raise HTTPException(
            422, f"width and height must be integers between 1 and {MAX_RESCALE_DIM}."
        ) from None

    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_MB} MB.") from None
    try:
        xml_text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Uploaded file is not valid UTF-8 text.") from exc
    if "<surface" not in xml_text and "bbox=" not in xml_text:
        raise HTTPException(
            422, "Input does not look like TEITOK facsimile XML (no <surface> or bbox)."
        ) from None

    try:
        result = rescale_teitok(xml_text, width, height, fix_name_tags=fix_names)
    except RescaleError as exc:
        raise HTTPException(422, str(exc)) from exc

    # TEITOK output contract verdict (issue #28). Advisory, not a 4xx: this
    # endpoint faithfully transforms whatever it is handed, including legacy
    # documents that predate the schema, so rejecting them would break a
    # working tool. Callers that care can gate on `schema_valid`.
    result["schema_valid"], result["schema_errors"] = _schema_verdict(result["teitok_xml"])

    fmt = format if format in ("json", "xml") else "json"
    if fmt == "xml":
        name = Path(file.filename or "document").name
        for suf in (".teitok.xml", ".xml"):
            if name.lower().endswith(suf):
                name = name[: -len(suf)]
                break
        doc_id = sanitize_doc_id(name) or "document"
        return Response(
            content=result["teitok_xml"],
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{doc_id}.rescaled.teitok.xml"'},
        )
    return JSONResponse(result)


@app.post("/jobs")
async def submit_job(
    file: UploadFile = File(...),  # noqa: B008
    kw_method: str = Form(DEFAULT_KW_METHOD),
    num_keywords: int = Form(20),
    lang: str = Form("cs"),
):
    _validate_params(kw_method, lang, num_keywords)
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_MB} MB.") from None
    try:
        rows = normalize_upload(file.filename or "upload.csv", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    doc_id = file.filename or "document"

    now = time.time()
    to_del = [
        jid
        for jid, j in _jobs.items()
        if hasattr(j, "finished_at")
        and getattr(j, "finished_at", None)
        and now - j.finished_at > 3600
    ]
    for jid in to_del:
        del _jobs[jid]

    job = await create_job()
    asyncio.create_task(_run_job_background(job, rows, doc_id, kw_method, num_keywords, lang))
    return {"job_id": job.job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found") from None
    return {"job_id": job_id, "status": job.status, "error": job.error}


@app.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found") from None
    if job.status != "done":
        raise HTTPException(409, f"Job not complete (status: {job.status})") from None
    return job.result


@app.delete("/jobs/{job_id}")
async def cleanup_job(job_id: str):
    if job_id in _jobs:
        del _jobs[job_id]
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Job not found") from None

"""
service/api.py — FastAPI surface for the nlp-enrich pipeline (issue #8).

The single-file entry point is POST /enrich: upload a CSV/XLSX/TXT of ordered
text lines and receive TEITOK XML + keywords + paradata. The four core stages
(manifest → udp → nt → stats) always run and are never exposed as parameters.
LLM enrichment is intentionally excluded from every API entry point.

Run locally:
    uvicorn service.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .enrichment import (
    KeywordPreflightError,
    PipelineError,
    PipelineManager,
    count_words,
    normalize_upload,
)

# ── operator-tunable limits ───────────────────────────────────────────────────
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "5"))
MAX_WORDS = int(os.environ.get("MAX_WORDS", "30000"))
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()
]
DEFAULT_KW_METHOD = os.environ.get("DEFAULT_KW_METHOD", "keybert")

_ALLOWED_KW = ("keybert", "yake", "legacy", "none")
_ALLOWED_LANG = ("cs",)

app = FastAPI(
    title="ATRIUM nlp-enrich API",
    version="0.11.0",
    description="Text lines → NLP-enriched TEITOK XML + keywords (issue #8).",
)

# CORS (mirrors the page-classification reference; opt-in via env).
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

_manager = PipelineManager()
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


# ── helpers ────────────────────────────────────────────────────────────────────

def _validate_params(kw_method: str, lang: str, num_keywords: int) -> None:
    if kw_method not in _ALLOWED_KW:
        raise HTTPException(422, f"kw_method must be one of {_ALLOWED_KW}")
    if lang not in _ALLOWED_LANG:
        raise HTTPException(422, f"lang must be one of {_ALLOWED_LANG} in v1")
    if not (1 <= num_keywords <= 100):
        raise HTTPException(422, "num_keywords must be between 1 and 100")


def _run_pipeline_sync(rows, doc_id, kw_method, num_keywords):
    """Blocking pipeline call with graceful keybert→yake degradation."""
    requested = kw_method
    try:
        return _manager.enrich(rows, doc_id, kw_method=kw_method,
                               num_keywords=num_keywords), requested
    except KeywordPreflightError:
        if kw_method == "keybert":
            # Degrade once to yake rather than failing the whole enrichment.
            result = _manager.enrich(rows, doc_id, kw_method="yake",
                                     num_keywords=num_keywords)
            return result, requested
        raise


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
        "llm": None,  # reserved for forward compatibility; never populated here
    }


async def _enrich_common(rows, doc_id, kw_method, num_keywords, lang, fmt):
    _validate_params(kw_method, lang, num_keywords)
    if not rows:
        raise HTTPException(422, "No usable text rows found in input.")
    words = count_words(rows)
    if words > MAX_WORDS:
        raise HTTPException(413, f"Input too large: {words} words > {MAX_WORDS}.")

    if _semaphore.locked() and _semaphore._value <= 0:  # type: ignore[attr-defined]
        raise HTTPException(429, "Server busy; max concurrent jobs reached.")

    async with _semaphore:
        loop = asyncio.get_event_loop()
        try:
            result, requested = await loop.run_in_executor(
                None, _run_pipeline_sync, rows, doc_id, kw_method, num_keywords
            )
        except KeywordPreflightError as exc:
            raise HTTPException(503, str(exc))
        except PipelineError as exc:
            raise HTTPException(exc.http_status, str(exc))

        try:
            if fmt == "zip":
                zip_path = PipelineManager.zip_workspace_output(result)
                return FileResponse(
                    str(zip_path),
                    media_type="application/zip",
                    filename=f"{result.doc_id}_enriched.zip",
                )
            envelope = _build_envelope(result, requested)
            return JSONResponse(envelope)
        finally:
            # For zip we must keep the file until FileResponse streams it, so
            # only clean non-zip responses here; zip workspaces are swept by the
            # retention knob / OS temp policy.
            if fmt != "zip":
                PipelineManager.cleanup(result)


# ── endpoints ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (
        "<html><body><h1>ATRIUM nlp-enrich API</h1>"
        "<p>POST a CSV/XLSX/TXT of ordered text lines to "
        "<code>/enrich</code>. See <a href='/docs'>/docs</a>.</p>"
        "</body></html>"
    )


@app.get("/info")
async def info() -> Dict[str, Any]:
    facts = _manager.config_facts()
    return {
        "service": "atrium-nlp-enrich",
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
        "llm": "excluded from API entry points",
    }


@app.get("/health")
async def health() -> JSONResponse:
    rc, tail = _manager.dry_run(kw_method="none")
    ok = rc == 0
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "dry_run_returncode": rc,
         "detail": tail[-1500:]},
        status_code=200 if ok else 503,
    )


@app.post("/enrich")
async def enrich(
    file: UploadFile = File(...),
    kw_method: str = Form(DEFAULT_KW_METHOD),
    num_keywords: int = Form(20),
    lang: str = Form("cs"),
    format: str = Form("json"),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_MB} MB.")
    try:
        rows = normalize_upload(file.filename or "upload.csv", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    doc_id = file.filename or "document"
    fmt = format if format in ("json", "zip") else "json"
    return await _enrich_common(rows, doc_id, kw_method, num_keywords, lang, fmt)


@app.post("/enrich_text")
async def enrich_text(payload: Dict[str, Any]):
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise HTTPException(422, "'lines' must be a non-empty list.")
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

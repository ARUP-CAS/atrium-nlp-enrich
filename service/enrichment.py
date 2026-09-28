"""
service/enrichment.py — PipelineManager for the nlp-enrich API service.
"""

from __future__ import annotations

import collections
import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── repo layout ───────────────────────────────────────────────────────────────
_SERVICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SERVICE_DIR.parent

# The record's filename convention and reader come from the hub-canonical module rather
# than from a literal ".document.json" and a bare json.load() here, so this service reads
# what the CLI writes even after a schema migration. The sys.path insert is the same idiom
# as api_util/summarize_nt_udp.py: `uvicorn service.api:app` runs from the repo root and
# resolves the vendored copy anyway, but the insert keeps the import working if the module
# is ever reached with only service/ on the path.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import tool_limits  # noqa: E402
from atrium_document import FILE_SUFFIX, load_document  # noqa: E402

logger = logging.getLogger(__name__)

_CONFIG_TEMPLATE = _REPO_ROOT / "config_api.txt"
_RUN_PIPELINE = _REPO_ROOT / "run_pipeline.py"
#: Runs the pipeline in a process group of its own and stops the whole group at
#: API_JOB_TIMEOUT (atrium-project#53); exits with _TIMED_OUT when it had to.
_BOUNDED_RUN = _SERVICE_DIR / "bounded_run.py"
_TIMED_OUT = 124
#: Extra seconds the service waits for bounded_run.py itself, past API_JOB_TIMEOUT, before
#: it gives up on it (its SIGTERM grace is 5 s).
_SUPERVISOR_GRACE_S = 30

_API_JOBS_ROOT = Path(os.environ.get("API_JOBS_ROOT", _REPO_ROOT / "TEMP" / "api_jobs"))

_KEEP_WORKSPACES = os.environ.get("API_KEEP_WORKSPACES", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

_RUNNER_ENV_VARS = (
    "ATRIUM_RUNNER_IMAGE",
    "ATRIUM_RUNNER_REPO",
    "ATRIUM_RUNNER_REF",
)

_KW_METHODS = ("keybert", "yake", "legacy", "none")
_DOC_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_DEFAULT_DOC_ID = "document"


class UnsupportedUploadType(ValueError):
    """An upload of a type the pipeline does not read (atrium-project#32 round 2).

    A ``ValueError`` still, so every caller that caught the old one keeps working; the API
    answers it with 415 ``unsupported_media_type`` and ``accepted``, where any other
    ``ValueError`` of an upload stays a 422.
    """

    accepted = (".csv", ".xlsx", ".txt", ".xml")


# ── exit-code → HTTP mapping ──────────────────────────────────────────────────
class PipelineError(Exception):
    def __init__(self, message: str, http_status: int, returncode: int) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.returncode = returncode


class KeywordPreflightError(PipelineError):
    def __init__(self, message: str, returncode: int = 3) -> None:
        super().__init__(message, http_status=503, returncode=returncode)


@dataclass
class EnrichmentResult:
    job_id: str
    doc_id: str
    workspace: Path
    output_dir: Path
    returncode: int
    kw_method_requested: str
    kw_method_used: Optional[str]
    pages: int = 0
    stages: List[Dict[str, Any]] = field(default_factory=list)
    stdout_tail: str = ""
    #: Where run_pipeline.py was told to write the accreted ATRIUM Document JSON, or None
    #: when the caller did not opt into the accretion flow (atrium-project#10, J3). Not
    #: "the file exists": the path being set is what says the client ASKED for a record,
    #: which is the only way api.py can tell "not requested" from "requested and missing".
    document_json_out: Optional[Path] = None
    #: Where the TEITOK's page layout came from: "alto" (an uploaded ALTO file), "teitok"
    #: (an uploaded, flexiconv-converted TEITOK file) or "rows" (the input's own pages and
    #: lines, no coordinates) -- issue #38, F.
    layout_source: str = "rows"


@dataclass
class Layout:
    """A layout source uploaded with the text (issue #38, F): ``kind`` is "teitok" (the text
    and layout of a converted TEITOK file) or "alto" (the layout of a table's page)."""

    kind: str
    data: bytes


# ── input normalization helpers ───────────────────────────────────────────────


def sanitize_doc_id(name: str) -> str:
    stem = Path(str(name or "")).name
    for suffix in (".teitok.xml", ".alto.xml"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    root, ext = os.path.splitext(stem)
    if ext.lower() in (".csv", ".xlsx", ".txt", ".xml"):
        stem = root
    safe = _DOC_ID_RE.sub("_", stem).strip("._-")
    return safe or _DEFAULT_DOC_ID


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _write_canonical_csvs(rows: List[Dict[str, Any]], dest_dir: Path, fallback_id: str) -> int:
    dest_dir.mkdir(parents=True, exist_ok=True)
    # One request is one document, written as <doc_id>.csv. A row's `_source_path` used to
    # name the file it went to; nothing in this service sets it, so it could only come
    # from the caller -- a CSV column or an /enrich_text item -- and "../../x.csv" wrote
    # outside the job's workspace (atrium-project#53, D9). It is ignored.
    groups = collections.defaultdict(list)
    for r in rows:
        groups[f"{fallback_id}.csv"].append(r)

    max_page = 0
    for path_str, group_rows in groups.items():
        dest = dest_dir / path_str
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["text", "page_num", "line_num"])
            for r in group_rows:
                text = (r.get("text") or "").strip()
                if not text:
                    continue
                p = _coerce_int(r.get("page_num", r.get("page", 0)))
                ln = _coerce_int(r.get("line_num", r.get("line", 0)))
                max_page = max(max_page, p)
                writer.writerow([text, p, ln])
    return max_page


def _read_csv_bytes(data: bytes) -> List[Dict[str, Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "text" not in reader.fieldnames:
        raise ValueError("CSV input must contain a 'text' column.")
    return list(reader)


def _read_txt_bytes(data: bytes) -> List[Dict[str, Any]]:
    """One row per non-empty line; a form feed (``\\f``, what pdftotext writes between
    pages) starts the next page."""
    text = data.decode("utf-8-sig", errors="replace")
    rows: List[Dict[str, Any]] = []
    for page_num, page in enumerate(text.split("\f"), start=1):
        for i, line in enumerate(page.splitlines(), start=1):
            s = line.strip()
            if s:
                rows.append({"text": s, "page_num": page_num, "line_num": i})
    return rows


def is_teitok_upload(filename: str, data: bytes) -> bool:
    """A ``*.teitok.xml`` file, or any ``.xml`` whose first 4 KB contain ``<TEI``."""
    name = (filename or "").lower()
    if name.endswith(".teitok.xml"):
        return True
    return name.endswith(".xml") and b"<TEI" in data[:4096]


def _read_teitok_bytes(data: bytes) -> List[Dict[str, Any]]:
    """The rows of an uploaded TEITOK file (e.g. flexiconv's conversion of a PDF or DOCX),
    as ``teitok_read`` gives them to stage 1: page = the page's ordinal, as the layout
    reader numbers it. Rejects a file that is not well-formed or breaks TEITOK-core rules
    (duplicate ids, dangling references), with the diagnostics."""
    text = data.decode("utf-8-sig", errors="replace")
    try:
        from api_util.validate_teitok_xml import LxmlMissing, validate_xml_text

        errors = validate_xml_text(text, profile="core")
    except LxmlMissing:  # pragma: no cover - lxml ships in the service image
        errors = []
    if errors:
        raise ValueError("Uploaded TEITOK is not usable: " + "; ".join(errors[:10]))
    from api_util.teitok_read import read_teitok_rows

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.teitok.xml"
        path.write_text(text, encoding="utf-8")
        try:
            rows = read_teitok_rows(path)
        except ET.ParseError as exc:
            raise ValueError(f"Uploaded TEITOK is not well-formed XML: {exc}") from exc
    return [
        {
            "text": r["text"],
            "page_num": r.get("page_idx", r["page_num"]),
            "line_num": r["line_num"],
        }
        for r in rows
    ]


def read_alto_upload(filename: str, data: bytes) -> Layout:
    """An uploaded ALTO file, checked to be one. PAGE XML and hOCR take the CLI's flexiconv
    route (``api_flexiconv.sh``): converted to TEITOK, they can be uploaded as ``file``."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"The alto part is not well-formed XML: {exc}") from exc
    local = root.tag.split("}")[-1]
    if local.lower() != "alto":
        raise ValueError(
            f"The alto part has root <{local}>, not <alto>. PAGE XML and hOCR are read "
            "through flexiconv: convert them with api_flexiconv.sh (CLI) and upload the "
            "*.teitok.xml as the file."
        )
    return Layout(kind="alto", data=data)


def _read_xlsx_bytes(data: bytes) -> List[Dict[str, Any]]:
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ValueError("openpyxl is required for .xlsx input.") from exc
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    rows: List[Dict[str, Any]] = []
    for ws in wb.worksheets:
        header = None
        for r in ws.iter_rows(values_only=True):
            if header is None:
                header = [str(c).strip() if c is not None else "" for c in r]
                if "text" not in header:
                    break
                ti = header.index("text")
                pi = header.index("page_num") if "page_num" in header else -1
                li = header.index("line_num") if "line_num" in header else -1
                continue
            tv = r[ti] if ti < len(r) else None
            text = str(tv).strip() if tv is not None else ""
            if not text:
                continue
            p = _coerce_int(r[pi]) if pi != -1 and pi < len(r) else 0
            ln = _coerce_int(r[li]) if li != -1 and li < len(r) else 0
            rows.append({"text": text, "page_num": p, "line_num": ln})
    return rows


def normalize_upload(filename: str, data: bytes) -> List[Dict[str, Any]]:
    if is_teitok_upload(filename, data):
        return _read_teitok_bytes(data)
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".csv":
        return _read_csv_bytes(data)
    if ext == ".txt":
        return _read_txt_bytes(data)
    if ext == ".xlsx":
        return _read_xlsx_bytes(data)
    raise UnsupportedUploadType(
        f"Unsupported file type '{ext}'. Allowed: .csv, .xlsx, .txt, and a TEITOK .xml "
        "(e.g. flexiconv's conversion of a PDF or DOCX)."
    )


def count_words(rows: List[Dict[str, Any]]) -> int:
    return sum(len((r.get("text") or "").split()) for r in rows)


# ── config derivation ──────────────────────────────────────────────────────────


def _read_template_config() -> List[str]:
    with open(_CONFIG_TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.readlines()


_RELOCATED_KEYS = {
    "OUTPUT_DIR",
    "INPUT_TABLES_DIR",
    "WORK_DIR",
    "TEMP_TXT_DIR",
    "CHUNK_DIR",
    "PARADATA_DIR",
    "CONLLU_INPUT_DIR",
    "TSV_INPUT_DIR",
    "SUMMARY_OUTPUT_DIR",
    "TEITOK_OUTPUT_DIR",
    "INPUT_ALTO_DIR",
    "INPUT_PAGES_DIR",
    "LOG_FILE",
    "TEITOK_FLEXICONV_DIR",
    "FLEXICONV_ANNOTATE",
}

_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

#: Right-hand sides written as ``${VAR:-default}`` so that `source config_api.txt`
#: cannot clobber a deployment-set variable (atrium-project#63, see the comment
#: above ``UDPIPE_URL`` in config_api.txt). The shell resolves this form; a plain
#: text read of the file does not, so ``config_facts`` must unwrap it or the raw
#: ``${…}`` string reaches ``_deep_health``'s urllib call and every deep probe
#: reports "backend unreachable".
_SHELL_DEFAULT_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}$", re.DOTALL)

#: Config keys whose value the environment is allowed to override at run time —
#: the attachable backing services. The stage scripts resolve these through the
#: shell, so reporting the file's default here would name a host the pipeline
#: does not call. Model/limit keys are deliberately absent: they are file-only.
_ENV_OVERRIDABLE = {
    "UDPIPE_URL": "udpipe_url",
    "NAMETAG_URL": "nametag_url",
}


def _derive_config(workspace: Path, layout_kind: Optional[str] = None) -> Path:
    """The workspace's config. ``layout_kind`` ("teitok" or "alto") points stage 4 at the
    uploaded layout under ``layout/`` (outside ``in/``, which stage 1 reads as tables);
    otherwise no layout directory is set, whatever the template says."""
    out = workspace / "config_api.txt"
    ws = str(workspace)

    overrides = {
        "OUTPUT_DIR": f'"{ws}/out"',
        "INPUT_TABLES_DIR": f'"{ws}/in"',
        "WORK_DIR": f'"{ws}/tmp"',
        "TEMP_TXT_DIR": f'"{ws}/tmp/TXT_EXTRACT"',
        "CHUNK_DIR": f'"{ws}/tmp/CHUNKS"',
        "PARADATA_DIR": f'"{ws}/out/paradata"',
        "LOG_FILE": f'"{ws}/out/processing.log"',
        "CONLLU_INPUT_DIR": f'"{ws}/out/UDP"',
        "TSV_INPUT_DIR": f'"{ws}/out/NE"',
        "SUMMARY_OUTPUT_DIR": f'"{ws}/out/UDP_NE"',
        "TEITOK_OUTPUT_DIR": f'"{ws}/out/TEITOK"',
        "INPUT_ALTO_DIR": f'"{ws}/layout/alto"' if layout_kind == "alto" else '""',
        "INPUT_PAGES_DIR": '""',
        "TEITOK_FLEXICONV_DIR": f'"{ws}/layout/flexiconv"',
        "FLEXICONV_ANNOTATE": '"true"' if layout_kind == "teitok" else '"false"',
    }
    # The stage limits that are config_api.txt keys (atrium-project#53): the environment
    # wins over the template, which wins over the code default (tool_limits.py).
    limits = tool_limits.LIMITS
    overrides["WORD_CHUNK_LIMIT"] = str(limits.get(tool_limits.WORD_CHUNK_LIMIT.key))
    overrides["TIMEOUT"] = str(limits.get(tool_limits.LINDAT_TIMEOUT_S.key))
    overrides["MAX_RETRIES"] = str(limits.get(tool_limits.LINDAT_MAX_RETRIES.key))

    seen: set = set()
    lines_out: List[str] = []
    for raw in _read_template_config():
        m = _ASSIGN_RE.match(raw)
        if m and m.group(1) in overrides:
            key = m.group(1)
            lines_out.append(f"{key}={overrides[key]}\n")
            seen.add(key)
        else:
            lines_out.append(raw)

    for key, val in overrides.items():
        if key not in seen:
            lines_out.append(f"{key}={val}\n")

    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(lines_out)

    for sub in (
        "in",
        "out",
        "tmp",
        "out/UDP",
        "out/NE",
        "out/UDP_NE",
        "out/TEITOK",
        "out/paradata",
        "tmp/TXT_EXTRACT",
        "tmp/CHUNKS",
        "layout/alto",
        "layout/flexiconv",
    ):
        (workspace / sub).mkdir(parents=True, exist_ok=True)
    return out


def _stage_env(job_id: str = "") -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("ATRIUM_RUNNER_REPO", "https://github.com/ufal/atrium-nlp-enrich")
    env["PYTHONUNBUFFERED"] = "1"
    if job_id:
        env["ATRIUM_REQUEST_ID"] = job_id
    return env


def _run_and_log(
    cmd: List[str],
    cwd: Path,
    env: Dict[str, str],
    timeout: Optional[float] = None,
) -> Tuple[int, str]:
    """Run *cmd* and relay its combined stdout+stderr to the service logger
    (issue #61) instead of letting ``capture_output=True`` discard it once this
    function returns.

    ``run_pipeline.py`` (the child here) itself runs each stage script
    (api_1_manifest.sh … api_4_stats.sh) with its OWN stdout/stderr inherited
    rather than captured, and those scripts' own ``log()`` helper
    (api_util/api_common.sh) already ``tee``s to both ``$LOG_FILE`` and its
    stdout/stderr — so everything a partner needs was always reaching this
    process's pipes. ``capture_output=True`` just stopped it from going
    anywhere onward once collected. This relays it to the service's own event
    stream instead, so it reaches ``docker logs`` before the request returns
    (or the healthcheck answers) rather than never at all.

    Still just ``subprocess.run(..., capture_output=True)`` underneath —
    deliberately, so every existing ``patch("service.enrichment.subprocess.run")``
    test fixture keeps working unchanged — and returns the exact (returncode,
    tail) pair every caller already depends on: *tail* is still the last 4000
    characters of combined output, used verbatim in ``PipelineError`` messages
    and ``EnrichmentResult.stdout_tail``. A hung child still raises
    ``subprocess.TimeoutExpired`` when *timeout* is given, unchanged from
    today — the reason ``dry_run()``'s Docker-healthcheck caller can bound it
    at all.
    """
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    combined = proc.stdout + proc.stderr
    for line in combined.splitlines():
        logger.info(line)
    return proc.returncode, combined[-4000:]


def _detect_kw_method_used(out_dir: Path) -> str:
    """Return which keyword backend produced output in *out_dir*.

    Checks for per-document keyword CSV files in the three possible output
    subdirectories (KB → Y → L) and returns the name of the first that has
    at least one result file.  Returns ``"none"`` when no keyword output is
    found.  This is the API's window into which backend actually ran (e.g.
    keybert requested but yake used after degradation fallback).
    """
    for sub, name in (
        ("KW_PER_DOC_KB", "keybert"),
        ("KW_PER_DOC_Y", "yake"),
        ("KW_PER_DOC_L", "legacy"),
    ):
        d = out_dir / sub
        if d.exists() and any(d.glob("*_keywords.csv")):
            return name
    return "none"


class PipelineManager:
    def __init__(self) -> None:
        _API_JOBS_ROOT.mkdir(parents=True, exist_ok=True)

    def warmup(self, kw_method: str = "keybert") -> None:
        """
        Pre-populates the HuggingFace disk cache and validates KeyBERT loads
        so the first request degrades cleanly if needed.
        Note: This does not warm the in-RAM model per request, as extraction
        runs in a subprocess spawn pool.
        """
        if kw_method != "keybert":
            return
        try:
            from keywords import DEFAULT_KEYBERT_MODEL, _get_keybert_model

            _get_keybert_model(DEFAULT_KEYBERT_MODEL)
            logger.info("KeyBERT model loaded.")
        except Exception as exc:
            logger.warning("KeyBERT warmup failed: %s. Will degrade gracefully.", exc)

    def config_facts(self) -> Dict[str, Any]:
        facts = {
            "udpipe_model": None,
            "nametag_model": None,
            "udpipe_url": None,
            "nametag_url": None,
            "word_chunk_limit": None,
        }
        key_map = {
            "MODEL_UDPIPE": "udpipe_model",
            "MODEL_NAMETAG": "nametag_model",
            "UDPIPE_URL": "udpipe_url",
            "NAMETAG_URL": "nametag_url",
            "WORD_CHUNK_LIMIT": "word_chunk_limit",
        }
        for raw in _read_template_config():
            m = _ASSIGN_RE.match(raw)
            if not m:
                continue
            key = m.group(1)
            if key in key_map:
                val = raw.split("=", 1)[1].strip().strip('"').strip("'")
                val = val.split("#", 1)[0].strip()
                expanded = _SHELL_DEFAULT_RE.match(val)
                if expanded:
                    # "${UDPIPE_URL:-https://…}" → the default the shell would use
                    # when the variable is unset. The environment branch below
                    # covers the case where it is set.
                    val = expanded.group(2).strip()
                facts[key_map[key]] = val

        # Environment wins over the file, matching the precedence the stage
        # scripts implement (atrium-project#63). Without this, /info and the
        # ?deep=true probe in service/api.py would report and HEAD-request the
        # file's default while the pipeline talked to the operator's endpoint.
        for env_key, fact_key in _ENV_OVERRIDABLE.items():
            env_val = os.environ.get(env_key, "").strip()
            if env_val:
                facts[fact_key] = env_val

        return facts

    def dry_run(self, kw_method: str = "keybert") -> Tuple[int, str]:
        """Run the pipeline in --dry-run mode — the body of the deep health check
        (service/api.py's _deep_health, issue #55). Bounded by ``timeout=`` (added in
        the same issue): this is called from a Docker HEALTHCHECK's deep probe and,
        via service/api.py, from a human hitting ?deep=true, and an unbounded
        subprocess here would let a single hung dry-run leak a worker thread
        indefinitely. subprocess.TimeoutExpired is deliberately left to propagate —
        attach_health's deep_check wrapper (docs/templates/shared/atrium_service.py)
        already catches any exception and reports it as a degraded 503 rather than a
        500, so a second catch here would only duplicate that handling.
        """
        ws = _API_JOBS_ROOT / f"healthcheck-{uuid.uuid4().hex[:8]}"
        ws.mkdir(parents=True, exist_ok=True)
        try:
            cfg = _derive_config(ws)
            cmd = [sys.executable, str(_RUN_PIPELINE), "--config", str(cfg), "--dry-run"]
            return _run_and_log(cmd, cwd=_REPO_ROOT, env=_stage_env(), timeout=30)
        finally:
            if not _KEEP_WORKSPACES:
                shutil.rmtree(ws, ignore_errors=True)

    def enrich(
        self,
        rows: List[Dict[str, Any]],
        doc_id: str,
        kw_method: str = "keybert",
        num_keywords: int = 20,
        lang: str = "cs",
        document_json: Optional[bytes] = None,
        layout: Optional[Layout] = None,
        timeout: Optional[float] = None,
    ) -> EnrichmentResult:
        """Run the pipeline on *rows*. With *timeout* (seconds, the service passes
        API_JOB_TIMEOUT), the run and every process it started are stopped when it is up,
        the workspace is removed, and ``LimitExceeded`` (504) is raised."""
        if kw_method not in _KW_METHODS:
            raise ValueError(f"Invalid kw_method '{kw_method}'. Choose from {_KW_METHODS}.")
        doc_id = sanitize_doc_id(doc_id)
        job_id = uuid.uuid4().hex
        workspace = _API_JOBS_ROOT / job_id
        workspace.mkdir(parents=True, exist_ok=True)

        # F-S2: wrap all post-mkdir work in try/except so that the workspace
        # is deleted on any failure path (including non-zero exit codes that
        # raise PipelineError before an EnrichmentResult is built).
        # try/except — NOT try/finally — because on success the workspace must
        # survive until the caller reads the outputs and calls cleanup().
        try:
            cfg = _derive_config(workspace, layout.kind if layout else None)
            pages = _write_canonical_csvs(rows, workspace / "in", doc_id)
            # The text always enters as the canonical table (no input bypasses stage 1); the
            # uploaded layout sits where stage 4 looks for it. A converted TEITOK file named
            # after the table is claimed by it (api_util/doc_identity.py), so it is the
            # layout of that one document -- never a second document.
            if layout is not None and layout.kind == "teitok":
                (workspace / "layout" / "flexiconv" / f"{doc_id}.teitok.xml").write_bytes(
                    layout.data
                )
            elif layout is not None and layout.kind == "alto":
                (workspace / "layout" / "alto" / f"{doc_id}.alto.xml").write_bytes(layout.data)

            cmd = [sys.executable, str(_RUN_PIPELINE), "--config", str(cfg)]
            cmd.extend(["--kw-fallback", "--strict-empty", "--lang", lang])

            # (atrium-project#10, J3) Rule 1: a service accepts and returns an optional
            # document_json part. run_pipeline.py has supported both flags since the
            # document-JSON bridge landed; this subprocess call simply never appended
            # them, so the CLI honoured the accretion contract and the deployed API
            # surface did not. Threaded only on opt-in, matching translator's and
            # llm-enrich's services: without a baseline there is nothing to accrete onto,
            # and emitting a bare own-part record nobody asked for would be a second,
            # undocumented output.
            document_json_out: Optional[Path] = None
            if document_json is not None:
                baseline_path = workspace / f"baseline{FILE_SUFFIX}"
                baseline_path.write_bytes(document_json)
                # Under out/, so `format=zip` carries the updated record too, and beside
                # every other per-document output rather than in the private workspace.
                document_json_out = workspace / "out" / f"{doc_id}{FILE_SUFFIX}"
                cmd.extend(
                    [
                        "--document-json",
                        str(baseline_path),
                        "--document-json-out",
                        str(document_json_out),
                    ]
                )

            if kw_method != "none":
                cmd.extend(["--kw", "--kw-method", kw_method])
                if num_keywords is not None:
                    cmd.extend(["--num-keywords", str(num_keywords)])

            rc, tail = self._run_bounded(cmd, job_id, timeout)

            if rc == 1:
                raise PipelineError(
                    f"Pipeline produced no output (empty run, exit 1).\n{tail}",
                    http_status=502,
                    returncode=rc,
                )
            if rc == 2:
                raise PipelineError(
                    f"Required stage script missing (exit 2).\n{tail}",
                    http_status=502,
                    returncode=rc,
                )
            if rc == 3:
                raise KeywordPreflightError(
                    f"Keyword preflight failed (exit 3).\n{tail}", returncode=rc
                )
            if rc == 4:
                raise KeywordPreflightError("Keyword backend failed at runtime.", returncode=4)
            if rc == 5:
                raise PipelineError(
                    "The TEITOK output failed its output contract (exit 5): a writer defect, "
                    f"not a problem with the input -- please report it.\n{tail}",
                    http_status=500,
                    returncode=rc,
                )
            if rc != 0:
                raise PipelineError(
                    f"Pipeline stage failed (exit {rc}).\n{tail}", http_status=502, returncode=rc
                )

            teitok_dir = workspace / "out" / "TEITOK"
            teitok_files = list(teitok_dir.glob("*.teitok.xml")) if teitok_dir.exists() else []
            if not teitok_files:
                raise PipelineError(
                    f"Pipeline completed (exit 0) but produced no TEITOK output. "
                    f"This indicates a silent empty run.\n{tail}",
                    http_status=502,
                    returncode=0,
                )

            kw_method_used = _detect_kw_method_used(workspace / "out")

            return EnrichmentResult(
                job_id=job_id,
                doc_id=doc_id,
                workspace=workspace,
                output_dir=workspace / "out",
                returncode=rc,
                kw_method_requested=kw_method,
                kw_method_used=kw_method_used,
                pages=pages,
                stages=self._read_stage_records(workspace / "out" / "paradata"),
                stdout_tail=tail,
                document_json_out=document_json_out,
                layout_source=layout.kind if layout is not None else "rows",
            )

        except Exception:
            if not _KEEP_WORKSPACES:
                shutil.rmtree(workspace, ignore_errors=True)
            raise

    @staticmethod
    def _run_bounded(cmd: List[str], job_id: str, timeout: Optional[float]) -> Tuple[int, str]:
        """Run the pipeline command, under bounded_run.py when there is a *timeout*.

        ``subprocess.run(timeout=)`` alone kills only its direct child: the stage scripts and
        their UDPipe/NameTag/KeyBERT processes kept running after the service had answered
        504, and the executor thread -- with the job's slot -- was released before they
        ended, so MAX_CONCURRENT_JOBS was exceeded (atrium-project#53).
        """
        if not timeout:
            return _run_and_log(cmd, cwd=_REPO_ROOT, env=_stage_env(job_id))
        bounded = [sys.executable, str(_BOUNDED_RUN), f"{timeout:g}", "--", *cmd]
        try:
            rc, tail = _run_and_log(
                bounded,
                cwd=_REPO_ROOT,
                env=_stage_env(job_id),
                timeout=timeout + _SUPERVISOR_GRACE_S,
            )
        except subprocess.TimeoutExpired:
            rc, tail = _TIMED_OUT, ""
        if rc == _TIMED_OUT:
            raise tool_limits.API_JOB_TIMEOUT.exceeded(
                None,
                value=timeout,
                detail=(
                    f"Pipeline execution timed out: over {timeout:g} s (API_JOB_TIMEOUT). The run "
                    "and every process it started were stopped."
                ),
            )
        return rc, tail

    @staticmethod
    def _read_stage_records(paradata_dir: Path) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not paradata_dir.exists():
            return out
        for p in sorted(paradata_dir.glob("*_nlp-enrich.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            cfg = d.get("config", {}) or {}
            stats = d.get("statistics", {}) or {}
            out.append(
                {
                    "script": cfg.get("script"),
                    "successfully_processed": stats.get("successfully_processed"),
                    "skipped_files": stats.get("skipped_files"),
                    "output_counts_by_type": stats.get("output_counts_by_type", {}),
                }
            )
        return out

    @staticmethod
    def collect_teitok(result: EnrichmentResult) -> Optional[str]:
        tt = result.output_dir / "TEITOK" / f"{result.doc_id}.teitok.xml"
        if tt.exists():
            return tt.read_text(encoding="utf-8")
        cands = list((result.output_dir / "TEITOK").glob("*.teitok.xml"))
        return cands[0].read_text(encoding="utf-8") if cands else None

    @staticmethod
    def collect_keywords(result: EnrichmentResult) -> List[Dict[str, Any]]:
        if result.kw_method_used is None or result.kw_method_used == "none":
            return []
        suffix = {"legacy": "L", "yake": "Y", "keybert": "KB"}.get(
            result.kw_method_used, result.kw_method_used.upper()
        )
        kw_dir = result.output_dir / f"KW_PER_DOC_{suffix}"
        path = kw_dir / f"{result.doc_id}_keywords.csv"
        if not path.exists():
            cands = list(kw_dir.glob("*_keywords.csv"))
            if not cands:
                return []
            path = cands[0]
        kws: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    score = float(row.get("score", 0) or 0)
                except (TypeError, ValueError):
                    score = 0.0
                kws.append({"keyword": row.get("keyword", ""), "score": score})
        return kws

    @staticmethod
    def collect_ne_summary(result: EnrichmentResult) -> List[Dict[str, Any]]:
        summary = result.output_dir / "summary_ne_counts.csv"
        if not summary.exists():
            return []
        out: List[Dict[str, Any]] = []
        with open(summary, "r", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            # As many entity columns as the file has: NE_SUMMARY_TOP_N (#53), 20 by default.
            fields = set(reader.fieldnames or ())
            top_n = 0
            while f"ne{top_n + 1}" in fields:
                top_n += 1
            for row in reader:
                rec = {"file": row.get("file"), "page": row.get("page")}
                ents = []
                for i in range(1, top_n + 1):
                    ne = row.get(f"ne{i}")
                    typ = row.get(f"type{i}")
                    cnt = row.get(f"cnt-{i}")
                    if ne:
                        ents.append({"text": ne, "type": typ, "count": cnt})
                rec["entities"] = ents
                out.append(rec)
        return out

    @staticmethod
    def collect_document_json(result: EnrichmentResult) -> Optional[Dict[str, Any]]:
        """The accreted ATRIUM Document JSON this run produced, or None (#10 J3).

        None has two distinct causes and the caller can tell them apart from
        ``result.document_json_out``: unset means the client never opted in; set with no
        file means the stats stage never reached the document hook (no CoNLL-U, or the
        hook itself failed and degraded per rule 3 — run_pipeline.py says so on stdout,
        see its `[document-json] NOT WRITTEN` line). Reads through load_document() so an
        older schema_version migrates on the way out, exactly as a CLI consumer would.
        """
        out = result.document_json_out
        if out is None or not out.exists():
            return None
        try:
            return load_document(str(out))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def collect_merged_paradata(result: EnrichmentResult) -> Optional[Dict[str, Any]]:
        pd_dir = result.output_dir / "paradata"
        if not pd_dir.exists():
            return None
        cands = sorted(pd_dir.glob("*_nlp-enrich_pipeline-run.json"))
        if not cands:
            return None
        try:
            return json.loads(cands[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def zip_workspace_output(result: EnrichmentResult) -> Path:
        archive_base = result.workspace / f"{result.doc_id}_enriched"
        shutil.make_archive(str(archive_base), "zip", root_dir=str(result.output_dir))
        return Path(f"{archive_base}.zip")

    @staticmethod
    def cleanup(result: EnrichmentResult) -> None:
        if _KEEP_WORKSPACES:
            return
        shutil.rmtree(result.workspace, ignore_errors=True)

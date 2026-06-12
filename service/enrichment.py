"""
service/enrichment.py — PipelineManager for the nlp-enrich API service.

Treats run_pipeline.py as the ONLY execution interface (never the stage scripts
directly), mirroring the runner's own "never re-implement stage logic" rule.

Each request gets a fresh workspace under TEMP/api_jobs/<job_id>/ with every
pipeline directory (OUTPUT_DIR, INPUT_TABLES_DIR, TEMP_TXT_DIR, CHUNK_DIR,
PARADATA_DIR) relocated inside it, so resume / paradata-collision /
FAIL_ON_EMPTY semantics all behave as a clean first run and concurrent requests
cannot collide.

Input normalization: every accepted form (CSV / XLSX / TXT / inline JSON) is
materialized as a canonical CSV with columns text[,page_num,line_num] in
INPUT_TABLES_DIR, so stage 1 always runs on the same path and no input type
bypasses any mandatory step.

LLM is never invoked from this manager (issue #8 excludes it from API entry
points). The 4 core stages always run; keyword extraction is default-on with
keybert and opt-out only via kw_method="none".
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── repo layout ───────────────────────────────────────────────────────────────
_SERVICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SERVICE_DIR.parent

# Operator-pinned, read-only config template (models, service URLs, timeouts…).
_CONFIG_TEMPLATE = _REPO_ROOT / "config_api.txt"
_RUN_PIPELINE = _REPO_ROOT / "run_pipeline.py"

# Per-request workspaces live here unless overridden by env.
_API_JOBS_ROOT = Path(os.environ.get("API_JOBS_ROOT", _REPO_ROOT / "TEMP" / "api_jobs"))

# Keep workspaces after the response for debugging when truthy.
_KEEP_WORKSPACES = os.environ.get("API_KEEP_WORKSPACES", "").lower() in (
    "1", "true", "yes", "on",
)

# Provenance env forwarded to the runner (already honoured by it).
_RUNNER_ENV_VARS = (
    "ATRIUM_RUNNER_IMAGE",
    "ATRIUM_RUNNER_REPO",
    "ATRIUM_RUNNER_REF",
)

_KW_METHODS = ("keybert", "yake", "legacy", "none")
_DOC_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_DEFAULT_DOC_ID = "document"


# ── exit-code → HTTP mapping (per the runner's documented contract) ───────────
# 0  → 200
# 3  → keyword preflight failed (caller may retry with yake; else 503)
# 1  → empty run (no input processed)            → 502
# 2  → required stage script missing             → 502
# ≠0 → stage script itself failed                → 502
class PipelineError(Exception):
    """Raised when the pipeline subprocess fails. Carries an HTTP status hint."""

    def __init__(self, message: str, http_status: int, returncode: int) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.returncode = returncode


class KeywordPreflightError(PipelineError):
    """Exit code 3 — keyword backend dependency preflight failed."""

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


# ── input normalization helpers ───────────────────────────────────────────────

def sanitize_doc_id(name: str) -> str:
    """Restrict a doc id to [A-Za-z0-9._-]; never empty, never path-traversing.

    The pipeline uses the filename stem in shell paths and grep patterns
    (api_1_manifest.sh), so untrusted input must be stripped to a safe set.
    """
    stem = Path(str(name or "")).name
    root, ext = os.path.splitext(stem)
    if ext.lower() in (".csv", ".xlsx", ".txt"):
        stem = root
    safe = _DOC_ID_RE.sub("_", stem).strip("._-")
    return safe or _DEFAULT_DOC_ID


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rows_to_canonical_csv(rows: List[Dict[str, Any]], dest: Path) -> int:
    """Write canonical CSV (text[,page_num,line_num]); return max page_num."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_page = 0
    with open(dest, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["text", "page_num", "line_num"])
        for r in rows:
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
    text = data.decode("utf-8-sig", errors="replace")
    rows: List[Dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if s:
            rows.append({"text": s, "page_num": 1, "line_num": i})
    return rows


def _read_xlsx_bytes(data: bytes) -> List[Dict[str, Any]]:
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ValueError("openpyxl is required for .xlsx input.") from exc
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    rows: List[Dict[str, Any]] = []
    for ws in wb.worksheets:
        header = None
        for r in ws.iter_rows(values_only=True):
            if header is None:
                header = [str(c).strip() if c is not None else "" for c in r]
                if "text" not in header:
                    break  # this sheet has no text column; skip it
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
    """Dispatch on extension and return ordered rows. Raises ValueError on bad input."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".csv":
        return _read_csv_bytes(data)
    if ext == ".txt":
        return _read_txt_bytes(data)
    if ext == ".xlsx":
        return _read_xlsx_bytes(data)
    raise ValueError(f"Unsupported file type '{ext}'. Allowed: .csv, .xlsx, .txt")


def count_words(rows: List[Dict[str, Any]]) -> int:
    return sum(len((r.get("text") or "").split()) for r in rows)


# ── config derivation ──────────────────────────────────────────────────────────

def _read_template_config() -> List[str]:
    with open(_CONFIG_TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.readlines()


# Keys whose values are workspace-relative directories we relocate per request.
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
}

_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _derive_config(workspace: Path) -> Path:
    """Write a per-request config_api.txt that points every path into `workspace`.

    Service URLs, model identifiers, timeout/retry/chunk knobs are inherited
    verbatim from the template; only directory paths are overridden so the run
    is hermetic. The derived file is passed to the runner via --config.
    """
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
        # ALTO/pages are unusable for text-only API input: leave unset so
        # api_4_stats emits TEITOK without bboxes (a warning, not an error).
        "INPUT_ALTO_DIR": '""',
        "INPUT_PAGES_DIR": '""',
    }

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

    # Append any override keys absent from the template.
    for key, val in overrides.items():
        if key not in seen:
            lines_out.append(f"{key}={val}\n")

    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(lines_out)

    # Pre-create the directory tree the stages expect.
    for sub in ("in", "out", "tmp", "out/UDP", "out/NE", "out/UDP_NE",
                "out/TEITOK", "out/paradata", "tmp/TXT_EXTRACT", "tmp/CHUNKS"):
        (workspace / sub).mkdir(parents=True, exist_ok=True)
    return out


def _stage_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("ATRIUM_RUNNER_REPO", "https://github.com/ufal/atrium-nlp-enrich")
    # Make subprocess output predictable / unbuffered.
    env["PYTHONUNBUFFERED"] = "1"
    return env


class PipelineManager:
    """Runs the nlp-enrich pipeline for one request via run_pipeline.py."""

    def __init__(self) -> None:
        _API_JOBS_ROOT.mkdir(parents=True, exist_ok=True)

    # -- introspection for /info and /health ----------------------------------

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
                facts[key_map[key]] = val
        return facts

    def dry_run(self, kw_method: str = "keybert") -> Tuple[int, str]:
        """Run `run_pipeline.py --dry-run` to validate config & resolve the plan."""
        ws = _API_JOBS_ROOT / f"healthcheck-{uuid.uuid4().hex[:8]}"
        ws.mkdir(parents=True, exist_ok=True)
        try:
            cfg = _derive_config(ws)
            cmd = [sys.executable, str(_RUN_PIPELINE), "--config", str(cfg), "--dry-run"]
            if kw_method != "none":
                cmd += ["--kw", "--kw-method", kw_method]
            proc = subprocess.run(
                cmd, cwd=str(_REPO_ROOT), env=_stage_env(),
                capture_output=True, text=True,
            )
            return proc.returncode, (proc.stdout + proc.stderr)[-4000:]
        finally:
            if not _KEEP_WORKSPACES:
                shutil.rmtree(ws, ignore_errors=True)

    # -- the single-file entry point ------------------------------------------

    def enrich(
        self,
        rows: List[Dict[str, Any]],
        doc_id: str,
        kw_method: str = "keybert",
        num_keywords: int = 20,
    ) -> EnrichmentResult:
        """Materialize input, run the pipeline, return a result handle.

        Caller is responsible for collecting artifacts and (optionally) cleanup
        via `cleanup()`.
        """
        if kw_method not in _KW_METHODS:
            raise ValueError(
                f"Invalid kw_method '{kw_method}'. Choose from {_KW_METHODS}."
            )
        doc_id = sanitize_doc_id(doc_id)
        job_id = uuid.uuid4().hex
        workspace = _API_JOBS_ROOT / job_id
        workspace.mkdir(parents=True, exist_ok=True)

        cfg = _derive_config(workspace)
        input_csv = workspace / "in" / f"{doc_id}.csv"
        pages = _rows_to_canonical_csv(rows, input_csv)

        method_used: Optional[str] = None if kw_method == "none" else kw_method
        cmd = [sys.executable, str(_RUN_PIPELINE), "--config", str(cfg)]
        if kw_method != "none":
            cmd += ["--kw", "--kw-method", kw_method]

        proc = subprocess.run(
            cmd, cwd=str(_REPO_ROOT), env=_stage_env(),
            capture_output=True, text=True,
        )
        rc = proc.returncode
        tail = (proc.stdout + proc.stderr)[-4000:]

        # Map the runner's exit-code contract onto exceptions / retries.
        if rc == 3:
            # Keyword preflight failed. Caller decides whether to retry with yake.
            raise KeywordPreflightError(
                f"Keyword preflight failed (exit 3) for method '{kw_method}'.\n{tail}",
            )
        if rc == 1:
            raise PipelineError(
                f"Pipeline produced no output (empty run, exit 1).\n{tail}",
                http_status=502, returncode=rc,
            )
        if rc == 2:
            raise PipelineError(
                f"Required stage script missing (exit 2).\n{tail}",
                http_status=502, returncode=rc,
            )
        if rc != 0:
            raise PipelineError(
                f"Pipeline stage failed (exit {rc}).\n{tail}",
                http_status=502, returncode=rc,
            )

        return EnrichmentResult(
            job_id=job_id,
            doc_id=doc_id,
            workspace=workspace,
            output_dir=workspace / "out",
            returncode=rc,
            kw_method_requested=kw_method,
            kw_method_used=method_used,
            pages=pages,
            stages=self._read_stage_records(workspace / "out" / "paradata"),
            stdout_tail=tail,
        )

    # -- result collection -----------------------------------------------------

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
            out.append({
                "script": cfg.get("script"),
                "successfully_processed": stats.get("successfully_processed"),
                "skipped_files": stats.get("skipped_files"),
                "output_counts_by_type": stats.get("output_counts_by_type", {}),
            })
        return out

    @staticmethod
    def collect_teitok(result: EnrichmentResult) -> Optional[str]:
        tt = result.output_dir / "TEITOK" / f"{result.doc_id}.teitok.xml"
        if tt.exists():
            return tt.read_text(encoding="utf-8")
        # Fall back to any single TEITOK file produced.
        cands = list((result.output_dir / "TEITOK").glob("*.teitok.xml"))
        return cands[0].read_text(encoding="utf-8") if cands else None

    @staticmethod
    def collect_keywords(result: EnrichmentResult) -> List[Dict[str, Any]]:
        if result.kw_method_used is None:
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
            for row in reader:
                rec = {"file": row.get("file"), "page": row.get("page")}
                ents = []
                for i in range(1, 21):
                    ne = row.get(f"ne{i}")
                    typ = row.get(f"type{i}")
                    cnt = row.get(f"cnt-{i}")
                    if ne:
                        ents.append({"text": ne, "type": typ, "count": cnt})
                rec["entities"] = ents
                out.append(rec)
        return out

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
        """Zip the workspace OUTPUT_DIR; return the archive path."""
        archive_base = result.workspace / f"{result.doc_id}_enriched"
        shutil.make_archive(str(archive_base), "zip", root_dir=str(result.output_dir))
        return Path(f"{archive_base}.zip")

    @staticmethod
    def cleanup(result: EnrichmentResult) -> None:
        if _KEEP_WORKSPACES:
            return
        shutil.rmtree(result.workspace, ignore_errors=True)

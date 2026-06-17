"""
service/enrichment.py — PipelineManager for the nlp-enrich API service.
"""

from __future__ import annotations

import collections
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

_CONFIG_TEMPLATE = _REPO_ROOT / "config_api.txt"
_RUN_PIPELINE = _REPO_ROOT / "run_pipeline.py"

_API_JOBS_ROOT = Path(os.environ.get("API_JOBS_ROOT", _REPO_ROOT / "TEMP" / "api_jobs"))

_KEEP_WORKSPACES = os.environ.get("API_KEEP_WORKSPACES", "").lower() in (
    "1", "true", "yes", "on",
)

_RUNNER_ENV_VARS = (
    "ATRIUM_RUNNER_IMAGE",
    "ATRIUM_RUNNER_REPO",
    "ATRIUM_RUNNER_REF",
)

_KW_METHODS = ("keybert", "yake", "legacy", "none")
_DOC_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_DEFAULT_DOC_ID = "document"


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


# ── input normalization helpers ───────────────────────────────────────────────

def sanitize_doc_id(name: str) -> str:
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


def _write_canonical_csvs(rows: List[Dict[str, Any]], dest_dir: Path, fallback_id: str) -> int:
    dest_dir.mkdir(parents=True, exist_ok=True)
    groups = collections.defaultdict(list)
    for r in rows:
        path = r.get("_source_path", f"{fallback_id}.csv")
        groups[path].append(r)

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


_RELOCATED_KEYS = {
    "OUTPUT_DIR", "INPUT_TABLES_DIR", "WORK_DIR", "TEMP_TXT_DIR",
    "CHUNK_DIR", "PARADATA_DIR", "CONLLU_INPUT_DIR", "TSV_INPUT_DIR",
    "SUMMARY_OUTPUT_DIR", "TEITOK_OUTPUT_DIR", "INPUT_ALTO_DIR",
    "INPUT_PAGES_DIR", "LOG_FILE",
}

_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _derive_config(workspace: Path) -> Path:
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

    for key, val in overrides.items():
        if key not in seen:
            lines_out.append(f"{key}={val}\n")

    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(lines_out)

    for sub in ("in", "out", "tmp", "out/UDP", "out/NE", "out/UDP_NE",
                "out/TEITOK", "out/paradata", "tmp/TXT_EXTRACT", "tmp/CHUNKS"):
        (workspace / sub).mkdir(parents=True, exist_ok=True)
    return out


def _stage_env(job_id: str = "") -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("ATRIUM_RUNNER_REPO", "https://github.com/ufal/atrium-nlp-enrich")
    env["PYTHONUNBUFFERED"] = "1"
    if job_id:
        env["ATRIUM_REQUEST_ID"] = job_id
    return env


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
            from keywords import _get_keybert_model, DEFAULT_KEYBERT_MODEL
            _get_keybert_model(DEFAULT_KEYBERT_MODEL)
            print(f"[warmup] KeyBERT model loaded.")
        except Exception as exc:
            print(f"[warmup] KeyBERT warmup failed: {exc}. Will degrade gracefully.")

    def config_facts(self) -> Dict[str, Any]:
        facts = {
            "udpipe_model": None, "nametag_model": None,
            "udpipe_url": None, "nametag_url": None, "word_chunk_limit": None,
        }
        key_map = {
            "MODEL_UDPIPE": "udpipe_model", "MODEL_NAMETAG": "nametag_model",
            "UDPIPE_URL": "udpipe_url", "NAMETAG_URL": "nametag_url",
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
        ws = _API_JOBS_ROOT / f"healthcheck-{uuid.uuid4().hex[:8]}"
        ws.mkdir(parents=True, exist_ok=True)
        try:
            cfg = _derive_config(ws)
            cmd = [sys.executable, str(_RUN_PIPELINE), "--config", str(cfg), "--dry-run"]
            proc = subprocess.run(
                cmd, cwd=str(_REPO_ROOT), env=_stage_env(),
                capture_output=True, text=True,
            )
            return proc.returncode, (proc.stdout + proc.stderr)[-4000:]
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
    ) -> EnrichmentResult:
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
            cfg = _derive_config(workspace)
            pages = _write_canonical_csvs(rows, workspace / "in", doc_id)

            cmd = [sys.executable, str(_RUN_PIPELINE), "--config", str(cfg)]
            cmd.extend(["--kw-fallback", "--strict-empty", "--lang", lang])

            if kw_method != "none":
                cmd.extend(["--kw", "--kw-method", kw_method])
                if num_keywords is not None:
                    cmd.extend(["--num-keywords", str(num_keywords)])

            proc = subprocess.run(
                cmd, cwd=str(_REPO_ROOT), env=_stage_env(job_id),
                capture_output=True, text=True,
            )
            rc = proc.returncode
            tail = (proc.stdout + proc.stderr)[-4000:]

            if rc == 1:
                raise PipelineError(f"Pipeline produced no output (empty run, exit 1).\n{tail}", http_status=502, returncode=rc)
            if rc == 2:
                raise PipelineError(f"Required stage script missing (exit 2).\n{tail}", http_status=502, returncode=rc)
            if rc == 3:
                raise KeywordPreflightError(f"Keyword preflight failed (exit 3).\n{tail}", returncode=rc)
            if rc == 4:
                raise KeywordPreflightError("Keyword backend failed at runtime.", returncode=4)
            if rc != 0:
                raise PipelineError(f"Pipeline stage failed (exit {rc}).\n{tail}", http_status=502, returncode=rc)

            teitok_dir = workspace / "out" / "TEITOK"
            teitok_files = list(teitok_dir.glob("*.teitok.xml")) if teitok_dir.exists() else []
            if not teitok_files:
                raise PipelineError(
                    f"Pipeline completed (exit 0) but produced no TEITOK output. "
                    f"This indicates a silent empty run.\n{tail}",
                    http_status=502, returncode=0,
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
            )

        except Exception:
            if not _KEEP_WORKSPACES:
                shutil.rmtree(workspace, ignore_errors=True)
            raise

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
        archive_base = result.workspace / f"{result.doc_id}_enriched"
        shutil.make_archive(str(archive_base), "zip", root_dir=str(result.output_dir))
        return Path(f"{archive_base}.zip")

    @staticmethod
    def cleanup(result: EnrichmentResult) -> None:
        if _KEEP_WORKSPACES:
            return
        shutil.rmtree(result.workspace, ignore_errors=True)
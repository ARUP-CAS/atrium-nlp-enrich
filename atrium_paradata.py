"""
atrium_paradata.py  –  Unified provenance/paradata logger for ATRIUM pipelines.

DROP THIS FILE AS-IS into every ATRIUM repository root.

License of the log files themselves: CC BY-NC 4.0
https://creativecommons.org/licenses/by-nc/4.0/

Usage
-----
    from atrium_paradata import ParadataLogger

    logger = ParadataLogger(
        program="page-classification",          # short identifier for the tool
        config=vars(args),                      # any dict of run-time parameters
        paradata_dir="paradata",                # directory to write logs into (created if absent)
        output_types=["csv", "png"],            # declare all expected output file types
    )

    # during the run:
    logger.log_skip("bad_file.xml", "parse error: …")
    logger.log_success("csv")           # one csv produced
    logger.log_success("png", count=3)  # three pngs produced at once
    logger.log_document_success()       # one input document fully processed

    # at the very end (call inside a finally block):
    logger.finalize(input_total=1200)

The resulting file is written to:
    <paradata_dir>/YYMMDD-HHmmss_<program>.json
"""

from __future__ import annotations

import configparser
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from para_licenses import resolve_effective_license, merge_effective_licenses
except ImportError:  # keep logging functional even if the helper is missing
    resolve_effective_license = None      # type: ignore
    merge_effective_licenses = None       # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Conservative fallback used ONLY when para_licenses.py or para_config.txt is
# unavailable. For atrium-nlp-enrich the real floor (CC BY-NC-SA 4.0, set by the
# NameTag/UDPipe models) is computed from components via para_licenses.py.
LICENSE_NAME = "CC BY-NC 4.0"
LICENSE_URL  = "https://creativecommons.org/licenses/by-nc/4.0/"

_REPO_URLS: Dict[str, str] = {
    "page-classification": "https://github.com/ufal/atrium-page-classification",
    "alto-postprocess":    "https://github.com/ufal/atrium-alto-postprocess",
    "nlp-enrich":          "https://github.com/ufal/atrium-nlp-enrich",
    "translator":          "https://github.com/ufal/atrium-translator",
}

# Environment overrides so a logged reference points at the ACTUAL running
# image/runner rather than a static fork URL.
_ENV_RUNNER_IMAGE = "ATRIUM_RUNNER_IMAGE"
_ENV_RUNNER_REPO  = "ATRIUM_RUNNER_REPO"
_ENV_RUNNER_REF   = "ATRIUM_RUNNER_REF"


def _load_para_config(start_dir: str = ".") -> Dict[str, Any]:
    """
    Load repository-specific para_config.txt if present.

    Returns a dict:
        { "program": str, "version": str, "repository_fallback": str,
          "components": [ {name, license, loaded, role}, ... ] }
    Empty/missing file -> minimal dict so callers can fall back to kwargs.
    """
    path = os.path.join(start_dir, "para_config.txt")
    out: Dict[str, Any] = {"components": []}
    if not os.path.exists(path):
        return out

    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")

    if cfg.has_section("tool"):
        out["program"] = cfg.get("tool", "program", fallback=None)
        out["version"] = cfg.get("tool", "version", fallback=None)
        out["repository_fallback"] = cfg.get("tool", "repository_fallback", fallback=None)

    if cfg.has_section("components"):
        for name, spec in cfg.items("components"):
            # spec form: "<license> ; <always|conditional> ; <role>"
            fields = [s.strip() for s in spec.split(";")]
            lic = fields[0] if len(fields) > 0 else ""
            loaded = fields[1] if len(fields) > 1 else "always"
            role = fields[2] if len(fields) > 2 else ""
            out["components"].append({
                "name": name.strip(),
                "license": lic,
                "loaded": loaded,
                "role": role,
            })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# ParadataLogger
# ──────────────────────────────────────────────────────────────────────────────

class ParadataLogger:
    """
    Context-manager-friendly paradata recorder.

    Parameters
    ----------
    program : str
        Short tool name, e.g. "page-classification".
    config : dict
        Snapshot of the run-time configuration (argparse namespace, config-file
        values, model identifiers, …).  Nested dicts are accepted; non-JSON-
        serialisable values are coerced to str automatically.
    paradata_dir : str
        Path to the directory where the JSON log will be written.
        Created automatically if it does not exist.
    output_types : list[str], optional
        Declare the output file types this run will produce so that performance
        counters are initialised up-front (e.g. ["csv", "png"]).
        Additional types can still be added at runtime via log_success().
    """

    def __init__(
        self,
        program: str,
        config: Dict[str, Any],
        paradata_dir: str = "paradata",
        output_types: Optional[List[str]] = None,
        version: Optional[str] = None,
        config_dir: str = ".",
    ) -> None:
        self.program      = program
        self.paradata_dir = paradata_dir
        self._start_dt    = datetime.now(tz=timezone.utc)
        self._run_id      = self._start_dt.strftime("%y%m%d-%H%M%S")

        # repo-specific static facts (para_config.txt)
        self._para_cfg = _load_para_config(config_dir)

        # version: kwarg > para_config > "unknown"
        self.version = version or self._para_cfg.get("version") or "unknown"

        # sanitise config so it stays JSON-serialisable
        self.config = _sanitise(config)

        # per-output-type file counters
        self._output_counts: Dict[str, int] = {}
        if output_types:
            for t in output_types:
                self._output_counts[t] = 0

        # FIX #2: separate counter for fully-processed input documents.
        # log_document_success() increments this; it is used as the primary
        # source for "successfully_processed" in the statistics block.
        # Falls back to max(output_counts) when never called, for backwards
        # compatibility with callers that only use log_success().
        self._docs_processed: int = 0

        # Components actually exercised this run: {name: license}.
        # Auto-seed with components flagged "always" in para_config so every run
        # inherits at least the project-wide floor (CC BY-NC-SA 4.0 for
        # nlp-enrich, set by the NameTag/UDPipe models).
        self._components_used: Dict[str, str] = {}
        for comp in self._para_cfg.get("components", []):
            if comp.get("loaded") == "always":
                self._components_used[comp["name"]] = comp["license"]

        self._skipped:  List[Dict[str, str]] = []
        self._input_total: int = 0
        self._finalised: bool  = False

        # make sure the paradata directory exists
        os.makedirs(paradata_dir, exist_ok=True)

    # ── public API ─────────────────────────────────────────────────────────────

    def log_skip(self, filepath: str, reason: str) -> None:
        """Record a file that was skipped because of an error or unsupported format."""
        self._skipped.append({
            "file":      str(filepath),
            "reason":    str(reason),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })

    def log_success(self, output_type: str, count: int = 1) -> None:
        """
        Increment the counter for *output_type* by *count*.

        Call this every time one or more output files of a given type are
        successfully produced.  E.g.:

            logger.log_success("csv")
            logger.log_success("xml", count=batch_size)
        """
        self._output_counts[output_type] = (
            self._output_counts.get(output_type, 0) + count
        )

    def log_document_success(self) -> None:
        """
        Increment the successfully-processed document counter by one.

        FIX #2: Call this once per fully-processed *input document* (not per
        output file).  When this method is called at least once, finalize()
        uses ``_docs_processed`` as the canonical "successfully_processed"
        value in the statistics block, giving an accurate document count even
        when each document produces multiple output files (e.g. ``n_pages``
        TSV files) via log_success().

        Backwards compatibility: callers that only use log_success() and never
        call log_document_success() continue to work — finalize() falls back
        to max(output_counts) as before.
        """
        self._docs_processed += 1

    def log_component(self, name: str, license: Optional[str] = None) -> None:
        """
        Record that a licensed component was ACTUALLY exercised this run.

        If *license* is omitted it is looked up from para_config.txt. Call this
        the first time a conditional component is invoked (e.g. when the YAKE or
        KeyBERT backend is selected, or the NameTag/UDPipe engine is hit) so the
        effective output license reflects real usage rather than the worst case.
        """
        if license is None:
            for comp in self._para_cfg.get("components", []):
                if comp["name"] == name:
                    license = comp["license"]
                    break
        self._components_used[name] = license or "UNKNOWN"

    # ── reference / license resolution ────────────────────────────────────────

    def _resolve_repository(self) -> str:
        """Dynamic runner reference: env > para_config fallback > static map."""
        return (
            os.environ.get(_ENV_RUNNER_REPO)
            or self._para_cfg.get("repository_fallback")
            or _REPO_URLS.get(self.program, "https://github.com/ufal")
        )

    def _license_block(self) -> Dict[str, Any]:
        """Compute the effective output license from components actually used."""
        comps = list(self._components_used.items())
        if resolve_effective_license is not None and comps:
            return resolve_effective_license(comps)
        # Fallback if helper missing or no components recorded: stay safe.
        return {
            "effective_license": LICENSE_NAME,
            "effective_license_url": LICENSE_URL,
            "is_non_commercial": True,
            "is_share_alike": False,
            "determined_by": [],
            "components": [{"name": n, "license": l} for n, l in comps],
            "unknown_licenses": [],
            "notes": "License helper unavailable or no components recorded; "
                     "defaulted conservatively to CC BY-NC 4.0.",
        }

    def finalize(self, input_total: Optional[int] = None) -> str:
        """
        Write the paradata JSON file and return its path.

        Parameters
        ----------
        input_total : int, optional
            Total number of input files/documents that were submitted to the
            pipeline (including skipped ones).  If None, it is inferred as
            successfully_processed + skipped.
        """
        if self._finalised:
            raise RuntimeError("finalize() has already been called.")

        end_dt          = datetime.now(tz=timezone.utc)
        duration_sec    = (end_dt - self._start_dt).total_seconds()
        duration_min    = duration_sec / 60.0 if duration_sec > 0 else 0.0

        skipped_count   = len(self._skipped)

        # FIX #2: use the explicit document counter when available; otherwise
        # fall back to max(output_counts) for backwards compatibility with
        # callers that only call log_success() and not log_document_success().
        if self._docs_processed > 0:
            processed_docs = self._docs_processed
        else:
            processed_docs = max(self._output_counts.values()) if self._output_counts else 0

        if input_total is None:
            input_total = processed_docs + skipped_count

        # per-type throughput (files per minute)
        perf_per_min: Dict[str, float] = {}
        for otype, cnt in self._output_counts.items():
            perf_per_min[otype] = round(cnt / duration_min, 4) if duration_min > 0 else 0.0

        lic = self._license_block()

        payload = {
            # ── provenance ──────────────────────────────────────────────────
            "schema_version":      "2.0",
            "program":             self.program,
            "tool_version":        self.version,
            "repository":          self._resolve_repository(),
            "runner_ref":          os.environ.get(_ENV_RUNNER_REF, ""),
            "python_version":      sys.version,
            "run_id":              self._run_id,

            # ── license (computed from components actually used) ─────────────
            "license":             lic["effective_license"],
            "license_url":         lic["effective_license_url"],
            "license_detail":      lic,

            # ── timing ──────────────────────────────────────────────────────
            "start_time":          self._start_dt.isoformat(),
            "end_time":            end_dt.isoformat(),
            "duration_seconds":    round(duration_sec, 3),

            # ── configuration snapshot ───────────────────────────────────────
            "config":              self.config,

            # ── statistics ───────────────────────────────────────────────────
            "statistics": {
                "input_files_total":         input_total,
                "successfully_processed":    processed_docs,
                "skipped_files":             skipped_count,
                "output_counts_by_type":     dict(self._output_counts),
                "performance_per_minute":    perf_per_min,
            },

            # ── skipped file details ─────────────────────────────────────────
            "skipped_files_detail": self._skipped,
        }

        out_path = os.path.join(
            self.paradata_dir,
            f"{self._run_id}_{self.program}.json",
        )
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

        self._finalised = True
        print(f"[paradata] Log written → {out_path}", flush=True)
        return out_path

    # ── context manager support ───────────────────────────────────────────────

    def __enter__(self) -> "ParadataLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Automatically finalise on exit, even if an exception was raised."""
        if not self._finalised:
            try:
                self.finalize()
            except Exception as e:   # never let logging crash the program
                print(f"[paradata] WARNING – could not write log: {e}", file=sys.stderr)
        return False   # do not suppress exceptions

    # ── JSON state serialisation (used by CLI shim) ───────────────────────────

    def _to_state_dict(self) -> Dict[str, Any]:
        """Serialise mutable logger state to a JSON-safe dict."""
        return {
            "program":        self.program,
            "version":        self.version,
            "config":         self.config,
            "paradata_dir":   self.paradata_dir,
            "output_counts":  self._output_counts,
            "components_used": self._components_used,
            "skipped":        self._skipped,
            "docs_processed": self._docs_processed,
            "start_iso":      self._start_dt.isoformat(),
            "run_id":         self._run_id,
            "para_cfg":       self._para_cfg,
        }

    @classmethod
    def _from_state_dict(cls, d: Dict[str, Any]) -> "ParadataLogger":
        """Reconstruct a ParadataLogger from a state dict produced by _to_state_dict."""
        inst = cls.__new__(cls)
        inst.program         = d["program"]
        inst.version         = d.get("version", "unknown")
        inst.config          = d["config"]
        inst.paradata_dir    = d["paradata_dir"]
        inst._output_counts  = d["output_counts"]
        inst._components_used = d.get("components_used", {})
        inst._skipped        = d["skipped"]
        inst._docs_processed = d.get("docs_processed", 0)
        inst._run_id         = d["run_id"]
        inst._start_dt       = datetime.fromisoformat(d["start_iso"])
        inst._para_cfg       = d.get("para_cfg", {"components": []})
        inst._input_total    = 0
        inst._finalised      = False
        return inst


# ──────────────────────────────────────────────────────────────────────────────
# CLI shim – used by Bash scripts (atrium-nlp-enrich)
# ──────────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    """
    Thin command-line interface so that Bash scripts can drive the logger via a
    persistent state file in the paradata directory.

    FIX #14: State is now persisted as plain JSON instead of a pickle file.
    This makes the state file human-readable and inspectable after a crash,
    and removes the risk of AttributeError when the ParadataLogger class
    definition changes between invocations during development.

    Commands
    --------
    start   --program NAME --config KEY=VAL [KEY=VAL ...]  [--paradata-dir DIR]
    skip    --state STATE_FILE --file PATH --reason REASON
    success --state STATE_FILE --type TYPE [--count N]
    finish  --state STATE_FILE [--input-total N]
    """
    import argparse

    p = argparse.ArgumentParser(prog="python atrium_paradata.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    # start
    s = sub.add_parser("start")
    s.add_argument("--program",      required=True)
    s.add_argument("--config",       nargs="*", default=[],
                   help="KEY=VALUE pairs")
    s.add_argument("--output-types", nargs="*", default=[])
    s.add_argument("--paradata-dir", default="paradata")
    s.add_argument("--component",    nargs="*", default=[],
                   help="Conditional component name(s) exercised this run "
                        "(looked up in para_config.txt for their license).")

    # skip
    sk = sub.add_parser("skip")
    sk.add_argument("--state",  required=True)
    sk.add_argument("--file",   required=True)
    sk.add_argument("--reason", required=True)

    # success
    su = sub.add_parser("success")
    su.add_argument("--state", required=True)
    su.add_argument("--type",  required=True)
    su.add_argument("--count", type=int, default=1)
    su.add_argument("--component", nargs="*", default=[],
                    help="Conditional component name(s) to record on success.")

    # component (record a conditional component without any success/skip event)
    co = sub.add_parser("component")
    co.add_argument("--state",     required=True)
    co.add_argument("--name",      required=True)
    co.add_argument("--license",   default=None)

    # finish
    fi = sub.add_parser("finish")
    fi.add_argument("--state",       required=True)
    fi.add_argument("--input-total", type=int, default=None)

    args = p.parse_args()

    if args.cmd == "start":
        cfg: Dict[str, Any] = {}
        for kv in (args.config or []):
            k, _, v = kv.partition("=")
            cfg[k.strip()] = v.strip()
        logger = ParadataLogger(
            program=args.program,
            config=cfg,
            paradata_dir=args.paradata_dir,
            output_types=args.output_types or None,
        )
        # record any conditional components named at start time
        for name in (args.component or []):
            logger.log_component(name)
        # FIX #14: persist state as JSON, not pickle.
        state_path = os.path.join(
            args.paradata_dir, f".state_{logger._run_id}_{args.program}.json"
        )
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(logger._to_state_dict(), fh, ensure_ascii=False)
        # print the state file path so the shell script can capture it
        print(state_path)

    elif args.cmd in ("skip", "success", "component", "finish"):
        # FIX #14: load from JSON instead of pickle.
        with open(args.state, "r", encoding="utf-8") as fh:
            state_dict = json.load(fh)
        logger = ParadataLogger._from_state_dict(state_dict)

        if args.cmd == "skip":
            logger.log_skip(args.file, args.reason)
        elif args.cmd == "success":
            logger.log_success(args.type, args.count)
            for name in (args.component or []):
                logger.log_component(name)
        elif args.cmd == "component":
            logger.log_component(args.name, args.license)
        elif args.cmd == "finish":
            logger.finalize(input_total=getattr(args, "input_total", None))
            os.remove(args.state)
            return

        # persist updated state
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(logger._to_state_dict(), fh, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sanitise(obj: Any, _depth: int = 0) -> Any:
    """Recursively coerce a dict/list to be JSON-serialisable."""
    if _depth > 10:
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _sanitise(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v, _depth + 1) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)




def merge_run_paradata(
    json_paths: List[str],
    out_path: str,
    pipeline: Optional[str] = None,
) -> str:
    """
    Merge the per-stage paradata JSONs of ONE end-to-end nlp-enrich run into a
    single summary record describing every processing stage and the
    intermediate file formats produced.

    Run-centric sibling of merge_paradata_files(): instead of "one input file
    through several repos", it captures "one run through several sequential
    stages of THIS repo" (api_1_manifest → api_2_udp → api_3_nt → api_4_stats,
    optionally keywords).

    The effective license is re-derived from the UNION of all components used
    across the stages (via merge_effective_licenses), so the end-to-end
    most-restrictive rule holds: a core enrichment run is CC BY-NC-SA 4.0
    (NameTag/UDPipe models), and a run that additionally exercised an AGPL-3.0
    component (YAKE) escalates accordingly — even if an individual stage was
    less restrictive.

    Parameters
    ----------
    json_paths : ordered list of per-stage paradata JSON paths (execution order)
    out_path   : where to write the merged summary JSON
    pipeline   : optional human label for the pipeline (e.g. "nlp-enrich")
    """
    stages: List[Dict[str, Any]] = []
    license_blocks: List[Dict[str, Any]] = []
    formats: Dict[str, int] = {}
    total_duration = 0.0
    total_inputs = 0
    total_processed = 0
    total_skipped = 0
    all_skips: List[Dict[str, Any]] = []
    repo = ""
    tool_version = ""
    earliest: Optional[str] = None
    latest: Optional[str] = None

    for order, p in enumerate(json_paths, 1):
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        repo = repo or data.get("repository", "")
        tool_version = tool_version or data.get("tool_version", "")

        cfg = data.get("config", {}) or {}
        stats = data.get("statistics", {}) or {}
        out_counts = stats.get("output_counts_by_type", {}) or {}

        # accumulate intermediate output formats across stages
        for ftype, cnt in out_counts.items():
            formats[ftype] = formats.get(ftype, 0) + int(cnt or 0)

        total_duration += float(data.get("duration_seconds") or 0.0)
        total_inputs += int(stats.get("input_files_total") or 0)
        total_processed += int(stats.get("successfully_processed") or 0)
        total_skipped += int(stats.get("skipped_files") or 0)
        all_skips.extend(data.get("skipped_files_detail", []) or [])

        st = data.get("start_time")
        en = data.get("end_time")
        if st and (earliest is None or st < earliest):
            earliest = st
        if en and (latest is None or en > latest):
            latest = en

        stages.append({
            "order":            order,
            "program":          data.get("program"),
            "script":           cfg.get("script"),
            "run_id":           data.get("run_id"),
            "input_dir":        cfg.get("input_dir"),
            "output_dir":       cfg.get("output_dir") or cfg.get("output_manifest"),
            "output_formats":   out_counts,
            "duration_seconds": data.get("duration_seconds"),
            "license":          data.get("license"),
            "input_files_total":      stats.get("input_files_total"),
            "successfully_processed": stats.get("successfully_processed"),
            "skipped_files":          stats.get("skipped_files"),
        })

        if data.get("license_detail"):
            license_blocks.append(data["license_detail"])

    if merge_effective_licenses is not None and license_blocks:
        merged_lic = merge_effective_licenses(license_blocks)
        # Deduplicate the component catalogue for readability: the union across
        # stages repeats always-on components (nametag3_models, udpipe2_models)
        # once per stage. Collapse to unique (name, license) pairs — cosmetic;
        # does not change the already-computed effective license.
        seen = set()
        unique_components = []
        for comp in merged_lic.get("components", []):
            key = (comp.get("name"), comp.get("license"))
            if key not in seen:
                seen.add(key)
                unique_components.append(comp)
        merged_lic["components"] = unique_components
    else:
        # Fallback only if para_licenses.py is unavailable or no stage emitted a
        # license_detail block (e.g. logs from an older logger version).
        merged_lic = {
            "effective_license": LICENSE_NAME,
            "effective_license_url": LICENSE_URL,
            "notes": "License helper unavailable or no per-stage license detail; "
                     "defaulted conservatively to CC BY-NC 4.0.",
        }

    payload = {
        "schema_version":  "2.0",
        "record_type":     "pipeline-run-merged",
        "pipeline":        pipeline or "nlp-enrich",
        "repository":      repo or _REPO_URLS.get("nlp-enrich", "https://github.com/ufal"),
        "tool_version":    tool_version,
        "run_id":          datetime.now(tz=timezone.utc).strftime("%y%m%d-%H%M%S"),
        "stage_count":     len(stages),
        "pipeline_stages": stages,
        "intermediate_formats": formats,
        "license":         merged_lic["effective_license"],
        "license_url":     merged_lic["effective_license_url"],
        "license_detail":  merged_lic,
        "start_time":      earliest or "",
        "end_time":        latest or "",
        "total_duration_seconds": round(total_duration, 3),
        "statistics": {
            "stages_total":           len(stages),
            "input_files_total":      total_inputs,
            "successfully_processed": total_processed,
            "skipped_files":          total_skipped,
        },
        "skipped_files_detail": all_skips,
        "merged_at":       datetime.now(tz=timezone.utc).isoformat(),
    }

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"[paradata] Merged pipeline-run log \u2192 {out_path}", flush=True)
    return out_path



if __name__ == "__main__":
    _cli()
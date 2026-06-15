#!/usr/bin/env python3
"""
run_pipeline.py — End-to-end orchestrator for the ATRIUM nlp-enrich pipeline.

Runs the four shell stages in order

    api_1_manifest.sh → api_2_udp.sh → api_3_nt.sh → api_4_stats.sh

optionally followed by the keyword-extraction stage (keywords.py) and the
optional LLM semantic-enrichment stage (llm_run.py), then merges every
per-stage paradata JSON produced during THIS run into a single
``pipeline-run-merged`` summary record via
``atrium_paradata.merge_run_paradata``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from atrium_paradata import merge_run_paradata
import importlib.util

# ───────────────────────────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent
_CONFIG_NAME = "config_api.txt"
_STAGE_SPACING_SECONDS = 1.1

_CORE_STAGES: Dict[str, Tuple[str, str]] = {
    "manifest": ("api_1_manifest.sh", "Generate manifest"),
    "udp": ("api_2_udp.sh", "UDPipe morphology/syntax"),
    "nt": ("api_3_nt.sh", "NameTag NER"),
    "stats": ("api_4_stats.sh", "Statistics + TEITOK"),
}
_CORE_ORDER = ["manifest", "udp", "nt", "stats"]

_RUNNER_ENV_VARS = (
    "ATRIUM_RUNNER_IMAGE",
    "ATRIUM_RUNNER_REPO",
    "ATRIUM_RUNNER_REF",
)


# ───────────────────────────────────────────────────────────────────────────────
# config_api.txt parsing
# ───────────────────────────────────────────────────────────────────────────────

def _parse_config(config_path: Path) -> Dict[str, str]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Run the pipeline from the repository root, or pass --config."
        )

    values: Dict[str, str] = {}
    assign_re = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

    with open(config_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = assign_re.match(line)
            if not m:
                continue
            key, rhs = m.group(1), m.group(2).strip()

            if rhs and rhs[0] in ("'", '"'):
                quote = rhs[0]
                end = rhs.find(quote, 1)
                if end != -1:
                    rhs = rhs[1:end]
                else:
                    rhs = rhs[1:]
            else:
                rhs = rhs.split("#", 1)[0].strip()

            def _expand(match: "re.Match[str]") -> str:
                name = match.group(1) or match.group(2)
                return values.get(name, os.environ.get(name, ""))

            rhs = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                         _expand, rhs)

            values[key] = rhs

    return values


def _config_bool(values: Dict[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _build_stage_env(config_path: Optional[Path] = None) -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("ATRIUM_RUNNER_REPO", "https://github.com/ufal/atrium-nlp-enrich")
    if config_path is not None:
        env["ATRIUM_CONFIG"] = str(config_path.resolve())
    return env


def _snapshot_paradata_dir(paradata_dir: Path) -> set:
    if not paradata_dir.exists():
        return set()
    out = set()
    for p in paradata_dir.glob("*.json"):
        if p.name.startswith(".state_"):
            continue
        out.add(p.resolve())
    return out


def _new_paradata_files(paradata_dir: Path, before: set) -> List[Path]:
    current: List[Path] = []
    if not paradata_dir.exists():
        return current
    for p in paradata_dir.glob("*.json"):
        if p.name.startswith(".state_"):
            continue
        rp = p.resolve()
        if rp not in before:
            current.append(rp)
    current.sort(key=lambda x: x.stat().st_mtime)
    return current


def _sweep_stale_state_files(paradata_dir: Path) -> List[str]:
    removed: List[str] = []
    if not paradata_dir.exists():
        return removed
    for p in paradata_dir.glob(".state_*.json"):
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def _keybert_deps_preflight() -> None:
    missing = []
    try:
        import torch
    except Exception as exc:
        missing.append(f"torch ({exc})")

    try:
        import torchvision
    except Exception:
        pass

    import sys
    if "torchvision" in sys.modules and not hasattr(sys.modules["torchvision"], "extension"):
        import types
        sys.modules["torchvision"].extension = types.ModuleType("torchvision.extension")
        sys.modules["torchvision"].extension._HAS_OPS = False

    try:
        import transformers
        real_classes = {}
        try:
            from transformers.modeling_utils import PreTrainedModel
            real_classes["PreTrainedModel"] = PreTrainedModel
        except Exception:
            pass
        try:
            from transformers.tokenization_utils import PreTrainedTokenizer
            real_classes["PreTrainedTokenizer"] = PreTrainedTokenizer
        except Exception:
            pass
        try:
            from transformers.configuration_utils import PretrainedConfig
            real_classes["PretrainedConfig"] = PretrainedConfig
        except Exception:
            pass
        try:
            from transformers.models.auto import AutoModel, AutoTokenizer, AutoProcessor, AutoConfig, \
                AutoFeatureExtractor, AutoImageProcessor
            real_classes["AutoModel"] = AutoModel
            real_classes["AutoTokenizer"] = AutoTokenizer
            real_classes["AutoProcessor"] = AutoProcessor
            real_classes["AutoConfig"] = AutoConfig
            real_classes["AutoFeatureExtractor"] = AutoFeatureExtractor
            real_classes["AutoImageProcessor"] = AutoImageProcessor
        except Exception:
            pass
        try:
            from transformers.processing_utils import ProcessorMixin
            real_classes["ProcessorMixin"] = ProcessorMixin
        except Exception:
            pass
        try:
            from transformers.feature_extraction_utils import BatchFeature
            real_classes["BatchFeature"] = BatchFeature
        except Exception:
            pass
        try:
            from transformers.trainer import Trainer
            real_classes["Trainer"] = Trainer
        except Exception:
            pass
        try:
            from transformers.training_args import TrainingArguments
            real_classes["TrainingArguments"] = TrainingArguments
        except Exception:
            pass

        class DummyPreTrained:
            pass

        _to_patch = (
            "PreTrainedModel", "PreTrainedTokenizer", "PretrainedConfig",
            "AutoModel", "AutoTokenizer", "AutoProcessor", "AutoConfig",
            "AutoFeatureExtractor", "AutoImageProcessor",
            "ProcessorMixin", "BatchFeature", "Trainer", "TrainingArguments"
        )

        for attr in _to_patch:
            try:
                _ = getattr(transformers, attr)
            except Exception:
                val = real_classes.get(attr, DummyPreTrained)
                setattr(transformers, attr, val)
                if "transformers" in sys.modules:
                    sys.modules["transformers"].__dict__[attr] = val
    except Exception:
        pass

    for mod in ("sentence_transformers", "keybert"):
        try:
            __import__(mod)
        except Exception as exc:
            missing.append(f"{mod} ({exc})")

    if missing:
        raise ImportError(
            "Keyword method 'keybert' requires the following package(s) which "
            f"failed to import: {', '.join(missing)}.\n"
            "  Fix:\n"
            "    pip install keybert sentence-transformers torch\n"
            "  Or use the CPU-only YAKE backend:  --kw-method yake"
        )


def _llm_deps_preflight() -> None:
    missing = []
    try:
        import torch
    except Exception as exc:
        missing.append(f"torch ({exc})")
    try:
        import transformers
    except Exception as exc:
        missing.append(f"transformers ({exc})")
    if missing:
        raise ImportError(
            "The --llm stage requires the following package(s) which are not "
            f"installed properly: {', '.join(missing)}."
        )


class StageResult:
    def __init__(self, name: str, label: str, returncode: int,
                 paradata: Optional[Dict[str, Any]],
                 paradata_path: Optional[Path]) -> None:
        self.name = name
        self.label = label
        self.returncode = returncode
        self.paradata = paradata
        self.paradata_path = paradata_path

    @property
    def stats(self) -> Dict[str, Any]:
        if not self.paradata:
            return {}
        return self.paradata.get("statistics", {}) or {}


def _run_subprocess(cmd: List[str], env: Dict[str, str], cwd: Path) -> int:
    print(f"\n──> $ {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
    proc = subprocess.run(cmd, env=env, cwd=str(cwd))
    return proc.returncode


def _collect_stage_paradata(paradata_dir: Path, before: set) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    new_files = _new_paradata_files(paradata_dir, before)
    if not new_files:
        return None, None
    newest = new_files[-1]
    try:
        with open(newest, "r", encoding="utf-8") as fh:
            return json.load(fh), newest
    except (OSError, json.JSONDecodeError):
        return None, newest


def _is_empty_failure(stats: Dict[str, Any], strict: bool = False) -> bool:
    if strict:
        return int(stats.get("processed", stats.get("successfully_processed", 0))) == 0

    total = int(stats.get("input_files_total") or 0)
    processed = int(stats.get("successfully_processed") or 0)
    skipped = int(stats.get("skipped_files") or 0)
    if total <= 0:
        return False
    if processed > 0:
        return False
    if skipped >= total and total > 0:
        return False
    return True


def _build_plan(args: argparse.Namespace, values: Dict[str, str]) -> Dict[str, Any]:
    core = [s for s in _CORE_ORDER if s in args.stages]

    output_dir = values.get("OUTPUT_DIR", "")
    paradata_dir = values.get("PARADATA_DIR") or (
        f"{output_dir}/paradata" if output_dir else "paradata"
    )

    plan_stages: List[Dict[str, str]] = []
    for name in core:
        script, label = _CORE_STAGES[name]
        plan_stages.append({"name": name, "script": script, "label": label})
    if getattr(args, "kw", False):
        plan_stages.append({
            "name": "keywords",
            "script": "keywords.py",
            "label": f"Keyword extraction ({getattr(args, 'kw_method', 'yake')})",
        })
    if getattr(args, "llm", False):
        plan_stages.append({
            "name": "llm",
            "script": "llm_run.py",
            "label": "LLM semantic enrichment",
        })

    fail_on_empty = False if getattr(args, "force", False) else _config_bool(values, "FAIL_ON_EMPTY", True)

    return {
        "repository": "https://github.com/ufal/atrium-nlp-enrich",
        "config_file": str(getattr(args, "config", "config_api.txt")),
        "output_dir": output_dir,
        "paradata_dir": paradata_dir,
        "input_tables_dir": values.get("INPUT_TABLES_DIR", ""),
        "fail_on_empty": fail_on_empty,
        "kw": bool(getattr(args, "kw", False)),
        "kw_method": getattr(args, "kw_method", "yake"),
        "llm": bool(getattr(args, "llm", False)),
        "llm_config": getattr(args, "llm_config", "llm_config.txt"),
        "stage_plan": plan_stages,
        "runner_provenance": {
            v: os.environ.get(v, "") for v in _RUNNER_ENV_VARS
        },
    }


def _space_stages(last_start: Optional[float]) -> float:
    now = time.time()
    if last_start is not None:
        elapsed = now - last_start
        if elapsed < _STAGE_SPACING_SECONDS:
            time.sleep(_STAGE_SPACING_SECONDS - elapsed)
    return time.time()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the ATRIUM nlp-enrich pipeline end-to-end and merge "
                    "per-stage paradata into a single run record.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=Path, default=_REPO_ROOT / _CONFIG_NAME)
    parser.add_argument("--stages", nargs="+", choices=_CORE_ORDER, default=list(_CORE_ORDER))
    parser.add_argument("--kw", action="store_true")
    parser.add_argument("--kw-method", default="yake", choices=["legacy", "yake", "keybert"])
    parser.add_argument("-n", "--num-keywords", type=int, default=None, help="Number of keywords to extract")
    parser.add_argument("--kw-fallback", action="store_true", help="Fallback to yake if keybert fails")
    parser.add_argument("--strict-empty", action="store_true", help="Treat all-skipped runs as failures")
    parser.add_argument("--lang", default="cs", help="Language code passed to extraction")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--llm-config", default="llm_config.txt")
    parser.add_argument("--merged-out", default=None)
    parser.add_argument("--clean-state", action="store_true")
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-config", choices=["json"], default=None)
    args = parser.parse_args(argv)

    values = _parse_config(args.config)
    plan = _build_plan(args, values)

    if args.print_config == "json":
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    output_dir = plan["output_dir"]
    paradata_dir = Path(plan["paradata_dir"])
    fail_on_empty = plan["fail_on_empty"]
    effective_kw_method = args.kw_method

    print("=== ATRIUM nlp-enrich pipeline runner ===")
    print(f"    config:        {args.config}")
    print(f"    output_dir:    {output_dir or '(unset)'}")
    print(f"    paradata_dir:  {paradata_dir}")

    try:
        if args.kw and effective_kw_method == "keybert":
            _keybert_deps_preflight()
        if getattr(args, "llm", False):
            _llm_deps_preflight()
    except ImportError as exc:
        if args.kw and effective_kw_method == "keybert" and getattr(args, "kw_fallback", False):
            print(f"\n[WARNING] KeyBERT missing, falling back to YAKE: {exc}", file=sys.stderr)
            effective_kw_method = "yake"
        elif args.force:
            print(f"\n[WARNING] Dependency preflight failed:\n{exc}\n[WARNING] --force enabled. Bypassing crash.",
                  file=sys.stderr)
        else:
            print(f"\n[ERROR] Dependency preflight failed:\n{exc}", file=sys.stderr)
            return 3

    if args.dry_run:
        print("\n[dry-run] Configuration valid; stage plan resolved. No stages executed.")
        return 0

    if args.clean_state:
        _sweep_stale_state_files(paradata_dir)

    env = _build_stage_env(config_path=args.config)
    if args.force:
        env["ATRIUM_FORCE_RUN"] = "1"

    before = _snapshot_paradata_dir(paradata_dir)
    results: List[StageResult] = []
    last_start: Optional[float] = None
    empty_failures: List[str] = []

    for name in [s["name"] for s in plan["stage_plan"] if s["name"] in _CORE_ORDER]:
        script, label = _CORE_STAGES[name]
        script_path = _REPO_ROOT / script
        if not script_path.exists():
            return 2

        last_start = _space_stages(last_start)
        snapshot = _snapshot_paradata_dir(paradata_dir)
        print(f"\n=== Stage: {name} — {label} ===")
        rc = _run_subprocess(["bash", str(script_path)], env, _REPO_ROOT)

        paradata, ppath = _collect_stage_paradata(paradata_dir, snapshot)
        results.append(StageResult(name, label, rc, paradata, ppath))

        if rc != 0 and not args.force:
            _finalize_merge(results, paradata_dir, args, before)
            return rc

        if paradata is not None and _is_empty_failure(paradata.get("statistics", {}), strict=args.strict_empty):
            empty_failures.append(name)

    if getattr(args, "kw", False):
        last_start = _space_stages(last_start)
        snapshot = _snapshot_paradata_dir(paradata_dir)
        kw_method = effective_kw_method

        def run_kw(method):
            label = f"Keyword extraction ({method})"
            print(f"\n=== Stage: keywords — {label} ===")
            suffix_l = {"legacy": "l", "yake": "y", "keybert": "kb"}.get(method, method)
            suffix_u = {"legacy": "L", "yake": "Y", "keybert": "KB"}.get(method, method.upper())

            kw_out_file = str(Path(output_dir) / f"keywords_summary_{suffix_l}.csv")
            kw_per_doc_dir = str(Path(output_dir) / f"KW_PER_DOC_{suffix_u}")

            cmd = [
                sys.executable, str(_REPO_ROOT / "keywords.py"),
                "-i", str(Path(output_dir) / "UDP"),
                "-m", method,
                "-o", kw_out_file,
                "-d", kw_per_doc_dir,
                "--paradata-dir", str(paradata_dir),
                "-l", args.lang
            ]
            if args.num_keywords is not None:
                cmd.extend(["-n", str(args.num_keywords)])

            return _run_subprocess(cmd, env, _REPO_ROOT), label

        rc, label = run_kw(kw_method)

        if rc == 4 and getattr(args, "kw_fallback", False) and kw_method == "keybert":
            print("\n[WARNING] KeyBERT runtime load failed. Re-running with YAKE.", file=sys.stderr)
            kw_method = "yake"
            rc, label = run_kw(kw_method)

        paradata, ppath = _collect_stage_paradata(paradata_dir, snapshot)
        results.append(StageResult("keywords", label, rc, paradata, ppath))

        if rc != 0 and not args.force:
            _finalize_merge(results, paradata_dir, args, before)
            return rc

        if paradata is not None and _is_empty_failure(paradata.get("statistics", {}), strict=args.strict_empty):
            empty_failures.append("keywords")

    if getattr(args, "llm", False):
        last_start = _space_stages(last_start)
        llm_paradata_dir = _resolve_llm_paradata_dir(args.llm_config, paradata_dir)
        snapshot = _snapshot_paradata_dir(llm_paradata_dir)
        print(f"\n=== Stage: llm — LLM semantic enrichment ===")
        cmd = [sys.executable, str(_REPO_ROOT / "llm_run.py"), args.llm_config]
        rc = _run_subprocess(cmd, env, _REPO_ROOT)
        paradata, ppath = _collect_stage_paradata(llm_paradata_dir, snapshot)
        results.append(StageResult("llm", "LLM semantic enrichment", rc, paradata, ppath))

        if rc != 0 and not args.force:
            _finalize_merge(results, paradata_dir, args, before)
            return rc

        if paradata is not None and _is_empty_failure(paradata.get("statistics", {}), strict=args.strict_empty):
            empty_failures.append("llm")

    _finalize_merge(results, paradata_dir, args, before)

    if empty_failures and fail_on_empty:
        print(f"\n[FAIL] Stage(s) processed nothing despite having input and no resume: {', '.join(empty_failures)}.",
              file=sys.stderr)
        return 1

    return 0


def _resolve_llm_paradata_dir(llm_config_path: str, default_dir: Path) -> Path:
    p = Path(llm_config_path)
    if not p.exists(): return default_dir
    try:
        values = _parse_config(p)
        if values.get("PARADATA_DIR"):
            return Path(values["PARADATA_DIR"])
    except Exception:
        pass
    return default_dir


def _finalize_merge(results: List[StageResult], paradata_dir: Path,
                    args: argparse.Namespace, before: set) -> Optional[str]:
    ordered_paths: List[str] = []
    for r in results:
        if r.paradata_path is not None and Path(r.paradata_path).exists():
            ordered_paths.append(str(r.paradata_path))

    if not ordered_paths:
        ordered_paths = [str(p) for p in _new_paradata_files(paradata_dir, before)]

    if not ordered_paths:
        return None

    if args.merged_out:
        out_path = args.merged_out
    else:
        from datetime import datetime, timezone
        run_id = datetime.now(tz=timezone.utc).strftime("%y%m%d-%H%M%S")
        paradata_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(paradata_dir / f"{run_id}_nlp-enrich_pipeline-run.json")

    try:
        return merge_run_paradata(ordered_paths, out_path, pipeline="nlp-enrich")
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())